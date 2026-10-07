"""legacy 전용 논리 URL 브리지 — legacy 의 KIS·KRX·DART 직접 호출을 KBJ 어댑터로(설계 §3.7, D-P2-6).

legacy 는 "주소 상수 + `requests`/세션" 으로 부른다. 주소 상수를 **논리 URL** 로 바꾸고 `requests`·
세션 자리에 이 모듈을 끼우면 나머지 코드(파라미터 채우기·파서·재시도)는 그대로 돈다.

스킴별 처리(호출자가 넘긴 키·토큰은 버리고 KBJ 설정 값을 넣는다):

- `kis:/uapi/domestic-stock/v1/quotations/inquire-investor` → `KisRestClient.get`. 토큰(auth 가 둔
  값 — 읽기만)·앱키·시크릿을 넣는다. 한도 `rl:kis` — 기본 P3, 헤더 `x-kbj-priority` 로 바꾼다.
- `kis-master:fo_idx_code_mts.mst.zip` → `master.download_fo_master()`.
- `krx:/sto/stk_bydd_trd` → `KrxClient.daily(endpoint, basDd)`. `AUTH_KEY` 를 넣는다. 한도
  `rl:krx` + `krx:calls:<날짜>`.
- `dart:/list.json` → `DartClient.get_json`·`get_raw`. `crtfc_key` 를 넣는다. 한도 `rl:dart` +
  `budget:dart:<날짜>`.
- `http(s)://…` → **`ValueError`** — 남은 직접 호출이 조용히 새지 않게.

- 응답은 `requests.Response` 모양(`status_code`·`text`·`content`·`headers`·`json()`·
  `raise_for_status()`·`ok`). ET `http.get`·SD `r.json()` 이 그대로 돈다.
- **발급하지 않는다**(ADR 0004). KIS 토큰이 없으면 HTTP 503 + `{"rt_cd": "1", "msg_cd":
  "KBJ_TOKEN_UNAVAILABLE", "msg1": "토큰 없음 — auth 대기"}` — legacy 의 기존 오류 경로
  (`rt_cd != "0"`)가 사유를 문자열로 남긴다.
- KRX·DART 의 실패도 응답으로 돌려준다(legacy 가 원래 보던 모양): DART 상태 코드 오류는 DART 처럼
  HTTP 200 + `{"status": 코드, "message": …}`(zip 엔드포인트는 같은 내용의 XML), 일 예산 소진은
  `020` 과 같은 뜻으로, KRX 는 HTTP 상태(401·429·5xx)와 가린 사유 문구. 전송 오류는
  `BridgeConnectionError`(legacy 의 `except Exception` 이 사유를 남긴다).
- legacy 는 옛 환경변수 이름(`DART_API_KEY`·`KRX_API_KEY`·`KIS_APP_KEY`)을 읽는다. 브리지는 호출자가
  넣은 키 파라미터·헤더를 버리고 KBJ 설정(`KBJ_*`)의 키를 넣는다(docs/secrets.md §2).
- 오류 문구에는 키·토큰이 없다(각 어댑터가 가리고, 여기서 한 번 더 `scrub`).
- 이 모듈은 P9(legacy 삭제)에 지운다. kbj 코드는 이 모듈을 쓰지 않는다 — 어댑터를 바로 쓴다.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final
from xml.sax.saxutils import escape

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.time import utcnow
from kbj.data.budget import BudgetExhausted
from kbj.data.http import scrub
from kbj.data.private.kis.errors import TokenError
from kbj.data.private.kis.master import FO_MASTER_FILE, download_fo_master
from kbj.data.private.kis.rest import KisRestClient
from kbj.data.private.kis.token import CachedTokenProvider, reader
from kbj.data.private.krx.client import KrxClient, KrxError, KrxNotSubscribed
from kbj.data.public.dart.client import DartClient, DartError
from kbj.data.ratelimit import Priority, RateLimitTimeout

__all__ = [
    "PRIORITY_HEADER",
    "SCHEMES",
    "BridgeConnectionError",
    "BridgeError",
    "BridgeHTTPError",
    "BridgeResponse",
    "BridgeSession",
    "access_token_or_none",
    "configure",
    "get",
    "reset",
    "session",
]

SCHEMES: Final = ("kis:", "kis-master:", "krx:", "dart:")
PRIORITY_HEADER: Final = "x-kbj-priority"
DEFAULT_PRIORITY: Final = Priority.P3  # legacy 브리지 기본(설계 §4.2 — 장마감 수집과 같은 등급)
DEFAULT_ACQUIRE_S: Final = 10.0  # 리미터 허가 대기 상한(초) — P3 는 짧게 기다리고 다음 주기로
TOKEN_UNAVAILABLE: Final = "KBJ_TOKEN_UNAVAILABLE"  # noqa: S105 — 오류 코드 이름(비밀 아님)
RATE_LIMIT_TIMEOUT: Final = "KBJ_RATE_LIMIT_TIMEOUT"
BUDGET_EXHAUSTED: Final = "KBJ_BUDGET_EXHAUSTED"
DART_QUOTA: Final = "020"  # DART '요청 제한 초과' — 일 예산 소진도 legacy 에는 같은 뜻으로

# 호출자가 넘긴 자격 증명(옛 환경변수에서 읽은 값)은 버리고 KBJ 설정 값을 넣는다. KIS·KRX 는
# 헤더에서 tr_id·tr_cont·우선순위·basDd 만 골라 쓰므로 나머지(authorization·appkey·AUTH_KEY)는
# 어디에도 실리지 않는다. DART 는 파라미터로 오므로 이름으로 지운다
_CALLER_SECRET_PARAMS: Final = frozenset({"crtfc_key", "auth_key"})

log = logging.getLogger(__name__)


class BridgeError(RuntimeError):
    """브리지 실패의 공통 부모. 문구에 키·토큰이 없다."""


class BridgeHTTPError(BridgeError):
    """`raise_for_status()` — 2xx·3xx 가 아닌 응답. `.response` 에 응답이 있다."""

    def __init__(self, message: str, response: BridgeResponse) -> None:
        super().__init__(message)
        self.response = response


class BridgeConnectionError(BridgeError, ConnectionError):
    """출처에 닿지 못했다(전송 오류·시간 초과) — `requests.ConnectionError` 자리."""


# ── 응답 ─────────────────────────────────────────────────────────────────────────────────


class _Headers(MutableMapping[str, str]):
    """대소문자를 가리지 않는 헤더(`requests.structures.CaseInsensitiveDict` 자리)."""

    def __init__(self, data: Mapping[str, str] | None = None) -> None:
        self._d: dict[str, tuple[str, str]] = {}
        if data:
            self.update(data)

    def __getitem__(self, key: str) -> str:
        return self._d[key.lower()][1]

    def __setitem__(self, key: str, value: str) -> None:
        self._d[key.lower()] = (key, value)

    def __delitem__(self, key: str) -> None:
        del self._d[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return (k for k, _ in self._d.values())

    def __len__(self) -> int:
        return len(self._d)

    def __repr__(self) -> str:  # 값(토큰·키가 섞일 수 있다)은 보이지 않는다
        return f"_Headers({sorted(self)!r})"


@dataclass
class BridgeResponse:
    """`requests.Response` 와 같은 모양의 응답(legacy 가 쓰는 부분만)."""

    status_code: int
    content: bytes
    headers: MutableMapping[str, str] = field(default_factory=lambda: _Headers())
    url: str = ""
    encoding: str = "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, "replace")

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def reason(self) -> str:
        return "OK" if self.ok else "KBJ bridge error"

    def json(self, **kw: Any) -> Any:
        return json.loads(self.text, **kw)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise BridgeHTTPError(f"{self.status_code} for {self.url}: {self.text[:200]}", self)

    def iter_content(self, chunk_size: int = 65536) -> Iterator[bytes]:
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def close(self) -> None:
        return None


def _json_response(status: int, body: object, url: str, **headers: str) -> BridgeResponse:
    h = _Headers({"content-type": "application/json; charset=utf-8", **headers})
    return BridgeResponse(status, json.dumps(body, ensure_ascii=False).encode(), h, url)


def _text_response(status: int, text: str, url: str, ctype: str = "text/plain") -> BridgeResponse:
    h = _Headers({"content-type": f"{ctype}; charset=utf-8"})
    return BridgeResponse(status, text.encode(), h, url)


# ── 어댑터 묶음(프로세스당 하나) ─────────────────────────────────────────────────────────


class _Backends:
    """설정·Redis·어댑터를 처음 쓸 때 만든다. 시험은 `configure` 로 갈아 끼운다."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings: Settings | None = None
        self.redis: Redis | None = None
        self.redis_given = False
        self.kis: KisRestClient | None = None
        self.krx: KrxClient | None = None
        self.dart: DartClient | None = None
        self.master: Callable[[], bytes] | None = None
        self.token_reader: CachedTokenProvider | None = None
        self.now: Callable[[], datetime] = utcnow  # 토큰 만료·예산 날짜 판정 시계(시험이 바꾼다)

    def get_settings(self) -> Settings:
        with self.lock:
            if self.settings is None:
                self.settings = Settings()
            return self.settings

    def get_redis(self) -> Redis | None:
        with self.lock:
            if self.redis is None and not self.redis_given:
                url = self.get_settings().redis_url
                if url is not None:
                    self.redis = Redis.from_url(
                        url.get_secret_value(), socket_connect_timeout=2.0, socket_timeout=5.0
                    )
                self.redis_given = True
            return self.redis

    def get_kis(self) -> KisRestClient:
        with self.lock:
            if self.kis is None:
                r = self.get_redis()
                if r is None:
                    raise _NoToken("KBJ_REDIS_URL 이 없다 — 토큰은 auth 가 Redis 에 둔다")
                self.kis = KisRestClient.for_service(self.get_settings(), r, now=self.now)
            return self.kis

    def get_token_reader(self) -> CachedTokenProvider:
        with self.lock:
            if self.token_reader is None:
                r = self.get_redis()
                if r is None:
                    raise _NoToken("KBJ_REDIS_URL 이 없다 — 토큰은 auth 가 Redis 에 둔다")
                s = self.get_settings()
                self.token_reader = reader(r, s, now=self.now, by=s.service or "legacy")
            return self.token_reader

    def get_krx(self) -> KrxClient:
        with self.lock:
            if self.krx is None:
                self.krx = KrxClient.from_settings(
                    self.get_settings(), self.get_redis(), now=self.now
                )
            return self.krx

    def get_dart(self) -> DartClient:
        with self.lock:
            if self.dart is None:
                s = self.get_settings()
                r = self.get_redis()
                if r is not None:
                    self.dart = DartClient.from_settings(s, r, now=self.now)
                else:
                    self.dart = DartClient(s.dart_api_key, now=self.now)
            return self.dart

    def get_master(self) -> Callable[[], bytes]:
        with self.lock:
            return self.master if self.master is not None else download_fo_master


