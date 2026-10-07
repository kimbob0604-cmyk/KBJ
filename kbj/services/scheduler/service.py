"""scheduler 서비스 — 세션 상태 발행 + 작업 실행기 구동(설계 §1.7·§6.1).

GEXLAB `services/scheduler/service.py`(43a9ed1)의 세션 상태 발행 부분(`Scheduler.step`:187·
`_transition`:200·`_publish`:225·`run`:408) 승격. 마스터·KRX 파생·분봉·개장 확인·무결측 단계는
P7 까지 legacy GX 에 남는다(D-P2-5 — 등록부에는 `runner: external` 로 데이터 키만).

- 1초마다 `kbj.core.calendar.state_at` 을 보고 바뀌면 Redis `session:state`(키) + `session.events`
  (채널)에 `SessionState` JSON {state, trade_date, session, at}, `ops.session_log` 에 전이 한 건.
  발행이 실패하면(Redis) 다음 초에 다시. 키는 30초마다 다시 써 둔다(Redis 가 비워져도 곧 채운다).
  기록 실패는 발행을 막지 않는다(health).
- 전이 때 실행기 `on_state`(state_enter 작업), 매 step 실행기 `tick`. 실행기 오류는 health 로만
  남기고 상태 발행을 막지 않는다(격리).
- 캘린더 덮어쓰기 파일은 `Settings.config_dir` 의 것을 쓴다(`TradingCalendar.from_override` —
  묶음 B 참고). 레포 루트 기본(`TradingCalendar.default()`)과 같은 파일이면 결과도 같다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError

from kbj.core.calendar import SessionInfo, State, TradingCalendar, state_at
from kbj.core.time import utcnow
from kbj.services.runtime.health import HealthEvent, HealthSink, Severity
from kbj.services.runtime.heartbeat import Heartbeater
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.runner import JobRunner
from kbj.store.db import StoreError, event_digest
from kbj.store.redis_keys import SESSION_EVENTS, SESSION_STATE

__all__ = [
    "PgSessionLogStore",
    "SchedulerService",
    "SchedulerStore",
    "SessionLogRecord",
    "SessionState",
    "run",
]

SERVICE = "scheduler"
STATE_REFRESH_S = 30.0
STEP_S = 1.0

log = logging.getLogger("kbj.services.scheduler")


def _utc(v: datetime) -> datetime:
    return v.astimezone(UTC)


class SessionState(BaseModel):
    """`session:state`·`session.events` — `state_at` 결과와 판정 시각(GX `services/bus.py`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: State
    trade_date: date | None
    session: Literal["day", "night"] | None
    at: AwareDatetime

    _at = field_validator("at")(_utc)

    @classmethod
    def of(cls, info: SessionInfo, at: datetime) -> SessionState:
        return cls(state=info.state, trade_date=info.trade_date, session=info.session, at=at)


class SessionLogRecord(BaseModel):
    """`ops.session_log` 한 행 — 상태 전이 등(GX `data/store.py`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date | None = None
    session: Literal["day", "night"] | None = None
    service: str = SERVICE
    kind: str
    state: State | None = None
    prev_state: State | None = None
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @classmethod
    def transition(
        cls, ts: datetime, info: SessionInfo, prev: State | None, **detail: Any
    ) -> SessionLogRecord:
        return cls(
            ts=ts,
            trade_date=info.trade_date,
            session=info.session,
            kind="transition",
            state=info.state,
            prev_state=prev,
            detail=detail,
        )


class SchedulerStore(Protocol):
    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None: ...

    def flush_spool(self) -> bool: ...


_INSERT_SESSION = (
    "INSERT INTO ops.session_log (ts, trade_date, session, service, kind, state, prev_state, "
    "detail, digest) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (ts, digest) DO NOTHING"
)


class PgSessionLogStore:
    """`ops.session_log`(0002 — GX 열 그대로). 같은 전이를 두 번 써도 `(ts, digest)` 로 한 줄.

    스풀은 없다(전이는 하루 몇 건 — 실패는 health 로 남고 다음 전이는 그대로 기록된다).
    """

    def __init__(self, connect_fn: Callable[[], psycopg.Connection[Any]]) -> None:
        self._connect = connect_fn

    def __repr__(self) -> str:
        return "PgSessionLogStore()"

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        out: list[tuple[Any, ...]] = []
        for r in rows:
            detail = json.dumps(r.detail, sort_keys=True, ensure_ascii=False, default=str)
            digest = event_digest(
                r.service,
                r.kind,
                r.state.value if r.state else "",
                r.prev_state.value if r.prev_state else "",
                detail,
            )
            out.append(
                (
                    r.ts,
                    r.trade_date,
                    r.session,
                    r.service,
                    r.kind,
                    r.state.value if r.state else None,
                    r.prev_state.value if r.prev_state else None,
                    Jsonb(r.detail),
                    digest,
                )
            )
        if not out:
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany(_INSERT_SESSION, out)
        except psycopg.Error as e:
            kind = type(e).__name__
            raise StoreError(f"session_log 저장 실패: {kind}", cause=kind) from None

    def flush_spool(self) -> bool:
        return True


class SchedulerService:
    """세션 상태 머신 + 실행기. `step()` 을 1초마다(`run`)."""

    def __init__(
        self,
        redis: Redis,
        runner: JobRunner | None,
        kr: TradingCalendar,
        store: SchedulerStore,
        *,
        now: Callable[[], datetime] = utcnow,
        health: HealthSink,
        state_refresh_s: float = STATE_REFRESH_S,
    ) -> None:
        self.redis = redis
        self.runner = runner
        self.cal = kr
        self.store = store
        self._now = now
        self._health = health
        self._refresh = state_refresh_s
        self.state: State | None = None
        self.info: SessionInfo | None = None
        self._current: SessionState | None = None
        self._unpublished = False
        self._key_set_at: datetime | None = None
        self._last_emit: dict[str, datetime] = {}

    def step(self) -> SessionInfo:
        now = self._now()
        info = state_at(now, self.cal)
        if info.state != self.state:
            self._transition(now, info)
        self._publish(now)
        self._jobs(now)
        return info

    def _transition(self, now: datetime, info: SessionInfo) -> None:
        prev = self.state
        self.state, self.info = info.state, info
        self._current = SessionState.of(info, now)
        self._unpublished = True
        tag = (info.trade_date, info.session)
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "state_changed",
            tag,
            state=info.state.value,
            prev_state=prev.value if prev is not None else None,
        )
        detail = {"startup": True} if prev is None else {}
        try:
            self.store.write_session_log([SessionLogRecord.transition(now, info, prev, **detail)])
        except Exception as e:  # 기록 실패가 상태 발행을 막지 않는다
            self._emit(now, "session_log_failed", f"상태 전이 기록 실패: {type(e).__name__}")
        if self.runner is not None and prev is not None:
            try:
                self.runner.on_state(info.state, now)
            except Exception as e:  # 격리 — 상태 발행은 계속
                self._emit(
                    now, "job_state_trigger_failed", f"상태 진입 작업 오류: {type(e).__name__}"
                )

    def _publish(self, now: datetime) -> None:
        cur = self._current
        if cur is None:
            return
        body = cur.model_dump_json()
        try:
            if self._unpublished:
                pipe = self.redis.pipeline()
                pipe.set(SESSION_STATE, body)
                pipe.publish(SESSION_EVENTS, body)
                pipe.execute()
                self._unpublished = False
                self._key_set_at = now
            elif (
                self._key_set_at is None
                or (now - self._key_set_at).total_seconds() >= self._refresh
            ):
                self.redis.set(SESSION_STATE, body)
                self._key_set_at = now
        except RedisError as e:
            self._emit(
                now,
                "session_publish_failed",
                f"상태 발행 실패({type(e).__name__}) — 다음 초에 다시",
                every=60,
            )

    def _jobs(self, now: datetime) -> None:
        if self.runner is None:
            return
        try:
            self.runner.tick(now)
        except Exception as e:  # 격리 — 상태 발행·다음 tick 은 계속
            self._emit(now, "job_tick_failed", f"작업 실행기 오류: {type(e).__name__}")

    def _emit(
        self,
        now: datetime,
        kind: str,
        detail: str,
        severity: Severity = "warning",
        *,
        every: float = 60,
    ) -> None:
        last = self._last_emit.get(kind)
        if every and last is not None and (now - last).total_seconds() < every:
            return
        self._last_emit[kind] = now
        try:
            self._health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)


def run(
    scheduler: SchedulerService,
    stop: threading.Event,
    *,
    heartbeat: Heartbeater | None = None,
    step_s: float = STEP_S,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], object] | None = None,
) -> None:
    """stop 까지 step_s 마다 한 번. step 의 예외는 로그로 남기고 계속(GX `run` 그대로).

    clock(단조 시계)·wait(기본 stop.wait)는 시험이 바꿔 끼운다 — 실제 시계 없이 돌린다.
    """
    pause = wait if wait is not None else stop.wait
    while not stop.is_set():
        t0 = clock()
        try:
            info = scheduler.step()
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "step_failed", error=type(e).__name__)
            info = None
        try:
            scheduler.store.flush_spool()
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "spool_flush_failed", error=type(e).__name__)
        if heartbeat is not None:
            heartbeat.beat(state=info.state.value if info else None)
        pause(max(0.0, step_s - (clock() - t0)))
    log_event(log, logging.INFO, SERVICE, "stopped")
