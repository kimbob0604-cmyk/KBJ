"""ws-gateway 서비스 조립 — 진입점 `python -m services.ws_gateway` (PLAN §4.1·§4.3, 설계 §2·§4).

- `KisWsClient`(단일 세션, 접속키는 Redis `kis:ws_key` 를 auth reader 로 — 발급하지 않는다)와
  `SubscriptionController`(세션·만기·ATM 회전)를 한 이벤트 루프에서 돌린다
- 체인: scheduler 가 둔 마스터(`kis:master`) + poller 문맥(`poller:chain_context` — 월물리스트·KIS
  최종거래일·선물 순서). poller 문맥이 없으면 마스터 + 캘린더 계산 최종거래일로 대신하고 health
  (`ws_context_fallback`). 5초마다 Redis 를 다시 본다(읽기는 스레드에서, 적용은 루프에서)
- ATM 기준가: 받은 선물 체결가(`LatestFuturesPrice`)
- 받은 것은 이벤트 루프를 막지 않게 큐(`Outbox`)로 작업 스레드(`GatewayWorker`)에 넘긴다:
  - 원문 → `ws.raw` 발행(recorder). 받는 쪽이 없거나 Redis 오류면 직접 raw_messages
  - 체결 → `ticks.fut`·`ticks.opt` 발행(`fut_ticks`·`opt_ticks` 행 JSON) + PostgresSink 에 묶어 쓰기
    (500건·0.5초, DB 장애는 디스크 스풀). 옵션 틱의 만기·행사가·콜풋은 마스터에서
  - health → health_events (로그는 곧바로, DB 는 작업 스레드)
  - 큐가 가득 차면(20만 건) 버리고 세어 critical — 조용히 잃지 않는다
  - 멈출 때(SIGTERM) 작업 스레드가 큐를 비우길 `WORKER_JOIN_S`(40초)까지 기다린다 — compose 의
    stop_grace_period(60초)보다 짧게. 그 안에 못 끝내면 남은 큐·묶음 수를 critical 로 남기고 싱크를
    닫지 않는다(스레드가 아직 쓰는 중일 수 있다 — 스풀 잠금은 프로세스가 끝나면 풀린다)
- 체결 시각: 원문 HHMMSS(KST)를 수신 시각에 가장 가까운 날로 푼다(`tick_time`) — 야간 자정 넘김과
  24~30시 표기(미실측, 확인 필요) 모두. 거래일·세션은 수신 시각 태그(클라이언트)
- seq: 클라이언트 수신 순번 + 기동 시각(초) × 10⁶ — 다시 기동해도 (ts, code, seq) 가 겹치지 않는다.
  클라이언트는 재연결마다 순번 하나를 건너뛴다(끊김이 틱 스트림의 순번 공백으로 보인다)
- 닫힌 세션 확인 구독(설계 §6 이중 확인, services/ws_gateway/watch.py `ClosedWatch`): 컨트롤러가
  캘린더상 닫힌 날·밤의 개장 창에 선물 1건을 구독하고, tick 콜백이 그 체결을 보면 health
  `ws_unexpected_open` + session_log `unexpected_open`(큐 → 작업 스레드 → `write_session_log`).
  '닫힘 확인'은 그 구독이 클라이언트 연결 중 서버 쪽 구독(`status().active` — 응답 없는 등록은 된
  것으로 친다)에 창 대부분 있었을 때만(`live_subscriptions`)
- 범위 밖(확인 필요): 웹소켓 파서 실패의 quarantine 적재(원문은 raw_messages 에 남고 health 로
  센다), 재연결 뒤 REST 복구 스냅샷(poller 몫)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import KST, TradingCalendar
from data.kis.master import MasterRow
from data.kis.ws import FuturesTick
from data.store import FuturesTickRecord, OptionTickRecord, SessionLogRecord
from services.auth.health import HealthEvent, HealthSink, Severity
from services.bus import (
    TICKS_FUT,
    TICKS_OPT,
    WS_RAW,
    ChainContextSnapshot,
    encode_envelope,
    publish_or_none,
)
from services.chain_feed import MasterWatcher, apply_context, calendar_context, load_context
from services.poller.context import ChainContext
from services.recorder.envelope import RawEnvelope
from services.runtime import Heartbeater, Tagger, log_event, utcnow
from services.ws_gateway.client import (
    SERVICE,
    KeySource,
    KisWsClient,
    Sleep,
    TickEvent,
    WsConfig,
    default_tagger,
    load_config,
)
from services.ws_gateway.session import (
    ContextChainSource,
    LatestFuturesPrice,
    SubscriptionController,
)
from services.ws_gateway.subscriptions import Subscription
from services.ws_gateway.watch import ClosedWatch

OUTBOX_MAX = 200_000
BATCH_MAX = 500
BATCH_AGE_S = 0.5
SPOOL_EVERY_S = 1.0
CONTEXT_EVERY_S = 5.0
SEQ_SCALE = 1_000_000
WORKER_JOIN_S = 40.0  # compose ws-gateway stop_grace_period(60초)보다 짧게 — SIGKILL 전에 기록

log = logging.getLogger("services.ws_gateway")

TickRecord = FuturesTickRecord | OptionTickRecord


# ── 체결 → 행 ────────────────────────────────────────────────────────────────


def tick_time(hhmmss: str, received_at: datetime) -> datetime:
    """체결 시각 HHMMSS(KST, 24~30시 표기 허용) → UTC. 날짜는 수신 시각에 가장 가까운 날."""
    s = hhmmss.strip()
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"체결 시각 형식이 아니다: {hhmmss!r}")
    h, m, sec = int(s[:2]), int(s[2:4]), int(s[4:])
    if h > 30 or m > 59 or sec > 59:
        raise ValueError(f"체결 시각 범위 밖: {hhmmss!r}")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    rk = received_at.astimezone(KST)
    midnight = datetime(rk.year, rk.month, rk.day, tzinfo=KST)
    base = midnight + timedelta(hours=h, minutes=m, seconds=sec)
    best = min(
        (base + timedelta(days=d) for d in (-1, 0, 1)),
        key=lambda c: abs((c - rk).total_seconds()),
    )
    return best.astimezone(UTC)


def tick_record(
    ev: TickEvent, master_of: Callable[[str], MasterRow | None], seq_base: int = 0
) -> TickRecord | None:
    """틱 이벤트 → 행. 거래일·세션 태그가 없으면(장 밖 수신) None — 원문은 녹화돼 있다."""
    if ev.trade_date is None or ev.session is None:
        return None
    t = ev.tick
    ts = tick_time(t.hhmmss, ev.received_at)
    seq = seq_base + ev.seq
    if isinstance(t, FuturesTick):
        return FuturesTickRecord.from_tick(
            t, ts=ts, trade_date=ev.trade_date, seq=seq, received_at=ev.received_at
        )
    m = master_of(t.code)
    return OptionTickRecord.from_tick(
        t,
        ts=ts,
        trade_date=ev.trade_date,
        seq=seq,
        received_at=ev.received_at,
        master=m if m is not None and m.code == t.code else None,
    )


# ── 큐 ───────────────────────────────────────────────────────────────────────

Item = (
    tuple[Literal["raw"], RawEnvelope]
    | tuple[Literal["tick"], TickEvent]
    | tuple[Literal["health"], HealthEvent]
    | tuple[Literal["session_log"], SessionLogRecord]
)


class Outbox:
    """이벤트 루프 → 작업 스레드. 넣기는 막히지 않는다 — 가득 차면 버리고 센다."""

    def __init__(self, maxsize: int = OUTBOX_MAX) -> None:
        self.q: queue.Queue[Item] = queue.Queue(maxsize)
        self.dropped: Counter[str] = Counter()

    def put(self, item: Item) -> bool:
        try:
            self.q.put_nowait(item)
        except queue.Full:
            self.dropped[item[0]] += 1
            return False
        return True

    def raw(self, env: RawEnvelope) -> None:
        self.put(("raw", env))

    def tick(self, ev: TickEvent) -> None:
        self.put(("tick", ev))

    def session_log(self, rec: SessionLogRecord) -> None:
        self.put(("session_log", rec))


class QueuedHealth:
    """health: 로그는 곧바로(빠름), DB 는 작업 스레드로 (이벤트 루프가 DB 를 기다리지 않게)."""

    def __init__(self, outbox: Outbox, log_sink: HealthSink) -> None:
        self._outbox = outbox
        self._log = log_sink

    def emit(self, event: HealthEvent) -> None:
        try:
            self._log.emit(event)
        except Exception as e:  # 로그 실패가 DB 기록을 막지 않는다
            log_event(log, logging.ERROR, SERVICE, "health_log_failed", error=type(e).__name__)
        self._outbox.put(("health", event))


class GatewayStore(Protocol):
    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None: ...

    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None: ...

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None: ...

    def write_health(self, events: Sequence[Any], *, tagger: Tagger | None = None) -> None: ...

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None: ...

    def flush_spool(self) -> bool: ...


@dataclass
class WorkerStats:
    raw: int = 0
    raw_published: int = 0
    raw_direct: int = 0
    ticks: int = 0
    ticks_written: int = 0
    untagged: int = 0  # 장 밖 수신 — 행을 만들지 않았다(원문은 녹화)
    bad_ticks: int = 0  # 행으로 못 바꾼 틱(시각 형식 등)
    write_failed: int = 0  # 저장하지 못한 행·원문 (health)
    tick_publish_failed: int = 0


class GatewayWorker:
    """큐를 비우며 발행·저장한다. `run(stop)` — stop 뒤에도 큐에 남은 것을 비우고 끝낸다."""

    def __init__(
        self,
        outbox: Outbox,
        redis: Redis,
        store: GatewayStore,
        *,
        master_of: Callable[[str], MasterRow | None],
        tagger: Tagger | None = None,
        seq_base: int = 0,
        batch_max: int = BATCH_MAX,
        batch_age_s: float = BATCH_AGE_S,
        spool_every_s: float = SPOOL_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.outbox = outbox
        self._r = redis
        self.store = store
        self._master_of = master_of
        self._tagger = tagger
        self._seq_base = seq_base
        self._max = batch_max
        self._age = batch_age_s
        self._spool_every = spool_every_s
        self._clock = clock
        self._now = now
        self.stats = WorkerStats()
        self._fut: list[FuturesTickRecord] = []
        self._opt: list[OptionTickRecord] = []
        self._raw: list[RawEnvelope] = []
        self._health: list[HealthEvent] = []
        self._session_log: list[SessionLogRecord] = []
        self._first: float | None = None
        self._next_spool = 0.0
        self._reported_drops = 0
        self._last_emit: dict[str, float] = {}

    # ── 루프 ──

    def run(self, stop: threading.Event, *, poll_s: float = 0.1) -> None:
        while True:
            try:
                item = self.outbox.q.get(timeout=poll_s)
            except queue.Empty:
                if stop.is_set():
                    break
                item = None
            try:  # 한 건의 결함이 작업 스레드를 죽이지 않게 (죽으면 큐만 찬다)
                if item is not None:
                    self.handle(item)
                self.maybe_flush()
            except Exception as e:
                self._report("ws_worker_error", f"작업 스레드 오류: {type(e).__name__}", "critical")
        try:
            self.flush()
        finally:
            self._flush_spool()

    def handle(self, item: Item) -> None:
        kind, obj = item
        if kind == "raw" and isinstance(obj, RawEnvelope):
            self._on_raw(obj)
        elif kind == "tick" and isinstance(obj, TickEvent):
            self._on_tick(obj)
        elif isinstance(obj, SessionLogRecord):
            self._session_log.append(obj)
            self._touch()
        elif isinstance(obj, HealthEvent):
            self._health.append(obj)
            self._touch()

    def _touch(self) -> None:
        if self._first is None:
            self._first = self._clock()

    def _on_raw(self, env: RawEnvelope) -> None:
        self.stats.raw += 1
        if publish_or_none(self._r, WS_RAW, encode_envelope(env)):
            self.stats.raw_published += 1
            return
        self._raw.append(env)
        self._touch()

    def _on_tick(self, ev: TickEvent) -> None:
        self.stats.ticks += 1
        try:
            rec = tick_record(ev, self._master_of, self._seq_base)
        except (ValueError, ValidationError) as e:
            self.stats.bad_ticks += 1
            self._report("ws_tick_invalid", f"틱을 행으로 못 바꿨다: {type(e).__name__}", "warning")
            return
        if rec is None:
            self.stats.untagged += 1
            return
        channel = TICKS_FUT if isinstance(rec, FuturesTickRecord) else TICKS_OPT
        if publish_or_none(self._r, channel, rec.model_dump_json()) is None:
            self.stats.tick_publish_failed += 1
        if isinstance(rec, FuturesTickRecord):
            self._fut.append(rec)
        else:
            self._opt.append(rec)
        self._touch()

    def pending(self) -> int:
        return (
            len(self._fut)
            + len(self._opt)
            + len(self._raw)
            + len(self._health)
            + len(self._session_log)
        )

    def maybe_flush(self) -> None:
        if self._first is not None and (
            self.pending() >= self._max or self._clock() - self._first >= self._age
        ):
            self.flush()
        if self._clock() >= self._next_spool:
            self._next_spool = self._clock() + self._spool_every
            self._flush_spool()
            self._check_drops()

    def flush(self) -> None:
        fut, self._fut = self._fut, []
        opt, self._opt = self._opt, []
        raw, self._raw = self._raw, []
        if fut:
            self._write("fut_ticks", fut, self.store.write_fut_ticks)
        if opt:
            self._write("opt_ticks", opt, self.store.write_opt_ticks)
        if raw:
            self.stats.raw_direct += len(raw)
            self._write("raw_messages", raw, self.store.write_raw)
        logs, self._session_log = self._session_log, []
        if logs:
            self._write("session_log", logs, self.store.write_session_log)
        health, self._health = self._health, []  # 위 쓰기에서 난 health 까지
        self._first = None
        if health:
            try:
                self.store.write_health(health, tagger=self._tagger)
            except Exception as e:
                err = type(e).__name__
                log_event(log, logging.ERROR, SERVICE, "health_write_failed", error=err)

    def _write[R](self, table: str, rows: list[R], write: Callable[[Sequence[R]], None]) -> None:
        """묶음이 거부되면(데이터 오류) 한 건씩 — 한 건 때문에 묶음 전체를 잃지 않게."""
        try:
            write(rows)
            if table in ("fut_ticks", "opt_ticks"):
                self.stats.ticks_written += len(rows)
            return
        except Exception as e:
            first = type(e).__name__
        lost = 0
        for r in rows:
            try:
                write([r])
                if table in ("fut_ticks", "opt_ticks"):
                    self.stats.ticks_written += 1
            except Exception:
                lost += 1
        if lost:
            self.stats.write_failed += lost
            self._report(
                "ws_write_failed",
                f"{table} {lost}건을 저장하지 못했다 ({first}) — 누적 {self.stats.write_failed}건",
                "critical",
                every=0,
            )

    def _flush_spool(self) -> None:
        try:
            self.store.flush_spool()
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "spool_flush_failed", error=type(e).__name__)

    def _check_drops(self) -> None:
        total = sum(self.outbox.dropped.values())
        if total > self._reported_drops:
            new = total - self._reported_drops
            self._reported_drops = total
            detail = f"큐가 가득 차 {new}건을 버렸다 (누적 {dict(self.outbox.dropped)})"
            self._report("ws_outbox_overflow", detail, "critical", every=0)

    def _report(self, kind: str, detail: str, severity: Severity, *, every: float = 60) -> None:
        t = self._clock()
        last = self._last_emit.get(kind)
        if every and last is not None and t - last < every:
            return
        self._last_emit[kind] = t
        ev = HealthEvent(kind, detail, self._now(), severity, service=SERVICE)
        log_event(log, logging.WARNING, SERVICE, kind, detail=detail)
        self._health.append(ev)
        self._touch()


# ── 체인 문맥 ────────────────────────────────────────────────────────────────


class GatewayContext:
    """ws-gateway 의 ChainContext — Redis 마스터 + poller 문맥(없으면 캘린더)."""

    def __init__(
        self,
        redis: Redis,
        calendar: TradingCalendar,
        health: HealthSink,
        *,
        masters: MasterWatcher | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._r = redis
        self._cal = calendar
        self._health = health
        self._masters = masters if masters is not None else MasterWatcher(redis, SERVICE)
        self._now = now
        self.ctx = ChainContext()
        self.source: Literal["none", "poller", "calendar"] = "none"
        self._codes: dict[str, MasterRow] = {}

    def fetch(self, *, force: bool = False) -> tuple[list[MasterRow] | None, Any]:
        """Redis 읽기(스레드에서). (새 마스터 또는 None, poller 문맥 또는 None·오류)."""
        rows = self._masters.poll(force=force)
        try:
            snap: Any = load_context(self._r)
        except RedisError as e:
            snap = e
        return rows, snap

    def apply(self, fetched: tuple[list[MasterRow] | None, Any]) -> None:
        """루프 스레드에서 적용."""
        rows, snap = fetched
        if rows is not None:
            self.ctx.set_master(rows)
            self._codes = {r.code: r for r in rows}
        if isinstance(snap, ChainContextSnapshot):
            apply_context(self.ctx, snap)
            if self.source != "poller":
                self._emit("ws_context_poller", "poller 체인 문맥을 쓴다", "info")
            self.source = "poller"
        elif isinstance(snap, RedisError):
            return  # 지금 문맥을 둔다
        elif self.ctx.master and self.source != "poller":
            n = calendar_context(self.ctx, self._cal)
            if self.source != "calendar":
                self._emit(
                    "ws_context_fallback",
                    f"poller 체인 문맥이 없다 — 마스터 + 캘린더 최종거래일로 대신({n}시리즈)",
                    "warning",
                )
            self.source = "calendar"

    def master_of(self, code: str) -> MasterRow | None:
        return self._codes.get(code)

    def _emit(self, kind: str, detail: str, severity: Severity) -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, self._now(), severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)


# ── 조립 ─────────────────────────────────────────────────────────────────────


def live_subscriptions(client: KisWsClient) -> Callable[[], frozenset[Subscription]]:
    """지금 서버에 걸려 있다고 보는 구독 — 연결이 끊겼으면 빈 집합(닫힌 세션 확인 구독 판정용)."""

    def active() -> frozenset[Subscription]:
        st = client.status()
        return st.active if st.connected else frozenset()

    return active


@dataclass
class Gateway:
    client: KisWsClient
    controller: SubscriptionController
    context: GatewayContext
    worker: GatewayWorker
    outbox: Outbox
    price: LatestFuturesPrice
    watch: ClosedWatch | None = None


def build_gateway(
    *,
    url: str,
    key_source: KeySource,
    redis: Redis,
    store: GatewayStore,
    calendar: TradingCalendar,
    log_health: HealthSink,
    now: Callable[[], datetime] = utcnow,
    config: WsConfig | None = None,
    sleep: Sleep = asyncio.sleep,
    proxy: str | Literal[True] | None = True,
    seq_base: int | None = None,
    outbox_max: int = OUTBOX_MAX,
    worker_clock: Callable[[], float] = time.monotonic,
) -> Gateway:
    outbox = Outbox(outbox_max)
    health = QueuedHealth(outbox, log_health)
    context = GatewayContext(redis, calendar, health, now=now)
    price = LatestFuturesPrice()
    cfg = config if config is not None else load_config()
    watch = ClosedWatch(calendar, health, outbox.session_log, grace_s=cfg.client.late_grace_s)

    def on_tick(ev: TickEvent) -> None:
        price.observe(ev)
        watch.observe(ev)
        outbox.tick(ev)

    client = KisWsClient(
        url,
        key_source,
        on_raw=outbox.raw,
        on_tick=on_tick,
        tagger=default_tagger(calendar),
        health=health,
        config=cfg,
        now=now,
        sleep=sleep,
        proxy=proxy,
    )
    controller = SubscriptionController(
        client,
        ContextChainSource(context.ctx, calendar),
        price,
        health=health,
        calendar=calendar,
        grace_s=cfg.client.late_grace_s,
        watch=watch,
        active=live_subscriptions(client),
    )
    base = int(time.time()) * SEQ_SCALE if seq_base is None else seq_base
    worker = GatewayWorker(
        outbox,
        redis,
        store,
        master_of=context.master_of,
        tagger=default_tagger(calendar),
        seq_base=base,
        clock=worker_clock,
        now=now,
    )
    return Gateway(client, controller, context, worker, outbox, price, watch)


async def run_gateway(
    gw: Gateway,
    stop: asyncio.Event,
    *,
    now: Callable[[], datetime] = utcnow,
    heartbeat: Heartbeater | None = None,
    context_every_s: float = CONTEXT_EVERY_S,
    controller_tick_s: float = 1.0,
    worker_join_s: float = WORKER_JOIN_S,
) -> bool:
    """stop 까지 세션·컨트롤러·문맥 갱신을 돌리고, 끝나면 작업 스레드가 큐를 비우길 기다린다.

    작업 스레드가 worker_join_s 안에 끝났으면 True. 못 끝냈으면 남은 큐·묶음 수를 critical 로
    남기고 False — 호출자는 그때 싱크를 닫지 않는다.
    """
    wstop = threading.Event()
    wthread = threading.Thread(target=gw.worker.run, args=(wstop,), name="ws-worker", daemon=True)
    wthread.start()
    gw.context.apply(await asyncio.to_thread(gw.context.fetch, force=True))

    async def refresher() -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), context_every_s)
            if stop.is_set():
                break
            try:
                gw.context.apply(await asyncio.to_thread(gw.context.fetch))
                if heartbeat is not None:
                    st = gw.client.status()
                    await asyncio.to_thread(
                        heartbeat.beat,
                        connected=st.connected,
                        active=len(st.active),
                        pacing_s=st.pacing_s,
                        queue=gw.outbox.q.qsize(),
                        dropped=sum(gw.outbox.dropped.values()),
                        context=gw.context.source,
                    )
            except Exception as e:  # 문맥 갱신 실패가 세션을 멈추지 않는다
                err = type(e).__name__
                log_event(log, logging.ERROR, SERVICE, "context_refresh_failed", error=err)

    if heartbeat is not None:
        await asyncio.to_thread(heartbeat.beat, force=True, connected=False)
    log_event(log, logging.INFO, SERVICE, "started", context=gw.context.source)
    try:
        await asyncio.gather(
            gw.client.run(stop),
            gw.controller.run(stop, now=now, tick_s=controller_tick_s),
            refresher(),
        )
    finally:
        wstop.set()
        await asyncio.to_thread(wthread.join, worker_join_s)
        s = gw.worker.stats
        finished = not wthread.is_alive()
        if not finished:
            log_event(
                log,
                logging.CRITICAL,
                SERVICE,
                "ws_worker_unfinished",
                detail=f"작업 스레드가 {worker_join_s:g}초 안에 끝나지 않았다 — 남은 것은 잃는다",
                join_s=worker_join_s,
                queue=gw.outbox.q.qsize(),
                pending=gw.worker.pending(),
                dropped=sum(gw.outbox.dropped.values()),
            )
        log_event(log, logging.INFO, SERVICE, "stopped", ticks=s.ticks, written=s.ticks_written)
    return finished


def main() -> int:  # pragma: no cover — compose 진입점 (.env·실제 Redis·DB·KIS 웹소켓)
    import signal

    from config.settings import Settings
    from data.store import PostgresSink
    from services.auth.health import LogHealthSink
    from services.runtime import connect_redis, setup_logging, tagger_for
    from services.ws_gateway.client import redis_key_source

    setup_logging()
    settings = Settings()
    if not settings.redis_url or settings.kis_app_key is None:
        log_event(log, logging.ERROR, SERVICE, "config_error", error="REDIS_URL·KIS_APP_KEY 필요")
        return 2
    cal = TradingCalendar.default()
    redis = connect_redis(settings.redis_url)
    store = PostgresSink.from_settings(service=SERVICE, spool=True, tagger=tagger_for(cal))
    cfg = load_config()

    async def amain() -> bool:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        gw = build_gateway(
            url=cfg.url_for(settings.kis_env),
            key_source=redis_key_source(redis, settings),
            redis=redis,
            store=store,
            calendar=cal,
            log_health=LogHealthSink(tagger=tagger_for(cal)),
            config=cfg,
        )
        return await run_gateway(gw, stop, heartbeat=Heartbeater(redis, SERVICE))

    finished = False
    try:
        finished = asyncio.run(amain())
    finally:
        if (
            finished
        ):  # 작업 스레드가 아직 쓰는 중이면 닫지 않는다 (run_gateway 가 critical 로 남겼다)
            store.close()
    return 0
