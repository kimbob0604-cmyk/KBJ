"""HTTP 공통 — 클라이언트·크기/시간 상한·오류 사유·키 지우기(설계 §1.1).

승격 원본: ETF-Traker `board/ingest/http.py`(`SECRET_PARAMS`·`scrub`·`why`·`get`) + GEXLAB
`data/krx/eod.py`(접속·읽기 시간 제한, 크기 상한, 조각 사이 전체 시간 상한 — `KrxClient._get`).

- 실패는 예외로 올리고 사유를 담는다(절대 규칙 4). 상태 코드만이 아니라 **본문의 사유**를 싣는다 —
  2026-09-01 공공데이터포털 403 이 "HTTP 403" 뿐이라 원인을 못 읽은 회귀(ET test_http_reason).
- 사유·오류 문구는 내보내기 전에 키를 지운다(절대 규칙 5): 쿼리 파라미터로 보내는 인증키 값과
  그 URL 인코딩 형태, `이름=값` 꼴, 그리고 kbj.core.masking 의 비밀값 형태. 가린 자리는 `***`.
- 본문은 앞부분만 본다(정규식 백트래킹 회귀 — ET NoBacktrackTest). 가린 **뒤에** 자른다.
- `get` 은 세션 객체(`requests.Session`·`httpx.Client`·legacy 브리지 세션 — `.get(url, params=,
  timeout=)` 이 있으면 된다)를 받아 재시도한다(ET `get` 그대로 — legacy 가 다시 내보내 쓴다).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final, Protocol
from urllib.parse import quote, quote_plus

import httpx

from kbj.core.masking import MASK, SecretLike, mask_text, redact

TIMEOUT: Final = 25.0
CONNECT_S: Final = 5.0
READ_S: Final = 30.0

# 쿼리 파라미터로 보내는 인증키 이름. 예외 메시지에 URL 이 통째로 실려 오므로 사유를 만들기 전에
# 값을 지운다. DART 는 crtfc_key, 공공데이터포털은 serviceKey, KRX 는 AUTH_KEY(헤더) 다.
SECRET_PARAMS: Final = (
    "crtfc_key",
    "serviceKey",
    "apiKey",
    "auth_key",
    "AUTH_KEY",
    "key",
    "token",
    "access_token",
    "appkey",
    "appsecret",
)
_PARAM_VALUE = re.compile(r"((?:" + "|".join(SECRET_PARAMS) + r")=)[^&\s'\"]+", re.I)

BODY_SNIP: Final = 300
# 정규식에 물리는 본문 길이 상한. 오류 봉투는 사유를 맨 앞에 담으므로 뒤를 볼 이유가 없고, 상한이
# 없으면 큰 본문에서 탐색이 길어진다.
BODY_SCAN: Final = 20_000
# 가린 뒤 자르기: 앞 BODY_SNIP 자 끝에 걸친 비밀값도 통째로 보이게 조금 더 넓게 가린다
_MASK_WINDOW: Final = BODY_SNIP + 512

# 값 자리는 `[^<\]]*` 하나뿐이다. 같은 구간을 훑는 수량자를 겹치면 안 된다(ET 머리말):
#   `(.*?)` 는 닫히지 않은 여는 태그가 많은 본문에서 O(n²), 앞뒤 `\s*` 는 값 자리와 공백 구간을 나눠
#   갖는 경우의 수를 만든다. 값의 앞뒤 공백은 정규식이 아니라 strip 으로 턴다.
_ERR_TAG = re.compile(
    r"<(returnAuthMsg|returnReasonCode|errMsg|resultMsg|resultCode|message)>"
    r"(?:<!\[CDATA\[)?([^<\]]*)(?:\]\]>)?</\1>",
    re.I,
)
_ERR_KEY: Final = ("message", "msg", "resultMsg", "error_description", "error", "msg1")


class FetchError(RuntimeError):
    """수집 실패. 문구에 키가 없다(만들 때 가린다). status = HTTP 상태(응답을 받았을 때)."""

    def __init__(self, source: str, dataset: str, status: int | None, reason: str) -> None:
        self.source = source
        self.dataset = dataset
        self.status = status
        self.reason = mask_text(reason)
        where = " ".join(x for x in (source, dataset) if x)
        super().__init__(f"{where}: {self.reason}" if where else self.reason)


class Fetch(FetchError):
    """ET `board/ingest/http.Fetch` 와 같은 모양(문구 하나) — `get` 이 올린다."""

    def __init__(self, reason: str) -> None:
        super().__init__("", "", None, reason)


class _Response(Protocol):
    status_code: int

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


class _Session(Protocol):
    def get(self, url: str, *, params: Any = ..., timeout: Any = ...) -> Any: ...


def _param_secrets(params: Mapping[str, object] | None) -> list[str]:
    """파라미터의 인증키 값과 그 URL 인코딩 형태(예외 문구의 URL 에는 인코딩된 값이 실린다)."""
    out: list[str] = []
    for k in SECRET_PARAMS:
        v = (params or {}).get(k)
        if v:
            s = str(v)
            out += [s, quote(s, safe=""), quote_plus(s)]
    return out


def scrub(
    msg: object,
    params: Mapping[str, object] | None = None,
    secrets: Iterable[SecretLike] = (),
) -> str:
    """오류 문구에서 자격증명을 지운다.

    연결 예외 문구에는 **쿼리스트링이 든 URL** 이 들어 있다 — 그대로 사유로 올리면 run_log·화면에
    인증키가 남는다. 아는 값(파라미터의 키 값·`secrets`)을 먼저, URL 의 `키=값` 꼴, 그다음 비밀값
    형태(kbj.core.masking)를 가린다.
    """
    out = redact(str(msg), [*_param_secrets(params), *secrets])
    out = _PARAM_VALUE.sub(lambda m: m.group(1) + MASK, out)
    return mask_text(out)


def why(r: _Response) -> str:
    """응답 본문에서 사람이 읽을 사유를 뽑는다. 못 뽑으면 앞부분을 그대로(가린 뒤 자른다).

    공공데이터포털은 오류일 때 `resultType=json` 을 무시하고 XML 을 준다 — JSON 파서로만 보면 사유가
    통째로 사라진다. 본문이 비어 있으면 빈 문자열이다(호출자가 '본문 없음' 을 따로 적는다).
    """
    try:
        body = (r.text or "")[:BODY_SCAN]
    except Exception:  # 본문을 못 읽는 응답 — 사유 없음과 같다(호출자가 상태 코드를 적는다)
        return ""
    hits = [f"{k}={v.strip()}" for k, v in _ERR_TAG.findall(body) if v.strip()]
    if hits:
        return _bounded(" ".join(dict.fromkeys(hits)))
    try:
        js = r.json()
    except Exception:  # JSON 이 아니다 — 아래 본문 앞부분으로
        js = None
    if isinstance(js, dict):
        for k in _ERR_KEY:
            v: object = js.get(k)  # pyright: ignore[reportUnknownMemberType]
            if v:
                return _bounded(f"{k}={v}")
    return _bounded(" ".join(body[: _MASK_WINDOW * 4].split()))


def _bounded(text: str) -> str:
    """가린 뒤 BODY_SNIP 자로 — 응답이 되돌려 준 키(`AUTH_KEY=…` 등)도 사유에 남기지 않는다."""
    return scrub(text[:_MASK_WINDOW])[:BODY_SNIP]


def get(
    s: _Session,
    url: str,
    params: Mapping[str, Any] | None = None,
    retries: int = 3,
    backoff: float = 0.8,
    want: str = "json",
    timeout: float | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """재시도 포함 GET(ET `http.get` 그대로). want='json' 이면 파싱까지, 'text' 면 본문 그대로.

    실패는 `Fetch`(사유에 상태 코드와 본문 사유, 키는 가렸다). 200 인데 JSON 이 아니면 재시도하지
    않는다 — 다시 불러도 같은 것이 온다.
    """
    last: str | None = None
    wait_s = TIMEOUT if timeout is None else timeout
    for i in range(retries):
        try:
            r = s.get(url, params=params, timeout=wait_s)
        except Exception as ex:  # 전송 오류 — 사유를 문자열로 보존하고 다시 시도
            last = scrub(f"{type(ex).__name__}: {ex}", params)
        else:
            if r.status_code == 200:
                if want == "text":
                    return r.text
                try:
                    return r.json()
                except ValueError:
                    reason = why(r) or "본문 비어 있음"
                    raise Fetch(
                        scrub(f"{url} 200 이지만 JSON 이 아니다 — {reason}", params)
                    ) from None
            last = f"HTTP {r.status_code} · {why(r) or '본문 없음'}"
        if i < retries - 1:
            sleep(backoff * (i + 1))
    raise Fetch(scrub(f"{url} 실패: {last}", params))


def make_client(
    base_url: str,
    *,
    connect_s: float = CONNECT_S,
    read_s: float = READ_S,
    transport: httpx.BaseTransport | None = None,
    headers: Mapping[str, str] | None = None,
) -> httpx.Client:
    """접속·읽기 시간 제한이 있는 클라이언트. 넘겨주기(redirect)는 따라가지 않는다 — 키가 붙은
    요청이 다른 호스트로 가지 않게. 시험은 `transport=httpx.MockTransport(...)`."""
    if min(connect_s, read_s) <= 0:
        raise ValueError("시간 제한은 0 보다 커야 한다")
    return httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(read_s, connect=connect_s),
        transport=transport,
        headers=dict(headers or {}),
        follow_redirects=False,
    )


def get_capped(
    client: httpx.Client,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    max_bytes: int,
    total_s: float,
    clock: Callable[[], float] = time.monotonic,
    source: str = "",
    dataset: str = "",
) -> tuple[int, bytes]:
    """상태 코드와 본문 바이트. 조각을 받을 때마다 크기·전체 시간을 본다(GX `KrxClient._get`).

    크기가 `max_bytes` 를 넘거나 전체가 `total_s` 를 넘으면 `FetchError`(받던 것은 버린다).
    전송 오류(`httpx.HTTPError`)는 그대로 올라간다 — 부르는 쪽이 자기 오류로 바꾸며 가린다.
    """
    if max_bytes <= 0 or total_s <= 0:
        raise ValueError("크기 상한·전체 시간은 0 보다 커야 한다")
    deadline = clock() + total_s
    buf = bytearray()
    with client.stream("GET", path, params=dict(params or {}), headers=dict(headers or {})) as r:
        status = r.status_code
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > max_bytes:
                raise FetchError(source, dataset or path, status, f"응답이 {max_bytes}B 보다 크다")
            if clock() > deadline:
                raise FetchError(source, dataset or path, status, f"응답 {total_s:g}초 초과")
        return status, bytes(buf)
