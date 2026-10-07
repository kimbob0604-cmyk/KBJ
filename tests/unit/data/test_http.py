"""HTTP 공통(kbj.data.http) — ETF-Traker `board/tests/test_http_reason.py` 승격 + 새 시험.

승격(unittest → pytest 함수, 단언은 그대로): WhyTest 6 · NoBacktrackTest 4 · GetTest 4.
같은 파일의 DatagoKeyFormTest 5 는 공공데이터포털 전송(`kbj/data/datago.py` 의 `key_forms`·`call`)
시험이라 묶음 C 가 옮긴다.

non-200 의 사유는 상태코드가 아니라 본문에 있다(CLAUDE.md 절대 규칙 4). 2026-09-01 공공데이터포털이
403 을 줬는데 로그에는 "HTTP 403" 뿐이라 무엇을 해야 하는지 알 수 없었다. 그 회귀를 막는다.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from kbj.data import http
from kbj.data.http import Fetch, FetchError


class _R:
    def __init__(self, status: int = 200, text: str = "", js: Any = None) -> None:
        self.status_code = status
        self.text = text
        self._js = js

    def json(self) -> Any:
        if self._js is None:
            raise ValueError("not json")
        return self._js


class _S:
    def __init__(self, *replies: _R) -> None:
        self._it = iter(replies)

    def get(self, url: str, params: Any = None, timeout: Any = None) -> _R:
        return next(self._it)


DATAGO_403 = (
    "<OpenAPI_ServiceResponse><cmmMsgHeader>"
    "<errMsg>SERVICE ERROR</errMsg>"
    "<returnAuthMsg>SERVICE_ACCESS_DENIED_ERROR</returnAuthMsg>"
    "<returnReasonCode>20</returnReasonCode>"
    "</cmmMsgHeader></OpenAPI_ServiceResponse>"
)


# ── WhyTest (승격) ──


def test_xml_error_tags() -> None:
    w = http.why(_R(403, DATAGO_403))
    assert "SERVICE_ACCESS_DENIED_ERROR" in w
    assert "returnReasonCode=20" in w


def test_json_message() -> None:
    assert "막힘" in http.why(_R(403, "{}", {"message": "막힘"}))


def test_falls_back_to_body_snippet() -> None:
    w = http.why(_R(500, "  이유를   모를   본문 "))
    assert w == "이유를 모를 본문"


def test_snippet_is_bounded() -> None:
    assert len(http.why(_R(500, "x" * 5000))) <= http.BODY_SNIP


def test_empty_body_gives_empty_string() -> None:
    # 사유가 없는 것과 못 읽은 것은 다르다. 호출자가 구분해 적을 수 있어야
    # 한다 — 빈 문자열을 돌려주지 않으면 "HTTP 403 · " 처럼 구분자만 남는다.
    assert http.why(_R(403, "")) == ""
    assert http.why(_R(403, "   \n  ")) == ""


def test_value_whitespace_and_cdata() -> None:
    assert http.why(_R(403, "<errMsg>\n  막힘\n</errMsg>")) == "errMsg=막힘"
    assert http.why(_R(403, "<errMsg><![CDATA[막힘]]></errMsg>")) == "errMsg=막힘"


# ── NoBacktrackTest (승격) ──
# 사유를 뽑다가 멈추면 안 된다. 첫 판이 두 번 멈췄다. `(.*?)` 는 닫히지 않은 여는 태그가 많은
# 본문에서 O(n²) 였고, 그걸 고치며 넣은 앞뒤 `\s*` 는 값 자리와 같은 공백 구간을 나눠 갖느라 다시
# 멈췄다. 수집기가 부르는 자리라 여기서 멈추면 그날 보드가 통째로 안 나온다.

LIMIT = 2.0


def _under_limit(body: str) -> None:
    t = time.time()
    http.why(_R(403, body))
    dt = time.time() - t
    assert dt < LIMIT, f"{len(body):,}자에 {dt:.1f}초"


def test_many_unclosed_tags() -> None:
    _under_limit("<message>" * 20000 + "x" * 100000)


def test_whitespace_run() -> None:
    _under_limit("<message>" + " " * 300000 + "<" * 100)


def test_interleaved_tags() -> None:
    _under_limit("<errMsg><message>" * 10000 + "y" * 200000)


def test_large_html() -> None:
    _under_limit("<html>" + "<div>가나다</div>" * 40000 + "</html>")


# ── GetTest (승격) ──


def test_non_200_carries_reason() -> None:
    s = _S(*[_R(403, DATAGO_403)] * 3)
    with pytest.raises(Fetch) as cm:
        http.get(s, "https://x/y", params={"serviceKey": "SEKRIT"}, retries=3, backoff=0)
    msg = str(cm.value)
    assert "HTTP 403" in msg
    assert "SERVICE_ACCESS_DENIED_ERROR" in msg
    assert "SEKRIT" not in msg  # 인증키는 여전히 안 샌다


def test_200_but_not_json_raises_with_reason_and_does_not_retry() -> None:
    # 두 번째 응답을 주지 않는다 — 재시도하면 StopIteration 으로 터진다.
    s = _S(_R(200, DATAGO_403))
    with pytest.raises(Fetch) as cm:
        http.get(s, "https://x/y", retries=3, backoff=0)
    assert "SERVICE_ACCESS_DENIED_ERROR" in str(cm.value)


def test_200_json_still_returns() -> None:
    assert http.get(_S(_R(200, '{"a":1}', {"a": 1})), "https://x/y") == {"a": 1}


def test_text_mode_untouched() -> None:
    assert http.get(_S(_R(200, "hi")), "https://x/y", want="text") == "hi"


# ── 새로: 키 지우기 ──

KEY = "fake+svc/key=="  # 합성 값 — URL 에 실리면 %2B·%2F·%3D 로 인코딩된다


def test_scrub_removes_raw_encoded_and_url_forms() -> None:
    params = {"serviceKey": KEY, "numOfRows": 10}
    url = httpx.URL("https://apis.example/x", params=params)
    msg = f"ConnectError: {url} (raw {KEY})"
    out = http.scrub(msg, params)
    assert KEY not in out and "fake%2Bsvc" not in out and "fake+svc" not in out
    assert "numOfRows=10" in out and "***" in out
    # 파라미터를 못 받은 경로에서도 URL 안의 키=값 꼴은 잘린다
    assert "abc123" not in http.scrub("GET https://x/?crtfc_key=abc123&page=1")
    # 형태로 아는 비밀(봇 토큰·JWT 등)도 가린다(kbj.core.masking)
    # 합성 JWT 형태 — 조각을 이어 만든다(공개 안전 검사가 소스의 토큰 형태를 잡는다)
    jwt = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJ0ZXN0In0", "c2lnbmF0dXJl" * 2])
    assert jwt not in http.scrub(f"bad token {jwt}")
    assert "s3cr3t-value" not in http.scrub("boom", secrets=["s3cr3t-value"]) + http.scrub(
        "x s3cr3t-value y", secrets=["s3cr3t-value"]
    )


def test_why_masks_secrets_echoed_in_the_body() -> None:
    body = '{"error": "invalid key", "detail": "AUTH_KEY=raw-key-0123456789"}'
    assert "raw-key" not in http.why(
        _R(401, body, {"error": "invalid AUTH_KEY=raw-key-0123456789"})
    )
    assert "raw-key" not in http.why(_R(401, "x " * 10 + "AUTH_KEY=raw-key-0123456789"))


def test_get_masks_transport_errors() -> None:
    class Boom:
        def get(self, url: str, params: Any = None, timeout: Any = None) -> _R:
            raise ConnectionError(f"failed {url}?serviceKey={params['serviceKey']}")

    slept: list[float] = []
    with pytest.raises(Fetch) as e:
        http.get(Boom(), "https://x/y", params={"serviceKey": KEY}, backoff=0.5, sleep=slept.append)
    assert KEY not in str(e.value) and "ConnectionError" in str(e.value)
    assert slept == [0.5, 1.0]  # 재시도 간격(backoff × 회차) — 시계를 주입한다


def test_fetch_error_fields_and_masking() -> None:
    e = FetchError("KRX", "sto/stk_bydd_trd", 401, "Unauthorized token=abcdef0123")
    assert (e.source, e.dataset, e.status) == ("KRX", "sto/stk_bydd_trd", 401)
    assert str(e).startswith("KRX sto/stk_bydd_trd: Unauthorized") and "abcdef0123" not in str(e)
    assert isinstance(Fetch("x"), FetchError) and str(Fetch("사유")) == "사유"


# ── 새로: 클라이언트·크기/시간 상한 ──


class Chunks(httpx.SyncByteStream):
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    def __iter__(self) -> Iterator[bytes]:
        yield from self.parts


def test_make_client_timeouts_and_no_redirects() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(302, headers={"location": "https://elsewhere.example/"})

    with http.make_client(
        "https://api.example", connect_s=2.0, read_s=7.0, transport=httpx.MockTransport(handle)
    ) as c:
        r = c.get("/a", params={"q": "1"})
    assert (
        r.status_code == 302 and len(seen) == 1
    )  # 넘겨주기를 따라가지 않는다(키가 새 호스트로 안 감)
    assert seen[0].extensions["timeout"] == {"connect": 2.0, "read": 7.0, "write": 7.0, "pool": 7.0}
    with pytest.raises(ValueError):
        http.make_client("https://x", connect_s=0)


def test_get_capped_returns_status_and_body() -> None:
    with http.make_client(
        "https://api.example",
        transport=httpx.MockTransport(lambda r: httpx.Response(404, content=b"none")),
    ) as c:
        assert http.get_capped(c, "/p", max_bytes=100, total_s=5) == (404, b"none")


def test_get_capped_refuses_an_oversized_response() -> None:
    t = httpx.MockTransport(lambda _: httpx.Response(200, stream=Chunks([b"x" * 60] * 2)))
    with (
        http.make_client("https://api.example", transport=t) as c,
        pytest.raises(FetchError, match="100B") as e,
    ):
        http.get_capped(c, "/p", max_bytes=100, total_s=5, source="KRX", dataset="d")
    assert e.value.status == 200 and e.value.source == "KRX"


def test_get_capped_stops_a_slow_trickle_at_the_total_deadline() -> None:
    t_now = [0.0]

    def clock() -> float:
        t_now[0] += 50.0  # 조각마다 50초 — 120초를 넘는다
        return t_now[0]

    t = httpx.MockTransport(lambda _: httpx.Response(200, stream=Chunks([b"x" * 10] * 10)))
    with (
        http.make_client("https://api.example", transport=t) as c,
        pytest.raises(FetchError, match="120초"),
    ):
        http.get_capped(c, "/p", max_bytes=1 << 20, total_s=120, clock=clock)


def test_get_capped_lets_transport_errors_through() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with http.make_client("https://api.example", transport=httpx.MockTransport(boom)) as c:
        with pytest.raises(httpx.ConnectError):
            http.get_capped(c, "/p", max_bytes=10, total_s=1)
        with pytest.raises(ValueError):
            http.get_capped(c, "/p", max_bytes=0, total_s=1)
