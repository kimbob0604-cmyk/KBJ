"""scheduler 서비스 — 세션 상태 머신 구동·마스터 갱신 (PLAN §4.6, docs/phase1_design.md §2·§5·§6).
진입점 `python -m services.scheduler`.

- 1초마다 `core.calendar.state_at` 을 보고 바뀌면 Redis `session:state`(키) + `session.events`
  (채널)에 `SessionState` JSON {state, trade_date, session, at}, `session_log` 에 전이 한 건.
  발행이 실패하면(Redis) 다음 초에 다시. 키는 30초마다 다시 써 둔다(Redis 가 비워져도 곧 채운다)
- `PRE_DAY`·`PRE_NIGHT` 에 들어가면 KIS 지수선물옵션 마스터를 내려받아 `master_snapshots`
  (PRE_DAY = 그날 주간, PRE_NIGHT = 다음 거래일 야간)와 Redis `kis:master`(poller·ws-gateway 가
  읽는다)에 둔다. 기동할 때 Redis 의 마스터가 없거나 지금 세션 것이 아니면 곧바로 받는다
- 내려받기·파싱이 실패하면 직전 마스터를 계속 쓰고(Redis 를 건드리지 않는다) health, 60초 뒤 다시
- 내려받기는 주입(`MasterDownloader` — zip 바이트). 기본은 KIS 배포 URL(REST 한도 밖, §4.4) — 접속
  5초·읽기 15초·전체 30초로 끊는다. 상태 루프를 막지 않게 데몬 스레드에서 받고(`thread_submit`),
  step 은 끝났는지만 본다 — 끝나면 시작한 시각의 세션 태그로 적용한다(PRE_NIGHT 에 시작해 18:00
  뒤에 끝나도 야간 것). 받는 동안 새로 받을 일이 생기면(전이) 끝난 뒤 다시 받는다
- KRX 전일 적재(`krx` — services/scheduler/krx.py `KrxDaily`): 거래일 08:05(PRE_DAY)부터 전
  거래일 KRX 코스피200 옵션·선물 일별을 작업 스레드에서 받고, 지금 쓰는 마스터(`self.master` —
  Redis 에 둔 것)와 마스터 ⊇ KRX 행사가 대조(설계 §5)를 한다. step 은 끝났는지만 보고, 그 단계의
  오류는 health 로 남기고 상태 발행·마스터를 막지 않는다. KRX 인증키가 없으면 끈다(health)
- KIS 선물 분봉 적재(`minute` — services/scheduler/minute.py `MinuteDaily`): 거래일 T 의 16:00
  (POST_DAY)에 T 주간(`F`), 06:10(IDLE)에 T 로 귀속되는 밤(`CM`)의 근월물 분봉을 작업 스레드에서
  `minute_bars` 에. 근월물은 지금 쓰는 마스터(`self.master`)로 정한다. 호출은 앱키당 하나인 Redis
  레이트리미터 P4 + auth 토큰 읽기만(`reader_kis_client`). 그 단계의 오류는 health 로 남기고 상태
  발행·마스터·KRX 를 막지 않는다. KIS 앱키가 없으면 끈다(health)
- 개장 이중 확인(`open_check` — services/scheduler/open_check.py `OpenCheck`, 설계 §6): 캘린더상
  열린 세션의 개장 뒤 3분(주간 08:48, 야간 18:03)에 선물 체결이 없으면 health `calendar_mismatch` +
  session_log. 읽기는 작업 스레드·스풀 없는 저장소, 오류는 health 로 격리
- 무결측 판정(`gaps` — services/scheduler/gaps.py `GapDaily`, 설계 §9): 주간은 POST_DAY 16:00~,
  야간은 IDLE 06:10~ 그 세션 분봉 적재가 끝난 뒤 저장된 데이터로 스트림별 공백을 찾아
  collection_gaps·collection_reports 를 교체하고 요약 health. 작업 스레드·스풀 없는 저장소,
  오류는 health 로 격리
- 범위 밖(이 파일 다음 단계, 설계 §2·§11): 월물리스트
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from datetime import date, datetime, timedelta
from typing import Protocol

import httpx
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import SessionInfo, State, TradingCalendar, state_at
from data.kis.master import MasterRow, master_text_from_zip, parse_master
from data.store import SessionLogRecord
from services.auth.health import HealthEvent, HealthSink, Severity
from services.bus import (
    MASTER_KEY,
    MASTER_SHA_KEY,
    SESSION_EVENTS,
    SESSION_STATE,
    MasterSnapshot,
    SessionState,
)
from services.poller.context import session_tag
from services.runtime import (
    Heartbeater,
    ServiceHealthSink,
    connect_redis,
    install_stop,
    log_event,
    run_in_thread,
    setup_logging,
    tagger_for,
    utcnow,
)
from services.scheduler.gaps import GapDaily
from services.scheduler.krx import KrxDaily
from services.scheduler.minute import MinuteDaily
from services.scheduler.open_check import OpenCheck

SERVICE = "scheduler"
MASTER_RETRY_S = 60.0
STATE_REFRESH_S = 30.0
STEP_S = 1.0

MASTER_CONNECT_S = 5.0  # 확인 필요: KIS 배포 서버 응답·파일 크기 미실측 — 보수적으로
MASTER_READ_S = 15.0
MASTER_TOTAL_S = 30.0
MASTER_MAX_BYTES = 32 << 20

MasterDownloader = Callable[[], bytes]
Submit = Callable[[MasterDownloader], "Future[bytes]"]

log = logging.getLogger("services.scheduler")


class SchedulerStore(Protocol):
    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None: ...

    def write_master(
        self, rows: Sequence[MasterRow], *, ts: datetime, trade_date: date, session: str
    ) -> None: ...

    def flush_spool(self) -> bool: ...


def http_master_downloader(
    *,
    connect_s: float = MASTER_CONNECT_S,
    read_s: float = MASTER_READ_S,
    total_s: float = MASTER_TOTAL_S,
    max_bytes: int = MASTER_MAX_BYTES,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> MasterDownloader:
    """KIS 마스터 zip 을 받는다. 접속·읽기 한 번마다 시간 제한, 전체는 조각 사이에서 total_s 로
    끊는다(TimeoutError) — 느리게 흘러와도 끝이 있다. 시험은 transport(가짜)를 넣는다."""
    from scripts.probe_common import MASTER_URL

    timeout = httpx.Timeout(read_s, connect=connect_s)

    def get() -> bytes:
        deadline = clock() + total_s
        buf = bytearray()
        with (
            httpx.Client(timeout=timeout, transport=transport) as client,
            client.stream("GET", MASTER_URL) as r,
        ):
            r.raise_for_status()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise ValueError(f"마스터가 {max_bytes}B 보다 크다")
                if clock() > deadline:
                    raise TimeoutError(f"마스터 내려받기 {total_s:g}초 초과")
        return bytes(buf)

    return get


def thread_submit(fn: MasterDownloader) -> Future[bytes]:
    """데몬 스레드 하나로 부른다 — 상태 루프가 기다리지 않고, 멈출 때도 기다리지 않는다."""
    return run_in_thread(fn, name="master-download")


class Scheduler:
    def __init__(
        self,
        redis: Redis,
        store: SchedulerStore,
        calendar: TradingCalendar,
        *,
        downloader: MasterDownloader,
        health: HealthSink,
        now: Callable[[], datetime] = utcnow,
        master_retry_s: float = MASTER_RETRY_S,
        state_refresh_s: float = STATE_REFRESH_S,
        submit: Submit = thread_submit,
        krx: KrxDaily | None = None,
        minute: MinuteDaily | None = None,
        open_check: OpenCheck | None = None,
        gaps: GapDaily | None = None,
    ) -> None:
        self.redis = redis
        self.store = store
        self.cal = calendar
        self._download = downloader
        self._health = health
        self._now = now
        self._retry = master_retry_s
        self._refresh = state_refresh_s
        self.state: State | None = None
        self.info: SessionInfo | None = None
        self._current: SessionState | None = None  # 마지막 전이 (키에 두는 값)
        self._unpublished = False
        self._key_set_at: datetime | None = None
        self.master_due: datetime | None = None
        self.master_sha: str | None = None
        self.master: MasterSnapshot | None = None  # 지금 Redis 에 둔 마스터 (KRX 대조 입력)
        self._submit = submit
        self._inflight: tuple[datetime, Future[bytes]] | None = None  # (시작 시각, 내려받기)
        self._last_emit: dict[str, datetime] = {}
        self.krx = krx
        self.minute = minute
        self.open_check = open_check
        self.gaps = gaps

    # ── 한 번 ──

    def step(self) -> SessionInfo:
        now = self._now()
        info = state_at(now, self.cal)
        if info.state != self.state:
            self._transition(now, info)
        self._publish(now)
        self._master_step(now)
        self._krx_step(now)
        self._minute_step(now)
        self._open_step(now)
        self._gap_step(now)
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
        if info.state in (State.PRE_DAY, State.PRE_NIGHT):
            self.master_due = now
        elif prev is None and self._master_stale(now):
            self.master_due = now

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

    # ── 마스터 ──

    def _master_stale(self, now: datetime) -> bool:
        """Redis 에 쓸 마스터가 없거나 지금 세션 것이 아니다 (기동 때)."""
        try:
            raw = self.redis.get(MASTER_KEY)
            if raw is None:
                return True
            snap = MasterSnapshot.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
        except (RedisError, ValidationError, ValueError):
            return True
        self.master_sha = snap.sha256
        self.master = snap
        tag = session_tag(now, self.cal)
        return tag is not None and (snap.trade_date, snap.session) != tag

    def _master_step(self, now: datetime) -> None:
        """때가 되면 내려받기를 시작하고(스레드), 끝났으면 적용한다 — 기다리지 않는다."""
        if self._inflight is None:
            if self.master_due is None or now < self.master_due:
                return
            try:
                self._inflight = (now, self._submit(self._download))
            except Exception as e:  # 스레드를 못 띄웠다
                self._master_failed(now, now, f"내려받기 시작 실패: {type(e).__name__}")
                return
        started, fut = self._inflight
        if not fut.done():
            return
        self._inflight = None
        try:
            data = fut.result()
        except Exception as e:
            self._master_failed(now, started, f"내려받기 실패: {type(e).__name__}")
            return
        self._apply_master(now, started, data)

    def _apply_master(self, now: datetime, started: datetime, data: bytes) -> bool:
        """받은 zip → master_snapshots·Redis. 세션 태그는 받기 시작한 시각(그 전이의 세션)."""
        try:
            text = master_text_from_zip(data)
            rows = parse_master(text)
        except ValueError as e:
            msg = str(e).splitlines()[0][:120] if str(e) else ""
            return self._master_failed(now, started, f"파싱 실패 — 직전 마스터를 계속 쓴다: {msg}")
        options = sum(1 for r in rows if r.is_option)
        if options == 0:
            return self._master_failed(
                now, started, "옵션 행이 없는 마스터 — 직전 마스터를 계속 쓴다"
            )
        tag = session_tag(started, self.cal)
        if tag is not None:
            try:
                self.store.write_master(rows, ts=now, trade_date=tag[0], session=tag[1])
            except Exception as e:  # Redis 에는 둔다 — 수집은 계속돼야 한다
                self._emit(
                    now, "master_store_failed", f"master_snapshots 저장 실패: {type(e).__name__}"
                )
        sha = MasterSnapshot.digest(text)
        snap = MasterSnapshot(
            asof=now,
            trade_date=tag[0] if tag else None,
            session=tag[1] if tag else None,
            rows=len(rows),
            sha256=sha,
            text=text,
        )
        try:
            pipe = self.redis.pipeline()
            pipe.set(MASTER_KEY, snap.model_dump_json())
            pipe.set(MASTER_SHA_KEY, sha)
            pipe.execute()
        except RedisError as e:
            return self._master_failed(now, started, f"Redis 저장 실패: {type(e).__name__}")
        if self.master_due is not None and self.master_due <= started:
            self.master_due = None  # 받는 동안 새로 생긴 요청(전이)이 있으면 남겨 다음 step 에
        self.master_sha = sha
        self.master = snap
        where = f"{tag[0]} {tag[1]}" if tag else "장 밖"
        self._emit(
            now,
            "master_refreshed",
            f"마스터 {len(rows)}행(옵션 {options}) sha {sha[:12]} — {where}",
            "info",
            every=0,
        )
        return True

    def _master_failed(self, now: datetime, started: datetime, detail: str) -> bool:
        """직전 마스터를 두고 retry 초 뒤 다시 — 받는 동안 새 요청(전이)이 생겼으면 그것이 먼저."""
        if self.master_due is None or self.master_due <= started:
            self.master_due = now + timedelta(seconds=self._retry)
        self._emit(now, "master_refresh_failed", f"{detail} — {self._retry:g}초 뒤 다시", every=0)
        return False

    # ── KRX 전일 적재 ──

    def _krx_step(self, now: datetime) -> None:
        if self.krx is None:
            return
        try:
            self.krx.step(now, self.master)
        except Exception as e:  # 격리 — 상태 발행·마스터는 계속
            self._emit(now, "krx_step_failed", f"KRX 전일 적재 단계 오류: {type(e).__name__}")

    # ── KIS 분봉 적재 ──

    def _minute_step(self, now: datetime) -> None:
        if self.minute is None:
            return
        try:
            self.minute.step(now, self.master)
        except Exception as e:  # 격리 — 상태 발행·마스터·KRX 는 계속
            self._emit(now, "minute_step_failed", f"분봉 적재 단계 오류: {type(e).__name__}")

    # ── 개장 이중 확인 ──

    def _open_step(self, now: datetime) -> None:
        if self.open_check is None:
            return
        try:
            self.open_check.step(now)
        except Exception as e:  # 격리 — 상태 발행·마스터·적재는 계속
            self._emit(
                now, "open_check_step_failed", f"개장 이중 확인 단계 오류: {type(e).__name__}"
            )

    # ── 무결측 판정 ──

    def _gap_step(self, now: datetime) -> None:
        if self.gaps is None:
            return
        try:
            self.gaps.step(now)
        except Exception as e:  # 격리 — 상태 발행·마스터·적재는 계속
            self._emit(now, "gap_step_failed", f"무결측 판정 단계 오류: {type(e).__name__}")

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
    scheduler: Scheduler,
    stop: threading.Event,
    *,
    heartbeat: Heartbeater | None = None,
    step_s: float = STEP_S,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], object] | None = None,
) -> None:
    """stop 까지 step_s 마다 한 번. step 의 예외는 로그로 남기고 계속.

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