class _NoToken(TokenError):
    pass


_B = _Backends()


def configure(
    *,
    settings: Settings | None = None,
    redis: Redis | None = None,
    kis: KisRestClient | None = None,
    krx: KrxClient | None = None,
    dart: DartClient | None = None,
    master: Callable[[], bytes] | None = None,
    now: Callable[[], datetime] | None = None,
) -> None:
    """어댑터를 갈아 끼운다(시험·진입점). 준 것만 바꾸고, 설정·Redis 가 바뀌면 그것으로 만든
    어댑터는 버린다."""
    with _B.lock:
        if settings is not None or redis is not None or now is not None:
            _B.kis = _B.krx = _B.dart = None
            _B.token_reader = None
        if now is not None:
            _B.now = now
        if settings is not None:
            _B.settings = settings
        if redis is not None:
            _B.redis = redis
            _B.redis_given = True
        if kis is not None:
            _B.kis = kis
        if krx is not None:
            _B.krx = krx
        if dart is not None:
            _B.dart = dart
        if master is not None:
            _B.master = master


def reset() -> None:
    """처음 상태로(다음 호출에서 환경변수로 다시 만든다)."""
    global _B
    with _B.lock:
        _B = _Backends()


# ── 공개 함수 ─────────────────────────────────────────────────────────────────────────────


