"""ws-gateway 서비스 조립 (services/ws_gateway/service.py, PLAN §4.1·§4.3, 설계 §2·§4).

가짜 KIS 웹소켓 서버(127.0.0.1 임의 포트)·fakeredis·메모리 저장소만 쓴다. 프레임은 합성(SYNTHETIC).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Coroutine, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest
from pydantic import SecretStr

from config.settings import Settings
from core.calendar import TradingCalendar
from data.kis.auth_client import RedisTokenCache, TokenRecord, token_owner, utcnow
from data.kis.master import MasterRow, series_for
from data.kis.ws import FuturesTick, OptionTick
from data.store import FuturesTickRecord, OptionTickRecord, SessionLogRecord, StoreError
from services.auth.health import HealthEvent, MemoryHealthSink
from services.auth.service import WS_KEY_KEY
from services.bus import (
    MASTER_KEY,
    MASTER_SHA_KEY,
    TICKS_FUT,
    TICKS_OPT,
    WS_RAW,
    MasterSnapshot,
    heartbeat_key,
)
from services.chain_feed import ContextPublisher
from services.poller.context import ChainContext, ExpiryInfo, Series
from services.recorder.envelope import RawEnvelope
from services.runtime import Heartbeater
from services.ws_gateway.client import TickEvent
from services.ws_gateway.service import (
    GatewayContext,
    GatewayWorker,
    Outbox,
    build_gateway,
    run_gateway,
    tick_record,
    tick_time,
)
from tests.fakes.kis_server import default_chain
from tests.fakes.ws_server import FakeKisWsServer

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
APP_KEY = "PSappKEYforGatewayTests01"
WS_KEY = "approval-key-for-gateway-tests"
DAY_NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST 월 — DAY
CHAIN = default_chain()
MASTER_TEXT = "\n".join(CHAIN.master_lines()) + "\n"
ROWS = CHAIN.master_rows()


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


# ── 체결 시각·행 ──


@pytest.mark.parametrize(
    ("hhmmss", "received", "want"),
    [
        ("093001", kst(2026, 9, 28, 9, 30, 2), kst(2026, 9, 28, 9, 30, 1)),
        ("235959", kst(2026, 9, 29, 0, 0, 1), kst(2026, 9, 28, 23, 59, 59)),  # 자정 넘어 도착
        ("001004", kst(2026, 9, 29, 0, 10, 5), kst(2026, 9, 29, 0, 10, 4)),  # 야간 새벽분
        ("243000", kst(2026, 9, 29, 0, 30, 1), kst(2026, 9, 29, 0, 30, 0)),  # 24~30시 표기
        ("054959", kst(2026, 9, 19, 5, 50, 0), kst(2026, 9, 19, 5, 49, 59)),  # 금요일 밤 토 새벽
    ],
)
def test_tick_time_picks_the_day_nearest_to_arrival(
    hhmmss: str, received: datetime, want: datetime
) -> None:
    got = tick_time(hhmmss, received)
    assert got == want and got.utcoffset() == timedelta(0)


@pytest.mark.parametrize("bad", ["9300", "093060", "310000", "ab0000", ""])
def test_tick_time_rejects_bad_values(bad: str) -> None:
    with pytest.raises(ValueError):
        tick_time(bad, DAY_NOW)


def _fut_tick(price: str = "1100.05", hhmmss: str = "093000") -> FuturesTick:
    return FuturesTick.model_validate(
        {
            "tr_id": "H0IFCNT0",
            "session": "day",
            "bsop_hour": hhmmss,
            "futs_shrn_iscd": "A01612",
            "futs_prpr": price,
            "last_cnqn": "2",
            "seln_cntg_smtn": "100",
            "shnu_cntg_smtn": "120",
        }
    )


def _opt_code(strike: str = "1100.0", cp: str = "C") -> MasterRow:
    rows = series_for(ROWS, "kospi200_weekly_mon", "260904", cp)  # type: ignore[arg-type]
    return next(r for r in rows if r.strike == Decimal(strike))


def _opt_tick(code: str, hhmmss: str = "093001") -> OptionTick:
    return OptionTick.model_validate(
        {
            "tr_id": "H0IOCNT0",
            "session": "day",
            "bsop_hour": hhmmss,
            "optn_shrn_iscd": code,
            "optn_prpr": "5.20",
            "hts_ints_vltl": "0",
        }
    )


def test_tick_record_uses_the_event_tag_master_and_seq_base() -> None:
    ev = TickEvent(_fut_tick(), 7, DAY_NOW, date(2026, 9, 28), "day")
    rec = tick_record(ev, lambda _c: None, seq_base=5_000_000)
    assert isinstance(rec, FuturesTickRecord)
    assert (rec.ts, rec.trade_date, rec.session, rec.seq) == (
        kst(2026, 9, 28, 9, 30),
        date(2026, 9, 28),
        "day",
        5_000_007,
    )
    assert (rec.cum_buy_qty, rec.cum_sell_qty) == (120, 100)
    m = _opt_code()
    by_code = {r.code: r for r in ROWS}
    d28 = date(2026, 9, 28)
    opt = tick_record(TickEvent(_opt_tick(m.code), 8, DAY_NOW, d28, "day"), by_code.get)
    assert isinstance(opt, OptionTickRecord)
    assert (opt.mrkt_cls, opt.expiry, opt.strike, opt.cp, opt.iv) == (
        "WKM",
        "260904",
        Decimal("1100.0"),
        "C",
        None,
    )
    unknown = tick_record(TickEvent(_opt_tick("ZZZ"), 9, DAY_NOW, d28, "day"), by_code.get)
    assert isinstance(unknown, OptionTickRecord) and unknown.expiry is None
    assert tick_record(TickEvent(_fut_tick(), 1, DAY_NOW, None, None), by_code.get) is None


# ── 작업 스레드 ──


class MemStore:
    def __init__(self) -> None:
        self.fut: list[FuturesTickRecord] = []
        self.opt: list[OptionTickRecord] = []
        self.raw: list[RawEnvelope] = []
        self.health: list[Any] = []
        self.session_log: list[SessionLogRecord] = []
        self.flushes = 0
        self.reject_seq: set[int] = set()
        self.lock = threading.Lock()

    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None:
        if any(r.seq in self.reject_seq for r in rows):
            raise StoreError("fut_ticks: CheckViolation")
        with self.lock:
            self.fut.extend(rows)

    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None:
        with self.lock:
            self.opt.extend(rows)

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        with self.lock:
            self.raw.extend(envelopes)

    def write_health(self, events: Sequence[Any], *, tagger: Any = None) -> None:
        with self.lock:
            self.health.extend(events)

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        with self.lock:
            self.session_log.extend(rows)

    def flush_spool(self) -> bool:
        self.flushes += 1
        return True


class Tick:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _raw(i: int) -> RawEnvelope:
    return RawEnvelope(
        received_at=DAY_NOW,
        source="kis_ws",
        tr_id="H0IFCNT0",
        key="A01612",
        payload=f"0|H0IFCNT0|001|A01612^{i}",
        trade_date=date(2026, 9, 28),
        session="day",
    )


def test_worker_publishes_raw_or_writes_it_when_nobody_listens() -> None:
    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    store = MemStore()
    clock = Tick()
    w = GatewayWorker(Outbox(), r, store, master_of=lambda _c: None, clock=clock)
    w.handle(("raw", _raw(0)))
    sub = r.pubsub(ignore_subscribe_messages=True)
    sub.subscribe(WS_RAW)
    w.handle(("raw", _raw(1)))
    clock.t = 1.0
    w.maybe_flush()
    assert [e.payload for e in store.raw] == [_raw(0).payload]
    assert (w.stats.raw_published, w.stats.raw_direct) == (1, 1)


def test_worker_batches_ticks_publishes_them_and_isolates_a_bad_row() -> None:
    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    sub = r.pubsub(ignore_subscribe_messages=True)
    sub.subscribe(TICKS_FUT, TICKS_OPT)
    sub.get_message(timeout=0.01)
    sub.get_message(timeout=0.01)
    store = MemStore()
    store.reject_seq = {2}
    clock = Tick()
    w = GatewayWorker(
        Outbox(), r, store, master_of={x.code: x for x in ROWS}.get, clock=clock, batch_max=500
    )
    for seq in (1, 2, 3):
        w.handle(("tick", TickEvent(_fut_tick(), seq, DAY_NOW, date(2026, 9, 28), "day")))
    w.handle(("tick", TickEvent(_opt_tick(_opt_code().code), 4, DAY_NOW, date(2026, 9, 28), "day")))
    w.handle(("tick", TickEvent(_fut_tick(), 5, DAY_NOW, None, None)))  # 장 밖 — 행 없음
    w.maybe_flush()
    assert store.fut == []  # 0.5초 전
    clock.t = 0.6
    w.maybe_flush()
    assert [x.seq for x in store.fut] == [1, 3] and len(store.opt) == 1
    assert (w.stats.ticks, w.stats.ticks_written, w.stats.write_failed, w.stats.untagged) == (
        5,
        3,
        1,
        1,
    )
    kinds = [e.kind for e in store.health]
    assert kinds == ["ws_write_failed"]
    msgs = [sub.get_message(timeout=0.01) for _ in range(4)]
    chans = [m["channel"] for m in msgs if m is not None]
    assert chans.count(TICKS_FUT.encode()) == 3 and chans.count(TICKS_OPT.encode()) == 1
    first = json.loads(next(m for m in msgs if m is not None)["data"])
    assert first["code"] == "A01612" and first["trade_date"] == "2026-09-28"


def test_outbox_overflow_is_counted_and_reported() -> None:
    r = fakeredis.FakeRedis()
    box = Outbox(maxsize=2)
    assert box.put(("raw", _raw(0))) and box.put(("raw", _raw(1)))
    assert box.put(("raw", _raw(2))) is False and box.dropped["raw"] == 1
    store = MemStore()
    w = GatewayWorker(box, r, store, master_of=lambda _c: None, clock=Tick())
    stop = threading.Event()
    stop.set()
    w.run(stop, poll_s=0.01)  # stop 이어도 큐를 비우고 끝낸다
    assert len(store.raw) == 2
    (ev,) = [e for e in store.health if e.kind == "ws_outbox_overflow"]
    assert ev.severity == "critical" and "1건" in ev.detail


# ── 체인 문맥 ──


def _seed_master(r: Any) -> None:
    snap = MasterSnapshot(
        asof=DAY_NOW,
        trade_date=date(2026, 9, 28),
        session="day",
        rows=len(ROWS),
        sha256=MasterSnapshot.digest(MASTER_TEXT),
        text=MASTER_TEXT,
    )
    r.set(MASTER_KEY, snap.model_dump_json())
    r.set(MASTER_SHA_KEY, snap.sha256)


def test_context_prefers_poller_and_falls_back_to_the_calendar() -> None:
    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    health = MemoryHealthSink()
    gc = GatewayContext(r, CAL, health, now=lambda: DAY_NOW)
    gc.apply(gc.fetch(force=True))
    assert gc.source == "none" and not health.events  # 아직 마스터도 없다
    _seed_master(r)
    gc.apply(gc.fetch(force=True))
    assert gc.source == "calendar" and health.kinds() == ["ws_context_fallback"]
    assert gc.master_of(_opt_code().code) == _opt_code()
    poller = ChainContext(ROWS)
    poller.set_listed("WKM", ["261001"])
    poller.set_expiry(Series("WKM", "261001"), ExpiryInfo(date(2026, 10, 6), "kis"))
    ContextPublisher(r).publish(poller, DAY_NOW)
    gc.apply(gc.fetch())
    assert gc.source == "poller" and health.kinds()[-1] == "ws_context_poller"
    assert gc.ctx.expiries[Series("WKM", "261001")].source == "kis"


# ── 끝에서 끝 (가짜 웹소켓 서버) ──


def run[T](coro: Coroutine[Any, Any, T], timeout: float = 20.0) -> T:
    return asyncio.run(asyncio.wait_for(coro, timeout))


def settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr("SECRETforGatewayTests987654"),
    )


def seed_ws_key(r: Any) -> None:
    now = utcnow()
    rec = TokenRecord(
        access_token=SecretStr(WS_KEY),
        expires_at=now + timedelta(hours=12),
        issued_at=now,
        owner=token_owner(settings().kis_base, APP_KEY),
    )
    RedisTokenCache(r, WS_KEY_KEY).store(rec, now)


async def fast_sleep(_s: float) -> None:
    await asyncio.sleep(0.001)


async def _until(pred: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "시간 초과"
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("recorder_listens", [True, False])
def test_gateway_end_to_end_day_session(recorder_listens: bool) -> None:
    from services.ws_gateway.client import redis_key_source

    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    _seed_master(r)
    seed_ws_key(r)
    sub = r.pubsub(ignore_subscribe_messages=True)
    sub.subscribe(TICKS_FUT, *([WS_RAW] if recorder_listens else []))
    store = MemStore()
    log_health = MemoryHealthSink()
    atm = _opt_code("1100.0", "C")

    async def body() -> None:
        async with FakeKisWsServer(approval_key=WS_KEY) as srv:
            gw = build_gateway(
                url=srv.url,
                key_source=redis_key_source(r, settings()),
                redis=r,
                store=store,
                calendar=CAL,
                log_health=log_health,
                now=lambda: DAY_NOW,
                sleep=fast_sleep,
                proxy=None,
                seq_base=0,
            )
            stop = asyncio.Event()
            hb = Heartbeater(r, "ws-gateway", now=lambda: DAY_NOW)
            task = asyncio.create_task(
                run_gateway(
                    gw,
                    stop,
                    now=lambda: DAY_NOW,
                    heartbeat=hb,
                    context_every_s=0.02,
                    controller_tick_s=0.01,
                )
            )
            # 가격이 없으면 선물 근월물만 — 캘린더 대체 문맥의 최근접 만기 WKM:260904
            fut_sub = ("H0IFCNT0", "A01612")
            await _until(lambda: bool(srv.connections) and fut_sub in srv.conn().subs)
            assert gw.context.source == "calendar"
            await srv.send_tick(
                "H0IFCNT0", futs_shrn_iscd="A01612", bsop_hour="093000", futs_prpr="1100.40"
            )
            await _until(lambda: len(srv.conn().subs) == 39)  # 선물 1 + ATM±9 × 콜·풋
            assert ("H0IOCNT0", atm.code) in srv.conn().subs
            await srv.send_tick(
                "H0IOCNT0", optn_shrn_iscd=atm.code, bsop_hour="093001", optn_prpr="5.20"
            )
            await _until(lambda: gw.worker.stats.ticks == 2)
            stop.set()
            assert await asyncio.wait_for(task, 10) is True  # 작업 스레드가 다 비웠다
            assert srv.errors == []

    run(body())
    (fut,) = store.fut
    assert (fut.code, fut.price, fut.ts, fut.trade_date) == (
        "A01612",
        Decimal("1100.40"),
        kst(2026, 9, 28, 9, 30),
        date(2026, 9, 28),
    )
    (opt,) = store.opt
    want = (atm.code, "260904", Decimal("1100.0"), "C")
    assert (opt.code, opt.expiry, opt.strike, opt.cp) == want
    got = [sub.get_message(timeout=0.01) for _ in range(20)]
    msgs = [m for m in got if m is not None]
    fut_msgs = [m for m in msgs if m["channel"] == TICKS_FUT.encode()]
    assert len(fut_msgs) == 1 and json.loads(fut_msgs[0]["data"])["price"] == "1100.40"
    raw_frames = [e for e in store.raw if str(e.payload).startswith("0|H0IFCNT0|")]
    if recorder_listens:
        assert raw_frames == [] and _raw_published(msgs) >= 2
    else:
        assert len(raw_frames) == 1 and raw_frames[0].trade_date == date(2026, 9, 28)
    assert r.get(heartbeat_key("ws-gateway")) is not None
    assert "ws_context_fallback" in [e.kind for e in store.health]
    assert all(WS_KEY not in e.detail for e in log_health.events)


def _raw_published(msgs: list[Any]) -> int:
    return sum(1 for m in msgs if m["channel"] == WS_RAW.encode())


def test_health_goes_to_the_log_at_once_and_to_the_store_via_the_worker() -> None:
    from services.ws_gateway.service import QueuedHealth

    box = Outbox()
    log_sink = MemoryHealthSink()
    ev = HealthEvent("ws_backoff", "1초 뒤", DAY_NOW, "info", service="ws-gateway")
    QueuedHealth(box, log_sink).emit(ev)
    assert log_sink.kinds() == ["ws_backoff"] and box.q.qsize() == 1


def test_a_bad_item_does_not_kill_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    r = fakeredis.FakeRedis()
    box = Outbox()
    store = MemStore()
    w = GatewayWorker(box, r, store, master_of=lambda _c: None, clock=Tick())
    calls = [0]
    real = w._on_raw

    def flaky(env: RawEnvelope) -> None:
        calls[0] += 1
        if calls[0] == 1:
            raise KeyError("bug")
        real(env)

    monkeypatch.setattr(w, "_on_raw", flaky)
    box.raw(_raw(0))
    box.raw(_raw(1))
    stop = threading.Event()
    stop.set()
    w.run(stop, poll_s=0.01)
    assert [e.payload for e in store.raw] == [_raw(1).payload]
    assert [e.kind for e in store.health] == ["ws_worker_error"]


# ── 멈출 때: 작업 스레드가 다 비우지 못하면 ──


class StuckStore(MemStore):
    """flush_spool 에서 gate 가 열릴 때까지(최대 5초) 막힌다 — DB·디스크가 느린 경우."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()

    def flush_spool(self) -> bool:
        self.entered.set()
        self.gate.wait(5)
        return True


