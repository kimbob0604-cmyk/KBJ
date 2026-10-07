"""출처별 전역 레이트리미터 — GEXLAB `data/kis/ratelimit.py` 승격(docs/p2_design.md §1.1·§4).

GX 설계(GX docs/phase1_design.md §3, 2026-09-28 문서 검토 수정 반영)를 그대로 옮기고, KIS 앱키
말고도 출처(KRX·DART·공공데이터포털·KOSIS·ECOS·텔레그램)마다 버킷을 두는 `RedisRateLimiter.scoped`
를 더했다.

- KIS 는 앱키당 하나(`limiter_key(앱키)` = `rl:kis:<해시>` — GX 와 같은 키라 전환 기간 legacy GX 와
  버킷을 나눠 쓴다). 다른 출처는 `scoped_key(출처, 범위)` = `rl:<출처>:<해시>`(범위 = 그 출처의 키·
  데이터셋 등 — 해시만 남는다). 키 이름은 kbj/store/redis_keys.py 한 곳에서 만든다.
- 출처별 속도·감속 값은 config/limits.yaml(kbj/data/limits.py `SourceLimits.rate_config`).
- 모든 프로세스가 Redis 의 같은 키를 Lua 로 원자 갱신한다(`RedisRateLimiter`).
  Redis 없이 한 프로세스 안에서만 쓰는 `LocalRateLimiter` 는 같은 규칙을 파이썬으로 한다.

규칙
- 속도 4.0건/초, 버킷 1 → 허가 간격 ≥ 0.25초, 어떤 반열린 1초 창에도 허가 ≤ 4건.
  버킷 4면 1초에 7~8건이 나가 실측 무오류 상한 5/s(#10)를 넘는다
- 우선순위 P0(가장 높음)~P4. 더 높은 등급이 버킷을 기다리는 중이면 낮은 등급은 허가받지 못한다.
  기다리는 호출은 대기열(zset)에 임대 기한과 함께 등록되고, 허가·포기 때 빠진다. 죽은 프로세스의
  등록은 기한(대기 예정 + 1초)이 지나면 저절로 빠진다. 같은 등급끼리는 먼저 시도한 쪽이 가져간다
- TR별 최소 간격: 전광판 콜/풋(FHPIF05030100) 1.0초. TR 간격을 기다리는 호출은 대기열에 넣지 않는다
  (어차피 토큰을 못 쓰므로 낮은 등급을 막지 않는다)
- 한도초과(EGW00201) `on_rate_limited()`: 현재 속도를 반으로(최저 1.0/s) 60초 유지, 그 뒤 60초마다
  +0.5/s 씩 기본 속도까지 회복(4→2, 60초 뒤 2.5, 120초 3.0, 180초 3.5, 240초 4.0). 직전 반감
  (`halved_at`) 1초 안에 온 신호는 같은 버스트로 보고 반으로 더 줄이지 않고 유지 시간만 다시 센다.
  1초는 마지막 신호가 아니라 마지막 반감부터 잰다 — 신호가 0.5초마다 이어져도(감속이 모자라다)
  1초마다 다시 반으로 줄어 하한에 닿는다. 감속 직후 한 간격은 쉬게 한다
- 시각(에포크 마이크로초 정수)은 호출자가 넘긴다(Redis TIME 을 쓰지 않는다) → 가짜 시계로 결정적
  테스트. 대신 모든 프로세스가 같은 호스트 시계를 쓴다는 전제다(서비스는 한 VM 의 컨테이너)
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol

from redis import Redis

from kbj.store.redis_keys import rate_limit_key, rate_limit_wait_key

US = 1_000_000
TR_DISPLAY_BOARD_CALLPUT = "FHPIF05030100"  # 전광판 콜/풋 — 권장 1초 1건 (PLAN §2.5)


class Priority(IntEnum):
    """숫자가 작을수록 높다 (설계 §3). '모자랄 때' 처리는 호출자가 timeout 으로 정한다."""

    P0 = 0  # 웹소켓 재연결 뒤 복구 스냅샷, 월물리스트 — 기다린다
    P1 = 1  # 전광판 콜/풋, 선물 전광판·기초자산 — 기다린다
    P2 = 2  # 투자자별 — 다음 주기로
    P3 = 3  # 보강 1 (월물 ATM±20) — 다음 주기로, 누락은 quality=stale
    P4 = 4  # 보강 2, 분봉 적재·백필 — 건너뛴다


class Reason(IntEnum):
    GRANTED = 0
    BUCKET = 1  # 전역 토큰이 아직 없다
    PRIORITY = 2  # 더 높은 등급이 기다리는 중
    TR_INTERVAL = 3  # 같은 TR 최소 간격


@dataclass(frozen=True)
class Decision:
    granted: bool
    wait_us: int  # 다시 시도할 때까지 (허가면 0)
    reason: Reason
    rate: float  # 판정 시점의 실효 속도(건/초)


class RateLimitTimeout(TimeoutError):
    def __init__(self, priority: Priority, tr_id: str, reason: Reason) -> None:
        super().__init__(f"레이트리미터 대기 초과: {priority.name} {tr_id} ({reason.name})")
        self.priority = priority
        self.tr_id = tr_id
        self.reason = reason


class Clock(Protocol):
    def now_us(self) -> int:
        """에포크 마이크로초. 같은 Redis 를 쓰는 프로세스는 같은 호스트 시계여야 한다."""
        ...

    def sleep(self, seconds: float, /) -> None: ...


class SystemClock:
    def now_us(self) -> int:
        return time.time_ns() // 1000

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _default_tr_intervals() -> dict[str, float]:
    return {TR_DISPLAY_BOARD_CALLPUT: 1.0}


@dataclass(frozen=True)
class RateLimitConfig:
    rate: float = 4.0  # 기본 속도(건/초). 실측 무오류 5/s 에서 20% 여유 (#10)
    capacity: int = 1  # 버킷. 1 이면 반열린 1초 창에 ceil(rate) 건 이하
    floor_rate: float = 1.0  # 감속 하한
    hold_s: float = 60.0  # 감속 유지
    step_rate: float = 0.5  # 회복 단계
    step_s: float = 60.0  # 회복 간격
    debounce_s: float = 1.0  # 직전 반감부터 이 시간 안의 한도초과 신호는 같은 버스트
    tr_min_interval_s: Mapping[str, float] = field(default_factory=_default_tr_intervals)
    lease_margin_s: float = 1.0  # 대기 등록 임대 = 대기 예정 + 이 값
    poll_s: float = 0.01  # 높은 등급에 막혔을 때 다시 볼 간격
    max_sleep_s: float = 1.0  # 한 번에 자는 최대 시간 (임대 갱신 주기)
    state_ttl_s: float = 900.0  # Redis 상태 키 수명 (마지막 쓰기부터)

    def __post_init__(self) -> None:
        if not (0 < self.floor_rate <= self.rate):
            raise ValueError("0 < floor_rate <= rate 이어야 한다")
        if self.capacity < 1:
            raise ValueError("capacity 는 1 이상")
        if self.step_rate <= 0 or self.step_s <= 0 or self.hold_s < 0:
            raise ValueError("회복 단계·간격은 양수")
        if min(self.lease_margin_s, self.poll_s, self.max_sleep_s) <= 0:
            raise ValueError("lease_margin_s·poll_s·max_sleep_s 는 양수")
        if any(v < 0 for v in self.tr_min_interval_s.values()):
            raise ValueError("TR 최소 간격은 0 이상")
        if self.state_ttl_s <= self.recovery_s() + max(self.tr_min_interval_s.values(), default=0):
            raise ValueError("state_ttl_s 가 감속 회복 시간보다 짧다")

    def recovery_s(self) -> float:
        """최저 속도에서 기본 속도까지 돌아오는 시간."""
        steps = math.ceil((self.rate - self.floor_rate) / self.step_rate)
        return self.hold_s + self.step_s * max(steps - 1, 0)

    def tr_interval_us(self, tr_id: str) -> int:
        return round(self.tr_min_interval_s.get(tr_id, 0.0) * US)


def interval_us(rate: float) -> int:
    """허가 간격(마이크로초, 올림). Lua 쪽 `math.ceil(1000000 / rate)` 와 같은 값."""
    return math.ceil(US / rate)


def effective_rate(
    cfg: RateLimitConfig, slow_rate: float | None, slow_since_us: int | None, now_us: int
) -> float:
    """감속·회복을 반영한 현재 속도. Lua `_RATE_LUA` 와 같은 식."""
    if slow_rate is None:
        return cfg.rate
    elapsed = now_us - (now_us if slow_since_us is None else slow_since_us)
    hold, step = round(cfg.hold_s * US), round(cfg.step_s * US)
    if elapsed < hold:
        return slow_rate
    return min(cfg.rate, slow_rate + cfg.step_rate * (1 + (elapsed - hold) // step))


def limiter_key(app_key: str) -> str:
    """`rl:kis:<앱키 해시>` — 앱키 원문은 Redis 에 남기지 않는다(GX 와 같은 값)."""
    return scoped_key("kis", app_key)


def scoped_key(source: str, scope: str) -> str:
    """`rl:<출처>:<sha256(scope) 16자>` — 같은 출처·같은 범위면 프로세스가 달라도 같은 버킷."""
    return rate_limit_key(source, scope)


class RateLimiter(Protocol):
    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        """허가를 받을 때까지 기다린다. timeout 초 안에 못 받으면 `RateLimitTimeout`.

        timeout=None 은 무기한, 0 은 한 번만 시도(대기열에 등록하지 않음).
        """
        ...

    def on_rate_limited(self) -> float:
        """한도초과 응답을 알린다. 새 실효 속도를 돌려준다."""
        ...


class _PollingLimiter(ABC):
    """대기 루프 공통부. 한 번의 판정(`attempt`)은 구현체가 원자적으로 한다."""

    def __init__(self, config: RateLimitConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock

    @abstractmethod
    def attempt(
        self, priority: Priority, tr_id: str, *, waiter: str = "", register: bool = False
    ) -> Decision:
        """지금 시각으로 한 번 판정한다. register=True 면 거절될 때 대기열에 등록(갱신)한다."""

    @abstractmethod
    def release(self, priority: Priority, waiter: str) -> None:
        """대기열 등록을 뺀다 (포기·예외 때)."""

    @abstractmethod
    def on_rate_limited(self) -> float: ...

    @abstractmethod
    def current_rate(self) -> float: ...

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        cfg = self.config
        waiter = uuid.uuid4().hex
        deadline = None if timeout is None else self.clock.now_us() + round(timeout * US)
        registered = False
        try:
            while True:
                remaining = None if deadline is None else deadline - self.clock.now_us()
                may_wait = remaining is None or remaining > 0
                d = self.attempt(priority, tr_id, waiter=waiter, register=may_wait)
                if d.granted:
                    registered = False  # 허가 때 대기열에서 빠졌다
                    return
                registered = registered or (
                    may_wait and d.reason in (Reason.BUCKET, Reason.PRIORITY)
                )
                sleep_us = min(d.wait_us, round(cfg.max_sleep_s * US))
                if remaining is not None:
                    # 버킷·TR 대기 시간은 하한이라 기한 안에 못 받는 게 확실하면 바로 포기
                    hopeless = d.reason != Reason.PRIORITY and d.wait_us > remaining
                    if not may_wait or hopeless:
                        raise RateLimitTimeout(priority, tr_id, d.reason)
                    sleep_us = min(sleep_us, remaining)
                self.clock.sleep(sleep_us / US)
        finally:
            if registered:
                self.release(priority, waiter)


# ---- Redis ----------------------------------------------------------------------------------

_RATE_LUA = """
local function istr(x) return string.format('%d', x) end
local function eff_rate(sr, since, now, base, hold, step_rate, step)
  if not sr then return base end
  local el = now - (since or now)
  if el < hold then return sr end
  return math.min(base, sr + step_rate * (1 + math.floor((el - hold) / step)))
