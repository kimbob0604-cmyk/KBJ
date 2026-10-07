"""레이트리미터 속성: 임의 호출 순서·등급·감속 신호에서도 반열린 1초 창에 허가 ≤ 4건."""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

import fakeredis
from hypothesis import given, settings
from hypothesis import strategies as st

from data.kis.ratelimit import (
    TR_DISPLAY_BOARD_CALLPUT,
    US,
    LocalRateLimiter,
    Priority,
    RateLimitTimeout,
    RedisRateLimiter,
)

T0 = 1_790_553_600 * US
TRS = [TR_DISPLAY_BOARD_CALLPUT, "FHMIF10000000", "FHPTJ04030000"]
Op = Literal["attempt", "register", "acquire", "acquire_short", "acquire_zero", "rate_limited"]
OPS: list[Op] = ["attempt", "register", "acquire", "acquire_short", "acquire_zero", "rate_limited"]

op_lists = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=700_000),  # 앞 연산과의 간격(µs)
        st.sampled_from(list(Priority)),
        st.sampled_from(TRS),
        st.sampled_from(OPS),
        st.integers(min_value=0, max_value=3),  # 어느 인스턴스(프로세스)에서
    ),
    max_size=40,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = T0

    def now_us(self) -> int:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += round(seconds * US)


Limiter = RedisRateLimiter | LocalRateLimiter


def run(
    lims: list[Limiter], clock: FakeClock, ops: list[tuple[int, Priority, str, Op, int]]
) -> tuple[list[int], list[int], list[tuple[object, ...]]]:
    grants: list[int] = []
    callput: list[int] = []
    trace: list[tuple[object, ...]] = []
    for n, (gap, prio, tr, op, who) in enumerate(ops):
        clock.t += gap
        lim = lims[who % len(lims)]
        ok = False
        if op in ("attempt", "register"):
            d = lim.attempt(prio, tr, waiter=f"w{n}", register=op == "register")
            ok = d.granted
            trace.append((d.granted, d.wait_us, d.reason, d.rate))
        elif op == "rate_limited":
            trace.append(("slow", lim.on_rate_limited()))
        else:
            timeout = {"acquire": None, "acquire_short": 0.3, "acquire_zero": 0.0}[op]
            try:
                lim.acquire(prio, tr, timeout=timeout)
                ok = True
            except RateLimitTimeout as e:
                trace.append(("timeout", e.reason))
            trace.append(("clock", clock.t))
        if ok:
            grants.append(clock.t)
            if tr == TR_DISPLAY_BOARD_CALLPUT:
                callput.append(clock.t)
    return grants, callput, trace


def max_in_window(times: list[int]) -> int:
    ts = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(ts):
        while ts[j] <= t - US:
            j += 1
        best = max(best, i - j + 1)
    return best


def check(grants: list[int], callput: list[int]) -> None:
    assert grants == sorted(grants)  # 가짜 시계는 되돌지 않는다
    assert max_in_window(grants) <= 4
    assert all(b - a >= 250_000 for a, b in pairwise(grants))  # 버킷 1: 간격 ≥ 1/속도
    assert all(b - a >= US for a, b in pairwise(callput))  # 전광판 1초 1건


@settings(max_examples=150, deadline=None)
@given(op_lists)
def test_local_never_exceeds_four_per_second(ops: list[tuple[int, Priority, str, Op, int]]) -> None:
    clock = FakeClock()
    grants, callput, _ = run([LocalRateLimiter(clock=clock)], clock, ops)
    check(grants, callput)


@settings(max_examples=150, deadline=None)
@given(op_lists)
def test_shared_redis_never_exceeds_four_per_second(
    ops: list[tuple[int, Priority, str, Op, int]],
) -> None:
    """네 프로세스가 한 Redis 를 나눠 쓴다."""
    server = fakeredis.FakeServer()
    clock = FakeClock()
    lims: list[Limiter] = [
        RedisRateLimiter(fakeredis.FakeRedis(server=server), "APPKEY", clock=clock)
        for _ in range(4)
    ]
    grants, callput, _ = run(lims, clock, ops)
    check(grants, callput)


@settings(max_examples=100, deadline=None)
@given(op_lists)
def test_redis_and_local_decide_identically(ops: list[tuple[int, Priority, str, Op, int]]) -> None:
    """Lua 와 파이썬 구현이 같은 규칙인지: 같은 연산열에 같은 판정·같은 시각."""
    c1, c2 = FakeClock(), FakeClock()
    redis_lim = RedisRateLimiter(fakeredis.FakeRedis(), "APPKEY", clock=c1)
    r = run([redis_lim], c1, ops)
    loc = run([LocalRateLimiter(clock=c2)], c2, ops)
    assert r == loc