def access_token_or_none() -> str | None:
    """auth 가 Redis 에 둔 접근토큰(읽기만). 없으면 None — legacy 가 '토큰 없음' 경로로 간다."""
    try:
        return _B.get_token_reader().get()
    except TokenError as e:
        log.warning("KIS 토큰 없음(auth 대기): %s", type(e).__name__)
        return None


def get(
    url: str,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | tuple[float, float] | None = None,
    **_kw: Any,
) -> BridgeResponse:
    """`requests.get` 자리. 논리 URL(`kis:`·`kis-master:`·`krx:`·`dart:`)만 받는다."""
    scheme = next((s for s in SCHEMES if url.startswith(s)), None)
    if scheme is None:
        raise ValueError(
            "legacy_bridge 는 논리 URL(kis:·kis-master:·krx:·dart:)만 받는다 — "
            f"직접 호출 금지(설계 §3.7): {scrub(url)[:120]}"
        )
    rest = url[len(scheme) :]
    p = {str(k): v for k, v in (params or {}).items() if v is not None}
    h = _Headers({str(k): str(v) for k, v in (headers or {}).items()})
    if scheme == "kis:":
        return _kis(url, rest, p, h, timeout)
    if scheme == "kis-master:":
        return _kis_master(url, rest)
    if scheme == "krx:":
        return _krx(url, rest, p)
    return _dart(url, rest, p)


