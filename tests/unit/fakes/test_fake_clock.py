"""시험용 가짜 시계(tests/fakes/clock.py) — 다른 묶음의 시험·시뮬레이션이 같은 시계 하나를 본다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from kbj.data.ratelimit import LocalRateLimiter, Priority, RateLimitConfig
from tests.fakes.clock import US, FakeClock

KST = ZoneInfo("Asia/Seoul")
START = datetime(2026, 10, 6, 5, 0, tzinfo=KST)  # 하루 시뮬레이션 창 시작(설계 §10.3)


def test_one_instant_in_every_unit() -> None:
    c = FakeClock(START)
    assert c.now() == START and c.now().tzinfo is UTC and c() == START
    assert c.now_us() == round(START.timestamp() * US)
    assert c.monotonic() == pytest.approx(START.timestamp())
    c.sleep(1.5)
    c.advance(0.5)
    assert c.now() == START + timedelta(seconds=2) and c.slept == [1.5]
    c.sleep(0)  # 0·음수 sleep 은 시각을 옮기지 않는다
    c.sleep(-1)
    assert c.now() == START + timedelta(seconds=2) and c.slept == [1.5]
    c.set(datetime(2026, 10, 7, 4, 0, tzinfo=KST))
    assert c.now().astimezone(KST).hour == 4


def test_refuses_naive_times_and_going_back() -> None:
    with pytest.raises(ValueError, match="naive"):
        FakeClock(datetime(2026, 10, 6, 5, 0))  # noqa: DTZ001 — 일부러 naive
    c = FakeClock(START)
    with pytest.raises(ValueError):
        c.advance(-1)


def test_drives_a_rate_limiter() -> None:
    """리미터 `Clock` 프로토콜(now_us·sleep)로 그대로 쓴다 — 기다림은 시각만 옮긴다."""
    c = FakeClock(START)
    lim = LocalRateLimiter(RateLimitConfig(), c)
    for _ in range(5):
        lim.acquire(Priority.P2, "FHKST01010100")
    assert c.now() - START == timedelta(seconds=1)  # 4/s → 다섯 번째는 1초 뒤
    assert c.slept == [0.25, 0.25, 0.25, 0.25]
