"""SD 2026 휴장 하드코딩 대 정본 캘린더 — 차이 다섯 날 고정 (docs/p2_design.md §7.3).

SD `server.py:_KR_HOLIDAYS_2026`:17971(f46178c)·`_is_kr_holiday`:17987·
`_next_trading_open_kst`:17994 는 legacy 재배선(묶음 H)에서 `kbj.core.calendar_compat` 로
바뀐다. 이 시험은

1. SD 하드코딩을 고정 사본(`SD_KR_HOLIDAYS_2026`)과 그 판정 규칙(주말 + 사본)으로 재현하고,
2. 2026년 모든 날에서 SD 판정과 정본(XKRX + `config/holidays_override.yaml`)이 **정확히 다섯 날**만
   다르다는 것(방향까지)을 고정하고,
3. 바꾼 뒤의 호환 함수가 그 다섯 날에 정본 값을 낸다는 것을 고정한다.

고정 사본은 legacy 소스에 그 집합이 남아 있는 동안 소스와 같은지도 본다(H 가 지우면 사본이 기준).
"""

from __future__ import annotations

import ast
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from kbj.core.calendar import KST, TradingCalendar
from kbj.core.calendar_compat import is_kr_holiday, is_kr_regular_hours, next_trading_open

D = date
REPO_ROOT = Path(__file__).resolve().parents[3]
SD_SERVER = REPO_ROOT / "legacy" / "stock_dashboard" / "server.py"

# SD `server.py:_KR_HOLIDAYS_2026`:17971 (f46178c) 고정 사본 — 손대지 않는다
SD_KR_HOLIDAYS_2026 = frozenset(
    {
        "2026-01-01",  # 신정
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",  # 설날
        "2026-03-02",  # 삼일절 대체 (3/1 일요일)
        "2026-05-05",  # 어린이날
        "2026-05-25",  # 부처님오신날 대체 (5/24 일요일)
        "2026-06-06",  # 현충일 (토요일이지만 KRX 휴장)
        "2026-08-15",  # 광복절 (토요일)
        "2026-09-24",
        "2026-09-25",
        "2026-09-28",  # 추석 + 대체월요일
        "2026-10-03",  # 개천절 (토요일)
        "2026-10-09",  # 한글날
        "2026-12-25",  # 성탄절
        "2026-12-31",  # 연말 휴장
    }
)

# 설계 §7.3 표: 날짜 → (정본 휴장?, SD 휴장?)
EXPECTED_DIFF: dict[date, tuple[bool, bool]] = {
    D(2026, 5, 1): (True, False),  # 근로자의 날 — SD 누락
    D(2026, 6, 3): (True, False),  # 지방선거(덮어쓰기) — SD 누락
    D(2026, 8, 17): (True, False),  # 광복절 대체공휴일 — SD 누락
    D(2026, 9, 28): (False, True),  # 개장 — SD 가 추석 대체로 잘못 넣음
    D(2026, 10, 5): (True, False),  # 개천절 대체공휴일 — SD 누락
}


def sd_is_kr_holiday(dt: datetime) -> bool:
    """SD `_is_kr_holiday`:17987 의 규칙 그대로 — 주말 또는 2026 하드코딩."""
    if dt.weekday() >= 5:
        return True
    return dt.strftime("%Y-%m-%d") in SD_KR_HOLIDAYS_2026


def sd_next_trading_open_kst(now: datetime) -> datetime:
    """SD `_next_trading_open_kst`:17994 의 규칙 그대로."""
    cand = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)
    while sd_is_kr_holiday(cand):
        cand += timedelta(days=1)
    return cand


def _days(year: int) -> list[date]:
    d = D(year, 1, 1)
    out: list[date] = []
    while d.year == year:
        out.append(d)
        d += timedelta(days=1)
    return out


def _at(d: date, hh: int = 10) -> datetime:
    return datetime.combine(d, time(hh), tzinfo=KST)


