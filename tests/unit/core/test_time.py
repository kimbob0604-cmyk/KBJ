"""kbj/core/time.py — 벽시계 한 곳·KST 변환·naive 거부 (docs/p2_design.md §1.3).

시험은 벽시계 값에 기대지 않는다: `utcnow`·`now_kst` 는 두 번의 `datetime.now(UTC)` 사이에 드는지와
시간대만 본다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from kbj.core import calendar
from kbj.core.time import KST, kst_date, now_kst, to_kst, utcnow

LEGACY_KST = timezone(timedelta(hours=9))  # SD·ET 의 KST 정의


def test_kst_is_seoul_and_shared_with_calendar() -> None:
    assert KST == ZoneInfo("Asia/Seoul")
    assert calendar.KST is KST  # 정의는 한 곳 — 캘린더는 다시 내보낸다


def test_utcnow_is_aware_utc_and_reads_the_wall_clock() -> None:
    before = datetime.now(UTC)
    got = utcnow()
    after = datetime.now(UTC)
    assert got.tzinfo is UTC
    assert before <= got <= after


def test_now_kst_is_kst_and_the_same_moment() -> None:
    before = datetime.now(UTC)
    got = now_kst()
    after = datetime.now(UTC)
    assert got.tzinfo is KST
    assert got.utcoffset() == timedelta(hours=9)
    assert before <= got <= after


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (datetime(2026, 10, 5, 15, 0, tzinfo=UTC), datetime(2026, 10, 6, 0, 0, tzinfo=KST)),
        (
            datetime(2026, 10, 5, 14, 59, 59, tzinfo=UTC),
            datetime(2026, 10, 5, 23, 59, 59, tzinfo=KST),
        ),
        (datetime(2026, 10, 6, 9, 0, tzinfo=LEGACY_KST), datetime(2026, 10, 6, 9, 0, tzinfo=KST)),
        # 뉴욕 서머타임 끝(2026-11-01) 전후 — 같은 뉴욕 16:00 이 KST 로는 05:00·06:00
        (
            datetime(2026, 10, 30, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 10, 31, 5, 0, tzinfo=KST),
        ),
        (
            datetime(2026, 11, 2, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 11, 3, 6, 0, tzinfo=KST),
        ),
    ],
)
def test_to_kst_keeps_the_moment(ts: datetime, expected: datetime) -> None:
    got = to_kst(ts)
    assert got == expected == ts
    assert got.tzinfo is KST
    assert (got.date(), got.time()) == (expected.date(), expected.time())


def test_legacy_fixed_offset_kst_is_the_same_moment() -> None:
    """legacy `timezone(timedelta(hours=9))` 와 섞여도 비교·뺄셈이 맞다(한국은 서머타임 없음)."""
    a = datetime(2026, 10, 6, 15, 30, tzinfo=LEGACY_KST)
    b = datetime(2026, 10, 6, 15, 30, tzinfo=KST)
    assert a == b and a - b == timedelta(0)
    assert to_kst(a).isoformat(timespec="seconds") == "2026-10-06T15:30:00+09:00"


@pytest.mark.parametrize(
    ("ts", "d"),
    [
        (datetime(2026, 10, 5, 15, 30, tzinfo=UTC), date(2026, 10, 6)),  # UTC 로는 전날
        (datetime(2026, 10, 5, 14, 59, tzinfo=UTC), date(2026, 10, 5)),
        (datetime(2026, 10, 6, 0, 0, tzinfo=KST), date(2026, 10, 6)),
        (datetime(2026, 10, 6, 23, 59, 59, 999999, tzinfo=KST), date(2026, 10, 6)),
    ],
)
def test_kst_date(ts: datetime, d: date) -> None:
    assert kst_date(ts) == d


def test_naive_is_rejected() -> None:
    naive = datetime(2026, 10, 6, 9, 0)  # noqa: DTZ001 — naive 거부 확인용
    with pytest.raises(ValueError, match="naive"):
        to_kst(naive)
    with pytest.raises(ValueError, match="naive"):
        kst_date(naive)


def test_date_is_rejected() -> None:
    with pytest.raises(TypeError, match="datetime"):
        to_kst(date(2026, 10, 6))  # pyright: ignore[reportArgumentType]
