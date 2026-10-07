"""XNYS 캘린더 — `TradingCalendar(exchange="XNYS")`·`us_calendar()` (docs/p2_design.md §7.1·§6.4).

미국 일정(`us.eod` 의 `us_trading_day`, 아침 브리핑의 `after_us_session`)을 판정한다. 날짜는 뉴욕
날짜이고 KRX 덮어쓰기 파일은 쓰지 않는다. 기대값은 NYSE 2026 휴장 공지와 같은 날들이다
(exchange_calendars 4.13.2 결과와 대조 — 2026-10-07).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from kbj.core.calendar import (
    KST,
    SUPPORTED_EXCHANGES,
    TradingCalendar,
    is_equity_regular_hours,
    monthly_expiry,
    night_bar_time,
    night_session_opens,
    session_tag,
    state_at,
    us_calendar,
    weeklies_listed,
    weekly_monday_expiry,
    weekly_thursday_expiry,
)

D = date

# NYSE 2026 휴장(평일) — 독립기념일(토 07-04)은 금 07-03 대체
XNYS_HOLIDAYS_2026 = (
    D(2026, 1, 1),  # New Year's Day
    D(2026, 1, 19),  # Martin Luther King Jr. Day
    D(2026, 2, 16),  # Washington's Birthday
    D(2026, 4, 3),  # Good Friday
    D(2026, 5, 25),  # Memorial Day
    D(2026, 6, 19),  # Juneteenth
    D(2026, 7, 3),  # Independence Day (observed)
    D(2026, 9, 7),  # Labor Day
    D(2026, 11, 26),  # Thanksgiving Day
    D(2026, 12, 25),  # Christmas Day
)


@pytest.fixture(scope="module")
def us() -> TradingCalendar:
    return us_calendar()


def _weekdays(year: int) -> list[date]:
    d = D(year, 1, 1)
    out: list[date] = []
    while d.year == year:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_us_calendar_is_xnys_and_built_once(us: TradingCalendar) -> None:
    assert us.exchange == "XNYS"
    assert us is us_calendar()  # 프로세스당 한 번
    assert TradingCalendar.default().exchange == "XKRX"
    assert TradingCalendar().exchange == "XKRX"  # 기본값은 GX 와 같다
    assert SUPPORTED_EXCHANGES == ("XKRX", "XNYS")


def test_xnys_2026_holidays_are_exactly_the_nyse_list(us: TradingCalendar) -> None:
    closed = [d for d in _weekdays(2026) if not us.is_trading_day(d)]
    assert closed == list(XNYS_HOLIDAYS_2026)
    assert len(_weekdays(2026)) - len(closed) == 251


@pytest.mark.parametrize("d", XNYS_HOLIDAYS_2026)
def test_each_xnys_holiday_is_closed(us: TradingCalendar, d: date) -> None:
    assert not us.is_trading_day(d)


@pytest.mark.parametrize(
    "d",
    [
        D(2026, 11, 27),  # 추수감사절 다음 날 — 조기 폐장(13:00)이지만 거래일
        D(2026, 12, 24),  # 성탄 전날 — 조기 폐장이지만 거래일
        D(2026, 7, 2),
        D(2026, 7, 6),
    ],
)
def test_early_close_and_neighbour_days_are_sessions(us: TradingCalendar, d: date) -> None:
    assert us.is_trading_day(d)


@pytest.mark.parametrize(
    "d",
    [
        D(2026, 5, 1),  # 근로자의 날(KRX 휴장)
        D(2026, 6, 3),  # 지방선거 — KRX 덮어쓰기 휴장. XNYS 에는 덮어쓰기를 쓰지 않는다
        D(2026, 9, 24),  # 추석
        D(2026, 9, 25),
        D(2026, 10, 5),  # 개천절 대체공휴일
        D(2026, 10, 9),  # 한글날
        D(2026, 12, 31),  # KRX 연말 휴장
    ],
)
def test_krx_holidays_are_us_sessions(us: TradingCalendar, d: date) -> None:
    assert us.is_trading_day(d)
    assert not TradingCalendar.default().is_trading_day(d)


def test_next_prev_trading_day_on_us_holidays(us: TradingCalendar) -> None:
    assert us.next_trading_day(D(2026, 11, 25)) == D(2026, 11, 27)  # 추수감사절 건너뜀
    assert us.prev_trading_day(D(2026, 7, 6)) == D(2026, 7, 2)  # 07-03 대체휴일 + 주말
    assert us.next_trading_day(D(2026, 12, 31)) == D(2027, 1, 4)  # 2027-01-01 신정(금)
    assert not us.is_trading_day(D(2027, 1, 1))
    assert us.prev_trading_day(D(2026, 1, 20)) == D(2026, 1, 16)  # MLK + 주말


def test_after_us_session_example_differs_from_krx(us: TradingCalendar) -> None:
    """KST 화 2026-10-06 아침: 간밤에 끝난 뉴욕 월 10-05 세션이 있다(KRX 는 그날 휴장)."""
    kst_morning = datetime(2026, 10, 6, 8, 10, tzinfo=KST)
    ny_prev_day = kst_morning.date() - timedelta(days=1)
    assert us.is_trading_day(ny_prev_day)
    assert not TradingCalendar.default().is_trading_day(ny_prev_day)


def test_xnys_beyond_coverage_falls_back_to_weekdays(us: TradingCalendar) -> None:
    assert us.coverage == (D(2000, 1, 1), D(2050, 12, 31))
    assert not us.covered(D(2051, 1, 2))
    assert us.is_trading_day(D(2051, 1, 2))  # 범위 밖 평일(공휴일을 모른다)
    assert not us.is_trading_day(D(2051, 1, 1))  # 일요일


def test_xnys_accepts_extra_days_like_xkrx() -> None:
    c = TradingCalendar(exchange="XNYS", extra_closed=[D(2026, 10, 5)])
    assert c.exchange == "XNYS"
    assert not c.is_trading_day(D(2026, 10, 5))
    assert c.next_trading_day(D(2026, 10, 2)) == D(2026, 10, 6)
    with pytest.raises(ValueError, match="거꾸로"):
        TradingCalendar(exchange="XNYS", start=D(2027, 1, 1), end=D(2026, 1, 1))


@pytest.mark.parametrize("exchange", ["XNAS", "xkrx", "", "KRX"])
def test_unsupported_exchange_is_rejected(exchange: str) -> None:
    with pytest.raises(ValueError, match="거래소"):
        TradingCalendar(exchange=exchange)


def test_krx_rules_refuse_the_us_calendar(us: TradingCalendar) -> None:
    """야간장·파생 상태 머신·만기·분봉·주식 정규장은 KRX 규칙이다.

    XNYS 캘린더를 넣으면 조용히 틀리므로 거부한다.
    """
    ts = datetime(2026, 10, 6, 10, 0, tzinfo=KST)
    calls = [
        lambda: state_at(ts, us),
        lambda: session_tag(ts, us),
        lambda: night_session_opens(D(2026, 10, 6), us),
        lambda: is_equity_regular_hours(ts, us),
        lambda: night_bar_time("20261006", "240000", us),
        lambda: monthly_expiry(2026, 10, us),
        lambda: weekly_monday_expiry(D(2026, 10, 5), us),
        lambda: weekly_thursday_expiry(D(2026, 10, 1), us),
        lambda: weeklies_listed(D(2026, 10, 5), us),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="XKRX"):
            call()
