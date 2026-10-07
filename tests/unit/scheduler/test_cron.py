"""kbj/core/cron.py — 5필드 부분집합 파싱·일치·다음 시각(설계 §1.7)."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from kbj.core.cron import CronSpec

NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


def dt(*a: int) -> datetime:
    return datetime(*a)  # type: ignore[misc]  # noqa: DTZ001 — 벽시계(naive) 일부러


@pytest.mark.parametrize(
    ("expr", "minutes", "hours"),
    [
        ("5 8 * * 1-5", {5}, {8}),
        ("*/30 8-20 * * *", {0, 30}, set(range(8, 21))),
        ("0,30 8-20 * * 1-5", {0, 30}, set(range(8, 21))),
        ("10/20 * * * *", {10, 30, 50}, set(range(24))),
        ("0-10/5 3 * * *", {0, 5, 10}, {3}),
    ],
)
def test_parse_fields(expr: str, minutes: set[int], hours: set[int]) -> None:
    spec = CronSpec.parse(expr)
    assert spec.minutes == minutes and spec.hours == hours


@pytest.mark.parametrize(
    "bad",
    [
        "5 8 * *",  # 4필드
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "5-1 * * * *",
        "*/0 * * * *",
        "MON * * * *",
        "0 0 L * *",
        "0 0 31 2 *",  # 2월 31일은 없다
        "0 0 * * ?",
    ],
)
def test_rejects_unsupported_or_impossible(bad: str) -> None:
    with pytest.raises(ValueError):
        CronSpec.parse(bad)


def test_weekday_seven_is_sunday() -> None:
    assert CronSpec.parse("0 4 * * 7").weekdays == CronSpec.parse("0 4 * * 0").weekdays == {0}
    assert CronSpec.parse("0 4 * * 0").matches(dt(2026, 10, 11, 4, 0))  # 일요일


def test_dom_or_dow_when_both_restricted() -> None:
    spec = CronSpec.parse("0 9 16 * 1")  # 16일 또는 월요일(Vixie)
    assert spec.matches(dt(2026, 10, 16, 9, 0))  # 금 16일
    assert spec.matches(dt(2026, 10, 12, 9, 0))  # 월 12일
    assert not spec.matches(dt(2026, 10, 13, 9, 0))


def test_matches_ignores_seconds_and_uses_wall_clock() -> None:
    spec = CronSpec.parse("10 16 * * 1-5")
    assert spec.matches(dt(2026, 10, 6, 16, 10, 59))
    assert spec.matches(datetime(2026, 10, 6, 16, 10, tzinfo=NY))
    assert not spec.matches(dt(2026, 10, 10, 16, 10))  # 토


def test_next_after_is_strictly_after_and_keeps_tz() -> None:
    spec = CronSpec.parse("5 8 * * 1-5")
    assert spec.next_after(dt(2026, 10, 6, 8, 5)) == dt(2026, 10, 7, 8, 5)
    assert spec.next_after(dt(2026, 10, 6, 8, 4, 59)) == dt(2026, 10, 6, 8, 5)
    assert spec.next_after(dt(2026, 10, 9, 9, 0)) == dt(2026, 10, 12, 8, 5)  # 금 → 월
    got = spec.next_after(datetime(2026, 10, 6, 7, 0, tzinfo=KST))
    assert got == datetime(2026, 10, 6, 8, 5, tzinfo=KST) and got.tzinfo is KST


def test_next_after_crosses_months_and_leap_day() -> None:
    assert CronSpec.parse("0 9 16 2,5,8,11 *").next_after(dt(2026, 10, 7)) == dt(2026, 11, 16, 9, 0)
    assert CronSpec.parse("0 0 29 2 *").next_after(dt(2026, 3, 1)) == dt(2028, 2, 29, 0, 0)


def test_prev_at_or_before_same_day_only() -> None:
    spec = CronSpec.parse("40 16 * * 1-5")
    assert spec.prev_at_or_before(dt(2026, 10, 6, 18, 0)) == dt(2026, 10, 6, 16, 40)
    assert spec.prev_at_or_before(dt(2026, 10, 6, 16, 40, 30)) == dt(2026, 10, 6, 16, 40)
    assert spec.prev_at_or_before(dt(2026, 10, 6, 16, 39)) is None
    assert spec.prev_at_or_before(dt(2026, 10, 10, 18, 0)) is None  # 토


def test_times_on() -> None:
    spec = CronSpec.parse("0,30 8-9 * * 1-5")
    assert spec.times_on(date(2026, 10, 6)) == [time(8, 0), time(8, 30), time(9, 0), time(9, 30)]
    assert spec.times_on(date(2026, 10, 10)) == []
