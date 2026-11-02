"""`global.daily` — 야후 일봉·월봉·해외 시총을 global_daily·global_cap 에 쓴다(합성 응답)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from kbj.data.private.yahoo.client import (
    YahooClient,
    YahooError,
    YahooThrottled,
    parse_quote_caps,
)
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.engines.market.relcap import INDICES, daily_symbols
from kbj.services.collectors import global_daily
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import MemoryRepos

NOW = datetime(2026, 11, 1, 22, 25, tzinfo=UTC)  # 07:25 KST 11-02
KEYS = (
    DataKey("YAHOO", "global_daily", "2026-11-02"),
    DataKey("YAHOO", "quote_caps", "2026-11-02"),
)
T1, T2 = 1793404800, 1793491200  # 2026-10-31·11-01 00:00 UTC


def chart(close: float, currency: str = "USD") -> Any:
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": currency, "gmtoffset": 0},
                    "timestamp": [T1, T2],
                    "indicators": {"quote": [{"close": [close, close * 1.1], "open": [1, 1],
                                              "high": [1, 1], "low": [1, 1], "volume": [1, 1]}]},
                }
            ]
        }
    }  # fmt: skip


class FakeYahoo:
    def __init__(self, fail: set[str] | None = None, quote_fail: bool = False) -> None:
        self.fail = fail or set()
        self.quote_fail = quote_fail
        self.calls: list[tuple[str, str, bool]] = []

    def chart_payload(self, sym: str, range_: str, interval: str, *, span_all: bool = False) -> Any:
        self.calls.append((sym, interval, span_all))
        if sym in self.fail:
            raise YahooError("chart", 404, "없음")
        return chart(10.0)

    def quote_caps(self, symbols: Any) -> dict[str, tuple[float, str | None]]:
        if self.quote_fail:
            raise YahooError("quote", 401, "crumb 못 받음(HTTP 401)")
        return {s: (1e11, "USD") for s in symbols}


def _ctx(repos: MemoryRepos, y: Any) -> JobContext:
    return JobContext("global.daily", "2026-11-02", "r1", 1, NOW, keys=KEYS,
                      resources={"repos": repos, "yahoo": y})  # fmt: skip


def test_run_writes_daily_monthly_and_caps() -> None:
    repos = MemoryRepos()
    y = FakeYahoo(fail={"JPY=X"})
    res = global_daily.run(_ctx(repos, y))
    assert res.status == "ok" and set(res.collected) == set(KEYS)
    daily = repos.market.global_closes(daily_symbols(), "1d", date(2026, 1, 1))
    assert daily["MU"] == [(date(2026, 10, 31), 10.0), (date(2026, 11, 1), 11.0)]
    assert "JPY=X" not in daily and res.detail["failed"] == {"JPY=X": "없음"}
    monthly = repos.market.global_closes([s for s, _ in INDICES], "1mo", date(1900, 1, 1))
    assert set(monthly) == {s for s, _ in INDICES}
    assert all(span for _, iv, span in y.calls if iv == "1mo")
    caps = repos.market.global_caps(date(2026, 11, 1))
    assert set(caps) == set(global_daily.foreign_symbols())
    assert caps["MU"].as_of == date(2026, 11, 2)  # KST 날짜


def test_quote_failure_drops_only_its_key_and_no_bars_is_failed() -> None:
    repos = MemoryRepos()
    res = global_daily.run(_ctx(repos, FakeYahoo(quote_fail=True)))
    assert res.status == "ok" and [k.dataset for k in res.collected] == ["global_daily"]
    allfail = FakeYahoo(fail=set(daily_symbols()) | {s for s, _ in INDICES})
    assert global_daily.run(_ctx(MemoryRepos(), allfail)).status == "failed"


def test_foreign_symbols() -> None:
    assert global_daily.foreign_symbols() == ("4062.T", "6981.T", "AVGO", "MU", "NVDA", "TSM")


class _Limiter:
    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.5


def test_quote_caps_crumb_flow_and_span_all() -> None:
    seen: list[str] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.url.host}{req.url.path}?{req.url.query.decode()}")
        if req.url.host == "fc.yahoo.com":
            return httpx.Response(404, headers={"set-cookie": "A3=x; Domain=.yahoo.com; Path=/"})
        if req.url.path == "/v1/test/getcrumb":
            assert "A3=x" in req.headers.get("cookie", "")
            return httpx.Response(200, text="abcDEF12")
        if req.url.path == "/v7/finance/quote":
            body = {"quoteResponse": {"result": [
                {"symbol": "MU", "marketCap": 1.5e11, "currency": "USD"},
                {"symbol": "6981.T", "marketCap": 0}]}}  # fmt: skip
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=chart(5.0))

    c = YahooClient(_Limiter(), transport=httpx.MockTransport(handle),
                    base_url="https://query1.finance.yahoo.com")  # fmt: skip
    assert c.quote_caps(["MU", "6981.T"]) == {"MU": (1.5e11, "USD")}
    assert c.quote_caps(["MU"]) == {"MU": (1.5e11, "USD")}
    assert sum("getcrumb" in s for s in seen) == 1  # crumb 는 한 번
    assert "crumb=abcDEF12" in seen[2]
    c.chart_payload("^GSPC", "max", "1mo", span_all=True)
    assert "period1=0" in seen[-1] and "range=" not in seen[-1]


def test_quote_errors() -> None:
    def busy(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    c = YahooClient(_Limiter(), transport=httpx.MockTransport(busy), base_url="https://y")
    with pytest.raises(YahooThrottled):
        c.quote_caps(["MU"])
    with pytest.raises(ValueError):
        parse_quote_caps({"x": 1})
