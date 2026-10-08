"""calendar.json — 거래일·휴장·지연 개장·만기·야간 세션(docs/p3_design.md §7.2). 시계는 주입한다."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.public_export.calendar_json import (
    CALENDAR_DAYS,
    SOURCE,
    build_calendar,
    expiries_between,
)

NOW = datetime(2026, 10, 7, 5, 30, tzinfo=KST)


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar.default()


def _days(cal: TradingCalendar, now: datetime = NOW) -> dict[str, dict[str, object]]:
    f = build_calendar(now, cal)
    return {str(r["date"]): r for r in f.data["days"]}


def test_envelope_and_range(cal: TradingCalendar) -> None:
    f = build_calendar(NOW, cal)
    env = f.envelope(NOW)
    assert env["source"] == SOURCE
    assert env["as_of"] == "2026-10-07T05:30:00+09:00"
    assert env["quality"] == "ok"
    days = f.data["days"]
    assert len(days) == CALENDAR_DAYS
    assert days[0]["date"] == "2026-10-07"
    assert days[-1]["date"] == "2026-12-05"
    assert f.data["from"] == "2026-10-07" and f.data["to"] == "2026-12-05"
    assert f.data["next_trading_day"] == "2026-10-08"
    assert f.data["session"]["open"] == "09:00" and f.data["session"]["close"] == "15:30"
    assert f.data["session"]["tz"] == "Asia/Seoul"


def test_today_follows_kst_not_utc(cal: TradingCalendar) -> None:
    # UTC 10-06 21:00 = KST 10-07 06:00 — 첫날은 KST 날짜, as_of 도 KST 로 적는다
    f = build_calendar(datetime.fromisoformat("2026-10-06T21:00:00+00:00"), cal)
    assert f.data["days"][0]["date"] == "2026-10-07"
    assert f.envelope(NOW)["as_of"] == "2026-10-07T06:00:00+09:00"


def test_holiday_weekend_and_trading_rows(cal: TradingCalendar) -> None:
    d = _days(cal)
    hangul = d["2026-10-09"]  # 한글날
    assert hangul["trading"] is False and hangul["open"] is None and hangul["expiry"] is None
    assert d["2026-10-10"]["trading"] is False  # 토
    assert d["2026-10-10"]["weekday"] == 6
    wed = d["2026-10-07"]
    assert wed["trading"] is True and wed["open"] == "09:00" and wed["close"] == "15:30"
    assert wed["late_open"] is False


def test_night_session_rule(cal: TradingCalendar) -> None:
    d = _days(cal)
    assert d["2026-10-07"]["night_session"] is True  # 다음 날 거래일
    assert d["2026-10-08"]["night_session"] is False  # 목 — 다음 날(한글날) 휴장
    assert d["2026-10-16"]["night_session"] is True  # 금요일 밤은 늘
    assert d["2026-10-11"]["night_session"] is False  # 일


def test_late_open_from_override(cal: TradingCalendar) -> None:
    suneung = _days(cal)["2026-11-19"]  # holidays_override.yaml late_open
    assert suneung["trading"] is True
    assert suneung["late_open"] is True
    assert (suneung["open"], suneung["close"]) == ("10:00", "16:30")


def test_expiries_monthly_wins_and_weeklies(cal: TradingCalendar) -> None:
    d = _days(cal)
    assert d["2026-10-08"]["expiry"] == "monthly"  # 10월 둘째 목요일
    assert d["2026-10-12"]["expiry"] == "weekly"  # 월요일 위클리
    assert d["2026-10-15"]["expiry"] == "weekly"  # 목요일 위클리(월물 주 아님)
    assert d["2026-11-12"]["expiry"] == "monthly"
    assert d["2026-10-07"]["expiry"] is None
    ex = expiries_between(date(2026, 10, 5), date(2026, 10, 11), cal)
    assert ex[date(2026, 10, 8)] == "monthly"
    assert all(date(2026, 10, 5) <= k <= date(2026, 10, 11) for k in ex)


def test_outside_exchange_range_is_estimated() -> None:
    narrow = TradingCalendar(start=date(2026, 1, 1), end=date(2026, 10, 31))
    f = build_calendar(NOW, narrow)
    assert f.quality == "estimated"
    assert any("범위 밖" in n for n in f.notes)


def test_rejects_naive_and_bad_days(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError):
        build_calendar(datetime(2026, 10, 7, 5, 30), cal)  # noqa: DTZ001
    with pytest.raises(ValueError):
        build_calendar(NOW, cal, days=0)


def test_first_trading_day_of_year_is_late_open(cal: TradingCalendar) -> None:
    f = build_calendar(datetime.combine(date(2026, 12, 30), time(6), tzinfo=KST), cal, days=10)
    rows = {r["date"]: r for r in f.data["days"]}
    first = next(r for r in f.data["days"] if r["trading"] and str(r["date"]).startswith("2027"))
    assert first["late_open"] is True and first["open"] == "10:00"
    assert rows["2027-01-01"]["trading"] is False
