"""레이트리미터 (설계 §3, 2026-09-28 검토 수정: 4.0/s·버킷 1).

Redis 판·프로세스 판을 같은 규칙으로 잰다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from itertools import pairwise

import fakeredis
import pytest

from data.kis.ratelimit import (
    TR_DISPLAY_BOARD_CALLPUT,
    US,
    LocalRateLimiter,
    Priority,
    RateLimitConfig,
    RateLimiter,
    RateLimitTimeout,
    Reason,
    RedisRateLimiter,
    effective_rate,
    interval_us,
    limiter_key,
)

APP_KEY = "PSfakeAppKey0123456789"
T0 = 1_790_553_600 * US  # 2026-09-28 무렵 (에포크 마이크로초)
TR_PRICE = "FHMIF10000000"
TR_INVESTOR = "FHPTJ04030000"

Limiter = RedisRateLimiter | LocalRateLimiter


class FakeClock:
    def __init__(self, t: int = T0) -> None:
        self.t = t
        self.slept: list[float] = []

    def now_us(self) -> int:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += round(seconds * US)

    def advance(self, seconds: float) -> None:
        self.t += round(seconds * US)


MakeLimiter = Callable[..., Limiter]


@pytest.fixture(params=["redis", "local"])
def make(request: pytest.FixtureRequest) -> MakeLimiter:
    server = fakeredis.FakeServer()

    def _make(clock: FakeClock, config: RateLimitConfig | None = None) -> Limiter:
        if request.param == "redis":
            return RedisRateLimiter(fakeredis.FakeRedis(server=server), APP_KEY, config, clock)
        return LocalRateLimiter(config, clock)

    return _make


def max_in_window(times: list[int], window: int = US) -> int:
    """반열린 창 [t, t+window) 안 최대 건수 (창 시작을 허가 시각에 두면 충분)."""
    ts = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(ts):
        while ts[j] <= t - window:
            j += 1
        best = max(best, i - j + 1)
    return best


def test_default_config_is_corrected_design() -> None:
    c = RateLimitConfig()
    assert (c.rate, c.capacity, c.floor_rate) == (4.0, 1, 1.0)
    assert c.tr_interval_us(TR_DISPLAY_BOARD_CALLPUT) == US
    assert c.tr_interval_us(TR_PRICE) == 0
    assert interval_us(4.0) == 250_000


def test_protocol_is_satisfied() -> None:
    lims: list[RateLimiter] = [LocalRateLimiter(), RedisRateLimiter(fakeredis.FakeRedis(), "k")]
    assert all(hasattr(x, "acquire") for x in lims)


def test_grants_are_spaced_a_quarter_second(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    times: list[int] = []
    for _ in range(12):
        lim.acquire(Priority.P1, TR_PRICE)
        times.append(clock.t)
    assert [b - a for a, b in pairwise(times)] == [250_000] * 11
    assert max_in_window(times) == 4


def test_burst_capacity_one_never_five_in_a_second(make: MakeLimiter) -> None:
    """쉬었다가 몰려도 버킷 1 이라 한꺼번에 나가지 않는다 (버킷 4 였다면 1초에 7~8건)."""
    clock = FakeClock()
    lim = make(clock)
    clock.advance(30)
    granted = 0
    for _ in range(10):
        granted += lim.attempt(Priority.P1, TR_PRICE).granted
    assert granted == 1


def test_timeout_zero_tries_once_without_sleeping(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_PRICE)
    with pytest.raises(RateLimitTimeout) as ei:
        lim.acquire(Priority.P4, TR_PRICE, timeout=0)
    assert ei.value.reason is Reason.BUCKET
    assert clock.slept == []
    # 등록하지 않았으므로 P4 가 P2 를 막지 않는다 (그 반대 방향만 막는다)
    clock.advance(0.25)
    assert lim.attempt(Priority.P2, TR_PRICE).granted


def test_hopeless_timeout_fails_fast_and_unregisters(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_PRICE)
    with pytest.raises(RateLimitTimeout):
        lim.acquire(Priority.P0, TR_PRICE, timeout=0.1)  # 0.25초 뒤에야 가능
    assert clock.slept == []
    clock.advance(0.25)
    # P0 등록이 남아 있었다면 P4 는 PRIORITY 로 막힌다
    assert lim.attempt(Priority.P4, TR_PRICE).reason is Reason.GRANTED


def test_timeout_long_enough_waits(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_PRICE)
    lim.acquire(Priority.P2, TR_PRICE, timeout=0.3)
    assert clock.t == T0 + 250_000
    assert sum(clock.slept) == pytest.approx(0.25)


def test_callput_min_interval_one_second(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_DISPLAY_BOARD_CALLPUT)
    clock.advance(0.25)
    d = lim.attempt(Priority.P1, TR_DISPLAY_BOARD_CALLPUT)
    assert (d.granted, d.reason, d.wait_us) == (False, Reason.TR_INTERVAL, 750_000)
    # 다른 TR 은 그 사이에 나간다
    assert lim.attempt(Priority.P1, TR_PRICE).granted
    lim.acquire(Priority.P1, TR_DISPLAY_BOARD_CALLPUT)
    assert clock.t == T0 + US


def test_tr_interval_wait_does_not_block_lower_priority(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_DISPLAY_BOARD_CALLPUT)
    clock.advance(0.25)
    d = lim.attempt(Priority.P1, TR_DISPLAY_BOARD_CALLPUT, waiter="board", register=True)
    assert d.reason is Reason.TR_INTERVAL
    assert lim.attempt(Priority.P4, TR_PRICE).granted


def simulate(
    lim: Limiter,
    clock: FakeClock,
    callers: dict[str, tuple[Priority, str, float, float]],
    until: float,
) -> dict[str, list[int]]:
    """호출자마다 [시작, 끝) 초 동안 쉬지 않고 요청하는 동시 실행 흉내 (이산 사건).

    같은 시각이면 낮은 등급이 먼저 시도한다(높은 등급에 불리한 순서).
    """
    start = {n: T0 + round(c[2] * US) for n, c in callers.items()}
    stop = {n: T0 + round(min(c[3], until) * US) for n, c in callers.items()}
    next_at = dict(start)
    grants: dict[str, list[int]] = {n: [] for n in callers}
    while True:
        live = [n for n in callers if next_at[n] < stop[n]]
        if not live:
            return grants
        n = min(live, key=lambda k: (next_at[k], -int(callers[k][0])))
        prio, tr, _, _ = callers[n]
        clock.t = next_at[n]
        d = lim.attempt(prio, tr, waiter=n, register=True)
        if d.granted:
            grants[n].append(clock.t)
            continue  # 곧바로 다음 요청
        next_at[n] = clock.t + min(d.wait_us, US)
        if next_at[n] >= stop[n]:
            lim.release(prio, n)  # 그만두는 호출자는 대기열에서 빠진다


def test_lower_priority_starves_first_under_contention(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    g = simulate(
        lim,
        clock,
        {
            "board": (Priority.P1, TR_PRICE, 0.0, 5.0),
            "backfill": (Priority.P4, TR_PRICE, 0.001, 10.0),
        },
        until=10.0,
    )
    board = [t - T0 for t in g["board"]]
    backfill = [t - T0 for t in g["backfill"]]
    assert len(board) == 20  # 0.00, 0.25, ..., 4.75 — P1 이 전부 가져간다
    assert not [t for t in backfill if t < 5 * US]
    assert len(backfill) == 20  # P1 이 그만둔 뒤에는 P4 가 쓴다
    assert max_in_window(g["board"] + g["backfill"]) == 4


def test_three_levels_highest_wins(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    g = simulate(
        lim,
        clock,
        {
            "p4": (Priority.P4, TR_PRICE, 0.0, 4.0),
            "p2": (Priority.P2, TR_INVESTOR, 0.0, 4.0),
            "p0": (Priority.P0, TR_PRICE, 0.0, 4.0),
        },
        until=4.0,
    )
    # 첫 토큰은 아무도 기다리지 않을 때 먼저 온 P4 가 받는다. 그 뒤로는 P0 만
    assert len(g["p4"]) == 1 and g["p4"][0] == T0
    assert g["p2"] == []
    assert len(g["p0"]) == 15


def test_crashed_high_priority_waiter_expires(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_PRICE)
    d = lim.attempt(Priority.P0, TR_PRICE, waiter="dead", register=True)  # 등록 후 죽음
    assert d.reason is Reason.BUCKET and d.wait_us == 250_000
    clock.advance(0.25)
    assert lim.attempt(Priority.P4, TR_PRICE).reason is Reason.PRIORITY
    clock.t = T0 + 250_000 + US  # 임대 = 대기 예정 0.25초 + 여유 1초
    assert lim.attempt(Priority.P4, TR_PRICE).granted


def test_slowdown_and_recovery_schedule(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    assert lim.on_rate_limited() == 2.0
    expected = [(59.999, 2.0), (60, 2.5), (119.9, 2.5), (120, 3.0), (180, 3.5), (240, 4.0)]
    for sec, rate in [*expected, (10_000, 4.0)]:
        clock.t = T0 + round(sec * US)
        assert lim.current_rate() == rate, sec
    # 판정이 쓰는 속도도 같다 (Lua 와 파이썬 식 일치)
    clock.t = T0 + 125 * US
    assert lim.attempt(Priority.P1, TR_PRICE).rate == 3.0


def test_slowdown_spaces_calls_and_pauses_one_interval(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    lim.acquire(Priority.P1, TR_PRICE)
    clock.advance(0.01)
    lim.on_rate_limited()
    times: list[int] = []
    for _ in range(5):
        lim.acquire(Priority.P1, TR_PRICE)
        times.append(clock.t - T0)
    assert times[0] == 10_000 + 500_000  # 감속 신호 뒤 한 간격(0.5초) 쉼
    assert [b - a for a, b in pairwise(times)] == [500_000] * 4


def test_slowdown_floor_and_same_burst(make: MakeLimiter) -> None:
    clock = FakeClock()
    lim = make(clock)
    assert lim.on_rate_limited() == 2.0
    clock.advance(0.5)
    assert lim.on_rate_limited() == 2.0  # 같은 버스트: 더 줄이지 않고 유지 시간만 다시
    clock.t = T0 + 60 * US
    assert lim.current_rate() == 2.0  # 첫 신호 기준이면 2.5 였을 것
    clock.t = T0 + round(60.5 * US)
    assert lim.current_rate() == 2.5
    assert lim.on_rate_limited() == 1.25  # 회복 중 새 사건: 현재 속도의 절반
    clock.advance(2)
    assert lim.on_rate_limited() == 1.0  # 하한
    clock.advance(2)
    assert lim.on_rate_limited() == 1.0


def test_steady_signals_keep_halving_to_floor(make: MakeLimiter) -> None:
    """같은 버스트 판정은 마지막 신호가 아니라 마지막 반감부터 잰다.

    한도초과가 0.5초마다 이어지면(감속이 모자라다) 1초마다 다시 반으로 → 하한 1.0/s.
    마지막 신호 기준이었다면 2.0/s 에 머문다.
    """
    clock = FakeClock()
    lim = make(clock)
    seen: list[float] = []
    for i in range(7):  # 0.0, 0.5, ..., 3.0초
        clock.t = T0 + i * 500_000
        seen.append(lim.on_rate_limited())
    assert seen == [2.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    # 유지 시간은 마지막 신호부터 다시 센다
    clock.t = T0 + 3 * US + 60 * US - 1
    assert lim.current_rate() == 1.0
    clock.t = T0 + 3 * US + 60 * US
    assert lim.current_rate() == 1.5


def test_steady_signals_halve_once_per_debounce_window(make: MakeLimiter) -> None:
    """반감은 1초 창에 한 번 — 8/s 에서 0.5초 간격 신호 30초: 4, 4, 2, 2, 1, 1, ... 하한 유지."""
    clock = FakeClock()
    lim = make(clock, RateLimitConfig(rate=8.0))
    seen: list[float] = []
    for i in range(60):
        clock.t = T0 + i * 500_000
        seen.append(lim.on_rate_limited())
    assert seen[:6] == [4.0, 4.0, 2.0, 2.0, 1.0, 1.0]
    assert set(seen[4:]) == {1.0}


def test_simultaneous_signals_halve_once(make: MakeLimiter) -> None:
    """같은 시각에 돌아온 한도초과 응답 여럿은 한 번만 반으로."""
    clock = FakeClock()
    lim = make(clock)
    assert [lim.on_rate_limited() for _ in range(3)] == [2.0, 2.0, 2.0]
    clock.advance(0.999_999)
    assert lim.on_rate_limited() == 2.0
    clock.advance(0.000_001)  # 반감부터 1초
    assert lim.on_rate_limited() == 1.0


def test_recovery_from_floor_fits_state_ttl() -> None:
    c = RateLimitConfig()
    assert c.recovery_s() == 360  # 1.0 → 4.0: 유지 60초 + 5단계 × 60초
    assert effective_rate(c, 1.0, 0, 360 * US) == 4.0
    assert effective_rate(c, 1.0, 0, 360 * US - 1) == 3.5
    assert c.state_ttl_s > c.recovery_s()


def test_redis_state_is_shared_between_instances() -> None:
    server = fakeredis.FakeServer()
    clock = FakeClock()
    a = RedisRateLimiter(fakeredis.FakeRedis(server=server), APP_KEY, clock=clock)
    b = RedisRateLimiter(fakeredis.FakeRedis(server=server), APP_KEY, clock=clock)
    other = RedisRateLimiter(fakeredis.FakeRedis(server=server), "another-key", clock=clock)
    assert a.attempt(Priority.P1, TR_DISPLAY_BOARD_CALLPUT).granted
    assert b.attempt(Priority.P1, TR_PRICE).reason is Reason.BUCKET
    assert other.attempt(Priority.P1, TR_PRICE).granted  # 앱키가 다르면 버킷도 다르다
    clock.advance(0.25)
    assert b.attempt(Priority.P1, TR_DISPLAY_BOARD_CALLPUT).reason is Reason.TR_INTERVAL
    # a 가 기다리는 P0 로 등록되면 b 의 P3 는 막힌다
    assert not a.attempt(Priority.P0, TR_DISPLAY_BOARD_CALLPUT, waiter="x", register=True).granted
    assert b.attempt(Priority.P1, TR_PRICE).granted  # P0 은 TR 간격 대기라 등록되지 않는다
    assert a.attempt(Priority.P0, TR_PRICE, waiter="y", register=True).reason is Reason.BUCKET
    clock.advance(0.25)
    assert b.attempt(Priority.P3, TR_PRICE).reason is Reason.PRIORITY
    # 감속도 공유
    a.on_rate_limited()
    assert b.current_rate() == 2.0
    assert other.current_rate() == 4.0


def test_redis_keys_hide_app_key() -> None:
    r = fakeredis.FakeRedis()
    clock = FakeClock()
    lim = RedisRateLimiter(r, APP_KEY, clock=clock)
    lim.acquire(Priority.P1, TR_DISPLAY_BOARD_CALLPUT)
    lim.attempt(Priority.P1, TR_PRICE, waiter="w", register=True)
    lim.on_rate_limited()
    keys = [k.decode() if isinstance(k, bytes) else str(k) for k in r.keys("*")]
    assert keys and all(k.startswith(limiter_key(APP_KEY)) for k in keys)
    assert limiter_key(APP_KEY).startswith("rl:kis:")
    dump = repr({k: r.type(k) for k in keys}) + repr(r.hgetall(lim.key))
    assert APP_KEY not in dump and APP_KEY not in lim.key
    assert 0 < r.pttl(lim.key) <= 900_000


def test_redis_down_raises_instead_of_calling() -> None:
    """설계 §2: 레이트리미터를 못 쓰면 KIS 호출을 멈춘다."""

    class Down(fakeredis.FakeRedis):
        def evalsha(self, *a: object, **k: object) -> object:  # type: ignore[override]
            raise ConnectionError("redis down")

    lim = RedisRateLimiter(Down(), APP_KEY, clock=FakeClock())
    with pytest.raises(ConnectionError):
        lim.acquire(Priority.P0, TR_PRICE)


@pytest.mark.parametrize(
    "kw",
    [
        {"floor_rate": 5.0},
        {"capacity": 0},
        {"state_ttl_s": 100.0},
        {"poll_s": 0.0},
        {"tr_min_interval_s": {TR_PRICE: -1.0}},
    ],
)
def test_config_validation(kw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RateLimitConfig(**kw)  # type: ignore[arg-type]


def test_local_limiter_is_thread_safe() -> None:
    """실제 시계·스레드 4개: 20건을 100/s 로 — 19 간격 이상 걸린다."""
    lim = LocalRateLimiter(RateLimitConfig(rate=100.0, floor_rate=50.0, step_rate=50.0))
    t0 = time.monotonic()

    def worker() -> None:
        for _ in range(5):
            lim.acquire(Priority.P2, TR_PRICE, timeout=5)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert time.monotonic() - t0 >= 19 * 0.01 - 0.002
