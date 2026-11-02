"""`global.proxy` — Hyperliquid·야후 시세를 global_quote 에 쓴다(합성 응답)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from kbj.data.private.hyperliquid.client import (
    HyperliquidClient,
    HyperliquidError,
    PerpCtx,
    parse_perp_ctx,
)
from kbj.data.private.yahoo.client import YahooError, YahooThrottled
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.engines.market.global_proxy import yahoo_symbols
from kbj.services.collectors import global_proxy
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import MemoryRepos

NOW = datetime(2026, 11, 2, 1, 7, 31, tzinfo=UTC)
KEYS = (DataKey("HYPERLIQUID", "perp_ctx", "x"), DataKey("YAHOO", "global_quotes", "x"))


class _Limiter:
    def __init__(self) -> None:
        self.calls = 0
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.calls += 1

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


PAYLOAD: Any = [
    {"universe": [{"name": n} for n in ("xyz:SMSN", "xyz:KR200", "xyz:OTHER", "xyz:SKHX")]},
    [
        {"markPx": "110.5", "prevDayPx": "100", "funding": "0.0000125", "openInterest": "5",
         "dayNtlVlm": "123456"},
        {"markPx": "505", "prevDayPx": "500", "funding": None},
        {"markPx": "1"},
        {"markPx": "0"},
    ],
]  # fmt: skip


def test_parse_perp_ctx_filters_and_drops_bad_mark() -> None:
    got = parse_perp_ctx(PAYLOAD, {"xyz:SMSN", "xyz:KR200", "xyz:SKHX"})
    assert set(got) == {"xyz:SMSN", "xyz:KR200"}  # OTHER 는 이름 밖, SKHX 는 mark 0
    s = got["xyz:SMSN"]
    assert (s.mark, s.prev_day, s.funding_1h, s.vol_24h) == (110.5, 100.0, 0.0000125, 123456.0)
    assert got["xyz:KR200"].funding_1h is None
    with pytest.raises(ValueError):
        parse_perp_ctx({"x": 1})


def test_client_posts_info_and_maps_errors() -> None:
    seen: list[Any] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=PAYLOAD)

    lim = _Limiter()
    c = HyperliquidClient(lim, transport=httpx.MockTransport(ok), base_url="https://h")
    assert set(c.perp_ctx({"xyz:SMSN"})) == {"xyz:SMSN"}
    assert seen == [{"type": "metaAndAssetCtxs", "dex": "xyz"}] and lim.calls == 1

    c429 = HyperliquidClient(
        lim, transport=httpx.MockTransport(lambda r: httpx.Response(429)), base_url="https://h"
    )
    with pytest.raises(HyperliquidError) as e:
        c429.perp_ctx()
    assert e.value.status == 429 and lim.slowed == 1
    bad = HyperliquidClient(
        lim,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url="https://h",
    )
    with pytest.raises(HyperliquidError):
        bad.perp_ctx()


class FakeHl:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def perp_ctx(self, names: Any, dex: str = "xyz") -> dict[str, PerpCtx]:
        if self.fail:
            raise HyperliquidError(None, "전송 실패: ConnectError")
        return {"xyz:SMSN": PerpCtx("xyz:SMSN", 110.0, 100.0, 0.0001, 1.0, 9.0)}


class FakeYahoo:
    def __init__(self, fail: set[str] | None = None, throttle_at: str | None = None) -> None:
        self.fail = fail or set()
        self.throttle_at = throttle_at
        self.calls: list[str] = []

    def chart_payload(self, sym: str, range_: str, interval: str) -> Any:
        self.calls.append(sym)
        if sym == self.throttle_at:
            raise YahooThrottled("chart", 429, "429")
        if sym in self.fail:
            raise YahooError("chart", 404, "없음")
        meta = {"regularMarketPrice": 10.0, "chartPreviousClose": 9.0, "currency": "USD"}
        return {"chart": {"result": [{"meta": meta}]}}


def _ctx(repos: MemoryRepos, **res: Any) -> JobContext:
    return JobContext(
        "global.proxy", "2026-11-02T10:07", "r1", 1, NOW, keys=KEYS,
        resources={"repos": repos, **res},
    )  # fmt: skip


def test_run_writes_both_sources() -> None:
    repos = MemoryRepos()
    y = FakeYahoo(fail={"EWY"})
    res = global_proxy.run(_ctx(repos, hyperliquid=FakeHl(), yahoo=y))
    assert res.status == "ok" and set(res.collected) == set(KEYS)
    got = repos.market.global_quotes(NOW - timedelta(minutes=5))
    assert got["xyz:SMSN"].price == 110.0 and got["xyz:SMSN"].ts == NOW.replace(second=0)
    assert got["KRW=X"].prev == 9.0 and "EWY" not in got
    assert res.detail["failed"] == {"EWY": "없음"}
    assert y.calls == list(yahoo_symbols())


def test_throttle_stops_rest_and_one_source_failure_is_partial() -> None:
    repos = MemoryRepos()
    first = yahoo_symbols()[0]
    res = global_proxy.run(
        _ctx(repos, hyperliquid=FakeHl(fail=True), yahoo=FakeYahoo(throttle_at=first))
    )
    assert res.status == "failed"
    res2 = global_proxy.run(_ctx(repos, hyperliquid=FakeHl(fail=True), yahoo=FakeYahoo()))
    assert res2.status == "ok" and [k.source for k in res2.collected] == ["YAHOO"]
    assert res2.detail["failed"] == {"hyperliquid": "전송 실패: ConnectError"}


def test_parse_meta() -> None:
    assert global_proxy.parse_meta({"chart": {"result": []}}) is None
    assert (
        global_proxy.parse_meta({"chart": {"result": [{"meta": {"regularMarketPrice": 0}}]}})
        is None
    )
    m = {"regularMarketPrice": "5", "chartPreviousClose": None, "currency": "EUR"}
    assert global_proxy.parse_meta({"chart": {"result": [{"meta": m}]}}) == (5.0, None, "EUR")
