"""주식 정규장(`equity_session_bounds`·`is_equity_regular_hours`)과 `session_tag` (설계 §1.3·§7.1).

- 주식 정규장은 [09:00, 15:30) KST 반열림이다. GX 상태 머신의 **파생** 주간 세션(08:45~15:45)보다
  좁다 — 정규장 안이면 언제나 파생 DAY 이고 귀속 거래일은 그 KST 날짜다(속성 시험)
- `session_tag` 는 GX `services/poller/context.py:49` 에서 옮겼다. 아래 첫 시험의 단언은 GX
  `tests/unit/test_poller_planner.py:test_session_tag_follows_state_at_and_pre_states_point_forward`
  와 같다(그 시험은 poller 시험이라 P7 까지 legacy 에 남는다)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kbj.core.calendar import (
    DAY_START,
    EQUITY_CLOSE,
    EQUITY_OPEN,
    KST,
    State,
    TradingCalendar,
    equity_session_bounds,
    is_equity_regular_hours,
    session_bounds,
    session_tag,
    state_at,
)

D = date
US = timedelta(microseconds=1)
CAL = TradingCalendar()  # XKRX 만 (override 파일과 무관)
DEFAULT = TradingCalendar.default()  # XKRX + config/holidays_override.yaml


def kst(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=KST)


# ── 정규장 경계 ──────────────────────────────────────────────────────────────


def test_equity_session_bounds_are_0900_1530_kst() -> None:
    assert (EQUITY_OPEN, EQUITY_CLOSE) == (time(9, 0), time(15, 30))
    start, end = equity_session_bounds(D(2026, 10, 6))
    assert (start, end) == (kst(2026, 10, 6, 9), kst(2026, 10, 6, 15, 30))
    assert start == datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=6, minutes=30)
    # 파생 주간 세션(08:45~15:45) 안에 들어 있다
    d_start, d_end = session_bounds(D(2026, 10, 6), "day")
    assert d_start < start and end < d_end


def test_equity_session_bounds_ignore_holidays_and_reject_datetime() -> None:
    # session_bounds 처럼 휴장 여부는 보지 않는다 — 열리는지는 is_trading_day 로 따로
    assert equity_session_bounds(D(2026, 10, 5))[0] == kst(2026, 10, 5, 9)
    with pytest.raises(TypeError):
        equity_session_bounds(kst(2026, 10, 6, 10))


@pytest.mark.parametrize(
    ("ts", "inside"),
    [
        (kst(2026, 10, 6, 9) - US, False),
        (kst(2026, 10, 6, 9), True),
        (kst(2026, 10, 6, 12), True),
        (kst(2026, 10, 6, 15, 29, 59), True),
        (kst(2026, 10, 6, 15, 30) - US, True),
        (kst(2026, 10, 6, 15, 30), False),  # 반열림 — SD 는 15:30 분 전체를 장중으로 봤다
        (kst(2026, 10, 6, 15, 30, 59), False),
        (kst(2026, 10, 6, 8, 45), False),  # 파생 DAY 지만 주식 정규장 전
        (kst(2026, 10, 6, 15, 40), False),  # 파생 DAY 지만 주식 정규장 뒤
        (kst(2026, 10, 6, 20), False),
        (kst(2026, 10, 7, 3), False),
    ],
)
def test_regular_hours_are_half_open(ts: datetime, inside: bool) -> None:
    assert is_equity_regular_hours(ts, CAL) is inside


@pytest.mark.parametrize(
    "d",
    [
        D(2026, 10, 5),  # 개천절 대체공휴일(월)
        D(2026, 10, 3),  # 토요일
        D(2026, 10, 4),  # 일요일
        D(2026, 10, 9),  # 한글날
        D(2026, 9, 24),  # 추석 전날
        D(2026, 12, 31),  # 연말 휴장
        D(2026, 5, 1),  # 근로자의 날
    ],
)
def test_closed_days_have_no_regular_hours(d: date) -> None:
    assert not is_equity_regular_hours(datetime.combine(d, time(10), tzinfo=KST), CAL)
    assert not is_equity_regular_hours(datetime.combine(d, time(10), tzinfo=KST), DEFAULT)


def test_override_closed_day_has_no_regular_hours() -> None:
    election = kst(2026, 6, 3, 10)  # 지방선거 — XKRX 만으로는 거래일, 덮어쓰기로 휴장
    assert is_equity_regular_hours(election, CAL)
    assert not is_equity_regular_hours(election, DEFAULT)


def test_regular_hours_are_timezone_invariant() -> None:
    ts = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # = 09:00 KST
    for tz in (UTC, KST, ZoneInfo("America/New_York"), ZoneInfo("Europe/London")):
        assert is_equity_regular_hours(ts.astimezone(tz), CAL)
    late = datetime(2026, 10, 6, 6, 30, tzinfo=UTC)  # = 15:30 KST
    assert not is_equity_regular_hours(late.astimezone(ZoneInfo("America/New_York")), CAL)


def test_regular_hours_reject_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        is_equity_regular_hours(datetime(2026, 10, 6, 10), CAL)  # noqa: DTZ001 — 거부 확인용


_LO = datetime(2000, 1, 2)  # noqa: DTZ001 — hypothesis 경계는 naive 로 받는다
_HI = datetime(2050, 12, 30)  # noqa: DTZ001
aware = st.datetimes(min_value=_LO, max_value=_HI, timezones=st.timezones())


@given(ts=aware)
def test_regular_hours_lie_inside_the_derivative_day_session(ts: datetime) -> None:
    """정규장 안이면 파생 DAY 이고 귀속 거래일은 그 KST 날짜.

    거꾸로는 성립하지 않는다(08:45~09:00·15:30~15:45 는 파생 DAY 지만 정규장 밖).
    정규장 경계는 `equity_bounds`(지연 개장 반영 — 그해 첫 거래일 10:00, 수능일 10:00~16:30)를
    오라클로 쓴다. 수능일은 파생 세션 지연을 아직 반영하지 않아(`state_at` 은 15:45 에 닫는다
    [확인 필요]) DAY 단언에서 뺀다.
    """
    inside = is_equity_regular_hours(ts, CAL)
    k = ts.astimezone(KST)
    start, end = CAL.equity_bounds(k.date())
    assert inside is (CAL.is_trading_day(k.date()) and start <= k < end)
    if inside and end.time() == EQUITY_CLOSE:
        info = state_at(ts, CAL)
        assert info.state is State.DAY
        assert info.trade_date == k.date()


# ── session_tag (GX poller 에서 옮긴 함수) ───────────────────────────────────


def test_session_tag_follows_state_at_and_pre_states_point_forward() -> None:
    assert session_tag(kst(2026, 9, 28, 10, 0), DEFAULT) == (D(2026, 9, 28), "day")
    assert session_tag(kst(2026, 9, 28, 8, 10), DEFAULT) == (D(2026, 9, 28), "day")  # PRE_DAY
    assert session_tag(kst(2026, 9, 28, 17, 55), DEFAULT) == (D(2026, 9, 29), "night")  # PRE_NIGHT
    assert session_tag(kst(2026, 9, 29, 1, 0), DEFAULT) == (D(2026, 9, 29), "night")
    assert session_tag(kst(2026, 9, 18, 23, 0), DEFAULT) == (D(2026, 9, 21), "night")  # 금요일 밤
    assert session_tag(kst(2026, 9, 28, 16, 0), DEFAULT) is None  # POST_DAY
    assert session_tag(kst(2026, 9, 24, 10, 0), DEFAULT) is None  # 추석 전날 휴장


@pytest.mark.parametrize(
    ("ts", "tag"),
    [
        (kst(2026, 10, 2, 17, 50), (D(2026, 10, 6), "night")),  # 월 10-05 휴장 앞 금요일 밤 → 화
        (kst(2026, 10, 3, 5, 59), (D(2026, 10, 6), "night")),
        (kst(2026, 10, 3, 6), None),
        (kst(2026, 10, 5, 8, 30), None),  # 휴장일엔 PRE_DAY 도 없다
        (kst(2026, 10, 6, 8), (D(2026, 10, 6), "day")),  # PRE_DAY 시작
        (kst(2026, 10, 6, 8) - US, None),
        (kst(2026, 10, 8, 17, 55), None),  # 한글날(금) 전날 — 야간장 안 열림
        (kst(2026, 6, 2, 17, 55), None),  # 지방선거(덮어쓰기) 전날 — 야간장 안 열림
    ],
)
def test_session_tag_around_holidays(ts: datetime, tag: tuple[date, str] | None) -> None:
    assert session_tag(ts, DEFAULT) == tag


@given(ts=aware)
def test_session_tag_agrees_with_state_at(ts: datetime) -> None:
    info = state_at(ts, CAL)
    tag = session_tag(ts, CAL)
    if info.state in (State.DAY, State.NIGHT):
        assert tag == (info.trade_date, info.session)
    elif info.state in (State.POST_DAY, State.IDLE):
        assert tag is None
    elif info.state is State.PRE_DAY:
        assert tag == (ts.astimezone(KST).date(), "day")
        assert ts.astimezone(KST).time() < DAY_START
    else:  # PRE_NIGHT → 곧 열릴 야간(귀속은 다음 거래일)
        assert tag == (CAL.next_trading_day(ts.astimezone(KST).date()), "night")


# ── 지연 개장 (설계 R24, 메인 결정 2026-10-07) ────────────────────────────────


def _late_cal() -> TradingCalendar:
    from kbj.core.calendar import load_override

    return TradingCalendar.from_override(load_override())


@pytest.mark.parametrize(
    ("d", "open_h", "close_hm"),
    [
        (date(2026, 1, 2), 10, (15, 30)),  # 그해 첫 거래일 — 규칙(XKRX 와 같다)
        (date(2027, 1, 4), 10, (15, 30)),
        (date(2025, 11, 13), 10, (16, 30)),  # 수능일 — override late_open
        (date(2026, 11, 19), 10, (16, 30)),
        (date(2026, 10, 7), 9, (15, 30)),  # 평소
        (date(2026, 1, 5), 9, (15, 30)),  # 첫 거래일 다음 날은 평소
    ],
)
def test_equity_bounds_follow_late_opens(d: date, open_h: int, close_hm: tuple[int, int]) -> None:
    cal = _late_cal()
    start, end = cal.equity_bounds(d)
    assert (start.hour, start.minute) == (open_h, 0)
    assert (end.hour, end.minute) == close_hm
    assert equity_session_bounds(d, cal) == (start, end)


def test_equity_session_bounds_without_calendar_stays_plain() -> None:
    start, end = equity_session_bounds(date(2026, 11, 19))
    assert (start.hour, end.hour, end.minute) == (9, 15, 30)


def test_regular_hours_on_csat_day() -> None:
    from kbj.core.time import KST

    cal = _late_cal()
    assert not is_equity_regular_hours(datetime(2026, 11, 19, 9, 30, tzinfo=KST), cal)
    assert is_equity_regular_hours(datetime(2026, 11, 19, 10, 0, tzinfo=KST), cal)
    assert is_equity_regular_hours(datetime(2026, 11, 19, 16, 0, tzinfo=KST), cal)
    assert not is_equity_regular_hours(datetime(2026, 11, 19, 16, 30, tzinfo=KST), cal)
    assert not is_equity_regular_hours(datetime(2026, 1, 2, 9, 30, tzinfo=KST), cal)


def test_next_trading_open_lands_on_the_late_open() -> None:
    from kbj.core.calendar_compat import next_trading_open
    from kbj.core.time import KST

    cal = _late_cal()
    got = next_trading_open(datetime(2026, 11, 18, 16, 0, tzinfo=KST), cal=cal)
    assert got == datetime(2026, 11, 19, 10, 0, tzinfo=KST)
    got = next_trading_open(datetime(2026, 12, 31, 16, 0, tzinfo=KST), cal=cal)
    assert got == datetime(2027, 1, 4, 10, 0, tzinfo=KST)


def test_late_open_override_validates() -> None:
    from pydantic import ValidationError

    from kbj.core.calendar import HolidayOverride

    with pytest.raises(ValidationError):
        HolidayOverride.model_validate(
            {
                "late_open": [
                    {"date": "2026-11-19", "open": "16:30", "close": "10:00", "reason": "x"}
                ]
            }
        )
    with pytest.raises(ValidationError):
        HolidayOverride.model_validate(
            {
                "closed": [{"date": "2026-11-19", "reason": "x"}],
                "late_open": [
                    {"date": "2026-11-19", "open": "10:00", "close": "16:30", "reason": "y"}
                ],
            }
        )