end
"""

# KEYS[1] 상태 해시(tat·slow_rate·slow_since(유지 시작)·halved_at(마지막 반감)·tr:<TR>)
# KEYS[2] 대기열 zset(멤버 '<등급>|<id>', 점수 = 임대 기한)
_ACQUIRE_LUA = (
    _RATE_LUA
    + """
local now = tonumber(ARGV[1])
local prio = tonumber(ARGV[2])
local me = ARGV[2] .. '|' .. ARGV[3]
local register = ARGV[4] == '1'
local trf = 'tr:' .. ARGV[5]
local tr_iv = tonumber(ARGV[6])
local base = tonumber(ARGV[7])
local cap = tonumber(ARGV[8])
local hold = tonumber(ARGV[9])
local step_rate = tonumber(ARGV[10])
local step = tonumber(ARGV[11])
local lease = tonumber(ARGV[12])
local poll = tonumber(ARGV[13])
local ttl = tonumber(ARGV[14])

local st = redis.call('HMGET', KEYS[1], 'tat', 'slow_rate', 'slow_since', trf)
local tat = tonumber(st[1]) or 0
local rate = eff_rate(tonumber(st[2]), tonumber(st[3]), now, base, hold, step_rate, step)
local iv = math.ceil(1000000 / rate)

redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', istr(now))