def post(url: str, *_a: Any, **_kw: Any) -> BridgeResponse:
    """POST 는 받지 않는다 — legacy 의 POST 는 KIS 토큰 발급뿐이었고 발급은 auth 만 한다."""
    raise ValueError(f"legacy_bridge 는 POST 를 받지 않는다(발급은 auth 만 — ADR 0004): {url[:40]}")


class BridgeSession:
    """`requests.Session` 자리 — `.headers` 를 요청마다 합친다."""

    def __init__(self) -> None:
        self.headers: MutableMapping[str, str] = _Headers()

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        timeout: float | tuple[float, float] | None = None,
        headers: Mapping[str, str] | None = None,
        **kw: Any,
    ) -> BridgeResponse:
        merged = _Headers(self.headers)
        merged.update(headers or {})
        return get(url, params=params, headers=merged, timeout=timeout, **kw)

    def post(self, url: str, *a: Any, **kw: Any) -> BridgeResponse:
        return post(url, *a, **kw)

    def close(self) -> None:
        return None

    def __enter__(self) -> BridgeSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "BridgeSession()"


def session() -> BridgeSession:
    """`requests.Session()` 자리."""
    return BridgeSession()


Session = BridgeSession  # `requests.Session()` 로 부르는 legacy 를 위해


# ── 스킴별 처리 ───────────────────────────────────────────────────────────────────────────


def _acquire_timeout(timeout: float | tuple[float, float] | None) -> float:
    if timeout is None:
        return DEFAULT_ACQUIRE_S
    if isinstance(timeout, tuple):
        return float(max(timeout))
    return float(timeout)


def _priority(h: Mapping[str, str]) -> Priority:
    raw = h.get(PRIORITY_HEADER, "").strip().upper()
    if not raw:
        return DEFAULT_PRIORITY
    try:
        return Priority[raw]
    except KeyError:
        raise ValueError(f"{PRIORITY_HEADER} 는 P0~P4: {raw[:8]!r}") from None


def _kis_fail(url: str, msg_cd: str, msg1: str) -> BridgeResponse:
    return _json_response(503, {"rt_cd": "1", "msg_cd": msg_cd, "msg1": msg1}, url)


