"""ForexFactory 주간 일정(로그인 — kbj/data/private/forexfactory.py, 사용자 결정 Q3). 합성
피드만."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from kbj.data.private.forexfactory import (
    DATASETS,
    FfClient,
    FfError,
    FfThrottled,
    parse_events,
    parse_forecast,
)
from kbj.data.ratelimit import Priority
from tests.fakes.ff_server import FakeFf

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)  # 화(뉴욕) — 이번 주 월 11-16


class _Limiter:
    def __init__(self) -> None:
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        assert tr_id == "ff:calendar"

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


def _client(fake: FakeFf, lim: _Limiter | None = None) -> FfClient:
    return FfClient(lim or _Limiter(), transport=fake.transport(), base_url="https://ff.test")


def test_this_week_normalises_country_time_importance_and_forecast() -> None:
    ev = _client(FakeFf(lambda: NOW)).this_week()
    by = {e.code: e for e in ev}
    assert "bank_holiday" not in by and "gbp_synthetic" not in by  # Holiday·모르는 통화는 버림
    cpi = by["cpi_m_m"]
    # 수 08:30 ET(EST) = 목 22:30 KST
    assert (cpi.country, cpi.kind, cpi.date, cpi.time_kst) == (
        "US",
        "cpi",
        date(2026, 11, 18),
        time(22, 30),
    )
    assert (cpi.forecast, cpi.unit, cpi.importance) == (Decimal("0.3"), "%", 3)
    nfp = by["non_farm_employment_change"]
    assert (nfp.kind, nfp.forecast, nfp.unit) == ("payrolls", Decimal("150"), "K")
    bok = by["bok_rate_decision"]
    assert (bok.country, bok.kind) == ("KR", "bok_rate")
    allday = by["synthetic_all_day_event"]
    assert allday.time_kst is None and allday.kind.startswith("ff_") and allday.importance == 1
    retail = by["retail_sales_m_m"]
    assert retail.forecast is None and retail.forecast_raw == "tbd"  # 숫자가 아니면 None(0 금지)
    assert [(e.date, e.time_kst or time(0)) for e in ev] == sorted(
        (e.date, e.time_kst or time(0)) for e in ev
    )


def test_errors_and_format_change() -> None:
    fake = FakeFf(lambda: NOW)
    lim = _Limiter()
    fake.inject(429)
    with pytest.raises(FfThrottled):
        _client(fake, lim).this_week()
    assert lim.slowed == 1
    fake.inject(502)
    with pytest.raises(FfError, match="HTTP 502"):
        _client(fake).this_week()
    fake.inject("not_list")
    with pytest.raises(FfError, match="배열"):
        _client(fake).this_week()
    with pytest.raises(ValueError, match="시간대"):
        parse_events(
            [{"title": "x", "country": "USD", "impact": "High", "date": "2026-11-18T08:30:00"}]
        )


def test_parse_forecast_suffixes() -> None:
    assert parse_forecast("-1.2B") == (Decimal("-1.2"), "B")
    assert parse_forecast("1,234") == (Decimal("1234"), None)
    assert parse_forecast("") == (None, None) and parse_forecast(None) == (None, None)
    assert parse_forecast("n/a") == (None, None)


def test_dataset_is_private_calendar() -> None:
    (d,) = DATASETS
    assert d.id == "FF:calendar" and d.tier.value == "private" and d.store == "prv_macro.calendar"
