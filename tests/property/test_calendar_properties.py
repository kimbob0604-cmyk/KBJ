"""core/calendar.py 속성 — 상태 머신 전역성·시간대 불변·trade_date 단조·하루 타일링, 만기 계산.

GEXLAB `tests/property/test_calendar_properties.py`(43a9ed1) 승격 — import 경로만 바꿨다.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import example, given
from hypothesis import strategies as st

from kbj.core.calendar import (
    KST,
    NIGHT_START,
    SessionInfo,
    State,
    TradingCalendar,
    monthly_expiry,
    night_bar_time,
    night_session_opens,
    state_at,
    weekly_monday_expiry,
    weekly_thursday_expiry,
)

CAL = TradingCalendar()  # XKRX 만 (override 파일과 무관)

# XKRX 범위(2000-01-01~2050-12-31) 안. hypothesis 는 경계를 naive 로 받는다
_LO = datetime(2000, 1, 2)  # noqa: DTZ001
_HI = datetime(2050, 12, 30)  # noqa: DTZ001
aware = st.datetimes(min_value=_LO, max_value=_HI, timezones=st.timezones())
days = st.dates(min_value=_LO.date(), max_value=_HI.date())

# 상태가 바뀔 수 있는 KST 시각(분 단위 경계, PLAN §4.6). 자정은 경계가 아니다(야간이 넘어간다)
_BOUNDARIES = {time(6), time(8), time(8, 45), time(15, 45), time(17, 50), time(18)}
# 한 달력일(KST 00:00~24:00)의 상태 순서: [전날 밤 뒷부분] IDLE [주간 묶음 [야간 앞부분 | IDLE]]
_DAY_PATTERN = re.compile(r"(NIGHT )?IDLE( PRE_DAY DAY POST_DAY (PRE_NIGHT NIGHT|IDLE))?")


def _key(info: SessionInfo) -> tuple[date, int] | None:
    """귀속 순서: 같은 trade_date 면 야간(전날 밤)이 주간보다 앞선다."""
    if info.trade_date is None:
        return None
    return info.trade_date, 0 if info.session == "night" else 1


def _night_start_date(ts: datetime) -> date:
    k = ts.astimezone(KST)
    return k.date() if k.time() >= NIGHT_START else k.date() - timedelta(days=1)


@given(ts=aware, other=st.timezones())
def test_state_at_total_and_timezone_invariant(ts: datetime, other: ZoneInfo) -> None:
    info = state_at(ts, CAL)
    assert state_at(ts.astimezone(UTC), CAL) == info
    assert state_at(ts.astimezone(other), CAL) == info

    in_session = info.state in (State.DAY, State.NIGHT)
    assert (info.trade_date is not None) is in_session
    assert (info.session is not None) is in_session
    if info.state is State.DAY:
        assert info.session == "day"
        kst_date = ts.astimezone(KST).date()
        assert info.trade_date == kst_date
        assert CAL.is_trading_day(kst_date)
    if info.state is State.NIGHT:
        assert info.session == "night"
        start = _night_start_date(ts)
        assert night_session_opens(start, CAL)
        assert info.trade_date == CAL.next_trading_day(start)  # T+1 귀속


@given(t1=aware, gap=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=12)))
def test_trade_date_is_non_decreasing(t1: datetime, gap: timedelta) -> None:
    t2 = t1 + gap
    k1, k2 = _key(state_at(t1, CAL)), _key(state_at(t2, CAL))
    if k1 is not None and k2 is not None:
        assert k1 <= k2


@given(d=days)
@example(d=date(2026, 9, 18))  # 금요일
@example(d=date(2026, 9, 19))  # 토요일(금요일 밤 뒷부분)
@example(d=date(2026, 9, 23))  # 휴장 전날
@example(d=date(2026, 9, 24))  # 휴장일
@example(d=date(2026, 10, 2))  # 월요일 휴장 앞 금요일
def test_states_tile_each_day_without_gaps(d: date) -> None:
    midnight = datetime.combine(d, time(0), tzinfo=KST)
    states: list[State] = []
    prev: SessionInfo | None = None
    for minute in range(24 * 60):
        ts = midnight + timedelta(minutes=minute)
        info = state_at(ts, CAL)
        if prev is not None and info != prev:
            # 상태는 정해진 경계에서만 바뀐다
            assert ts.astimezone(KST).time() in _BOUNDARIES, (ts, prev, info)
        if prev is None or info.state != prev.state:
            states.append(info.state)
        prev = info
    assert _DAY_PATTERN.fullmatch(" ".join(states)), (d, states)
    # 자정은 경계가 아니다: 23:59:59.999999 와 다음 날 00:00 은 같은 구간(야간 또는 IDLE)
    nxt = midnight + timedelta(days=1)
    assert state_at(nxt - timedelta(microseconds=1), CAL) == state_at(nxt, CAL)


@given(d=days, offset=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=1)))
def test_state_is_constant_within_each_minute(d: date, offset: timedelta) -> None:
    """경계가 모두 정각 분이라, 분 안의 어느 시각이든 그 분 시작과 같은 상태여야 한다."""
    ts = datetime.combine(d, time(0), tzinfo=KST) + offset
    floor = ts.replace(second=0, microsecond=0)
    assert state_at(ts, CAL) == state_at(floor, CAL)


@given(d=days, seconds=st.integers(min_value=18 * 3600, max_value=30 * 3600 - 1))
def test_night_bar_matches_state_machine(d: date, seconds: int) -> None:
    if not night_session_opens(d, CAL):
        return
    h, rem = divmod(seconds, 3600)
    ts, trade_date = night_bar_time(f"{d:%Y%m%d}", f"{h:02d}{rem // 60:02d}{rem % 60:02d}", CAL)
    assert state_at(ts, CAL) == SessionInfo(State.NIGHT, trade_date, "night")


@given(d=days)
def test_next_prev_trading_day_skip_only_closed_days(d: date) -> None:
    nxt, prv = CAL.next_trading_day(d), CAL.prev_trading_day(d)
    assert prv < d < nxt
    assert CAL.is_trading_day(nxt) and CAL.is_trading_day(prv)
    for gap in range(1, (nxt - prv).days):
        between = prv + timedelta(days=gap)
        assert between == d or not CAL.is_trading_day(between)
    assert CAL.next_weekday(d) > d and CAL.next_weekday(d).weekday() < 5


@given(year=st.integers(min_value=2000, max_value=2050), month=st.integers(1, 12))
def test_expiry_calculators_land_on_nearest_trading_day(year: int, month: int) -> None:
    first = date(year, month, 1)
    thursday2 = first + timedelta(days=(3 - first.weekday()) % 7 + 7)
    e = monthly_expiry(year, month, CAL)
    assert CAL.is_trading_day(e) and e <= thursday2
    skipped = (e + timedelta(days=i) for i in range(1, (thursday2 - e).days + 1))
    assert not any(CAL.is_trading_day(x) for x in skipped)

    monday = first + timedelta(days=(0 - first.weekday()) % 7)
    w = weekly_monday_expiry(monday, CAL)
    assert CAL.is_trading_day(w) and w >= monday
    assert not any(CAL.is_trading_day(monday + timedelta(days=i)) for i in range((w - monday).days))

    thursday = monday + timedelta(days=3)
    t = weekly_thursday_expiry(thursday, CAL)
    assert CAL.is_trading_day(t) and t <= thursday