def test_sd_and_canonical_differ_on_exactly_five_days_in_2026() -> None:
    cal = TradingCalendar.default()
    diff = {
        d: (not cal.is_trading_day(d), sd_is_kr_holiday(_at(d)))
        for d in _days(2026)
        if (not cal.is_trading_day(d)) != sd_is_kr_holiday(_at(d))
    }
    assert diff == EXPECTED_DIFF


@pytest.mark.parametrize(("d", "expected"), sorted(EXPECTED_DIFF.items()))
def test_each_difference_day_after_rewiring(d: date, expected: tuple[bool, bool]) -> None:
    canonical_closed, sd_closed = expected
    assert d.weekday() < 5  # 다섯 날 모두 평일
    assert sd_is_kr_holiday(_at(d)) is sd_closed
    # 바꾼 뒤(H): legacy 함수 본문 = calendar_compat → 정본 값
    assert is_kr_holiday(_at(d)) is canonical_closed
    assert is_kr_holiday(d) is canonical_closed
    assert is_kr_regular_hours(_at(d)) is (not canonical_closed)


def test_only_the_election_comes_from_the_override_file() -> None:
    """다섯 날 중 XKRX 자체가 아는 날은 넷, 지방선거(06-03)만 덮어쓰기에서 온다."""
    xkrx = TradingCalendar()
    xkrx_disagrees = {
        d for d, (closed, _) in EXPECTED_DIFF.items() if closed is xkrx.is_trading_day(d)
    }
    assert xkrx_disagrees == {D(2026, 6, 3)}


def test_compat_matches_canonical_on_every_day_of_2026() -> None:
    cal = TradingCalendar.default()
    for d in _days(2026):
        assert is_kr_holiday(d) is (not cal.is_trading_day(d)), d


@pytest.mark.parametrize(
    ("now", "sd_open", "canonical_open"),
    [
        # 근로자의 날 전날 장후: SD 는 05-01(휴장)에 연다고 봤다
        (datetime(2026, 4, 30, 16, tzinfo=KST), (2026, 5, 1), (2026, 5, 4)),
        (datetime(2026, 6, 2, 16, tzinfo=KST), (2026, 6, 3), (2026, 6, 4)),
        (datetime(2026, 8, 14, 16, tzinfo=KST), (2026, 8, 17), (2026, 8, 18)),
        # 추석 연휴: SD 는 09-28(개장)을 건너뛰었다
        (datetime(2026, 9, 23, 16, tzinfo=KST), (2026, 9, 29), (2026, 9, 28)),
        (datetime(2026, 10, 2, 16, tzinfo=KST), (2026, 10, 5), (2026, 10, 6)),
    ],
)
def test_next_open_changes_around_the_five_days(
    now: datetime, sd_open: tuple[int, int, int], canonical_open: tuple[int, int, int]
) -> None:
    assert sd_next_trading_open_kst(now) == datetime(*sd_open, 9, tzinfo=KST)
    assert next_trading_open(now) == datetime(*canonical_open, 9, tzinfo=KST)


def test_sd_rule_and_canonical_agree_elsewhere_in_2026() -> None:
    """다섯 날을 빼면 SD 규칙과 정본이 같다 — 차이는 하드코딩 누락·오기뿐이다."""
    for d in _days(2026):
        if d not in EXPECTED_DIFF:
            assert sd_is_kr_holiday(_at(d)) is is_kr_holiday(d), d


def _sd_holiday_literal(source: str) -> frozenset[str] | None:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_KR_HOLIDAYS_2026" for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, set)
            return frozenset(str(x) for x in value)  # pyright: ignore[reportUnknownVariableType]
    return None


def test_frozen_copy_matches_legacy_source_while_it_exists() -> None:
    if not SD_SERVER.exists():
        pytest.skip("legacy/stock_dashboard 가 없다(legacy 정리 뒤) — 고정 사본이 기준")
    found = _sd_holiday_literal(SD_SERVER.read_text(encoding="utf-8"))
    if found is None:
        pytest.skip("SD 하드코딩이 지워졌다(묶음 H 재배선 뒤) — 고정 사본이 기준")
    assert found == SD_KR_HOLIDAYS_2026