local tr_next = tonumber(st[4])
if tr_next and now < tr_next then
  redis.call('ZREM', KEYS[2], me)
  return {3, istr(tr_next - now), tostring(rate)}
end

local wait = tat - (cap - 1) * iv - now
local higher = false
for _, m in ipairs(redis.call('ZRANGE', KEYS[2], 0, -1)) do
  local p = tonumber(string.match(m, '^(%d+)|'))
  if p and p < prio then
    higher = true
    break
  end
end
if higher or wait > 0 then
  local code = 1
  if higher then
    code = 2
    if wait < poll then wait = poll end
  end
  if register then
    redis.call('ZADD', KEYS[2], istr(now + wait + lease), me)
    redis.call('PEXPIRE', KEYS[2], ttl)
  end
  return {code, istr(wait), tostring(rate)}
end

if tat < now then tat = now end
redis.call('HSET', KEYS[1], 'tat', istr(tat + iv))
if tr_iv > 0 then redis.call('HSET', KEYS[1], trf, istr(now + tr_iv)) end
redis.call('PEXPIRE', KEYS[1], ttl)
redis.call('ZREM', KEYS[2], me)
return {0, '0', tostring(rate)}
"""
)

_SLOWDOWN_LUA = (
    _RATE_LUA
    + """
local now = tonumber(ARGV[1])
local base = tonumber(ARGV[2])
local floor_rate = tonumber(ARGV[3])
local hold = tonumber(ARGV[4])
local step_rate = tonumber(ARGV[5])
local step = tonumber(ARGV[6])
local debounce = tonumber(ARGV[7])
local cap = tonumber(ARGV[8])
local ttl = tonumber(ARGV[9])

