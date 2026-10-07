"""ws-gateway 닫힌 세션 확인 구독·예상 밖 개장 (services/ws_gateway/watch.py, 설계 §6 이중 확인).

날짜: 2026-09-23(수 — 다음 날 추석, 그날 밤 없음), 09-24(목 추석 휴장), 09-28(월 정상), 10-02
(금 — 10-05 월 대체공휴일이라 그 밤은 캘린더상 닫힘, 미실측 가정), 09-26(토). 체결 프레임은
합성(SYNTHETIC).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from core.calendar import TradingCalendar
from data.kis.ws import FuturesTick, OptionTick
from data.store import SessionLogRecord
from services.auth.health import MemoryHealthSink
from services.ws_gateway.budget import derive, load
from services.ws_gateway.client import TickEvent
from services.ws_gateway.service import GatewayWorker, Outbox, build_gateway, run_gateway
from services.ws_gateway.session import ChainView, SubscriptionController
from services.ws_gateway.subscriptions import StrikeCodes, Subscription
from services.ws_gateway.watch import ClosedWatch, closed_window
from tests.fakes.ws_server import FakeKisWsServer
from tests.unit.test_ws_gateway_service import (
    WS_KEY,
    MemStore,
    Tick,
    _seed_master,
    fast_sleep,
    seed_ws_key,
    settings,
)

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
WED, THU, MON = date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 28)
FRI_BEFORE_HOLIDAY = date(2026, 10, 2)
BUDGET = load()


def kst(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


def fut_tick(tr_id: str = "H0IFCNT0", code: str = "A01612") -> FuturesTick:
    return FuturesTick.model_validate(
        {
            "tr_id": tr_id,
            "session": "day" if tr_id == "H0IFCNT0" else "night",
            "bsop_hour": "084600",
            "futs_shrn_iscd": code,
            "futs_prpr": "1100.25",
            "last_cnqn": "1",
        }
    )


def opt_tick() -> OptionTick:
    return OptionTick.model_validate(
        {
            "tr_id": "H0IOCNT0",
            "session": "day",
            "bsop_hour": "084600",
            "optn_shrn_iscd": "B01",
            "optn_prpr": "1.0",
        }
    )


def event(tick: Any, at: datetime, seq: int = 1) -> TickEvent:
    return TickEvent(tick, seq, at.astimezone(UTC), None, None)  # 장 밖 수신 — 태그 없음


# ── 창 ──


def test_closed_day_window_opens_a_minute_before_0845_for_ten_minutes() -> None:
    assert closed_window(kst(THU, 8, 43, 59), CAL) is None
    w = closed_window(kst(THU, 8, 44), CAL)
    assert w is not None
    assert (w.session, w.start_date, w.trade_date, w.tr_id) == ("day", THU, THU, "H0IFCNT0")
    assert (w.opens_at, w.start, w.end) == (kst(THU, 8, 45), kst(THU, 8, 44), kst(THU, 8, 55))
    assert closed_window(kst(THU, 8, 54, 59), CAL) == w
    assert closed_window(kst(THU, 8, 55), CAL) is None


@pytest.mark.parametrize(
    ("d", "trade_date"),
    [
        (WED, MON),  # 휴장 전날 밤(실측: 안 열림) — 열렸다면 09-28 귀속
        (THU, MON),  # 휴장일 밤
    ],
)
def test_closed_nights_are_watched_with_the_night_futures_tr(d: date, trade_date: date) -> None:
    w = closed_window(kst(d, 18, 5), CAL)
    assert w is not None
    assert (w.session, w.trade_date, w.tr_id, w.opens_at) == (
        "night",
        trade_date,
        "H0MFCNT0",
        kst(d, 18),
    )


def test_open_sessions_and_weekends_are_not_watched() -> None:
    for t in (kst(MON, 8, 46), kst(MON, 18, 5), kst(WED, 8, 46)):
        assert closed_window(t, CAL) is None
    assert closed_window(kst(date(2026, 9, 26), 8, 46), CAL) is None  # 토
    assert closed_window(kst(date(2026, 9, 18), 18, 5), CAL) is None  # 평범한 금요일 밤은 열린다
    # 월요일 휴장 앞 금요일 밤도 열린다(2026-09-29 실측 — 05-22·08-14) → 확인 구독 대상 아님
    assert closed_window(kst(FRI_BEFORE_HOLIDAY, 18, 5), CAL) is None
    with pytest.raises(ValueError, match="naive"):
        closed_window(datetime(2026, 9, 24, 8, 46), CAL)  # noqa: DTZ001 — naive 거부 확인


# ── 컨트롤러 ──


class Target:
    def __init__(self) -> None:
        self.calls: list[tuple[frozenset[Subscription], Any]] = []

    def set_desired(self, desired: frozenset[Subscription], budget: Any) -> None:
        self.calls.append((desired, budget))


class Chain:
    def __init__(self, futures: str | None = "A01612") -> None:
        self.futures = futures

    def nearest(self, now: datetime) -> ChainView | None:
        if self.futures is None:
            return None
        rows = tuple(StrikeCodes(Decimal(1000 + 2.5 * i), f"C{i}", f"P{i}") for i in range(20))
        return ChainView("WKI:261001", self.futures, rows)


def controller(
    chain: Chain | None = None,
    active: Callable[[Target], frozenset[Subscription]] | None = None,
) -> tuple[SubscriptionController, Target, MemoryHealthSink, list[SessionLogRecord]]:
    """active: 클라이언트가 서버에 걸려 있다고 보는 구독(연결 중) 흉내 — None 이면 모른다."""
    target, health, logs = Target(), MemoryHealthSink(), []
    watch = ClosedWatch(CAL, health, logs.append)
    ctl = SubscriptionController(
        target,
        chain or Chain(),
        lambda _c: None,
        health=health,
        calendar=CAL,
        grace_s=60,
        watch=watch,
        active=None if active is None else (lambda: active(target)),
    )
    return ctl, target, health, logs


def registered(target: Target) -> frozenset[Subscription]:
    """붙어 있는 클라이언트가 받은 집합을 곧바로 다 등록했다."""
    return target.calls[-1][0] if target.calls else frozenset()


def disconnected(_target: Target) -> frozenset[Subscription]:
    return frozenset()


def walk(ctl: SubscriptionController, start: datetime, end: datetime, step_s: float = 1) -> None:
    t = start
    while t <= end:
        ctl.step(t)
        t += timedelta(seconds=step_s)


def test_controller_subscribes_one_futures_feed_in_the_closed_day_window() -> None:
    ctl, target, health, _ = controller()
    assert ctl.step(kst(THU, 8, 43)).reason == "closed"
    d = ctl.step(kst(THU, 8, 44))
    assert (d.reason, d.session) == ("watch", None)
    assert d.desired == frozenset({Subscription("H0IFCNT0", "A01612")})
    assert (
        d.budget == derive(BUDGET, "day") and len(d.desired) <= d.budget.futures + d.budget.options
    )
    assert ctl.step(kst(THU, 8, 50)).reason == "watch"
    after = ctl.step(kst(THU, 8, 55))
    assert (after.reason, after.desired) == ("closed", frozenset())  # 여유 없이 바로 해지
    assert [len(x) for x, _ in target.calls] == [0, 1, 1, 0]
    assert "ws_plan_watch" in health.kinds()


def test_controller_watches_a_closed_night_with_the_night_tr() -> None:
    ctl, _, _, _ = controller()
    ctl.step(kst(WED, 15, 0))  # 주간 구독
    assert ctl.step(kst(WED, 17, 55)).reason == "closed"  # 밤이 없어 PRE_NIGHT 도 없다
    d = ctl.step(kst(WED, 17, 59))
    assert d.reason == "watch" and d.desired == frozenset({Subscription("H0MFCNT0", "A01612")})
    assert d.budget == derive(BUDGET, "night")
    assert ctl.step(kst(WED, 18, 10)).reason == "closed"


def test_without_a_chain_the_watch_warns_and_stays_closed() -> None:
    ctl, target, health, _ = controller(Chain(futures=None))
    d = ctl.step(kst(THU, 8, 45))
    assert (d.reason, d.desired) == ("closed", frozenset())
    assert health.of("ws_closed_watch_unavailable")[0].severity == "warning"
    assert target.calls[-1][0] == frozenset()


def test_a_normal_day_never_watches() -> None:
    ctl, _, health, _ = controller()
    t = kst(MON, 7, 0)
    while t < kst(MON, 19, 0):
        assert ctl.step(t).reason != "watch"
        t += timedelta(minutes=1)
    assert "ws_plan_watch" not in health.kinds()


# ── 예상 밖 개장 ──


def test_a_futures_trade_in_the_window_is_an_unexpected_open_once() -> None:
    health, logs = MemoryHealthSink(), []
    watch = ClosedWatch(CAL, health, logs.append)
    watch.observe(event(fut_tick(), kst(THU, 8, 45, 1)))
    watch.observe(event(fut_tick(), kst(THU, 8, 45, 2), 2))
    (ev,) = health.of("ws_unexpected_open")
    assert ev.severity == "warning" and ev.service == "ws-gateway"
    assert "주간 09-24 08:45" in ev.detail and "A01612 1100.25" in ev.detail
    (rec,) = logs
    assert (rec.kind, rec.service, rec.trade_date, rec.session) == (
        "unexpected_open",
        "ws-gateway",
        THU,
        "day",
    )
    assert rec.ts == kst(THU, 8, 45, 1) and rec.detail["calendar"] == "closed"
    assert rec.detail["tr_id"] == "H0IFCNT0" and rec.detail["price"] == "1100.25"
    assert watch.seen[("day", THU)].trades == 2


def test_only_the_watched_futures_tr_inside_the_window_counts() -> None:
    health, logs = MemoryHealthSink(), []
    watch = ClosedWatch(CAL, health, logs.append, grace_s=60)
    watch.observe(event(opt_tick(), kst(THU, 8, 46)))  # 옵션
    watch.observe(event(fut_tick("H0MFCNT0"), kst(THU, 8, 46)))  # 야간 TR 이 주간 창에
    watch.observe(event(fut_tick(), kst(THU, 9, 30)))  # 창 밖
    watch.observe(event(fut_tick(), kst(MON, 8, 46)))  # 열린 날
    assert logs == [] and health.events == []
    watch.observe(event(fut_tick(), kst(THU, 8, 55, 30)))  # 창 끝 뒤 마감 여유 안
    assert len(logs) == 1
    night = ClosedWatch(CAL, MemoryHealthSink(), (night_logs := []).append)
    night.observe(event(fut_tick("H0MFCNT0"), kst(WED, 18, 0, 3)))
    assert [(r.trade_date, r.session) for r in night_logs] == [(MON, "night")]


def test_a_quiet_armed_window_is_reported_as_confirmed_closed_once() -> None:
    health: MemoryHealthSink = MemoryHealthSink()
    watch = ClosedWatch(CAL, health, lambda _r: None, grace_s=60)
    assert watch.window(kst(THU, 8, 44)) is not None
    for m in range(44, 55):
        watch.armed(kst(THU, 8, m), True)
    watch.armed(kst(THU, 8, 54, 59), True)
    watch.step(kst(THU, 8, 55, 30))
    assert health.events == []  # 마감 여유 전
    watch.step(kst(THU, 8, 56))
    watch.step(kst(THU, 9, 30))
    (ev,) = health.of("ws_closed_watch_quiet")
    assert ev.severity == "info" and "10분간 선물 체결 없음" in ev.detail
    assert watch.seen[("day", THU)].armed_s == 599  # 개장 전(08:44~08:45)은 세지 않는다
    busy_health = MemoryHealthSink()
    busy = ClosedWatch(CAL, busy_health, lambda _r: None)
    busy.window(kst(THU, 8, 44))
    busy.observe(event(fut_tick(), kst(THU, 8, 45, 1)))
    busy.step(kst(THU, 9, 0))
    assert busy_health.kinds() == ["ws_unexpected_open"]  # 체결이 있었으면 조용하다는 기록은 없다


def test_a_window_never_subscribed_is_unverified_not_confirmed_closed() -> None:
    """체인이 없어 구독을 못 했으면 닫힘을 확인한 것이 아니다."""
    ctl, _, health, _ = controller(Chain(futures=None), registered)
    walk(ctl, kst(THU, 8, 44), kst(THU, 8, 57), 60)
    assert "ws_closed_watch_unavailable" in health.kinds()
    assert "ws_closed_watch_quiet" not in health.kinds()
    (ev,) = health.of("ws_closed_watch_unverified")
    assert ev.severity == "warning" and "닫힘을 확인하지 못했다" in ev.detail
    assert "0초/600초" in ev.detail


def test_a_disconnected_client_leaves_the_window_unverified() -> None:
    """구독을 원했어도 클라이언트가 붙어 있지 않으면(등록 안 됨) 확인이 아니다."""
    ctl, target, health, _ = controller(active=disconnected)
    walk(ctl, kst(THU, 8, 44), kst(THU, 8, 57))
    assert frozenset({Subscription("H0IFCNT0", "A01612")}) in [c for c, _ in target.calls]
    assert health.kinds().count("ws_closed_watch_unverified") == 1
    assert "ws_closed_watch_quiet" not in health.kinds()


def test_without_an_active_source_the_watch_cannot_confirm() -> None:
    ctl, _, health, _ = controller()
    walk(ctl, kst(THU, 8, 44), kst(THU, 8, 57), 30)
    assert "ws_closed_watch_quiet" not in health.kinds()
    assert "ws_closed_watch_unverified" in health.kinds()


def test_a_registered_watch_that_stays_quiet_confirms_closed() -> None:
    ctl, _, health, _ = controller(active=registered)
    walk(ctl, kst(WED, 17, 58), kst(WED, 18, 12))
    (ev,) = health.of("ws_closed_watch_quiet")
    assert "야간 09-23 18:00" in ev.detail
    assert "ws_closed_watch_unverified" not in health.kinds()


@pytest.mark.parametrize(
    ("until", "confirmed"),
    [
        (kst(THU, 8, 53), True),  # 개장 뒤 480초 = 창 600초의 80%
        (kst(THU, 8, 52, 59), False),
    ],
)
def test_the_watch_must_be_armed_for_most_of_the_window(until: datetime, confirmed: bool) -> None:
    health = MemoryHealthSink()
    watch = ClosedWatch(CAL, health, lambda _r: None)
    watch.armed(kst(THU, 8, 44), True)
    watch.armed(until, True)
    watch.armed(until + timedelta(seconds=1), False)  # 끊김 — 그 뒤는 세지 않는다
    watch.armed(kst(THU, 8, 54, 59), False)
    watch.step(kst(THU, 8, 57))
    want = "ws_closed_watch_quiet" if confirmed else "ws_closed_watch_unverified"
    assert health.kinds() == [want]


def test_armed_time_needs_both_samples_subscribed() -> None:
    """한쪽 표본이라도 구독이 없으면 그 사이는 세지 않는다 — 끊겼다 붙은 구간."""
    watch = ClosedWatch(CAL, MemoryHealthSink(), lambda _r: None)
    watch.armed(kst(THU, 8, 45), True)
    watch.armed(kst(THU, 8, 46), False)
    watch.armed(kst(THU, 8, 47), True)
    watch.armed(kst(THU, 8, 48), True)
    watch.armed(kst(THU, 9, 30), True)  # 창 밖 — 무시
    assert watch.seen[("day", THU)].armed_s == 60
    watch.armed(kst(THU, 8, 47, 30), True)  # 시계가 뒤로 — 세지 않는다
    watch.armed(kst(THU, 8, 49), True)
    assert watch.seen[("day", THU)].armed_s == 120
    for bad in (0.0, 1.5):
        with pytest.raises(ValueError, match="armed_ratio"):
            ClosedWatch(CAL, MemoryHealthSink(), lambda _r: None, armed_ratio=bad)


def test_the_worker_writes_session_log_items() -> None:
    store = MemStore()
    box = Outbox()
    w = GatewayWorker(box, fakeredis.FakeRedis(), store, master_of=lambda _c: None, clock=Tick())
    rec = SessionLogRecord(
        ts=kst(THU, 8, 45, 1),
        trade_date=THU,
        session="day",
        service="ws-gateway",
        kind="unexpected_open",
    )
    box.session_log(rec)
    stop = threading.Event()
    stop.set()
    w.run(stop, poll_s=0.01)
    assert store.session_log == [rec] and w.stats.ticks_written == 0


def run[T](coro: Coroutine[Any, Any, T], timeout: float = 20.0) -> T:
    return asyncio.run(asyncio.wait_for(coro, timeout))


def test_gateway_end_to_end_on_a_holiday_morning() -> None:
    """추석(09-24) 08:47: 컨트롤러가 선물 1건을 구독하고, 체결이 오면 session_log·health 가
    남는다."""
    from services.ws_gateway.client import redis_key_source

    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    _seed_master(r)
    seed_ws_key(r)
    store = MemStore()
    log_health = MemoryHealthSink()
    now = kst(THU, 8, 47).astimezone(UTC)

    async def body() -> None:
        async with FakeKisWsServer(approval_key=WS_KEY) as srv:
            gw = build_gateway(
                url=srv.url,
                key_source=redis_key_source(r, settings()),
                redis=r,
                store=store,
                calendar=CAL,
                log_health=log_health,
                now=lambda: now,
                sleep=fast_sleep,
                proxy=None,
                seq_base=0,
            )
            stop = asyncio.Event()
            task = asyncio.create_task(
                run_gateway(gw, stop, now=lambda: now, context_every_s=0.02, controller_tick_s=0.01)
            )
            await srv.wait_for(
                lambda: bool(srv.connections) and ("H0IFCNT0", "A01612") in srv.conn().subs
            )
            assert len(srv.conn().subs) == 1
            # 등록 응답을 받은 뒤 컨트롤러 표본이 '걸려 있음'이 된다(클라이언트 status().active)
            watch = gw.watch
            assert watch is not None

            def armed_now() -> bool:
                st = watch.seen.get(("day", THU))
                return st is not None and st.last is not None and st.last[1]

            await srv.wait_for(armed_now)
            await srv.send_tick(
                "H0IFCNT0", futs_shrn_iscd="A01612", bsop_hour="084659", futs_prpr="1101.00"
            )
            await srv.wait_for(lambda: gw.worker.stats.ticks == 1)
            stop.set()
            assert await asyncio.wait_for(task, 10) is True

    run(body())
    (rec,) = store.session_log
    assert (rec.kind, rec.trade_date, rec.session, rec.service) == (
        "unexpected_open",
        THU,
        "day",
        "ws-gateway",
    )
    assert store.fut == []  # 장 밖 수신 — 체결 행은 만들지 않는다
    assert "ws_unexpected_open" in log_health.kinds()
    assert "ws_unexpected_open" in [e.kind for e in store.health]
