"""poller 서비스 조립 (services/poller/service.py·services/chain_feed.py, 설계 §2·§3·§5).

가짜 KIS 서버(httpx.MockTransport)·fakeredis·가짜 시계만 쓴다. 실제 KIS·Redis·DB 는 부르지 않는다.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from core.calendar import TradingCalendar
from data.kis.ratelimit import LocalRateLimiter
from services.bus import (
    CHAIN_CONTEXT_KEY,
    MASTER_KEY,
    MASTER_SHA_KEY,
    REST_RAW,
    MasterSnapshot,
    decode_envelope,
    heartbeat_key,
)
from services.chain_feed import (
    ContextPublisher,
    MasterWatcher,
    apply_context,
    calendar_context,
    context_snapshot,
    load_context,
)
from services.poller.collector import Collector
from services.poller.context import ChainContext, ExpiryInfo, Series
from services.poller.service import PollerService, RawFanout, StopClock, Stopped
from services.poller.sink import InMemorySink
from services.runtime import Heartbeater
from tests.fakes.kis_server import FakeClock, FakeKisServer, default_chain, make_client

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
T0 = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
CHAIN = default_chain()
MASTER_TEXT = "\n".join(CHAIN.master_lines()) + "\n"


def seed_master(r: Any, text: str = MASTER_TEXT, at: datetime = T0) -> MasterSnapshot:
    snap = MasterSnapshot(
        asof=at,
        trade_date=date(2026, 9, 28),
        session="day",
        rows=len(text.splitlines()),
        sha256=MasterSnapshot.digest(text),
        text=text,
    )
    r.set(MASTER_KEY, snap.model_dump_json())
    r.set(MASTER_SHA_KEY, snap.sha256)
    return snap


# ── chain_feed ──


def test_master_watcher_loads_only_when_the_sha_changes() -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    t = [0.0]
    w = MasterWatcher(r, "poller", every_s=30, clock=lambda: t[0])
    assert w.poll() is None  # 아직 없다
    seed_master(r)
    assert w.poll() is None  # 30초 안
    t[0] = 31
    rows = w.poll()
    assert rows is not None and len(rows) == len(CHAIN.master_lines())
    t[0] = 62
    assert w.poll() is None  # sha 그대로
    seed_master(r, MASTER_TEXT + "1|A01999|KR4A01999|F 202703| |00000.00|3|2001|KOSPI200\n")
    t[0] = 93
    rows2 = w.poll()
    assert rows2 is not None and len(rows2) == len(rows) + 1
    server.connected = False
    assert w.poll(force=True) is None  # Redis 오류 — 직전 것을 둔다


def test_master_watcher_refuses_a_snapshot_whose_sha_does_not_match() -> None:
    r = fakeredis.FakeRedis()
    snap = seed_master(r)
    r.set(MASTER_KEY, snap.model_copy(update={"text": "tampered"}).model_dump_json())
    w = MasterWatcher(r, "poller")
    assert w.poll(force=True) is None and w.sha is None


def test_context_round_trip_and_calendar_fallback() -> None:
    ctx = ChainContext(CHAIN.master_rows())
    ctx.set_listed("WKM", ["260904", "261001"])
    ctx.set_listed("", ["202610"])
    ctx.set_expiry(Series("WKM", "260904"), ExpiryInfo(date(2026, 9, 28), "kis"))
    ctx.set_futures_codes(["A01612", "A01703"])
    snap = context_snapshot(ctx, T0)
    other = ChainContext(CHAIN.master_rows())
    apply_context(other, snap)
    assert other.listed == ctx.listed and other.expiries == ctx.expiries
    assert other.futures_codes == ("A01612", "A01703")

    fallback = ChainContext(CHAIN.master_rows())
    n = calendar_context(fallback, CAL)
    assert n >= 4 and set(fallback.listed) == {"", "WKM", "WKI"}
    assert fallback.expiries[Series("WKM", "260904")] == ExpiryInfo(date(2026, 9, 28), "calendar")
    assert fallback.targets(T0) is not None


def test_context_publisher_waits_for_the_option_list_and_throttles() -> None:
    r = fakeredis.FakeRedis()
    t = [0.0]
    pub = ContextPublisher(r, every_s=5, refresh_s=60, clock=lambda: t[0])
    ctx = ChainContext()
    assert pub.publish(ctx, T0) is False and load_context(r) is None  # 빈 문맥으로 덮지 않는다
    ctx.set_listed("", ["202610"])
    assert pub.publish(ctx, T0) is True
    ctx.set_listed("WKM", ["260904"])
    t[0] = 2
    assert pub.publish(ctx, T0) is False  # 5초 안
    t[0] = 6
    assert pub.publish(ctx, T0) is True
    t[0] = 20
    assert pub.publish(ctx, T0) is False  # 그대로면 60초마다
    t[0] = 70
    assert pub.publish(ctx, T0) is True
    got = load_context(r)
    assert got is not None and got.listed == {"": ["202610"], "WKM": ["260904"]}
    r.set(CHAIN_CONTEXT_KEY, b"{broken")
    assert load_context(r) is None


# ── 원문 발행 ──


def _env(i: int) -> Any:
    from services.recorder.envelope import RawEnvelope

    return RawEnvelope(
        received_at=T0 + timedelta(seconds=i),
        source="kis_rest",
        tr_id="FHPIF05030100",
        key=f"k{i}",
        payload={"i": i},
        trade_date=date(2026, 9, 28),
        session="day",
    )


def test_raw_goes_to_the_channel_or_directly_when_nobody_listens() -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    inner = InMemorySink()
    fan = RawFanout(inner, r)
    fan.write_raw([_env(0)])
    assert [e.key for e in inner.raw] == ["k0"] and fan.direct == 1  # 받는 쪽 없음
    sub = r.pubsub(ignore_subscribe_messages=True)
    sub.subscribe(REST_RAW)
    fan.write_raw([_env(1), _env(2)])
    assert len(inner.raw) == 1 and fan.published == 2
    sub.get_message(timeout=0.01)
    msg = sub.get_message(timeout=0.01)
    assert msg is not None and decode_envelope(msg["data"]).key == "k1"
    server.connected = False
    fan.write_raw([_env(3)])
    assert [e.key for e in inner.raw] == ["k0", "k3"]  # Redis 오류 → 직접


# ── 서비스 루프 ──


class Rig:
    def __init__(self, start: datetime = T0, *, subscribe: bool = False) -> None:
        self.clock = FakeClock(start)
        self.kis_server = FakeKisServer(self.clock, CHAIN)
        self.redis = fakeredis.FakeRedis(server=fakeredis.FakeServer())
        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        if subscribe:
            self.sub.subscribe(REST_RAW)
        self.inner = InMemorySink()
        self.sink = RawFanout(self.inner, self.redis)
        kis = make_client(self.kis_server, LocalRateLimiter(clock=self.clock))
        self.collector = Collector(kis, self.sink, calendar=CAL, clock=self.clock)
        mono = lambda: self.clock.now_us() / 1e6  # noqa: E731
        self.flushes = 0
        self.svc = PollerService(
            self.collector,
            masters=MasterWatcher(self.redis, "poller", clock=mono),
            context=ContextPublisher(self.redis, clock=mono),
            heartbeat=Heartbeater(self.redis, "poller", now=self.clock.now),
            flush_spool=self._flush,
        )

    def _flush(self) -> bool:
        self.flushes += 1
        return True


def test_two_minutes_of_day_collection_with_master_from_redis() -> None:
    rig = Rig(subscribe=True)
    seed_master(rig.redis)
    assert rig.collector.ctx.master == ()
    rig.svc.run(threading.Event(), until=T0 + timedelta(minutes=2))
    col = rig.collector
    assert len(col.ctx.master) == len(CHAIN.master_lines())  # scheduler 가 둔 마스터
    assert col.stats.executed["board"] > 0 and col.stats.executed["fill2"] > 0
    assert rig.inner.chain and rig.inner.futures and rig.inner.investor
    assert rig.inner.raw == [] and rig.sink.published > 0  # 원문은 recorder 로
    assert rig.kis_server.max_in_window(1.0) <= 4  # 설계 §3
    ctx = load_context(rig.redis)
    assert ctx is not None and set(ctx.listed) == {"", "WKM", "WKI"}
    assert any(e.source == "kis" for e in ctx.expiries) and ctx.futures_codes[0] == "A01612"
    assert rig.redis.get(heartbeat_key("poller")) is not None
    assert rig.flushes >= 100 and rig.svc.slices >= 100


def test_idle_makes_no_calls_but_keeps_beating() -> None:
    rig = Rig(datetime(2026, 9, 28, 16, 30, tzinfo=KST))  # POST_DAY
    rig.svc.run(threading.Event(), until=datetime(2026, 9, 28, 16, 32, tzinfo=KST))
    assert rig.kis_server.calls == [] and rig.redis.get(heartbeat_key("poller")) is not None


def test_stop_clock_raises_stopped_only_when_stopped() -> None:
    stop = threading.Event()
    clock = StopClock(stop)
    clock.sleep(0.001)
    assert clock.now_us() > 0
    stop.set()
    with pytest.raises(Stopped):
        clock.sleep(10)


def test_stopped_breaks_out_of_the_collector_loop() -> None:
    rig = Rig()
    calls = [0]
    orig = rig.clock.sleep

    def sleep(s: float, /) -> None:
        calls[0] += 1
        if calls[0] > 20:
            raise Stopped
        orig(s)

    rig.clock.sleep = sleep  # type: ignore[method-assign]
    rig.svc.run(threading.Event(), until=T0 + timedelta(hours=1))
    assert rig.clock.now() < T0 + timedelta(minutes=5)  # 한 시간을 다 돌지 않았다


def test_collector_errors_do_not_kill_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()
    stop = threading.Event()
    n = [0]

    def boom(_end: datetime) -> None:
        n[0] += 1
        if n[0] >= 3:
            stop.set()
        raise RuntimeError("bug")

    monkeypatch.setattr(rig.collector, "run_until", boom)
    monkeypatch.setattr(stop, "wait", lambda _t=None: stop.is_set())
    rig.svc.run(stop)
    assert n[0] == 3


def test_side_work_errors_do_not_stop_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()
    seed_master(rig.redis)

    def boom() -> bool:
        raise RuntimeError("spool bug")

    rig.svc._flush = boom
    monkeypatch.setattr(rig.svc._context, "publish", lambda *_a: 1 / 0)
    rig.svc.run(threading.Event(), until=T0 + timedelta(seconds=30))
    assert rig.collector.stats.executed["board"] > 0
