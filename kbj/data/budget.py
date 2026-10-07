"""출처별 하루 호출 예산 — GEXLAB `services/scheduler/krx.py:KrxCallBudget` 일반화(설계 §1.1·§4.1).

- KST 날짜별 카운터를 Redis 에 둔다(`krx:calls:<YYYYMMDD>`·`budget:<출처>[:<범위>]:<YYYYMMDD>`, TTL
  3일) — 재기동해도, 프로세스가 여럿이어도 그날 상한이 이어진다. 부르기 **전에** `take` 로 한 번
  몫을 가져간다(실패한 호출도 센다). 상한이면 `BudgetExhausted` 를 올리고 세지 않는다.
- `take` 는 Lua 한 번으로 '읽고 비교하고 올린다' — 두 프로세스가 마지막 한 몫을 함께 가져가지 않는다
  (GX 는 GET 뒤 INCR 이라 동시에 부르면 1건 넘을 수 있었다).
- Redis 가 안 되면 이 프로세스 셈만 쓴다(GX 그대로) — 쓴 수는 둘 중 큰 값. Redis 장애 알림은 한
  번만.
- **신규** `exhaust(at, reason)`: 출처가 '오늘 한도 초과'를 알려 오면(공공데이터포털 GW `22`, DART
  `020`) 그날 닫는다. 닫힌 날의 `take` 는 사유를 담아 `BudgetExhausted`. 사유는 가린 뒤 저장한다.
- 이 모듈은 kbj.services 를 모른다(계약 ⑥) — 로그는 표준 logging 에 JSON 한 줄로 남긴다.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Final

from redis import Redis
from redis.exceptions import RedisError

from kbj.core.masking import safe_snippet
from kbj.core.time import KST
from kbj.store.redis_keys import budget_closed_key, budget_key, krx_calls_key

TTL_S: Final = 3 * 86400
REASON_MAX: Final = 200

log = logging.getLogger(__name__)

# KEYS[1] 카운터, KEYS[2] 닫힘 사유. ARGV: 상한, 이 프로세스 셈(하한), TTL(초)
# 반환 {코드, 값}: 0 = 받음(값 = 이번 포함 쓴 수), 1 = 상한(값 = 쓴 수), 2 = 닫힘(값 = 사유)
_TAKE_LUA = """
local closed = redis.call('GET', KEYS[2])
if closed then return {2, closed} end
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local floor = tonumber(ARGV[2])
if floor > used then used = floor end
if used >= tonumber(ARGV[1]) then return {1, tostring(used)} end
used = used + 1
redis.call('SET', KEYS[1], string.format('%d', used), 'EX', tonumber(ARGV[3]))
return {0, tostring(used)}
"""


class BudgetExhausted(RuntimeError):
    """그날 상한·닫힘 — 부르지 않았다. 문구에 키·토큰이 없다."""

    def __init__(self, name: str, day: date, used: int, cap: int, reason: str | None = None):
        self.name = name
        self.day = day
        self.used = used
        self.cap = cap
        self.reason = reason
        if reason is None:
            msg = f"{name} 호출 {day} {used}/{cap} — 그날 상한"
        else:
            msg = f"{name} 호출 {day} 닫힘 — {reason}"
        super().__init__(msg)


class DailyBudget:
    """KST 날짜별 호출 예산. `name` 은 소문자 출처 이름(`krx`·`dart`·`datago`), `scope` 는 같은 출처
    안에서 따로 세는 범위(공공데이터포털 데이터셋 ID 등 — 비밀을 넣지 않는다)."""

    def __init__(
        self,
        redis: Redis | None,
        name: str,
        cap: int,
        *,
        scope: str | None = None,
        key_fn: Callable[[date], str] | None = None,
        ttl_s: int = TTL_S,
    ) -> None:
        if cap <= 0:
            raise ValueError("cap > 0")
        if ttl_s <= 86400:
            raise ValueError("ttl_s 는 하루보다 길어야 한다")
        self.name = name if scope is None else f"{name}:{scope}"
        self.cap = cap
        self._key_fn: Callable[[date], str] = key_fn or (lambda d: budget_key(name, scope, d))
        self._key_fn(date(2026, 1, 1))  # 이름·범위 형식을 만들 때 검사한다
        self._r = redis
        self._ttl = ttl_s
        self._local: dict[date, int] = {}
        self._closed: dict[date, str] = {}
        self._lock = threading.Lock()
        self._redis_failing = False
        self._take: Any = redis.register_script(_TAKE_LUA) if redis is not None else None

    def __repr__(self) -> str:
        return f"DailyBudget(name={self.name!r}, cap={self.cap})"

    @staticmethod
    def day_of(at: datetime) -> date:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        return at.astimezone(KST).date()

    def key(self, day: date) -> str:
        """그날 카운터의 Redis 키."""
        return self._key_fn(day)

    def used(self, at: datetime) -> int:
        """그날(KST) 쓴 호출 수."""
        day = self.day_of(at)
        with self._lock:
            return max(self._local.get(day, 0), self._redis_get_int(self.key(day)))

    def remaining(self, at: datetime) -> int:
        """그날 남은 몫(닫혔으면 0)."""
        if self.closed_reason(at) is not None:
            return 0
        return max(self.cap - self.used(at), 0)

    def closed_reason(self, at: datetime) -> str | None:
        """그날 닫혔으면 사유, 아니면 None."""
        day = self.day_of(at)
        with self._lock:
            local = self._closed.get(day)
            if local is not None:
                return local
            raw = self._redis_get(budget_closed_key(self.key(day)))
            return None if raw is None else _text(raw)

    def take(self, at: datetime) -> int:
        """한 번 몫을 가져가고 그날 쓴 수(이번 포함)를 돌려준다.

        상한·닫힘이면 `BudgetExhausted`(세지 않는다).
        """
        day = self.day_of(at)
        key = self.key(day)
        with self._lock:
            local_used = self._local.get(day, 0)
            if day in self._closed:
                raise BudgetExhausted(self.name, day, local_used, self.cap, self._closed[day])
            res = self._redis_take(key, local_used)
            if res is None:  # Redis 없음·장애 — 이 프로세스 셈만
                if local_used >= self.cap:
                    raise BudgetExhausted(self.name, day, local_used, self.cap)
                self._local[day] = local_used + 1
                return local_used + 1
            code, value = res
            if code == 2:
                self._closed[day] = value
                raise BudgetExhausted(self.name, day, local_used, self.cap, value)
            n = int(value)
            if code == 1:
                raise BudgetExhausted(self.name, day, n, self.cap)
            self._local[day] = n
            return n

    def exhaust(self, at: datetime, reason: str) -> None:
        """그날 예산을 닫는다(출처가 일 한도 초과를 알려 왔을 때). 사유는 가리고 줄여서 남긴다."""
        day = self.day_of(at)
        why = safe_snippet(" ".join(reason.split()) or "사유 없음", REASON_MAX)
        with self._lock:
            self._closed[day] = why
            if self._r is not None:
                try:
                    self._r.set(budget_closed_key(self.key(day)), why, ex=self._ttl)
                except RedisError as e:
                    # 이 프로세스는 닫혔다 — 다른 프로세스는 출처 오류를 받고 따로 닫는다
                    self._redis_failed(e)
                else:
                    self._redis_failing = False
        log.warning(
            json.dumps(
                {"event": "budget_exhausted", "budget": self.name, "day": day.isoformat()},
                ensure_ascii=False,
            )
        )

    # ── Redis ──────────────────────────────────────────────────────────────────────────

    def _redis_take(self, key: str, floor: int) -> tuple[int, str] | None:
        if self._take is None:
            return None
        try:
            res: Any = self._take(
                keys=[key, budget_closed_key(key)], args=[self.cap, floor, self._ttl]
            )
        except RedisError as e:
            self._redis_failed(e)
            return None
        self._redis_failing = False
        code, value = res
        return int(code), _text(value)

    def _redis_get(self, key: str) -> bytes | str | None:
        if self._r is None:
            return None
        try:
            raw: Any = self._r.get(key)
        except RedisError as e:
            self._redis_failed(e)
            return None
        self._redis_failing = False
        return raw

    def _redis_get_int(self, key: str) -> int:
        raw = self._redis_get(key)
        return int(raw) if raw is not None else 0

    def _redis_failed(self, e: RedisError) -> None:
        if not self._redis_failing:
            log.warning(
                json.dumps(
                    {
                        "event": "budget_redis_failed",
                        "budget": self.name,
                        "error": type(e).__name__,
                    },
                    ensure_ascii=False,
                )
            )
        self._redis_failing = True


def _text(raw: object) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def krx_budget(redis: Redis | None, cap: int, *, ttl_s: int = TTL_S) -> DailyBudget:
    """KRX OpenAPI 하루 호출 수 — 키 `krx:calls:<YYYYMMDD>`(GX 와 같다 — 전환 기간 legacy GX 와
    공유)."""
    return DailyBudget(redis, "krx", cap, key_fn=krx_calls_key, ttl_s=ttl_s)