def main() -> int:  # pragma: no cover — compose 진입점 (.env·실제 Redis·DB·KIS 마스터·KRX)
    from config.settings import Settings
    from data.kis.rest import KisClient
    from data.krx.eod import KrxClient
    from data.store import PostgresSink
    from services.chain_feed import load_context
    from services.gaps import GapConfig
    from services.poller.config import PollerConfig
    from services.scheduler.krx import KrxCallBudget, KrxLoader
    from services.scheduler.minute import MinuteLoader, reader_kis_client

    setup_logging()
    settings = Settings()
    if not settings.redis_url:
        log_event(log, logging.ERROR, SERVICE, "config_error", error="REDIS_URL 이 없다")
        return 2
    stop = threading.Event()
    install_stop(stop)
    cal = TradingCalendar.default()
    redis = connect_redis(settings.redis_url)
    tagger = tagger_for(cal)
    store = PostgresSink.from_settings(service=SERVICE, spool=True, tagger=tagger)
    # KRX 적재는 스풀 없는 따로 된 연결 — services/scheduler/krx.py 모듈 설명
    krx_store = PostgresSink.from_settings(service=SERVICE, tagger=tagger)
    # 개장 이중 확인·무결측 판정 읽기도 스풀 없는 따로 된 연결 — 상태 루프 싱크 잠금을 쥐지 않게
    report_store = PostgresSink.from_settings(service=SERVICE, tagger=tagger)
    kis: KisClient | None = None
    try:
        health = ServiceHealthSink(store, tagger)
        krx: KrxDaily | None = None
        if settings.krx_api_key is None:
            health.emit(
                HealthEvent(
                    "krx_disabled",
                    "KRX_API_KEY 가 없다 — KRX 전일 적재·마스터 ⊇ KRX 대조를 하지 않는다",
                    utcnow(),
                    "warning",
                    service=SERVICE,
                )
            )
        else:
            budget = KrxCallBudget(redis, settings.krx_daily_call_cap)
            client = KrxClient.from_settings(settings)
            loader = KrxLoader(client, krx_store, budget, tagger=tagger)
            krx = KrxDaily(loader, cal, health, tagger=tagger)
        # 분봉은 상태 루프 싱크(스풀)에 — 쓰기가 작고(한 번 102행 이하) DB 장애에도 잃지 않게
        minute: MinuteDaily | None = None
        if settings.kis_app_key is None:
            health.emit(
                HealthEvent(
                    "minute_disabled",
                    "KIS_APP_KEY 가 없다 — KIS 선물 분봉 적재를 하지 않는다",
                    utcnow(),
                    "warning",
                    service=SERVICE,
                )
            )
        else:
            kis = reader_kis_client(settings, redis)
            minute_loader = MinuteLoader(kis, store, cal, context=lambda: load_context(redis))
            minute = MinuteDaily(minute_loader, cal, health)
        sched = Scheduler(
            redis,
            store,
            cal,
            downloader=http_master_downloader(),
            health=health,
            krx=krx,
            minute=minute,
            open_check=OpenCheck(report_store, store, cal, health),
            gaps=GapDaily(
                report_store,
                cal,
                health,
                minute=minute,
                # 야간 분기·야간 투자자별은 poller 설정과 같게 (services/gaps.py GapConfig)
                gap_config=GapConfig(
                    night_mode=PollerConfig().night_mode,
                    night_investor=PollerConfig().night_investor,
                ),
            ),
        )
        log_event(log, logging.INFO, SERVICE, "started")
        run(sched, stop, heartbeat=Heartbeater(redis, SERVICE))
    finally:
        if kis is not None:
            kis.close()
        report_store.close()
        krx_store.close()
        store.close()
    return 0
