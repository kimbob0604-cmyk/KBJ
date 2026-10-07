"""recorder 서비스 — Redis `ws.raw`·`rest.raw` 의 원문을 그대로 raw_messages 에 (PLAN §4.1·§4.5,
docs/phase1_design.md §2). 진입점 `python -m services.recorder`.

- 메시지는 `RawEnvelope` JSON(`services.bus`). pydantic 검증에 실패한 메시지는 health 로 센다(내용은
  싣지 않는다 — 계약을 어긴 발행자 버그)
- `Batcher`(500건·1초)로 묶어 `PostgresSink.write_raw`. DB 장애는 싱크의 디스크 스풀이 받는다
  (예외 없음). 데이터 오류로 묶음이 거부되면 한 건씩 다시 써서 나머지를 살리고, 끝내 안 들어간
  건은 health(`recorder_write_failed`)로 센다
- Redis 가 끊기면 들고 있던 묶음을 쓰고 백오프(1초→30초) 뒤 다시 구독한다. 끊긴 동안 발행된
  원문은 recorder 에 오지 않는다 — 발행자가 수신자 0·오류를 보고 직접 DB 에 쓴다(`services.bus`)
- 멈출 때(SIGTERM) 들고 있던 묶음을 쓰고 스풀을 한 번 더 비워 본다
- 한계: 프로세스가 강제로 죽으면 아직 쓰지 않은 묶음(최대 1초·500건)은 잃는다
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import TradingCalendar
from services.auth.health import HealthEvent, HealthSink, Severity
from services.bus import RAW_CHANNELS, decode_envelope
from services.recorder.envelope import Batcher, RawEnvelope
from services.runtime import (
    Backoff,
    Heartbeater,
    ServiceHealthSink,
    connect_redis,
    install_stop,
    log_event,
    setup_logging,
    tagger_for,
    utcnow,
)

SERVICE = "recorder"
POLL_S = 0.2  # 구독 메시지 대기 한 번 — 묶음 나이·하트비트를 이만큼마다 본다
SPOOL_EVERY_S = 1.0

log = logging.getLogger("services.recorder")


class RawStore(Protocol):
    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None: ...

    def flush_spool(self) -> bool: ...


@dataclass
class RecorderStats:
    received: int = 0
    bad: int = 0  # 봉투 검증 실패
    written: int = 0
    failed: int = 0  # 한 건씩 다시 써도 안 들어간 봉투
    reconnects: int = 0


class Recorder:
    """메시지 → 묶음 → 저장. Redis 와 떨어져 있어 단위 시험이 쉽다(`run` 이 Redis 를 붙인다)."""

    def __init__(
        self,
        store: RawStore,
        health: HealthSink,
        *,
        batcher: Batcher | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.health = health
        self.batcher = batcher if batcher is not None else Batcher()
        self.stats = RecorderStats()
        self._now = now
        self._last_emit: dict[str, datetime] = {}

    def accept(self, data: bytes | str) -> None:
        self.stats.received += 1
        try:
            env = decode_envelope(data)
        except (ValidationError, ValueError) as e:
            self.stats.bad += 1
            self.emit_health(
                "recorder_bad_message",
                f"RawEnvelope 가 아닌 메시지 {self.stats.bad}건째 ({type(e).__name__}, "
                f"{len(data)}B) — 발행자 확인",
                "warning",
            )
            return
        batch = self.batcher.add(env)
        if batch is not None:
            self.write(batch)

    def tick(self) -> None:
        """묶음이 오래됐으면 쓴다."""
        if self.batcher.due():
            self.write(self.batcher.flush())

    def drain(self) -> None:
        if len(self.batcher):
            self.write(self.batcher.flush())

    def write(self, batch: list[RawEnvelope]) -> None:
        if not batch:
            return
        try:
            self.store.write_raw(batch)
            self.stats.written += len(batch)
            return
        except Exception as e:
            first = type(e).__name__
        lost: list[RawEnvelope] = []
        for env in batch:  # 한 건 때문에 묶음 전체를 잃지 않게
            try:
                self.store.write_raw([env])
                self.stats.written += 1
            except Exception:
                lost.append(env)
        if lost:
            self.stats.failed += len(lost)
            trs = sorted({e.tr_id for e in lost})[:5]
            self.emit_health(
                "recorder_write_failed",
                f"원문 {len(lost)}건을 저장하지 못했다 ({first}, tr_id {','.join(trs)}) — "
                f"누적 {self.stats.failed}건",
                "critical",
                every=0,
            )

    def emit_health(self, kind: str, detail: str, severity: Severity, *, every: float = 60) -> None:
        """같은 kind 는 every 초에 한 번 (0 이면 매번)."""
        now = self._now()
        last = self._last_emit.get(kind)
        if every and last is not None and (now - last).total_seconds() < every:
            return
        self._last_emit[kind] = now
        try:
            self.health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)


def run(
    recorder: Recorder,
    redis_factory: Callable[[], Redis],
    stop: threading.Event,
    *,
    heartbeat: Heartbeater | None = None,
    poll_s: float = POLL_S,
    backoff: Backoff | None = None,
    spool_every_s: float = SPOOL_EVERY_S,
    clock: Callable[[], float] | None = None,
) -> None:
    """stop 까지 구독해 기록한다. Redis 오류면 묶음을 쓰고 백오프 뒤 다시 붙는다."""
    mono = clock if clock is not None else time.monotonic
    back = backoff if backoff is not None else Backoff(1.0, 30.0)
    next_spool = 0.0
    while not stop.is_set():
        pubsub: Any = None
        try:
            r = redis_factory()
            pubsub = r.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(*RAW_CHANNELS)
            log_event(log, logging.INFO, SERVICE, "subscribed", channels=list(RAW_CHANNELS))
            while not stop.is_set():
                msg = pubsub.get_message(timeout=poll_s)
                if msg is not None and msg.get("type") == "message":
                    recorder.accept(msg["data"])
                    back.reset()
                recorder.tick()
                if mono() >= next_spool:
                    next_spool = mono() + spool_every_s
                    _flush_spool(recorder)
                if heartbeat is not None:
                    s = recorder.stats
                    heartbeat.beat(received=s.received, written=s.written, failed=s.failed)
        except RedisError as e:
            recorder.stats.reconnects += 1
            recorder.drain()  # 들고 있던 것은 먼저 쓴다
            delay = back.next()
            recorder.emit_health(
                "recorder_redis_down",
                f"Redis 구독 끊김({type(e).__name__}) — {delay:g}초 뒤 다시 구독",
                "warning",
            )
            stop.wait(delay)
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except (RedisError, OSError):
                    pass
    recorder.drain()
    _flush_spool(recorder)
    log_event(log, logging.INFO, SERVICE, "stopped", **vars(recorder.stats))


def _flush_spool(recorder: Recorder) -> None:
    try:
        recorder.store.flush_spool()
    except Exception as e:
        log_event(log, logging.ERROR, SERVICE, "spool_flush_failed", error=type(e).__name__)


def main() -> int:  # pragma: no cover — compose 진입점 (.env·실제 Redis·DB)
    from config.settings import Settings
    from data.store import PostgresSink

    setup_logging()
    settings = Settings()
    if not settings.redis_url:
        log_event(log, logging.ERROR, SERVICE, "config_error", error="REDIS_URL 이 없다")
        return 2
    stop = threading.Event()
    install_stop(stop)
    cal = TradingCalendar.default()
    tagger = tagger_for(cal)
    store = PostgresSink.from_settings(service=SERVICE, spool=True, tagger=tagger)
    url = settings.redis_url
    try:
        health = ServiceHealthSink(store, tagger)
        recorder = Recorder(store, health)
        hb = Heartbeater(connect_redis(url), SERVICE)
        log_event(log, logging.INFO, SERVICE, "started")
        run(recorder, lambda: connect_redis(url), stop, heartbeat=hb)
    finally:
        store.close()
    return 0