class IdleClient:
    async def run(self, stop: asyncio.Event) -> None:
        await stop.wait()


class IdleController:
    async def run(self, stop: asyncio.Event, *, now: Any, tick_s: float) -> None:
        await stop.wait()


def test_an_unfinished_worker_at_shutdown_is_reported_with_what_is_left(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """작업 스레드가 join 제한 안에 끝나지 않으면 남은 큐·묶음 수를 critical 로 남기고 False
    (main 은 그때 싱크를 닫지 않는다 — 스레드가 아직 쓰는 중일 수 있다)."""
    from services.ws_gateway.service import Gateway, GatewayContext

    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    box = Outbox()
    store = StuckStore()
    worker = GatewayWorker(box, r, store, master_of=lambda _c: None)
    ctx = GatewayContext(r, CAL, MemoryHealthSink(), now=lambda: DAY_NOW)
    gw = Gateway(
        client=IdleClient(),  # type: ignore[arg-type]
        controller=IdleController(),  # type: ignore[arg-type]
        context=ctx,
        worker=worker,
        outbox=box,
        price=None,  # type: ignore[arg-type]
    )

    async def body() -> bool:
        stop = asyncio.Event()
        task = asyncio.create_task(run_gateway(gw, stop, now=lambda: DAY_NOW, worker_join_s=0.2))
        await _until(store.entered.is_set)
        for i in range(3):
            box.raw(_raw(i))
        stop.set()
        return await task

    try:
        with caplog.at_level(logging.INFO, logger="services.ws_gateway"):
            finished = run(body())
    finally:
        store.gate.set()
    assert finished is False
    logs = [json.loads(m.getMessage()) for m in caplog.records]
    (ev,) = [x for x in logs if x["event"] == "ws_worker_unfinished"]
    assert (ev["queue"], ev["pending"]) == (3, 0)
    rec = next(m for m in caplog.records if "ws_worker_unfinished" in m.getMessage())
    assert rec.levelno == logging.CRITICAL
