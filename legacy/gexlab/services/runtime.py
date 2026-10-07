"""서비스 공통 실행 도구 — 로깅·종료 신호·Redis 접속·하트비트·health 싱크 (PLAN §6.5·§11.3).

- 로그는 구조화 JSON 한 줄(`log_event`): service·event·trade_date·session + 필드. 토큰·접속
  문자열은 싣지 않는다(호출자가 넣지 않는다)
- SIGTERM·SIGINT → `threading.Event` (graceful stop — 루프가 묶음을 비우고 끝난다)
- 하트비트: Redis `health:heartbeat:<서비스>`(TTL) — compose healthcheck 가
  `python -m services.health <서비스>` 로 나이를 본다. Redis 가 안 되면 로그만 남기고 계속
- `ServiceHealthSink`: health 이벤트를 JSON 로그 + health_events(PostgresSink — 스풀 포함)로.
  한쪽 실패가 다른 쪽·서비스를 막지 않는다
- `run_in_thread`: 오래 걸릴 수 있는 일(내려받기·KRX 적재)을 데몬 스레드 하나로 — 상태 루프는
  `Future.done()` 만 본다
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, date, datetime
from typing import Any, Protocol

from kbj.services.runtime import tagger_for  # noqa: F401 — 다시 내보내기(KBJ P2)
from redis import Redis
from redis.exceptions import RedisError

from services.auth.health import HealthEvent, LogHealthSink
from services.bus import Heartbeat, heartbeat_key

Tag = tuple[date | None, str | None]
Tagger = Callable[[datetime], tuple[date | None, str | None]]

HEARTBEAT_TTL_S = 60
HEARTBEAT_EVERY_S = 10.0
REDIS_TIMEOUT_S = 5.0


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    level: int,
    service: str,
    event: str,
    tag: Tag | None = None,
    **fields: object,
) -> None:
    """구조화 JSON 한 줄 (CLAUDE.md: 거래일·세션·서비스명)."""
    td, ss = tag if tag is not None else (None, None)
    rec: dict[str, object] = {
        "service": service,
        "event": event,
        "trade_date": td.isoformat() if td is not None else None,
        "session": ss,
        **fields,
    }
    logger.log(level, json.dumps(rec, ensure_ascii=False, default=str))


def install_stop(stop: threading.Event) -> None:
    """SIGTERM·SIGINT 가 오면 stop 을 세운다 (메인 스레드에서 부른다)."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())


def connect_redis(url: str) -> Redis:
    return Redis.from_url(
        url,
        socket_connect_timeout=REDIS_TIMEOUT_S,
        socket_timeout=REDIS_TIMEOUT_S,
        health_check_interval=30,
    )


def run_in_thread[T](fn: Callable[[], T], *, name: str = "worker") -> Future[T]:
    """데몬 스레드 하나로 부른다 — 부른 쪽은 기다리지 않고, 멈출 때도 기다리지 않는다. 결과·예외는
    Future 에."""
    fut: Future[T] = Future()

    def target() -> None:
        if not fut.set_running_or_notify_cancel():
            return
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)

    threading.Thread(target=target, name=name, daemon=True).start()
    return fut


# KBJ P2: tagger_for 는 kbj.services.runtime 으로 승격했다 — 위 import 로 다시 내보낸다.


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
        self._ttl = ttl_s
        self._every = every_s
        self._now = now
        self._last: datetime | None = None
        self._log = logger or logging.getLogger(f"services.{service}")
        self._failing = False

    def beat(self, *, force: bool = False, **status: Any) -> bool:
        """보낼 때가 됐으면(또는 force) 보낸다. 보냈으면 True."""
        now = self._now()
        last = self._last
        if not force and last is not None and (now - last).total_seconds() < self._every:
            return False
        hb = Heartbeat(service=self.service, at=now, pid=os.getpid(), status=status)
        try:
            self._r.set(heartbeat_key(self.service), hb.model_dump_json(), ex=self._ttl)
        except RedisError as e:
            if not self._failing:
                err = type(e).__name__
                log_event(self._log, logging.WARNING, self.service, "heartbeat_failed", error=err)
            self._failing = True
            self._last = now  # 실패도 한 주기 쉰다 (Redis 가 없을 때 매 루프 막히지 않게)
            return False
        self._failing = False
        self._last = now
        return True


def heartbeat_age(redis: Redis, service: str, now: datetime) -> float | None:
    """마지막 하트비트 뒤 지난 초. 없거나 읽을 수 없으면 None."""
    raw = redis.get(heartbeat_key(service))
    if raw is None:
        return None
    hb = Heartbeat.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
    return (now - hb.at).total_seconds()


class HealthStore(Protocol):
    def write_health(self, events: Any, *, tagger: Tagger | None = None) -> None: ...


class ServiceHealthSink:
    """health 이벤트 → JSON 로그(거래일·세션 태그) + health_events. 저장 실패는 로그만."""

    def __init__(
        self,
        store: HealthStore | None,
        tagger: Tagger | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._store = store
        self._tagger = tagger
        self._log = LogHealthSink(logger, tagger=tagger)

    def emit(self, event: HealthEvent) -> None:
        self._log.emit(event)
        if self._store is None:
            return
        try:
            self._store.write_health([event], tagger=self._tagger)
        except Exception as e:
            logging.getLogger("services").error(
                json.dumps(
                    {
                        "service": event.service,
                        "event": "health_write_failed",
                        "trade_date": None,
                        "session": None,
                        "kind": event.kind,
                        "error": type(e).__name__,
                    },
                    ensure_ascii=False,
                )
            )


class Backoff:
    """재시도 간격 (1초 → 최대, 두 배씩). 성공하면 `reset`."""

    def __init__(self, initial: float = 1.0, maximum: float = 30.0) -> None:
        if not 0 < initial <= maximum:
            raise ValueError("0 < initial <= maximum")
        self.initial = initial
        self.maximum = maximum
        self._next = initial

    def next(self) -> float:
        d = self._next
        self._next = min(self._next * 2, self.maximum)
        return d

    def reset(self) -> None:
        self._next = self.initial
