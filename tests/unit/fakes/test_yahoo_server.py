"""가짜 Yahoo(tests/fakes/yahoo_server.py — docs/p5_design.md §1.1).

일봉·장중 봉·없는 심볼·429·결정성."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tests.fakes.yahoo_server import DEFAULT_SYMBOLS, FakeYahoo

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)  # 10:00 ET(장중)


def _get(fake: FakeYahoo, sym: str, rng: str = "1mo") -> httpx.Response:
    with httpx.Client(transport=fake.transport("t"), base_url="https://y") as c:
        return c.get(f"/v8/finance/chart/{sym}", params={"range": rng, "interval": "1d"})


def test_daily_bars_are_weekdays_and_deterministic() -> None:
    a = _get(FakeYahoo(lambda: NOW), "^GSPC").json()
    b = _get(FakeYahoo(lambda: NOW), "^GSPC").json()
    assert a == b
    res = a["chart"]["result"][0]
    days = [datetime.fromtimestamp(t, UTC).weekday() for t in res["timestamp"]]
    assert days and max(days) < 5
    assert len(res["indicators"]["quote"][0]["close"]) == len(res["timestamp"])
    assert {"KRW=X", "^VIX", "BTC-USD", "^TNX"} <= set(DEFAULT_SYMBOLS)


def test_live_session_marks_regular_period() -> None:
    meta = _get(FakeYahoo(lambda: NOW, live=True), "^GSPC").json()["chart"]["result"][0]["meta"]
    reg = meta["currentTradingPeriod"]["regular"]
    assert reg["start"] <= meta["regularMarketTime"] < reg["end"]
    closed = _get(FakeYahoo(lambda: NOW), "^GSPC").json()["chart"]["result"][0]["meta"]
    assert closed["regularMarketTime"] == closed["currentTradingPeriod"]["regular"]["end"]


def test_unknown_symbol_429_and_injections() -> None:
    fake = FakeYahoo(lambda: NOW)
    r = _get(fake, "NOPE")
    assert r.status_code == 404 and r.json()["chart"]["error"]["code"] == "Not Found"
    fake.inject(429, 2)
    assert [_get(fake, "^VIX").status_code for _ in range(3)] == [429, 429, 200]
    fake.inject("empty")
    assert _get(fake, "^VIX").json()["chart"]["result"][0]["timestamp"] == []
    fake.inject("null_close")
    assert (
        _get(fake, "^VIX").json()["chart"]["result"][0]["indicators"]["quote"][0]["close"][0]
        is None
    )
    assert [s.status for s in fake.calls("NOPE")] == [404]
    assert len(fake.seen) == 6
