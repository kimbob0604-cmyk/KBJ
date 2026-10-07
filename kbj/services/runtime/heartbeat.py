"""하트비트·Redis 접속 — GEXLAB `services/runtime.py:Heartbeater`·`heartbeat_age`·`connect_redis`,
`services/bus.py:Heartbeat` 승격.

- 하트비트: Redis `health:heartbeat:<서비스>`(TTL) — compose healthcheck 가
  `python -m kbj.services.runtime.healthcheck <서비스>` 로 나이를 본다. Redis 가 안 되면 로그만
  남기고 계속(실패 알림은 한 번 — 다시 되면 또 알린다).
- status 필드에는 상태 요약(동작·만료 시각 등)만 — 토큰·키 값을 넣지 않는다. 문자열 값은 가린다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, SecretStr, field_validator
from redis import Redis
from redis.exceptions import RedisError

from kbj.core.masking import mask_text
from kbj.services.runtime.clock import utcnow
from kbj.services.runtime.log import log_event
from kbj.store.redis_keys import heartbeat_key

HEARTBEAT_TTL_S = 60
HEARTBEAT_EVERY_S = 10.0
REDIS_TIMEOUT_S = 5.0


class Heartbeat(BaseModel):
    """`health:heartbeat:<서비스>` 값(GX `services/bus.py:Heartbeat` 와 같은 모양)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    at: AwareDatetime
    pid: int
    status: dict[str, Any] = {}

    @field_validator("at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


def connect_redis(url: SecretStr) -> Redis:
    """접속 문자열(비밀번호 포함 — SecretStr)로 Redis 클라이언트. 접속·읽기 5초 제한."""
    return Redis.from_url(
        url.get_secret_value(),
        socket_connect_timeout=REDIS_TIMEOUT_S,
        socket_timeout=REDIS_TIMEOUT_S,
        health_check_interval=30,
    )


def _clean(status: dict[str, Any]) -> dict[str, Any]:
    return {k: mask_text(v) if isinstance(v, str) else v for k, v in status.items()}


class Heartbeater:
    """`every_s` 마다 한 번 Redis 에 하트비트(TTL). 실패는 로그만 — 서비스는 계속 돈다."""

    def __init__(
        self,
        redis: Redis,
        service: str,
        *,
        ttl_s: int = HEARTBEAT_TTL_S,
        every_s: float = HEARTBEAT_EVERY_S,
        now: Callable[[], datetime] = utcnow,
        logger: logging.Logger | None = None,
    ) -> None:
        if ttl_s <= every_s:
            raise ValueError("ttl_s 는 every_s 보다 길어야 한다")
        self._r = redis
        self.service = service
        self._key = heartbeat_key(service)
        self._ttl = ttl_s
        self._every = every_s
        self._now = now
        self._last: datetime | None = None
        self._log = logger or logging.getLogger(f"kbj.services.{service}")
        self._failing = False

    def beat(self, *, force: bool = False, **status: Any) -> bool:
        """보낼 때가 됐으면(또는 force) 보낸다. 보냈으면 True."""
        now = self._now()
        last = self._last
        if not force and last is not None and (now - last).total_seconds() < self._every:
            return False
        hb = Heartbeat(service=self.service, at=now, pid=os.getpid(), status=_clean(status))
        try:
            self._r.set(self._key, hb.model_dump_json(), ex=self._ttl)
        except RedisError as e:
            if not self._failing:
                err = type(e).__name__
                log_event(self._log, logging.WARNING, self.service, "heartbeat_failed", error=err)
            self._failing = True
            self._last = now  # 실패도 한 주기 쉰다(Redis 가 없을 때 매 루프 막히지 않게)
            return False
        self._failing = False
        self._last = now
        return True


def heartbeat_age(redis: Redis, service: str, now: datetime) -> float | None:
    """마지막 하트비트 뒤 지난 초. 없으면 None(읽기·형식 오류는 그대로 올라간다)."""
    raw: Any = redis.get(heartbeat_key(service))
    if raw is None:
        return None
    hb = Heartbeat.model_validate_json(raw)
    return (now - hb.at).total_seconds()
