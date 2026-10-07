"""KRX Open API 일별매매정보 클라이언트 — 가짜 KRX(httpx.MockTransport)로만 (네트워크 없음).

응답 모양은 2026-09-28 probe 실측: 행은 `OutBlock_1`, 빈 날은 HTTP 200 `{"OutBlock_1": []}`,
인증 오류는 HTTP 401 (docs/probe_results.md #15).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.krx.client import FUT_DAILY, OPT_DAILY, KrxClient, KrxError

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"
KEY = "krx-SECRET-key-0123456789"
D23 = date(2026, 9, 23)


def opt_rows() -> list[dict[str, Any]]:
    return json.loads((FIX / "opt_daily.json").read_text(encoding="utf-8"))["20260923"]


def client(
    handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> tuple[KrxClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    return KrxClient(SecretStr(KEY), transport=httpx.MockTransport(handle), **kw), seen


def test_daily_sends_the_key_as_a_header_and_returns_the_rows() -> None:
    rows = opt_rows()
    c, seen = client(lambda _: httpx.Response(200, json={"OutBlock_1": rows}))
    assert c.daily(OPT_DAILY, D23) == rows
    (req,) = seen
    assert req.method == "GET" and req.url.path == "/svc/apis/drv/opt_bydd_trd"
    assert dict(req.url.params) == {"basDd": "20260923"}
    assert req.headers["AUTH_KEY"] == KEY and KEY not in str(req.url)
    assert req.extensions["timeout"] == {"connect": 5.0, "read": 30.0, "write": 30.0, "pool": 30.0}


def test_an_empty_day_is_an_empty_list() -> None:
    c, _ = client(lambda _: httpx.Response(200, json={"OutBlock_1": []}))
    assert c.daily(FUT_DAILY, date(2026, 9, 24)) == []


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(401, text=f"Unauthorized Key {KEY}\nsecond line"), "HTTP 401"),
        (httpx.Response(200, json={"respCode": "401", "respMsg": KEY}), "응답 형식"),
        (httpx.Response(200, json={"OutBlock_1": ["not a row"]}), "응답 형식"),
        (httpx.Response(200, json=[{"OutBlock_1": []}]), "객체가 아니다"),
        (httpx.Response(200, text="<html>maintenance</html>"), "JSON 이 아니다"),
    ],
)
def test_bad_responses_raise_krx_error_without_the_key(
    response: httpx.Response, match: str
) -> None:
    """인증 오류 같은 본문을 빈 날로 읽지 않는다 — `OutBlock_1` 이 없으면 오류."""
    c, _ = client(lambda _: response)
    with pytest.raises(KrxError, match=match) as e:
        c.daily(OPT_DAILY, D23)
    assert KEY not in str(e.value) and "second line" not in str(e.value)
    assert e.value.status == response.status_code
    assert e.value.__cause__ is None


def test_transport_errors_are_redacted_and_not_chained() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed AUTH_KEY={KEY}")

    c, _ = client(boom)
    with pytest.raises(KrxError, match="ConnectError") as e:
        c.daily(OPT_DAILY, D23)
    assert KEY not in str(e.value) and "***" in str(e.value)
    assert e.value.status is None and e.value.__cause__ is None and e.value.__suppress_context__


class Chunks(httpx.SyncByteStream):
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    def __iter__(self) -> Iterator[bytes]:
        yield from self.parts


def test_a_slow_trickle_stops_at_the_total_deadline() -> None:
    t = [0.0]

    def clock() -> float:
        t[0] += 50.0  # 조각마다 50초 — 120초를 넘는다
        return t[0]

    c, _ = client(lambda _: httpx.Response(200, stream=Chunks([b"x" * 10] * 10)), clock=clock)
    with pytest.raises(KrxError, match="120초"):
        c.daily(OPT_DAILY, D23)


def test_an_oversized_response_is_refused() -> None:
    c, _ = client(lambda _: httpx.Response(200, stream=Chunks([b"x" * 60] * 2)), max_bytes=100)
    with pytest.raises(KrxError, match="100B"):
        c.daily(OPT_DAILY, D23)


def test_the_key_never_shows_in_repr_and_must_be_set() -> None:
    c, _ = client(lambda _: httpx.Response(200, json={"OutBlock_1": []}))
    assert KEY not in repr(c)
    with pytest.raises(ValueError):
        KrxClient(SecretStr("  "))
    with pytest.raises(KrxError, match="KRX_API_KEY"):
        KrxClient.from_settings(Settings(_env_file=None, krx_api_key=None))  # pyright: ignore[reportCallIssue]
    s = Settings(_env_file=None, krx_api_key=KEY)  # pyright: ignore[reportCallIssue]
    assert KEY not in repr(KrxClient.from_settings(s))
