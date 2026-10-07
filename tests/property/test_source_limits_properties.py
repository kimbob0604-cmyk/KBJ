"""출처별 리미터 속성: config/limits.yaml 의 값마다, 임의 요청 열에서도 어떤 반열린 1초 창에도
허가 ≤ ceil(rate) 건, 허가 간격 ≥ 1/rate(설계 §4.4 — data.go.kr·KOSIS·DART·ECOS·KRX·텔레그램).

GX 속성 시험(tests/property/test_ratelimit_properties.py)은 KIS 4/s 하나만 본다 — 여기서는 출처별
설정(감속 하한·유지 시간이 다름)을 같은 방식으로 돌린다. 여러 프로세스가 한 Redis 를 나눠 쓴다.
"""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import fakeredis
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.data.limits import parse_limits
from kbj.data.ratelimit import (
    US,
    LocalRateLimiter,
    Priority,
    RateLimitTimeout,
    RedisRateLimiter,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_791_244_800 * US  # 2026-10-06 무렵(에포크 µs)
# 레포의 config/limits.yaml — 바깥 KBJ_* 환경(운영 VM 의 KBJ_CONFIG_DIR 등)·.env 와 무관하게
LIMITS = parse_limits(yaml.safe_load((ROOT / "config" / "limits.yaml").read_text(encoding="utf-8")))
Op = tuple[int, Priority, str, int]
SOURCES = sorted(s for s in LIMITS.sources if s != "kis")  # KIS 는 GX 속성 시험이 본다

ops = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=400_000),  # 앞 연산과의 간격(µs)
        st.sampled_from(list(Priority)),
        st.sampled_from(["attempt", "acquire", "acquire_short", "rate_limited"]),
        st.integers(min_value=0, max_value=2),  # 어느 프로세스에서
    ),
    max_size=40,
)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now_us(self) -> int:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += round(seconds * US)


def max_in_window(times: list[int]) -> int:
    ts = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(ts):
        while ts[j] <= t - US:
            j += 1
        best = max(best, i - j + 1)
    return best


def run(
    lims: list[RedisRateLimiter] | list[LocalRateLimiter], clock: Clock, seq: list[Op]
) -> list[int]:
    grants: list[int] = []
    for gap, prio, op, who in seq:
        clock.t += gap
        lim: RedisRateLimiter | LocalRateLimiter = lims[who % len(lims)]
        if op == "attempt":
            if lim.attempt(prio, "ds").granted:
                grants.append(clock.t)
        elif op == "rate_limited":
            lim.on_rate_limited()
        else:
            try:
                lim.acquire(prio, "ds", timeout=None if op == "acquire" else 0.3)
            except RateLimitTimeout:
                continue
            grants.append(clock.t)
    return grants


@settings(max_examples=40, deadline=None)
@given(st.sampled_from(SOURCES), ops)
def test_every_source_config_holds_its_rate(source: str, seq: list[Op]) -> None:
    cfg = LIMITS.source(source).rate_config()
    server = fakeredis.FakeServer()
    clock = Clock()
    lims = [
        RedisRateLimiter.scoped(fakeredis.FakeRedis(server=server), source, "scope", cfg, clock)
        for _ in range(3)
    ]
    grants = run(lims, clock, seq)
    assert grants == sorted(grants)
    assert max_in_window(grants) <= math.ceil(cfg.rate)
    gap_min = math.ceil(US / cfg.rate)
    assert all(b - a >= gap_min for a, b in pairwise(grants))


@settings(max_examples=40, deadline=None)
@given(st.sampled_from(SOURCES), ops)
def test_local_and_redis_agree_for_every_source(source: str, seq: list[Op]) -> None:
    cfg = LIMITS.source(source).rate_config()
    c1, c2 = Clock(), Clock()
    redis_grants = run(
        [RedisRateLimiter.scoped(fakeredis.FakeRedis(), source, "s", cfg, c1)], c1, seq
    )
    local_grants = run([LocalRateLimiter(cfg, c2)], c2, seq)
    assert redis_grants == local_grants
