"""kbj/core/calendar_compat.py — legacy 휴장·장중 판정 대체 (docs/p2_design.md §1.3·§7.2).

호환 층은 규칙을 새로 갖지 않는다: 모든 값이 정본(`TradingCalendar.default()`·주식 정규장)과 같아야
한다. legacy 와 달라지는 날(2026년 다섯 날)은 `test_legacy_holiday_parity.py` 가 고정한다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from kbj.core.calendar import (
    KST,
    TradingCalendar,
    equity_session_bounds,
    is_equity_regular_hours,
)
from kbj.core.calendar_compat import is_kr_holiday, is_kr_regular_hours, next_trading_open

D = date
US = timedelta(microseconds=1)
DEFAULT = TradingCalendar.default()
XKRX_ONLY = TradingCalendar()


def kst(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=KST)


# ── is_kr_holiday ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("d", "holiday"),
    [
        (D(2026, 10, 2), False),  # 금
        (D(2026, 10, 3), True),  # 토(개천절)
        (D(2026, 10, 4), True),  # 일
        (D(2026, 10, 5), True),  # 개천절 대체공휴일
        (D(2026, 10, 6), False),
        (D(2026, 10, 9), True),  # 한글날
        (D(2026, 9, 28), False),  # 추석 연휴 뒤 월요일 — 개장
        (D(2026, 6, 3), True),  # 지방선거(덮어쓰기)
        (D(2026, 12, 31), True),  # 연말 휴장
        (D(2027, 1, 1), True),  # 2027년 — SD 하드코딩은 2026년뿐이었다
        (D(2027, 2, 8), True),  # 2027 설날 연휴(XKRX)
    ],
)
def test_is_kr_holiday_follows_the_default_calendar(d: date, holiday: bool) -> None:
    assert is_kr_holiday(d) is holiday
    assert is_kr_holiday(datetime.combine(d, time(10), tzinfo=KST)) is holiday
    assert holiday is not DEFAULT.is_trading_day(d)


def test_is_kr_holiday_reads_the_kst_date_of_a_datetime() -> None:
    # UTC 로는 10-04(일) 15:30 이지만 KST 로는 10-05(월, 대체공휴일) 00:30
    assert is_kr_holiday(datetime(2026, 10, 4, 15, 30, tzinfo=UTC))
    # UTC 로는 10-05(휴장) 15:30 이지만 KST 로는 10-06(화) 00:30 — 개장일
    assert not is_kr_holiday(datetime(2026, 10, 5, 15, 30, tzinfo=UTC))
    ny = datetime(2026, 10, 5, 20, 0, tzinfo=ZoneInfo("America/New_York"))  # = 10-06 09:00 KST
    assert not is_kr_holiday(ny)


def test_is_kr_holiday_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        is_kr_holiday(datetime(2026, 10, 5, 10))  # noqa: DTZ001 — SD 의 naive 호출을 막는다


def test_is_kr_holiday_takes_an_injected_calendar() -> None:
    assert is_kr_holiday(D(2026, 6, 3))
    assert not is_kr_holiday(D(2026, 6, 3), cal=XKRX_ONLY)  # XKRX 만으로는 거래일


# ── next_trading_open ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "opens"),
    [
        (kst(2026, 10, 2, 16), kst(2026, 10, 6, 9)),  # 금 장후 → 주말·10-05 건너 화
        (kst(2026, 10, 3, 12), kst(2026, 10, 6, 9)),
        (kst(2026, 10, 5, 8), kst(2026, 10, 6, 9)),  # 휴장일 아침
        (kst(2026, 10, 6, 8, 59), kst(2026, 10, 6, 9)),  # 개장 전 → 오늘
        (kst(2026, 10, 6, 9) - US, kst(2026, 10, 6, 9)),
        (
            kst(2026, 10, 6, 9),
            kst(2026, 10, 7, 9),
        ),  # 같은 시각은 제외(SD 도 cand <= now 면 다음 날)
        (kst(2026, 10, 6, 10), kst(2026, 10, 7, 9)),
        (kst(2026, 10, 8, 16), kst(2026, 10, 12, 9)),  # 한글날(금) + 주말
        (kst(2026, 9, 23, 16), kst(2026, 9, 28, 9)),  # 추석 연휴
        (kst(2026, 6, 2, 16), kst(2026, 6, 4, 9)),  # 지방선거(덮어쓰기)
        # 연말 휴장·신정. 2027-01-04 은 그해 첫 거래일이라 10:00 지연 개장(R24, 2026-10-07 결정)
        (kst(2026, 12, 30, 16), kst(2027, 1, 4, 10)),
        # 수능일(override late_open) — 10:00 개장
        (kst(2026, 11, 18, 16), kst(2026, 11, 19, 10)),
    ],
)
def test_next_trading_open(now: datetime, opens: datetime) -> None:
    got = next_trading_open(now)
    assert got == opens
    assert got.tzinfo is KST
    assert next_trading_open(now.astimezone(UTC)) == opens  # 입력 시간대와 무관


def test_next_trading_open_takes_an_injected_calendar() -> None:
    assert next_trading_open(kst(2026, 6, 2, 16)) == kst(2026, 6, 4, 9)
    assert next_trading_open(kst(2026, 6, 2, 16), cal=XKRX_ONLY) == kst(2026, 6, 3, 9)


def test_next_trading_open_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        next_trading_open(datetime(2026, 10, 6, 10))  # noqa: DTZ001 — 거부 확인용


def test_next_trading_open_is_the_first_open_after_now_over_autumn_2026() -> None:
    """2026-09-01~11-30 의 37분 간격 모든 시각에서.

    결과는 now 뒤의 첫 거래일 개장 시각(보통 09:00, 수능일 11-19 는 10:00)이고, 그 사이에
    지나치는 개장이 없다.
    """
    t, end = kst(2026, 9, 1), kst(2026, 11, 30)
    while t < end:
        got = next_trading_open(t)
        assert got > t and DEFAULT.is_trading_day(got.date())
        assert got == equity_session_bounds(got.date(), DEFAULT)[0]
        assert got.time() == (time(10) if got.date() == date(2026, 11, 19) else time(9))
        d = t.date()
        while d < got.date():
            if DEFAULT.is_trading_day(d):
                assert equity_session_bounds(d, DEFAULT)[0] <= t, (t, d)
            d += timedelta(days=1)
        t += timedelta(minutes=37)


# ── is_kr_regular_hours ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ts", "inside"),
    [
        (kst(2026, 10, 6, 9) - US, False),
        (kst(2026, 10, 6, 9), True),
        (kst(2026, 10, 6, 15, 30) - US, True),
        (kst(2026, 10, 6, 15, 30), False),  # SD 는 15:30 분을 장중으로 봤다 — 정본은 반열림
        (kst(2026, 10, 5, 10), False),  # 휴장일 — SD 는 평일이면 장중으로 봤다
        (kst(2026, 6, 3, 10), False),  # 지방선거(덮어쓰기)
        (kst(2026, 10, 3, 10), False),  # 토요일
        (datetime(2026, 10, 6, 1, 0, tzinfo=UTC), True),  # = 10:00 KST
    ],
)
def test_is_kr_regular_hours(ts: datetime, inside: bool) -> None:
    assert is_kr_regular_hours(ts) is inside


def test_is_kr_regular_hours_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        is_kr_regular_hours(datetime(2026, 10, 6, 10))  # noqa: DTZ001 — SD kis_api 의 naive now()


def test_is_kr_regular_hours_is_the_canonical_rule() -> None:
    """호환 층은 규칙을 갖지 않는다 — 2026-09-20~10-12 의 10분 간격 모든 시각에서 정본과 같다."""
    t, end = kst(2026, 9, 20), kst(2026, 10, 12)
    while t < end:
        assert is_kr_regular_hours(t) is is_equity_regular_hours(t, DEFAULT), t
        assert is_kr_regular_hours(t, cal=XKRX_ONLY) is is_equity_regular_hours(t, XKRX_ONLY)
        t += timedelta(minutes=10)