local st = redis.call('HMGET', KEYS[1], 'tat', 'slow_rate', 'slow_since', 'halved_at')
local sr, since, halved = tonumber(st[2]), tonumber(st[3]), tonumber(st[4])
local rate = eff_rate(sr, since, now, base, hold, step_rate, step)
local new
if sr and halved and now >= halved and now - halved < debounce then
  new = sr  -- 같은 버스트: 유지 시간만 다시 (halved_at 은 그대로)
else
  new = math.max(floor_rate, rate / 2)
  halved = now
end
local iv = math.ceil(1000000 / new)
local tat = tonumber(st[1]) or 0
if tat < now + cap * iv then tat = now + cap * iv end
local s = string.format('%.17g', new)
redis.call('HSET', KEYS[1], 'slow_rate', s, 'slow_since', istr(now), 'halved_at', istr(halved),
  'tat', istr(tat))
redis.call('PEXPIRE', KEYS[1], ttl)
return s
"""
)


class RedisRateLimiter(_PollingLimiter):
    """여러 프로세스가 공유하는 전역 리미터. Redis 를 못 쓰면 예외가 그대로 올라간다
    (설계 §2: 레이트리미터를 못 쓰면 KIS 호출을 멈춘다 — 한도 초과보다 결측이 낫다).

    `RedisRateLimiter(redis, 앱키)` 는 KIS 앱키 버킷(GX 그대로). 다른 출처는 `scoped` 로 만든다.
    """

    def __init__(
        self,
        redis: Redis,
        app_key: str,
        config: RateLimitConfig | None = None,
        clock: Clock | None = None,
        *,
        source: str = "kis",
    ) -> None:
        super().__init__(config or RateLimitConfig(), clock or SystemClock())
        self.source = source
        self.key = scoped_key(source, app_key)
        self._keys = [self.key, rate_limit_wait_key(self.key)]
        self._r = redis
        self._acquire = redis.register_script(_ACQUIRE_LUA)
        self._slowdown = redis.register_script(_SLOWDOWN_LUA)

    @classmethod
    def scoped(
        cls,
        redis: Redis,
        source: str,
        scope: str,
        config: RateLimitConfig | None = None,
        clock: Clock | None = None,
    ) -> RedisRateLimiter:
        """출처·범위별 리미터(`rl:<출처>:<해시>`).

        `scoped(r, "kis", 앱키)` 는 앱키 리미터와 같은 버킷이다. scope 는 그 출처의 키(원문을
        넣어도 해시만 남는다)나 데이터셋 id 다 — 같은 scope 면 프로세스가 달라도 같은 버킷을
        쓴다. config 를 안 주면 config/limits.yaml 의 그 출처 값(`kbj.data.limits`)을 쓴다 —
        KIS 기본값(4/s)을 다른 출처에 잘못 쓰지 않게.
        """
        if config is None:
            from kbj.data.limits import load_limits  # limits → ratelimit 순환을 피해 여기서

            config = load_limits().source(source).rate_config()
        return cls(redis, scope, config, clock, source=source)

    def attempt(
        self, priority: Priority, tr_id: str, *, waiter: str = "", register: bool = False
    ) -> Decision:
        c = self.config
        res: Any = self._acquire(
            keys=self._keys,
            args=[
                self.clock.now_us(),
                int(priority),
                waiter,
                1 if register else 0,
                tr_id,
                c.tr_interval_us(tr_id),
                repr(c.rate),
                c.capacity,
                round(c.hold_s * US),
                repr(c.step_rate),
                round(c.step_s * US),
                round(c.lease_margin_s * US),
                round(c.poll_s * US),
                round(c.state_ttl_s * 1000),
            ],
        )
        code, wait, rate = res
        return Decision(int(code) == 0, int(wait), Reason(int(code)), float(rate))

    def release(self, priority: Priority, waiter: str) -> None:
        self._r.zrem(self._keys[1], f"{int(priority)}|{waiter}")

    def on_rate_limited(self) -> float:
        c = self.config
        res: Any = self._slowdown(
            keys=self._keys[:1],
            args=[
                self.clock.now_us(),
                repr(c.rate),
                repr(c.floor_rate),
                round(c.hold_s * US),
                repr(c.step_rate),
                round(c.step_s * US),
                round(c.debounce_s * US),
                c.capacity,
                round(c.state_ttl_s * 1000),
            ],
        )
        return float(res)

    def current_rate(self) -> float:
        raw: Any = self._r.hmget(self.key, ["slow_rate", "slow_since"])
        sr, since = raw
        return effective_rate(
            self.config,
            None if sr is None else float(sr),
            None if since is None else int(since),
            self.clock.now_us(),
        )


# ---- 프로세스 안 ------------------------------------------------------------------------------


@dataclass
class _LocalState:
    tat: int = 0
    slow_rate: float | None = None
    slow_since: int | None = None  # 감속 유지 시작 (마지막 신호)
    halved_at: int | None = None  # 마지막 반감 (같은 버스트 판정 기준)
    tr_next: dict[str, int] = field(default_factory=dict[str, int])
    waiters: dict[str, tuple[int, int]] = field(default_factory=dict[str, tuple[int, int]])


class LocalRateLimiter(_PollingLimiter):
    """Redis 없이 한 프로세스 안에서만 쓰는 리미터 (probe·테스트). 규칙은 Redis 판과 같다."""

    def __init__(self, config: RateLimitConfig | None = None, clock: Clock | None = None) -> None:
        super().__init__(config or RateLimitConfig(), clock or SystemClock())
        self._lock = threading.Lock()
        self._s = _LocalState()

    def attempt(
        self, priority: Priority, tr_id: str, *, waiter: str = "", register: bool = False
    ) -> Decision:
        c = self.config
        prio = int(priority)
        me = f"{prio}|{waiter}"
        with self._lock:
            s = self._s
            now = self.clock.now_us()
            rate = effective_rate(c, s.slow_rate, s.slow_since, now)
            iv = interval_us(rate)
            s.waiters = {m: v for m, v in s.waiters.items() if v[1] > now}
            tr_next = s.tr_next.get(tr_id)
            if tr_next is not None and now < tr_next:
                s.waiters.pop(me, None)
                return Decision(False, tr_next - now, Reason.TR_INTERVAL, rate)
            wait = s.tat - (c.capacity - 1) * iv - now
            higher = any(p < prio for p, _ in s.waiters.values())
            if higher or wait > 0:
                reason = Reason.BUCKET
                if higher:
                    reason = Reason.PRIORITY
                    wait = max(wait, round(c.poll_s * US))
                if register:
                    s.waiters[me] = (prio, now + wait + round(c.lease_margin_s * US))
                return Decision(False, wait, reason, rate)
            s.tat = max(s.tat, now) + iv
            tr_iv = c.tr_interval_us(tr_id)
            if tr_iv > 0:
                s.tr_next[tr_id] = now + tr_iv
            s.waiters.pop(me, None)
            return Decision(True, 0, Reason.GRANTED, rate)

    def release(self, priority: Priority, waiter: str) -> None:
        with self._lock:
            self._s.waiters.pop(f"{int(priority)}|{waiter}", None)

    def on_rate_limited(self) -> float:
        c = self.config
        with self._lock:
            s = self._s
            now = self.clock.now_us()
            rate = effective_rate(c, s.slow_rate, s.slow_since, now)
            if (
                s.slow_rate is not None
                and s.halved_at is not None
                and 0 <= now - s.halved_at < round(c.debounce_s * US)
            ):
                new = s.slow_rate  # 같은 버스트: 더 줄이지 않고 유지 시간만 다시 센다
            else:
                new = max(c.floor_rate, rate / 2)
                s.halved_at = now
            s.slow_rate, s.slow_since = new, now
            s.tat = max(s.tat, now + c.capacity * interval_us(new))
            return new

    def current_rate(self) -> float:
        with self._lock:
            return effective_rate(
                self.config, self._s.slow_rate, self._s.slow_since, self.clock.now_us()
            )