def _kis(
    url: str,
    path: str,
    params: dict[str, Any],
    h: Mapping[str, str],
    timeout: float | tuple[float, float] | None,
) -> BridgeResponse:
    if not path.startswith("/") or "://" in path:
        raise ValueError(f"kis: 다음에는 '/' 로 시작하는 경로만: {path[:80]!r}")
    tr_id = h.get("tr_id", "").strip()
    if not tr_id:
        raise ValueError("kis: 호출에는 tr_id 헤더가 필요하다")
    prio = _priority(h)
    sent = {k: str(v) for k, v in params.items()}
    try:
        client = _B.get_kis()
        resp = client.get(
            path,
            tr_id,
            sent,
            h.get("tr_cont", ""),
            priority=prio,
            timeout=_acquire_timeout(timeout),
        )
    except TokenError as e:
        return _kis_fail(url, TOKEN_UNAVAILABLE, f"토큰 없음 — auth 대기 ({scrub(e)[:120]})")
    except RateLimitTimeout as e:
        return _kis_fail(url, RATE_LIMIT_TIMEOUT, f"KIS 리미터 대기 초과 — 다음 주기에 ({e})")
    except httpx.HTTPError as e:
        secrets = _B.kis.secrets() if _B.kis is not None else []
        raise BridgeConnectionError(
            scrub(f"KIS {path}: {type(e).__name__}: {e}", None, secrets)
        ) from None
    return _json_response(resp.status, resp.body, url, tr_cont=resp.tr_cont)


def _kis_master(url: str, name: str) -> BridgeResponse:
    if name.strip("/") != FO_MASTER_FILE:
        raise ValueError(f"kis-master: 는 {FO_MASTER_FILE} 만 받는다: {name[:60]!r}")
    try:
        data = _B.get_master()()
    except httpx.HTTPStatusError as e:
        return _text_response(e.response.status_code, f"마스터 HTTP {e.response.status_code}", url)
    except httpx.HTTPError as e:
        raise BridgeConnectionError(f"마스터 내려받기 실패: {type(e).__name__}") from None
    except (TimeoutError, ValueError) as e:
        return _text_response(502, f"마스터 내려받기 실패: {e}", url)
    h = _Headers({"content-type": "application/zip"})
    return BridgeResponse(200, data, h, url)


def _krx(url: str, endpoint: str, params: dict[str, Any]) -> BridgeResponse:
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    raw = str(params.get("basDd", "")).strip()
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError(f"krx: 호출에는 basDd(YYYYMMDD)가 필요하다: {raw[:12]!r}")
    bas_dd = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    try:
        rows = _B.get_krx().daily(endpoint, bas_dd)
    except KrxNotSubscribed as e:
        # 본문 표식을 남긴다 — legacy(SD krx_api 머리말)가 이 문구로 '미구독'을 가른다
        return _text_response(401, f"Unauthorized API Call — {e}", url)
    except KrxError as e:
        if e.status is not None:
            return _text_response(e.status, str(e), url)
        if e.retryable:
            raise BridgeConnectionError(str(e)) from None
        return _text_response(401 if e.critical else 502, str(e), url)
    except BudgetExhausted as e:
        return _text_response(429, f"{BUDGET_EXHAUSTED}: {e}", url)
    except RateLimitTimeout as e:
        return _text_response(503, f"{RATE_LIMIT_TIMEOUT}: {e}", url)
    return _json_response(200, {"OutBlock_1": rows}, url)


def _dart_error_body(code: str, message: str) -> dict[str, str]:
    return {"status": code, "message": message}


def _dart(url: str, endpoint: str, params: dict[str, Any]) -> BridgeResponse:
    name = endpoint.strip("/")
    if name.startswith("api/"):
        name = name[4:]
    sent = {k: v for k, v in params.items() if k.lower() not in _CALLER_SECRET_PARAMS}
    is_json = name.endswith(".json")
    try:
        client = _B.get_dart()
        if is_json:
            return _json_response(200, client.get_json(name, sent, use_cache=False), url)
        data = client.get_raw(name, sent)
    except DartError as e:
        if e.code:
            body = _dart_error_body(e.code, e.message)
        elif e.status is not None:
            return _text_response(e.status, str(e), url)
        else:
            return _text_response(503, f"DART 호출 실패: {e}", url)
    except BudgetExhausted as e:
        body = _dart_error_body(DART_QUOTA, f"{BUDGET_EXHAUSTED}: {e}")
    except RateLimitTimeout as e:
        return _text_response(503, f"{RATE_LIMIT_TIMEOUT}: {e}", url)
    else:
        return BridgeResponse(200, data, _Headers({"content-type": "application/zip"}), url)
    if is_json:
        return _json_response(200, body, url)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<result><status>{escape(body['status'])}</status>"
        f"<message>{escape(body['message'])}</message></result>"
    )
    return _text_response(200, xml, url, "application/xml")
