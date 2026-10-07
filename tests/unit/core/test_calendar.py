"""core/calendar.py — 거래일·야간장 규칙·세션 상태 머신·분봉 시각·만기 계산.

GEXLAB `tests/unit/test_calendar.py`(43a9ed1) 승격 — import 경로만 바꿨다(docs/p2_design.md §1.3).

실측 근거: docs/probe_results.md 실행 기록 2026-09-28 14:08(CM 분봉 — 금요일 09-18 밤 열림,
휴장 전날 09-23 밤 안 열림), runs/20260928_1442 night_board(단건 `futs_last_tr_date`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kbj.core.calendar import (
    KST,
    HolidayOverride,
    SessionInfo,
    State,
    TradingCalendar,
    WeeklyListing,
    day_bar_time,
    expiry_at,
    load_override,
    monthly_expiry,
    night_bar_time,
    night_session_opens,
    session_bounds,
    state_at,
    weeklies_listed,
    weekly_monday_expiry,
    weekly_thursday_expiry,
)

D = date
US = timedelta(microseconds=1)


def kst(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=KST)


def night(trade_date: date) -> SessionInfo:
    return SessionInfo(State.NIGHT, trade_date, "night")


def day(trade_date: date) -> SessionInfo:
    return SessionInfo(State.DAY, trade_date, "day")


IDLE = SessionInfo(State.IDLE)
PRE_DAY = SessionInfo(State.PRE_DAY)
POST_DAY = SessionInfo(State.POST_DAY)
PRE_NIGHT = SessionInfo(State.PRE_NIGHT)


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    # override 파일과 무관하게 XKRX 만으로 고정 (파일 편집이 이 테스트를 흔들지 않게)
    return TradingCalendar()


# ── 거래일 ────────────────────────────────────────────────────────────────────


def test_chuseok_week_2026_trading_days(cal: TradingCalendar) -> None:
    opened = {D(2026, 9, d) for d in (21, 22, 23, 28)}
    for d in range(21, 29):
        assert cal.is_trading_day(D(2026, 9, d)) is (D(2026, 9, d) in opened)
    assert cal.next_trading_day(D(2026, 9, 23)) == D(2026, 9, 28)
    assert cal.prev_trading_day(D(2026, 9, 28)) == D(2026, 9, 23)
    assert cal.next_trading_day(D(2026, 9, 22)) == D(2026, 9, 23)  # 자기 자신은 제외
    assert cal.prev_trading_day(D(2026, 9, 22)) == D(2026, 9, 21)
    assert cal.next_weekday(D(2026, 9, 23)) == D(2026, 9, 24)  # 휴장 무관
    assert cal.next_weekday(D(2026, 9, 18)) == D(2026, 9, 21)  # 금 → 월
    assert cal.next_weekday(D(2026, 9, 19)) == D(2026, 9, 21)  # 토 → 월


def test_substitute_and_hangul_holidays_2026(cal: TradingCalendar) -> None:
    assert not cal.is_trading_day(D(2026, 10, 5))  # 개천절 대체공휴일(월)
    assert not cal.is_trading_day(D(2026, 10, 9))  # 한글날(금)
    assert cal.next_trading_day(D(2026, 10, 2)) == D(2026, 10, 6)


def test_datetime_is_rejected_where_date_is_expected(cal: TradingCalendar) -> None:
    with pytest.raises(TypeError):
        cal.is_trading_day(kst(2026, 9, 28, 10))
    with pytest.raises(TypeError):
        TradingCalendar(extra_closed=[kst(2026, 6, 3)])


@pytest.mark.parametrize(
    ("d", "opens"),
    [
        (D(2026, 9, 18), True),  # 금요일 → 월요일 거래일: 실측 열림(토 06:00까지)
        (D(2026, 9, 22), True),
        (D(2026, 9, 23), False),  # 다음 날 추석 전날 휴장: 실측 안 열림
        (D(2026, 9, 24), False),  # 휴장일 자체
        (D(2026, 9, 26), False),  # 토요일
        (D(2026, 9, 28), True),
        (D(2026, 10, 2), True),  # 월요일(10-05) 휴장 앞 금요일 — 열림(05-22·08-14 실측)
        (D(2026, 5, 22), True),  # 실측(2026-09-29 CM 분봉): 월 05-25 휴장 앞 금요일 열림
        (D(2026, 8, 14), True),  # 실측: 토 08-15 공휴일·월 08-17 휴장 앞 금요일 열림
        (D(2026, 10, 8), False),  # 한글날(금) 전날
    ],
)
def test_night_session_opens(cal: TradingCalendar, d: date, opens: bool) -> None:
    assert night_session_opens(d, cal) is opens


# ── 상태 머신 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (kst(2026, 9, 22, 20), night(D(2026, 9, 23))),
        (kst(2026, 9, 23, 3), night(D(2026, 9, 23))),  # 09-22 밤의 자정 뒤
        (kst(2026, 9, 23, 10), day(D(2026, 9, 23))),
        (kst(2026, 9, 23, 16), POST_DAY),
        (kst(2026, 9, 23, 17, 55), IDLE),  # 휴장 전날: PRE_NIGHT 없음
        (kst(2026, 9, 23, 20), IDLE),
        (kst(2026, 9, 24, 3), IDLE),
        (kst(2026, 9, 24, 10), IDLE),
        (kst(2026, 9, 25, 10), IDLE),
        (kst(2026, 9, 25, 20), IDLE),
        (kst(2026, 9, 27, 20), IDLE),  # 일요일 밤
        (kst(2026, 9, 28, 3), IDLE),
        (kst(2026, 9, 28, 8, 30), PRE_DAY),
        (kst(2026, 9, 28, 10), day(D(2026, 9, 28))),
        (kst(2026, 9, 28, 20), night(D(2026, 9, 29))),
    ],
)
def test_chuseok_week_states(cal: TradingCalendar, ts: datetime, expected: SessionInfo) -> None:
    assert state_at(ts, cal) == expected


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (kst(2026, 9, 18, 17, 55), PRE_NIGHT),
        (kst(2026, 9, 18, 20), night(D(2026, 9, 21))),
        (kst(2026, 9, 19, 0), night(D(2026, 9, 21))),
        (kst(2026, 9, 19, 3), night(D(2026, 9, 21))),  # 토요일 새벽 → 월요일 귀속
        (kst(2026, 9, 19, 6) - US, night(D(2026, 9, 21))),
        (kst(2026, 9, 19, 6), IDLE),
        (kst(2026, 9, 19, 10), IDLE),
        (kst(2026, 9, 19, 20), IDLE),
        (kst(2026, 9, 20, 3), IDLE),
        (kst(2026, 9, 21, 3), IDLE),  # 월요일 새벽: 일요일 밤은 없다
        (kst(2026, 9, 21, 8), PRE_DAY),
    ],
)
def test_friday_night_belongs_to_monday(
    cal: TradingCalendar, ts: datetime, expected: SessionInfo
) -> None:
    assert state_at(ts, cal) == expected


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (kst(2026, 10, 2, 16), POST_DAY),
        (kst(2026, 10, 2, 17, 55), PRE_NIGHT),
        (kst(2026, 10, 2, 20), night(D(2026, 10, 6))),  # 월 10-05 휴장 → 화 귀속
        (kst(2026, 10, 3, 3), night(D(2026, 10, 6))),
        (kst(2026, 10, 3, 6), IDLE),
        (kst(2026, 10, 5, 10), IDLE),
        (kst(2026, 10, 5, 20), IDLE),  # 휴장일 밤은 없다
        (kst(2026, 10, 6, 3), IDLE),
        (kst(2026, 10, 6, 10), day(D(2026, 10, 6))),
        (kst(2026, 10, 6, 20), night(D(2026, 10, 7))),
    ],
)
def test_friday_before_monday_holiday_opens_and_belongs_to_tuesday(
    cal: TradingCalendar, ts: datetime, expected: SessionInfo
) -> None:
    """실측(2026-09-29 CM 분봉 — 05-22·08-14 금요일 밤 열림): 10-02(금) 밤도 열린다(화 귀속)."""
    assert state_at(ts, cal) == expected


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (kst(2026, 9, 22, 0), night(D(2026, 9, 22))),  # 09-21 밤의 뒷부분
        (kst(2026, 9, 22, 6) - US, night(D(2026, 9, 22))),
        (kst(2026, 9, 22, 6), IDLE),
        (kst(2026, 9, 22, 8) - US, IDLE),
        (kst(2026, 9, 22, 8), PRE_DAY),
        (kst(2026, 9, 22, 8, 45) - US, PRE_DAY),
        (kst(2026, 9, 22, 8, 45), day(D(2026, 9, 22))),
        (kst(2026, 9, 22, 15, 45) - US, day(D(2026, 9, 22))),
        (kst(2026, 9, 22, 15, 45), POST_DAY),
        (kst(2026, 9, 22, 17, 50) - US, POST_DAY),
        (kst(2026, 9, 22, 17, 50), PRE_NIGHT),
        (kst(2026, 9, 22, 18) - US, PRE_NIGHT),
        (kst(2026, 9, 22, 18), night(D(2026, 9, 23))),
        (kst(2026, 9, 23, 6) - US, night(D(2026, 9, 23))),
        (kst(2026, 9, 23, 6), IDLE),
    ],
)
def test_boundaries_are_half_open(
    cal: TradingCalendar, ts: datetime, expected: SessionInfo
) -> None:
    assert state_at(ts, cal) == expected


def test_state_at_rejects_naive(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="naive"):
        state_at(datetime(2026, 9, 28, 10), cal)  # noqa: DTZ001 — naive 거부 확인용


def test_state_at_is_timezone_invariant(cal: TradingCalendar) -> None:
    ts = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)  # = 18:00 KST
    assert state_at(ts, cal) == night(D(2026, 9, 23))
    for tz in (KST, ZoneInfo("America/New_York"), timezone(timedelta(hours=-11, minutes=-30))):
        assert state_at(ts.astimezone(tz), cal) == night(D(2026, 9, 23))


# ── override ──────────────────────────────────────────────────────────────────


def test_override_closed_makes_holiday_and_holiday_eve(cal: TradingCalendar) -> None:
    election = D(2026, 6, 3)  # 가짜 임시휴장 주입
    assert cal.is_trading_day(election)
    assert night_session_opens(D(2026, 6, 2), cal)

    c = TradingCalendar(extra_closed=[election])
    assert not c.is_trading_day(election)
    assert c.next_trading_day(D(2026, 6, 2)) == D(2026, 6, 4)
    assert not night_session_opens(D(2026, 6, 2), c)  # 휴장 전날 밤
    assert state_at(kst(2026, 6, 2, 20), c) == IDLE
    assert state_at(kst(2026, 6, 3, 10), c) == IDLE
    assert state_at(kst(2026, 6, 1, 20), c) == night(D(2026, 6, 2))
    assert state_at(kst(2026, 6, 3, 20), c) == IDLE  # 휴장일 밤도 없다
    assert state_at(kst(2026, 6, 4, 20), c) == night(D(2026, 6, 5))


def test_override_open_reopens_day(cal: TradingCalendar) -> None:
    c = TradingCalendar(extra_open=[D(2026, 9, 24)])
    assert c.is_trading_day(D(2026, 9, 24))
    assert state_at(kst(2026, 9, 23, 20), c) == night(D(2026, 9, 24))
    assert state_at(kst(2026, 9, 24, 10), c) == day(D(2026, 9, 24))
    assert state_at(kst(2026, 9, 24, 20), c) == IDLE  # 09-25 추석은 여전히 휴장


def test_override_conflict_raises() -> None:
    with pytest.raises(ValueError, match="양쪽"):
        TradingCalendar(extra_closed=[D(2026, 6, 3)], extra_open=[D(2026, 6, 3)])
    with pytest.raises(ValueError, match="거꾸로"):
        TradingCalendar(start=D(2027, 1, 1), end=D(2026, 1, 1))


def test_load_override_file(tmp_path: Path) -> None:
    p = tmp_path / "o.yaml"
    p.write_text(
        "closed:\n  - date: 2026-06-03\n    reason: 지방선거\nopen: []\n", encoding="utf-8"
    )
    o = load_override(p)
    assert [(x.date, x.reason) for x in o.closed] == [(D(2026, 6, 3), "지방선거")]
    assert o.open == ()
    c = TradingCalendar.from_override(o)
    assert not c.is_trading_day(D(2026, 6, 3))


@pytest.mark.parametrize("text", ["", "closed:\nopen:\n", "closed: []\n"])
def test_load_override_empty_forms(tmp_path: Path, text: str) -> None:
    p = tmp_path / "o.yaml"
    p.write_text(text, encoding="utf-8")
    assert load_override(p) == HolidayOverride()


@pytest.mark.parametrize(
    "text",
    [
        "closed:\n  - date: 2026-06-03\n",  # 사유 없음
        "closed:\n  - date: 2026-02-30\n    reason: x\n",  # 없는 날짜
        "closed:\n  - date: 2026-06-03\n    reason: ''\n",  # 빈 사유
        "closd: []\n",  # 오타 키
        # 같은 날이 closed·open 양쪽
        "closed:\n  - date: 2026-06-03\n    reason: a\n"
        "open:\n  - date: 2026-06-03\n    reason: b\n",
    ],
)
def test_load_override_rejects_bad_file(tmp_path: Path, text: str) -> None:
    p = tmp_path / "o.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):  # ValidationError 도 ValueError (없는 날짜는 yaml 이 먼저 거부)
        load_override(p)


def test_default_uses_repo_override_file() -> None:
    o = load_override()  # 실제 config/holidays_override.yaml 이 스키마를 통과한다
    c = TradingCalendar.default()
    assert c is TradingCalendar.default()  # 프로세스당 한 번
    for x in o.closed:
        assert not c.is_trading_day(x.date)
    for x in o.open:
        assert c.is_trading_day(x.date)


def test_beyond_xkrx_coverage_falls_back_to_weekdays(cal: TradingCalendar) -> None:
    assert cal.coverage == (D(2000, 1, 1), D(2050, 12, 31))
    assert cal.covered(D(2050, 12, 31))
    assert not cal.covered(D(2051, 1, 2))
    assert not cal.covered(D(1999, 12, 31))
    assert not cal.is_trading_day(D(2050, 12, 30))  # XKRX: 연말 휴장
    assert cal.next_trading_day(D(2050, 12, 29)) == D(2051, 1, 2)  # 범위 밖 평일
    assert not cal.is_trading_day(D(2051, 1, 1))  # 일요일
    c = TradingCalendar(extra_closed=[D(2051, 1, 2)])
    assert c.next_trading_day(D(2050, 12, 29)) == D(2051, 1, 3)  # override 는 범위 밖에도 적용


def test_long_closed_run_raises() -> None:
    c = TradingCalendar(extra_closed=[D(2026, 9, 29) + timedelta(days=i) for i in range(70)])
    with pytest.raises(RuntimeError, match="연속 휴장"):
        c.next_trading_day(D(2026, 9, 28))


# ── 세션 경계·만기 시각 ───────────────────────────────────────────────────────


def test_session_bounds(cal: TradingCalendar) -> None:
    assert session_bounds(D(2026, 9, 28), "day") == (
        kst(2026, 9, 28, 8, 45),
        kst(2026, 9, 28, 15, 45),
    )
    start, end = session_bounds(D(2026, 9, 18), "night")
    assert (start, end) == (kst(2026, 9, 18, 18), kst(2026, 9, 19, 6))
    assert state_at(start, cal) == night(D(2026, 9, 21))
    assert state_at(end - US, cal) == night(D(2026, 9, 21))
    assert state_at(end, cal) == IDLE
    with pytest.raises(ValueError, match="session"):
        session_bounds(D(2026, 9, 28), "evening")  # pyright: ignore[reportArgumentType]


def test_expiry_at() -> None:
    assert expiry_at(D(2026, 10, 8)) == kst(2026, 10, 8, 15, 20)
    assert expiry_at(D(2026, 10, 8)) == datetime(2026, 10, 8, 6, 20, tzinfo=UTC)


# ── 분봉 시각 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ymd", "hms", "ts", "trade_date"),
    [
        ("20260922", "300000", kst(2026, 9, 23, 6), D(2026, 9, 23)),  # phase1_design §8 예
        ("20260922", "240000", kst(2026, 9, 23, 0), D(2026, 9, 23)),
        ("20260922", "280400", kst(2026, 9, 23, 4, 4), D(2026, 9, 23)),
        ("20260922", "221900", kst(2026, 9, 22, 22, 19), D(2026, 9, 23)),
        ("20260918", "240000", kst(2026, 9, 19, 0), D(2026, 9, 21)),  # 금요일 밤 → 월요일
        ("20260918", "180000", kst(2026, 9, 18, 18), D(2026, 9, 21)),
        ("20260918", "295959", kst(2026, 9, 19, 5, 59, 59), D(2026, 9, 21)),
    ],
)
def test_night_bar_time(
    cal: TradingCalendar, ymd: str, hms: str, ts: datetime, trade_date: date
) -> None:
    got_ts, got_td = night_bar_time(ymd, hms, cal)
    assert got_ts == ts
    assert got_ts.tzinfo is UTC
    assert got_td == trade_date


def test_night_bar_inside_session_matches_state_machine(cal: TradingCalendar) -> None:
    for hms in ("180000", "221900", "240000", "280400", "295900"):
        ts, td = night_bar_time("20260918", hms, cal)
        assert state_at(ts, cal) == night(td)


@pytest.mark.parametrize(
    ("ymd", "hms"),
    [
        ("2026092", "240000"),
        ("202609221", "240000"),
        ("20261322", "240000"),  # 13월
        ("20260931", "240000"),  # 9월 31일
        ("２０２６０９２２", "240000"),  # 전각 숫자
        ("2026-09-22", "240000"),
        ("20260922", "175959"),  # 18시 전
        ("20260922", "060000"),  # 확장 표기가 아닌 새벽 시각
        ("20260922", "300001"),  # 30시 뒤
        ("20260922", "310000"),
        ("20260922", "246000"),  # 60분
        ("20260922", "240060"),  # 60초
        ("20260922", "24000"),
        ("20260922", "24:00:00"),
        ("20260922", " 240000"),
        ("20260922", ""),
    ],
)
def test_night_bar_time_rejects_garbage(cal: TradingCalendar, ymd: str, hms: str) -> None:
    with pytest.raises(ValueError):
        night_bar_time(ymd, hms, cal)


def test_day_bar_time() -> None:
    assert day_bar_time("20260928", "140100") == (kst(2026, 9, 28, 14, 1), D(2026, 9, 28))
    ts, td = day_bar_time("20260922", "154500")
    assert (ts, td) == (datetime(2026, 9, 22, 6, 45, tzinfo=UTC), D(2026, 9, 22))
    assert ts.tzinfo is UTC
    assert day_bar_time("20260928", "080000")[0] == kst(2026, 9, 28, 8)


@pytest.mark.parametrize(
    "hms", ["075959", "175000", "180000", "221900", "240000", "300000", "abcdef", "1401"]
)
def test_day_bar_time_rejects_outside_day_hours(hms: str) -> None:
    with pytest.raises(ValueError):
        day_bar_time("20260928", hms)


# ── 만기 계산 (교차검증용) ─────────────────────────────────────────────────────


def test_monthly_expiry_matches_kis(cal: TradingCalendar) -> None:
    # KIS 단건 futs_last_tr_date (runs/20260928_1442): 202610 옵션 20261008, 선물 A01612 20261210
    assert monthly_expiry(2026, 10, cal) == D(2026, 10, 8)
    assert monthly_expiry(2026, 12, cal) == D(2026, 12, 10)


def test_monthly_expiry_moves_earlier_on_holiday(cal: TradingCalendar) -> None:
    # 2025-10: 둘째 목요일 10-09 한글날, 10-03~08 개천절·추석 연휴 → 10-02
    assert not cal.is_trading_day(D(2025, 10, 9))
    assert monthly_expiry(2025, 10, cal) == D(2025, 10, 2)
    c = TradingCalendar(extra_closed=[D(2026, 10, 8)])
    assert monthly_expiry(2026, 10, c) == D(2026, 10, 7)
    with pytest.raises(ValueError):
        monthly_expiry(2026, 13, cal)


def test_weekly_monday_expiry_postponed_on_holiday(cal: TradingCalendar) -> None:
    # KIS futs_last_tr_date: 월요일 위클리 BAFBZWA39 20260928, BAFC0WA39 20261006
    assert weekly_monday_expiry(D(2026, 9, 28), cal) == D(2026, 9, 28)
    assert weekly_monday_expiry(D(2026, 10, 5), cal) == D(2026, 10, 6)
    with pytest.raises(ValueError, match="월요일"):
        weekly_monday_expiry(D(2026, 10, 6), cal)


def test_weekly_thursday_expiry(cal: TradingCalendar) -> None:
    # KIS futs_last_tr_date: 목요일 위클리 B09FFWA39 20261001
    assert weekly_thursday_expiry(D(2026, 10, 1), cal) == D(2026, 10, 1)
    # 09-24(목) 추석 전날 휴장 → [확인 필요] 기본값: 앞 거래일 09-23
    assert weekly_thursday_expiry(D(2026, 9, 24), cal) == D(2026, 9, 23)
    with pytest.raises(ValueError, match="목요일"):
        weekly_thursday_expiry(D(2026, 9, 28), cal)


@pytest.mark.parametrize(
    ("monday", "expected"),
    [
        # 월물리스트 실측: WKM 260904(09-28)·261001, WKI 261001(10-01)만 — 10-05 주는 월물 만기 주
        (D(2026, 9, 28), WeeklyListing(D(2026, 9, 28), D(2026, 10, 1), None)),
        (D(2026, 10, 5), WeeklyListing(D(2026, 10, 6), None, D(2026, 10, 8))),
        (D(2026, 9, 21), WeeklyListing(D(2026, 9, 21), D(2026, 9, 23), None)),
        (D(2026, 9, 7), WeeklyListing(D(2026, 9, 7), None, D(2026, 9, 10))),
        # 월물 만기가 앞 주로 당겨진 달: 그 앞 주가 월물 만기 주
        (D(2025, 9, 29), WeeklyListing(D(2025, 9, 29), None, D(2025, 10, 2))),
        # 월~목 휴장: 월요일 위클리는 금요일로 순연, 목요일 위클리는 그 주 안에 날이 없다
        (D(2025, 10, 6), WeeklyListing(D(2025, 10, 10), None, None)),
    ],
)
def test_weeklies_listed(cal: TradingCalendar, monday: date, expected: WeeklyListing) -> None:
    assert weeklies_listed(monday, cal) == expected


def test_weeklies_listed_requires_monday(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="월요일"):
        weeklies_listed(D(2026, 10, 1), cal)


# ── 실제 override 파일 ─────────────────────────────────────────────────────────


def test_default_calendar_closes_2026_local_election() -> None:
    # XKRX 4.13 은 2026-06-03(지방선거)을 거래일로 본다. KRX 선물 일별이 그날만 0행이라
    # config/holidays_override.yaml 에 넣었다(2026-09-28 확인)
    assert TradingCalendar().is_trading_day(D(2026, 6, 3))  # XKRX 만으로는 거래일
    c = TradingCalendar.default()
    assert not c.is_trading_day(D(2026, 6, 3))
    assert c.is_trading_day(D(2026, 6, 2)) and c.is_trading_day(D(2026, 6, 4))
    assert c.next_trading_day(D(2026, 6, 2)) == D(2026, 6, 4)
    assert not night_session_opens(D(2026, 6, 2), c)  # 휴장 전날 밤
    assert state_at(kst(2026, 6, 3, 10), c) == IDLE


# ── 거래일 수 (베이시스 이월 나이 — metrics §1.3, 2026-09-29 사용자 결정) ──


@pytest.mark.parametrize(
    ("a", "b", "n"),
    [
        (date(2026, 9, 28), date(2026, 9, 28), 0),  # 같은 날
        (date(2026, 9, 28), date(2026, 9, 29), 1),  # 월 → 화
        (date(2026, 9, 18), date(2026, 9, 21), 1),  # 금 → 월 (주말)
        (date(2026, 9, 23), date(2026, 9, 28), 1),  # 수 → 월 (추석 09-24·25 휴장 + 주말)
        (date(2026, 9, 22), date(2026, 9, 28), 2),
        (date(2026, 9, 26), date(2026, 9, 28), 1),  # 시작이 토요일 — (a, b] 의 거래일
        (date(2026, 9, 28), date(2026, 10, 5), 4),  # 10-05 개천절 대체휴일 — 끝이 휴장일
    ],
)
def test_trading_days_between_counts_trading_days_in_half_open_range(
    cal: TradingCalendar, a: date, b: date, n: int
) -> None:
    assert cal.trading_days_between(a, b) == n


def test_trading_days_between_rejects_reversed_range(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="앞"):
        cal.trading_days_between(date(2026, 9, 29), date(2026, 9, 28))


def test_session_day_maps_night_to_its_evening(cal: TradingCalendar) -> None:
    assert cal.session_day(date(2026, 9, 29), "day") == date(2026, 9, 29)
    assert cal.session_day(date(2026, 9, 29), "night") == date(2026, 9, 28)  # 월 저녁
    assert cal.session_day(date(2026, 9, 21), "night") == date(2026, 9, 18)  # 금 저녁 → 월 귀속
    with pytest.raises(ValueError, match="거래일"):
        cal.session_day(date(2026, 9, 24), "day")  # 추석 휴장
