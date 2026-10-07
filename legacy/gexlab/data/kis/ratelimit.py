"""앱키당 KIS 레이트리미터 — KBJ P2: 정본은 `kbj.data.ratelimit`(이 파일에서 승격, 설계 §1.1·§4).

같은 Redis 키(`rl:kis:<앱키 해시>` — `limiter_key`)를 쓰므로 전환 기간 legacy GX(poller·ws-gateway·
scheduler 분봉)와 KBJ 가 한 버킷을 나눠 쓴다. 승격한 시험: `tests/unit/test_ratelimit.py`·
`tests/property/test_ratelimit_properties.py` → kbj `tests/unit/data`·`tests/property`.
"""

from kbj.data.ratelimit import (
    TR_DISPLAY_BOARD_CALLPUT,
    US,
    Clock,
    Decision,
    LocalRateLimiter,
    Priority,
    RateLimitConfig,
    RateLimiter,
    RateLimitTimeout,
    Reason,
    RedisRateLimiter,
    SystemClock,
    effective_rate,
    interval_us,
    limiter_key,
)

__all__ = [
    "TR_DISPLAY_BOARD_CALLPUT",
    "US",
    "Clock",
    "Decision",
    "LocalRateLimiter",
    "Priority",
    "RateLimitConfig",
    "RateLimitTimeout",
    "RateLimiter",
    "Reason",
    "RedisRateLimiter",
    "SystemClock",
    "effective_rate",
    "interval_us",
    "limiter_key",
]
