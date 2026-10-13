"""경제 캘린더 합치기(kbj/engines/macro/calendar.py — D-P5-8): 공개 출처만·발표
결과·예상치(로그인)."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from kbj.config.macro import ManualEvent
from kbj.core.p5_rows import CalendarEvent, MacroSeriesRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.macro.calendar import (
    FRED_RELEASES,
    attach_actuals,
    fred_release_events,
    manual_events,
    merge,
    window,
)

NOW = datetime(2026, 11, 17, 16, 30, tzinfo=KST)


def test_fred_releases_map_known_names_only_and_dedupe() -> None:
    ev = fred_release_events(
        [
            ("Consumer Price Index", date(2026, 11, 13)),
            ("Some Unknown Release", date(2026, 11, 14)),
            ("Consumer Price Index", date(2026, 11, 13)),
        ]
    )
    assert [e.event_id for e in ev] == ["US:cpi:2026-11-13:fred"]
    e = ev[0]
    assert (
        e.is_public
        and e.source == "fred"
        and e.actual_series == "FRED:CPIAUCSL"
        and e.time_kst is None
    )
    assert all(r.country == "US" and 1 <= r.importance <= 3 for r in FRED_RELEASES.values())


def test_manual_events_become_public_rows() -> None:
    m = ManualEvent(
        country="KR",
        kind="bok_rate",
        date="2026-11-27",
        code="bok",
        title="금통위",
        importance=3,
        source="bok",
        time_kst="10:00",
    )
    (e,) = manual_events([m])
    assert (e.event_id, e.time_kst, e.is_public) == (
        "KR:bok_rate:2026-11-27:bok",
        time(10, 0),
        True,
    )


def _row(period: str, v: str | None, released: datetime | None) -> MacroSeriesRow:
    return MacroSeriesRow(
        "FRED:CPIAUCSL",
        period,
        None if v is None else Decimal(v),
        "x",
        "FRED",
        NOW,
        Quality.OK if v else Quality.INVALID,
        released_at=released,
    )


def test_actual_is_first_value_released_on_or_after_the_event_and_not_in_future() -> None:
    (e,) = fred_release_events([("Consumer Price Index", date(2026, 11, 13))])
    rows = [
        _row("2026-09", "320.1", datetime(2026, 10, 15, 21, 30, tzinfo=KST)),  # 그 전 공표 — 아니다
        _row("2026-10", "321.0", datetime(2026, 11, 13, 22, 30, tzinfo=KST)),  # 그날 공표 — 이것
        _row("2026-11", "322.0", datetime(2026, 12, 11, 22, 30, tzinfo=KST)),  # 미래 — 아니다
    ]
    (got,) = attach_actuals([e], {"FRED:CPIAUCSL": rows}, NOW)
    assert got.actual == Decimal("321.0")
    (none,) = attach_actuals([e], {"FRED:CPIAUCSL": rows[:1]}, NOW)
    assert none.actual is None  # 지어내지 않는다
    with pytest.raises(ValueError):
        attach_actuals([e], {}, datetime(2026, 11, 17))  # noqa: DTZ001


def _ev(country: str, kind: str, d: date, src: str, fc: str | None = None) -> CalendarEvent:
    return CalendarEvent(
        f"{country}:{kind}:{d.isoformat()}:{src}",
        kind,
        d,
        None,
        f"{kind}",
        country,
        2,
        src,
        Quality.OK,
        forecast=None if fc is None else Decimal(fc),
    )


def test_merge_attaches_forecast_only_when_logged_in() -> None:
    pub = [_ev("US", "cpi", date(2026, 11, 13), "fred")]
    prv = [
        _ev("US", "cpi", date(2026, 11, 13), "ff", "0.3"),
        _ev("US", "payrolls", date(2026, 12, 4), "ff", "150"),
    ]
    logged = merge(pub, prv, include_private=True)
    assert [(e.event_id, e.forecast, s) for e, s in logged] == [
        ("US:cpi:2026-11-13:fred", Decimal("0.3"), "ff"),
        ("US:payrolls:2026-12-04:ff", Decimal("150"), "ff"),
    ]
    public = merge(pub, prv, include_private=False)
    assert [(e.event_id, e.forecast, s) for e, s in public] == [
        ("US:cpi:2026-11-13:fred", None, None)
    ]
    with pytest.raises(ValueError):
        merge(prv[:1], [], include_private=False)  # 공개 쪽에 예상치가 섞이면 거부


def test_window_is_inclusive() -> None:
    evs = [_ev("KR", "cpi", date(2026, 11, d), "kosis") for d in (9, 10, 17, 18)]
    assert [e.date.day for e in window(evs, date(2026, 11, 10), date(2026, 11, 17))] == [10, 17]
