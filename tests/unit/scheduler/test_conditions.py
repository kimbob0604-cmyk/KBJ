"""kbj/services/scheduler/conditions.py — 캘린더 조건·as_of 규칙(설계 §6.4).

고정 날짜만 쓴다(시계 주입 — 벽시계 없음): 2026-10-05(월) 개천절 대체공휴일, 10-02(금) 밤,
미국 서머타임 끝 11-01(일), 2027-01-04(연초 첫 거래일 10:00 개장 — 지연 개장).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.time import KST
from kbj.services.scheduler.conditions import (
    WHEN,
    AsOfUnavailable,
    equity_anchor,
    holds,
    last_us_session,
    parse_anchor,
    prev_as_of,
    resolve_as_of,
)

KR = TradingCalendar.default()
US = us_calendar()


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("cond", "day", "want"),
    [
        ("always", date(2026, 10, 4), True),
        ("weekday", date(2026, 10, 5), True),  # 월 — 휴장이어도 평일
        ("weekday", date(2026, 10, 4), False),
        ("trading_day", date(2026, 10, 5), False),  # 개천절 대체공휴일
        ("trading_day", date(2026, 10, 6), True),
        ("us_trading_day", date(2026, 10, 5), True),  # 미국은 연다
        ("after_us_session", date(2026, 10, 6), True),  # 화 ← 월 미국장
        ("after_us_session", date(2026, 10, 5), False),  # 월 ← 일
        ("after_us_session", date(2026, 10, 10), True),  # 토 ← 금 미국장
        ("kr_or_after_us", date(2026, 10, 5), False),  # 한국 휴장 + 일요일 밤
        ("kr_or_after_us", date(2026, 10, 10), True),  # 토 — 간밤 미국장
        ("night_session", date(2026, 10, 2), True),  # 금 밤 — 월 휴장이어도 열림(GX 실측)
        ("night_session", date(2026, 10, 1), True),
        ("night_session", date(2026, 9, 23), False),  # 추석 전날 수 — 안 열림
        ("after_night_session", date(2026, 10, 3), True),  # 토 새벽 ← 금 밤
        ("after_night_session", date(2026, 10, 6), False),  # 화 새벽 ← 월(휴장) 밤 없음
    ],
)
def test_holds(cond: str, day: date, want: bool) -> None:
    assert holds(cond, day, KR, US) is want


def test_month_days_needs_list_and_unknown_is_error() -> None:
    assert holds("month_days", date(2026, 10, 11), KR, US, month_days=(1, 11, 21))
    assert not holds("month_days", date(2026, 10, 12), KR, US, month_days=(1, 11, 21))
    with pytest.raises(ValueError):
        holds("month_days", date(2026, 10, 11), KR, US)
    with pytest.raises(ValueError):
        holds("holiday", date(2026, 10, 11), KR, US)
    assert "kr_or_after_us" in WHEN


def test_as_of_dates_around_the_substitute_holiday() -> None:
    now = kst(2026, 10, 6, 8, 5)
    assert resolve_as_of("prev_trading_day", now, KR, US) == "2026-10-02"
    assert resolve_as_of("trade_date", now, KR, US) == "2026-10-06"
    assert resolve_as_of("run_date", now, KR, US) == "2026-10-06"
    with pytest.raises(AsOfUnavailable):
        resolve_as_of("trade_date", kst(2026, 10, 5, 16, 0), KR, US)
    with pytest.raises(AsOfUnavailable):
        resolve_as_of("event", now, KR, US)


def test_minute_and_slot_and_kst_from_utc() -> None:
    now = datetime(2026, 10, 6, 0, 37, 42, tzinfo=UTC)  # 09:37 KST
    assert resolve_as_of("minute", now, KR, US) == "2026-10-06T09:37"
    assert resolve_as_of("slot10m", now, KR, US) == "2026-10-06T09:30"
    with pytest.raises(ValueError):
        resolve_as_of("minute", datetime(2026, 10, 6, 9, 37), KR, US)  # noqa: DTZ001 — naive 거부


@pytest.mark.parametrize(
    ("day", "want"),
    [(1, "202609-3"), (10, "202609-3"), (11, "202610-1"), (20, "202610-1"), (21, "202610-2")],
)
def test_ten_day_is_the_last_published_period(day: int, want: str) -> None:
    assert resolve_as_of("ten_day", kst(2026, 10, day, 10, 0), KR, US) == want


def test_month_and_quarter_are_previous_completed() -> None:
    assert resolve_as_of("month", kst(2026, 10, 15, 10), KR, US) == "2026-09"
    assert resolve_as_of("month", kst(2027, 1, 15, 10), KR, US) == "2026-12"
    assert resolve_as_of("quarter", kst(2026, 10, 7, 7, 30), KR, US) == "2026Q3"
    assert resolve_as_of("quarter", kst(2027, 2, 1), KR, US) == "2026Q4"


def test_us_trade_date_follows_new_york_close_across_dst_end() -> None:
    # 서머타임(EDT) — 16:10 NY = 05:10 KST 다음 날
    assert resolve_as_of("us_trade_date", kst(2026, 10, 7, 5, 10), KR, US) == "2026-10-06"
    # 05:00 KST 직전 = 15:59 NY → 아직 그날 장이 안 끝났다 → 전 거래일
    assert resolve_as_of("us_trade_date", kst(2026, 10, 7, 4, 59), KR, US) == "2026-10-05"
    # 서머타임 끝(11-01) 뒤: EST — 16:00 NY = 06:00 KST
    assert last_us_session(kst(2026, 11, 3, 5, 30), US) == date(2026, 10, 30)  # 월 장 아직
    assert last_us_session(kst(2026, 11, 3, 6, 0), US) == date(2026, 11, 2)
    # 일요일 아침(KST) → 금요일 장
    assert resolve_as_of("us_trade_date", kst(2026, 10, 11, 6, 20), KR, US) == "2026-10-09"


def test_prev_as_of() -> None:
    assert prev_as_of("trade_date", "2026-10-06", KR, US) == "2026-10-02"
    assert prev_as_of("run_date", "2026-10-06", KR, US) == "2026-10-05"
    assert prev_as_of("us_trade_date", "2026-10-06", KR, US) == "2026-10-05"
    with pytest.raises(ValueError):
        prev_as_of("month", "2026-09", KR, US)


def test_equity_anchor_follows_late_open() -> None:
    assert parse_anchor("close+5") == ("close", 5)
    assert parse_anchor("open-10") == ("open", -10)
    with pytest.raises(ValueError):
        parse_anchor("noon")
    normal = equity_anchor(date(2026, 10, 6), parse_anchor("close+5"), KR)
    assert normal == kst(2026, 10, 6, 15, 35)
    # 연초 첫 거래일 10:00 개장(XKRX 와 같다 — 묶음 B equity_bounds)
    first = equity_anchor(date(2027, 1, 4), parse_anchor("open"), KR)
    assert first == kst(2027, 1, 4, 10, 0)
    # 수능일 같은 지연 개장은 덮어쓰기 late_open — 캘린더를 만들어 확인
    from datetime import time

    cal = TradingCalendar(late_open={date(2026, 11, 19): (time(10, 0), time(16, 30))})
    late_close = equity_anchor(date(2026, 11, 19), parse_anchor("close+5"), cal)
    assert late_close == kst(2026, 11, 19, 16, 35)
    assert late_close - normal > timedelta(days=40)
