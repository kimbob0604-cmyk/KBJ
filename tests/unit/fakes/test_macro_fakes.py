"""MAC 가짜 서버(tests/fakes/{fred,treasury,nyfed,ff}_server.py) 자체 시험 — 결정적·합성·주입."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from tests.fakes.ff_server import FakeFf
from tests.fakes.fred_server import FAKE_KEY, FakeFred, periods
from tests.fakes.nyfed_server import FakeNyfed
from tests.fakes.treasury_server import FakeTreasury

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _get(t: httpx.MockTransport, url: str, **params: str) -> httpx.Response:
    with httpx.Client(transport=t) as c:
        return c.get(url, params=params)


def test_fred_is_deterministic_and_checks_the_key() -> None:
    a, b = FakeFred(lambda: NOW), FakeFred(lambda: NOW)
    q = {
        "series_id": "DGS10",
        "api_key": FAKE_KEY,
        "file_type": "json",
        "observation_start": "2026-10-01",
    }
    r1 = _get(a.transport(), "https://f/fred/series/observations", **q)
    r2 = _get(b.transport(), "https://f/fred/series/observations", **q)
    assert r1.status_code == 200 and r1.text == r2.text
    bad = _get(a.transport(), "https://f/fred/series/observations", **{**q, "api_key": "x"})
    assert bad.status_code == 400 and "api_key" in bad.text
    assert all(s.series_id == "DGS10" for s in a.seen) and "x" not in {s.path for s in a.seen}
    a.inject(429, 2)
    assert [
        _get(a.transport(), "https://f/fred/series/observations", **q).status_code for _ in range(3)
    ] == [429, 429, 200]


def test_periods_cover_daily_monthly_quarterly() -> None:
    assert len(periods("D", date(2026, 11, 9), date(2026, 11, 15))) == 5
    assert periods("M", date(2026, 10, 15), date(2026, 12, 31)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
    ]
    assert periods("Q", date(2026, 1, 1), date(2026, 12, 31))[-1] == date(2026, 10, 1)


def test_treasury_nyfed_ff_answer_paths_and_injections() -> None:
    t = FakeTreasury(lambda: NOW)
    r = _get(t.transport(), "https://t/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all",
             type="daily_treasury_yield_curve")  # fmt: skip
    assert r.status_code == 200 and r.text.startswith("Date,") and "11/16/2026" in r.text
    n = FakeNyfed(lambda: NOW)
    r = _get(
        n.transport(),
        "https://n/api/rates/secured/sofr/search.json",
        startDate="2026-11-09",
        endDate="2026-11-13",
    )
    assert r.status_code == 200 and len(r.json()["refRates"]) == 5
    assert _get(n.transport(), "https://n/api/rates/unsecured/sofr/search.json",
                startDate="2026-11-09", endDate="2026-11-13").status_code == 400  # fmt: skip
    f = FakeFf(lambda: NOW)
    rows = _get(f.transport(), "https://ff/ff_calendar_thisweek.json").json()
    assert {x["country"] for x in rows} >= {"USD", "KRW"} and all("forecast" in x for x in rows)
    f.inject(500)
    assert _get(f.transport(), "https://ff/ff_calendar_thisweek.json").status_code == 500
