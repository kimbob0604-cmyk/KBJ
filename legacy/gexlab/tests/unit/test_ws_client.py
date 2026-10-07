"""ws-gateway 웹소켓 세션 클라이언트 (PLAN §4.1·§4.3·§6.5, 설계 §4).

실제 KIS 에 붙지 않는다 — `tests/fakes/ws_server.py`(127.0.0.1 임의 포트)와 fakeredis 만 쓴다.
프레임·응답 문구는 합성(SYNTHETIC)이다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest
import yaml
from pydantic import SecretStr

from config.settings import Settings
from core.calendar import TradingCalendar
from core.metrics.flow import is_seq_gap
from data.kis.auth_client import RedisTokenCache, TokenRecord, token_owner, utcnow
from data.kis.master import MasterRow
from data.kis.ws import DEFAULT_FIELDS_PATH, FuturesTick, OptionTick, load_fields
from services.auth.health import MemoryHealthSink
from services.auth.service import WS_KEY_KEY
from services.poller.context import ChainContext, ExpiryInfo, Series
from services.recorder.envelope import RawEnvelope
from services.ws_gateway.budget import Budget, derive, load
from services.ws_gateway.client import (
    KeySource,
    KisWsClient,
    TickEvent,
    WsClientParams,
    WsConfig,
    build_message,
    classify_reply,
    default_tagger,
    is_unsub_reply,
    load_config,
    redis_key_source,
)
from services.ws_gateway.session import (
    ChainView,
    ContextChainSource,
    LatestFuturesPrice,
    PriceSource,
    SubscriptionController,
)
from services.ws_gateway.subscriptions import StrikeCodes, Subscription, plan
from tests.fakes.ws_server import FakeKisWsServer, synth_frame, synth_record

CFG = load_config()
APP_KEY = "PSappKEYforWsTests0123456"
WS_KEY = "approval-key-for-ws-tests-0001"
DAY_NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST 월 — DAY, 거래일 09-28
NIGHT_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # 21:00 KST — NIGHT, 거래일 09-29
CAL = TradingCalendar.default()
DAY = derive(load(), "day")
NIGHT = derive(load(), "night")


def chain(n: int = 60, start: str = "1000.0") -> list[StrikeCodes]:
    k0 = Decimal(start)
    return [StrikeCodes(k0 + Decimal("2.5") * i, f"BC{i:03d}", f"BP{i:03d}") for i in range(n)]


def desired(budget: Budget = DAY, atm: int = 30) -> frozenset[Subscription]:
    return plan(budget, "A01612", chain(), atm_index=atm)


def cfg_with(**client: Any) -> WsConfig:
    return CFG.model_copy(update={"client": CFG.client.model_copy(update=client)})


def run[T](coro: Coroutine[Any, Any, T], timeout: float = 20.0) -> T:
    return asyncio.run(asyncio.wait_for(coro, timeout))


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@dataclass
class FakeSleep:
    """가짜 sleep — 요청 시간을 기록하고 바로(1ms) 돌아온다."""

    calls: list[float] = field(default_factory=list[float])

    async def __call__(self, s: float) -> None:
        self.calls.append(s)
        await asyncio.sleep(0.001)

    def backoffs(self) -> list[float]:
        return [s for s in self.calls if s >= 1.0 and s != CFG.client.key_wait_s]


@dataclass
class Rig:
    server: FakeKisWsServer
    client: KisWsClient
    raws: list[RawEnvelope]
    ticks: list[TickEvent]
    health: MemoryHealthSink
    sleep: FakeSleep
    clock: Clock

    def active(self) -> frozenset[Subscription]:
        return self.client.status().active

    async def until_active(self, want: frozenset[Subscription]) -> None:
        await self.server.wait_for(
            lambda: self.active() == want and self.client.status().pending == 0
        )


@contextlib.asynccontextmanager
async def rig(
    *,
    key_source: KeySource = lambda: WS_KEY,
    now: datetime = DAY_NOW,
    config: WsConfig = CFG,
    server: FakeKisWsServer | None = None,
    on_tick: Callable[[TickEvent], None] | None = None,
) -> AsyncIterator[Rig]:
    srv = server if server is not None else FakeKisWsServer(approval_key=WS_KEY, config=config)
    async with srv:
        raws: list[RawEnvelope] = []
        ticks: list[TickEvent] = []
        sink = MemoryHealthSink()
        sleep = FakeSleep()
        clock = Clock(now)
        client = KisWsClient(
            srv.url,
            key_source,
            on_raw=raws.append,
            on_tick=on_tick or ticks.append,
            tagger=default_tagger(CAL),
            health=sink,
            config=config,
            now=clock,
            sleep=sleep,
            proxy=None,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(stop))
        try:
            yield Rig(srv, client, raws, ticks, sink, sleep, clock)
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)


def settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr("SECRETforWsTests9876543210"),
    )


def seed_ws_key(r: fakeredis.FakeRedis, value: str = WS_KEY) -> None:
    now = utcnow()
    rec = TokenRecord(
        access_token=SecretStr(value),
        expires_at=now + timedelta(hours=12),
        issued_at=now,
        owner=token_owner(settings().kis_base, APP_KEY),
    )
    RedisTokenCache(r, WS_KEY_KEY).store(rec, now)


# ── 설정·메시지 ──


def test_config_records_official_source_and_endpoints() -> None:
    assert CFG.url_for("real") == "ws://ops.koreainvestment.com:21000"
    assert CFG.url_for("vts") == "ws://ops.koreainvestment.com:31000"
    assert CFG.source.repo == "https://github.com/koreainvestment/open-trading-api"
    # 컬럼 설정(config/kis_ws_fields.yaml)과 같은 공식 샘플 커밋
    fields = yaml.safe_load(DEFAULT_FIELDS_PATH.read_text(encoding="utf-8"))
    assert CFG.source.commit == fields["source"]["commit"]
    assert all(CFG.source.commit in f for f in CFG.source.files)
    assert (CFG.header.custtype, CFG.header.content_type) == ("P", "utf-8")
    assert (CFG.tr_type.subscribe, CFG.tr_type.unsubscribe) == ("1", "2")
    assert (CFG.pingpong.tr_id, CFG.pingpong.reply) == ("PINGPONG", "pong")
    b = CFG.make_backoff()
    assert [b.next_delay() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_subscribe_and_unsubscribe_message_format() -> None:
    sub = Subscription("H0IFCNT0", "A01612")
    assert json.loads(build_message(CFG, "KEY1", "subscribe", sub)) == {
        "header": {
            "approval_key": "KEY1",
            "custtype": "P",
            "tr_type": "1",
            "content-type": "utf-8",
        },
        "body": {"input": {"tr_id": "H0IFCNT0", "tr_key": "A01612"}},
    }
    un = json.loads(build_message(CFG, "KEY1", "unsubscribe", sub))
    assert un["header"]["tr_type"] == "2"
    assert un["body"] == {"input": {"tr_id": "H0IFCNT0", "tr_key": "A01612"}}
    with pytest.raises(ValueError):
        build_message(CFG, " ", "subscribe", sub)


@pytest.mark.parametrize(
    ("rt_cd", "msg1", "kind", "unsub"),
    [
        ("0", "SUBSCRIBE SUCCESS", "ok", False),
        ("0", "UNSUBSCRIBE SUCCESS", "ok", True),
        ("1", "MAX SUBSCRIBE OVER", "max_subscriptions", False),
        ("1", "ALREADY IN SUBSCRIBE", "already_subscribed", False),
        ("1", "UNSUBSCRIBE ERROR(not found!)", "not_subscribed", True),
        ("9", "invalid approval : NOT FOUND", "key_rejected", False),  # NOT FOUND 보다 먼저
        ("1", "ALREADY IN USE appkey", "key_in_use", False),
        ("1", "SOMETHING ELSE", "rejected", False),
        (None, None, "rejected", False),
    ],
)
def test_classify_reply(rt_cd: str | None, msg1: str | None, kind: str, unsub: bool) -> None:
    assert classify_reply(CFG, rt_cd, msg1) == kind
    assert is_unsub_reply(CFG, msg1) is unsub


def test_set_desired_rejects_more_than_budget() -> None:
    client = KisWsClient(
        "ws://127.0.0.1:1",
        lambda: WS_KEY,
        on_raw=lambda _e: None,
        on_tick=lambda _t: None,
        tagger=default_tagger(CAL),
        health=MemoryHealthSink(),
    )
    too_many = desired(DAY) | {Subscription("H0IOCNT0", "EXTRA")}
    with pytest.raises(ValueError, match="예산"):
        client.set_desired(too_many, DAY)
    with pytest.raises(ValueError):
        client.set_desired(desired(DAY), NIGHT)  # 주간 39건 > 야간 35건


# ── 등록·회전 ──


def test_connects_and_subscribes_desired_set_with_pacing() -> None:
    async def body() -> None:
        async with rig() as r:
            want = desired(DAY)
            r.client.set_desired(want, DAY)
            await r.until_active(want)
            reqs = r.server.requests()
            assert r.server.errors == []
            assert len(reqs) == len(want) == 39
            assert {x.tr_type for x in reqs} == {"1"}
            assert {x.approval_key for x in reqs} == {WS_KEY}
            assert {Subscription(*x.sub) for x in reqs} == want
            assert r.server.conn().subs == {(s.tr_id, s.key) for s in want}
            assert r.sleep.calls.count(CFG.client.pacing_s) >= 39  # 메시지마다 0.1초
            assert r.client.max_occupancy == 39 <= DAY.limit
            assert "ws_connected" in r.health.kinds()
            assert not any(WS_KEY in e.detail for e in r.health.events)

    run(body())


def test_rotation_unsubscribes_first_and_never_exceeds_budget() -> None:
    cap = DAY.futures + DAY.options  # 39
    # 서버 한도를 예산과 같게, 해제는 늦게 풀리게 — 해제 응답 전에 등록하면 초과로 잡힌다
    server = FakeKisWsServer(approval_key=WS_KEY, limit=cap, unsub_ack_delay=0.02)

    async def body() -> None:
        async with rig(server=server) as r:
            a, b = desired(DAY, 30), desired(DAY, 33)  # ATM 3행사가 이동 → 6건 교체
            r.client.set_desired(a, DAY)
            await r.until_active(a)
            n0 = len(server.requests())
            r.client.set_desired(b, DAY)
            await r.until_active(b)
            batch = server.requests()[n0:]
            assert [x.tr_type for x in batch] == ["2"] * 6 + ["1"] * 6
            assert {Subscription(*x.sub) for x in batch if x.tr_type == "2"} == a - b
            assert server.over_limit == 0 and server.max_subs <= cap
            assert r.client.max_occupancy <= cap

            # 세션 전환: 주간 39건 해제 → 야간 35건 등록 (야간 TR)
            n1 = len(server.requests())
            night = desired(NIGHT, 30)
            r.client.set_desired(night, NIGHT)
            await r.until_active(night)
            batch = server.requests()[n1:]
            types = [x.tr_type for x in batch]
            assert types == ["2"] * 39 + ["1"] * 35
            assert {x.tr_id for x in batch if x.tr_type == "1"} == {"H0MFCNT0", "H0EUCNT0"}
            assert server.conn().subs == {(s.tr_id, s.key) for s in night}
            assert server.over_limit == 0 and server.max_subs <= cap
            assert r.client.max_occupancy <= cap
            assert server.errors == []

    run(body())


# ── PINGPONG ──


@pytest.mark.parametrize("reply", ["pong", "echo"])
def test_pingpong_is_answered(reply: str) -> None:
    cfg = CFG.model_copy(update={"pingpong": CFG.pingpong.model_copy(update={"reply": reply})})

    async def body() -> None:
        async with rig(config=cfg) as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            text = await r.server.send_pingpong()
            if reply == "pong":  # 공식 샘플: ws.pong(raw) — PONG 제어 프레임에 원문
                await r.server.wait_for(lambda: text in r.server.pongs)
                assert r.server.echoes == []
            else:
                await r.server.wait_for(lambda: text in r.server.echoes)
            assert any(e.tr_id == "PINGPONG" and e.payload == text for e in r.raws)
            assert r.server.errors == []

    run(body())


def test_pingpong_too_long_for_a_control_frame_is_echoed_as_text() -> None:
    async def body() -> None:
        async with rig() as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            long = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "2" * 130}})
            await r.server.send(long)
            await r.server.wait_for(lambda: long in r.server.echoes)
            assert r.server.pongs == []
            assert r.health.kinds().count("ws_pingpong_too_long") == 1

    run(body())


# ── 재연결 ──


def test_reconnect_with_backoff_and_full_resubscribe() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    server.refuse_next = 3  # 핸드셰이크 3번 503

    async def body() -> None:
        async with rig(server=server) as r:
            want = desired(DAY)
            r.client.set_desired(want, DAY)
            await r.until_active(want)
            assert server.handshakes == 4 and len(server.connections) == 1
            assert r.sleep.backoffs() == [1.0, 2.0, 4.0]
            assert r.health.kinds().count("ws_connect_failed") == 3
            assert r.client.status().backoff_attempt == 3  # 아직 끊기지 않았다

            await server.drop(abrupt=True)
            await server.wait_for(lambda: len(server.connections) == 2)
            await r.until_active(want)
            second = server.requests(conn=2)
            assert {Subscription(*x.sub) for x in second} == want  # 전부 다시 등록
            assert [x.tr_type for x in second] == ["1"] * 39
            # 등록 성공을 받았어도 stable_s(60초) 전에 끊긴 연결 — 백오프는 이어 센다
            assert r.sleep.backoffs() == [1.0, 2.0, 4.0, 8.0]
            kinds = r.health.kinds()
            assert "ws_disconnected" in kinds and kinds.count("ws_connected") == 2
            assert r.client.status().connections == 2

            await server.drop()  # 정상 close 로 끊어도 같다
            await server.wait_for(lambda: len(server.connections) == 3)
            await r.until_active(want)
            assert r.sleep.backoffs() == [1.0, 2.0, 4.0, 8.0, 16.0]

    run(body())


def test_backoff_keeps_growing_when_connection_never_confirms() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    server.silent = {("H0IFCNT0", "A01612")}  # 응답이 없으면 건강하다고 보지 않는다

    async def body() -> None:
        async with rig(server=server, config=cfg_with(ack_timeout_s=0.3)) as r:
            r.client.set_desired(frozenset({Subscription("H0IFCNT0", "A01612")}), DAY)
            for n in (1, 2, 3):
                await server.wait_for(lambda n=n: len(server.connections) == n)
                await server.wait_for(lambda n=n: len(server.requests(conn=n)) == 1)
                await server.drop()
            await server.wait_for(lambda: len(server.connections) == 4)
            assert r.sleep.backoffs() == [1.0, 2.0, 4.0]

    run(body())


def test_backoff_keeps_growing_when_server_acks_then_drops() -> None:
    # 등록 성공을 받아도 곧 끊기면 건강하지 않다(같은 앱키를 두 세션이 다툴 때 등) — 1초마다
    # 다시 붙어 원하는 집합 전부를 되풀이해 보내지 않는다(PLAN §6.5)
    server = FakeKisWsServer(approval_key=WS_KEY)
    fut = frozenset({Subscription("H0IFCNT0", "A01612")})

    async def body() -> None:
        async with rig(server=server) as r:
            r.client.set_desired(fut, DAY)
            for n in (1, 2, 3, 4):
                await server.wait_for(lambda n=n: len(server.connections) == n)
                await r.until_active(fut)  # 등록 성공 응답을 받았다
                await server.drop()
            await server.wait_for(lambda: len(server.connections) == 5)
            assert r.sleep.backoffs() == [1.0, 2.0, 4.0, 8.0]

    run(body())


def test_backoff_starts_over_after_a_stable_connection() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    server.refuse_next = 2

    async def body() -> None:
        async with rig(server=server, config=cfg_with(stable_s=0.2)) as r:
            await server.wait_for(lambda: len(server.connections) == 1)
            assert r.sleep.backoffs() == [1.0, 2.0]
            await asyncio.sleep(0.3)  # stable_s 넘게 살았다
            await server.drop()
            await server.wait_for(lambda: len(server.connections) == 2)
            assert r.sleep.backoffs() == [1.0, 2.0, 1.0]
            await server.drop()  # 곧바로 끊기면 이어 센다
            await server.wait_for(lambda: len(server.connections) == 3)
            assert r.sleep.backoffs() == [1.0, 2.0, 1.0, 2.0]

    run(body())


# ── 콜백·태그 ──


def test_raw_and_tick_callbacks_carry_trade_date_and_session() -> None:
    async def body() -> None:
        async with rig() as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            fut = await r.server.send_tick(
                "H0IFCNT0",
                futs_shrn_iscd="A01612",
                bsop_hour="093000",
                futs_prpr="1096.40",
                last_cnqn="3",
                seln_cntg_smtn="100",
                shnu_cntg_smtn="120",
            )
            rec = synth_record("H0IOCNT0", optn_shrn_iscd="BC030", bsop_hour="093001")
            rec2 = synth_record("H0IOCNT0", optn_shrn_iscd="BC030", bsop_hour="093002")
            cols = load_fields()["H0IOCNT0"].columns
            rec[cols.index("optn_prpr")] = "5.20"
            rec2[cols.index("optn_prpr")] = "5.25"
            opt = synth_frame("H0IOCNT0", rec, rec2)  # 한 프레임에 2건
            await r.server.send(opt)
            await r.server.wait_for(lambda: len(r.ticks) == 3)

            assert [t.seq for t in r.ticks] == [1, 2, 3]
            f, o1, o2 = (t.tick for t in r.ticks)
            assert isinstance(f, FuturesTick) and isinstance(o1, OptionTick)
            assert (f.code, f.price, f.cum_sell_qty, f.cum_buy_qty) == (
                "A01612",
                Decimal("1096.40"),
                100,
                120,
            )
            assert (o1.price, o2.price) == (Decimal("5.20"), Decimal("5.25"))
            assert {(t.trade_date, t.session) for t in r.ticks} == {(date(2026, 9, 28), "day")}
            assert all(t.received_at == DAY_NOW for t in r.ticks)

            envs = {e.payload: e for e in r.raws}
            e = envs[fut]
            assert (e.source, e.tr_id, e.key) == ("kis_ws", "H0IFCNT0", "A01612")
            assert (e.trade_date, e.session, e.received_at) == (date(2026, 9, 28), "day", DAY_NOW)
            assert (envs[opt].tr_id, envs[opt].key) == ("H0IOCNT0", "BC030")

    run(body())


def test_a_reconnect_skips_one_tick_seq_so_receivers_see_the_break() -> None:
    """재연결마다 수신 순번 하나를 건너뛴다 — 끊긴 동안의 체결이 재연결 뒤 첫 틱의 누적 매수·매도에
    몰리므로, 받는 쪽(engine HIRO-lite)이 그 틱을 순번 공백으로 보고 반영 전에 리셋하게(metrics
    §6.1, 검토 F2). 한 연결 안에서는 이어진다."""

    async def tick(r: Rig, hour: str, buy: str) -> None:
        await r.server.send_tick(
            "H0IFCNT0",
            futs_shrn_iscd="A01612",
            bsop_hour=hour,
            futs_prpr="1096.40",
            last_cnqn="1",
            seln_cntg_smtn="100",
            shnu_cntg_smtn=buy,
        )

    async def body() -> None:
        async with rig() as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            await tick(r, "093000", "120")
            await tick(r, "093001", "121")
            await r.server.wait_for(lambda: len(r.ticks) == 2)
            await r.server.drop()
            await r.server.wait_for(lambda: len(r.server.connections) == 2)
            await tick(r, "093005", "621")  # 끊긴 동안 500 계약
            await r.server.wait_for(lambda: len(r.ticks) == 3)
            await r.server.drop(abrupt=True)
            await r.server.wait_for(lambda: len(r.server.connections) == 3)
            await tick(r, "093009", "622")
            await r.server.wait_for(lambda: len(r.ticks) == 4)
            seqs = [t.seq for t in r.ticks]
            assert seqs == [1, 2, 4, 6] and r.client.status().connections == 3
            gaps = [is_seq_gap(a, b) for a, b in zip([None, *seqs], seqs, strict=False)]
            assert gaps == [False, False, True, True]

    run(body())


def test_night_ticks_belong_to_next_trading_day() -> None:
    async def body() -> None:
        async with rig(now=NIGHT_NOW) as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            await r.server.send_tick(
                "H0MFCNT0", futs_shrn_iscd="A01612", bsop_hour="210000", futs_prpr="1100.05"
            )
            await r.server.wait_for(lambda: len(r.ticks) == 1)
            t = r.ticks[0]
            assert (t.trade_date, t.session) == (date(2026, 9, 29), "night")
            assert t.tick.session == "night"
            assert r.raws[-1].trade_date == date(2026, 9, 29)

    run(body())


def test_tick_arriving_just_after_close_keeps_its_session() -> None:
    after_close = datetime(2026, 9, 28, 6, 45, 20, tzinfo=UTC)  # 15:45:20 KST (POST_DAY)

    async def body() -> None:
        async with rig(now=after_close) as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            await r.server.send_pingpong()
            await r.server.send_tick(
                "H0IFCNT0", futs_shrn_iscd="A01612", bsop_hour="154500", futs_prpr="1096.40"
            )
            await r.server.wait_for(lambda: len(r.ticks) == 1)
            assert (r.ticks[0].trade_date, r.ticks[0].session) == (date(2026, 9, 28), "day")
            ping = next(e for e in r.raws if e.tr_id == "PINGPONG")
            assert (ping.trade_date, ping.session) == (None, None)  # 제어 프레임은 그대로

    run(body())


# ── 거절·오류 격리 ──


def test_rejected_subscription_emits_health_and_session_keeps_running() -> None:
    cap = DAY.futures + DAY.options
    server = FakeKisWsServer(approval_key=WS_KEY, limit=cap - 2)  # 거절 1 + 최대 초과 1
    bad = Subscription("H0IOCNT0", "BC030")
    server.reject[(bad.tr_id, bad.key)] = "SYNTHETIC REJECT"

    async def body() -> None:
        async with rig(server=server) as r:
            want = desired(DAY)
            r.client.set_desired(want, DAY)
            await server.wait_for(lambda: len(server.requests()) == 39)
            await server.wait_for(lambda: r.client.status().pending == 0)
            st = r.client.status()
            assert st.connected and bad not in st.active
            assert st.rejected[bad] == "rejected"
            assert list(st.rejected.values()).count("max_subscriptions") == 1
            assert len(st.active) == 37
            rej = r.health.of("ws_subscribe_rejected")
            assert len(rej) == 1
            assert "BC030" in rej[0].detail and "SYNTHETIC REJECT" in rej[0].detail
            over = r.health.of("ws_max_subscriptions")
            assert len(over) == 1 and over[0].severity == "critical"
            assert all(e.service == "ws-gateway" for e in r.health.events)
            # 세션은 계속 돈다
            await server.send_tick(
                "H0IFCNT0", futs_shrn_iscd="A01612", bsop_hour="093000", futs_prpr="1096.40"
            )
            await server.wait_for(lambda: len(r.ticks) == 1)
            assert len(server.connections) == 1

            # 다음 맞추기(resync)에서 거절된 것만 다시 시도한다
            n0 = len(server.requests())
            r.client.resync()
            await server.wait_for(lambda: len(server.requests()) == n0 + 2)
            assert {Subscription(*x.sub) for x in server.requests()[n0:]} <= want - st.active

    run(body())


# ── 등록·해제 감속 (설계 §4: 오류 응답이면 감속하고 health 기록) ──

FUT = Subscription("H0IFCNT0", "A01612")
OPT = Subscription("H0IOCNT0", "BC030")


def test_error_reply_slows_pacing_up_to_the_cap_and_keeps_it_across_reconnect() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    server.reject[(OPT.tr_id, OPT.key)] = "SYNTHETIC TOO FAST"  # 합성: 속도 초과 문구는 미실측
    cfg = cfg_with(pacing_max_s=0.4, pacing_recover_after=100)

    async def body() -> None:
        async with rig(server=server, config=cfg) as r:
            base = CFG.client.pacing_s

            async def rejected_again(n: int) -> None:
                await server.wait_for(lambda: len(server.requests()) == n)
                await server.wait_for(lambda: r.client.status().pending == 0)

            assert r.client.status().pacing_s == base
            r.client.set_desired(frozenset({FUT, OPT}), DAY)
            await rejected_again(2)
            assert r.client.status().pacing_s == 0.2 and r.client.status().rejected[OPT]
            slow = r.health.of("ws_pacing_slowdown")
            assert len(slow) == 1 and slow[0].severity == "warning"
            assert "rejected" in slow[0].detail and "0.1 → 0.2" in slow[0].detail
            # 감속 뒤에 보낸 요청도 거절 — 한 번 더 늘린다
            r.client.resync()
            await rejected_again(3)
            assert r.client.status().pacing_s == 0.4
            assert "0.2 → 0.4" in r.health.of("ws_pacing_slowdown")[-1].detail
            n_sleep = len(r.sleep.calls)
            r.client.resync()  # 상한(0.4)에서도 오류 — 간격은 그대로, health 는 1분에 한 번
            await rejected_again(4)
            assert r.sleep.calls[n_sleep:] == [0.4]  # 다시 보낸 1건 뒤 늘어난 간격으로 쉰다
            assert r.client.status().pacing_s == 0.4
            assert "상한 0.4초인데도" in r.health.of("ws_pacing_slowdown")[-1].detail
            r.client.resync()
            await rejected_again(5)
            assert len(r.health.of("ws_pacing_slowdown")) == 3
            # 다시 붙어도 늘린 간격으로 다시 등록한다
            n_sleep = len(r.sleep.calls)
            await server.drop()
            await server.wait_for(lambda: len(server.connections) == 2)
            await server.wait_for(lambda: len(server.requests(conn=2)) == 2)
            await server.wait_for(lambda: r.client.status().pending == 0)
            pacing = [x for x in r.sleep.calls[n_sleep:] if x < 1.0]  # 백오프 1초는 빼고
            assert pacing and set(pacing) == {0.4}
            assert r.client.status().active == {FUT}
            assert server.errors == [] and len(server.connections) == 2

    run(body())


def test_errors_from_one_burst_slow_down_once_and_successes_recover() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    gate = asyncio.Event()
    server.reject_gate = gate
    want = desired(DAY)
    bad = sorted(want - {FUT})[:3]
    for b in bad:
        server.reject[(b.tr_id, b.key)] = "SYNTHETIC TOO FAST"
    cfg = cfg_with(pacing_recover_after=5)

    async def body() -> None:
        async with rig(server=server, config=cfg) as r:
            r.client.set_desired(want, DAY)
            await server.wait_for(lambda: len(server.requests()) == 39)
            gate.set()  # 39건을 다 보낸 뒤에야 거절 3건이 온다 — 모두 감속 전에 보낸 요청
            await server.wait_for(lambda: len(r.client.status().rejected) == 3)
            await server.wait_for(lambda: r.client.status().pending == 0)
            assert r.client.status().pacing_s == 0.2
            assert len(r.health.of("ws_pacing_slowdown")) == 1  # 한 버스트 — 한 번만 늘린다
            # 성공 5건이 이어지면 한 단계 되돌린다(기본 0.1 까지)
            server.reject_gate = None
            server.reject.clear()
            n_sleep = len(r.sleep.calls)
            r.client.resync()  # 거절됐던 3건을 늘어난 간격으로 다시
            await r.until_active(want)
            assert r.sleep.calls[n_sleep:] == [0.2] * 3
            assert r.client.status().pacing_s == 0.2  # 아직 3건
            rotated = desired(DAY, 33)  # 해제 6 + 등록 6 — 성공 응답 12건
            r.client.set_desired(rotated, DAY)
            await r.until_active(rotated)
            assert r.client.status().pacing_s == CFG.client.pacing_s
            rec = r.health.of("ws_pacing_recovered")
            assert len(rec) == 1 and rec[0].severity == "info" and "0.2 → 0.1" in rec[0].detail
            assert len(r.health.of("ws_pacing_slowdown")) == 1

    run(body())


def test_state_mismatch_replies_do_not_slow_pacing() -> None:
    # 이미 등록됨·등록 안 됨은 서버와 셈이 달랐을 뿐 속도 탓이 아니다(등록된·풀린 것으로 본다)
    server = FakeKisWsServer(approval_key=WS_KEY)

    async def body() -> None:
        async with rig(server=server) as r:
            r.client.set_desired(frozenset({FUT}), DAY)
            await r.until_active(frozenset({FUT}))
            server.conn().subs.add((OPT.tr_id, OPT.key))  # 서버엔 이미 있다 → ALREADY
            r.client.set_desired(frozenset({FUT, OPT}), DAY)
            await r.until_active(frozenset({FUT, OPT}))
            server.conn().subs.discard((OPT.tr_id, OPT.key))  # 서버엔 없다 → NOT FOUND
            r.client.set_desired(frozenset({FUT}), DAY)
            await r.until_active(frozenset({FUT}))
            assert r.client.status().pacing_s == CFG.client.pacing_s
            assert r.health.of("ws_pacing_slowdown") == []

    run(body())


def test_pacing_config_is_validated() -> None:
    c = CFG.client.model_dump()
    assert (c["pacing_s"], c["pacing_max_s"], c["pacing_factor"]) == (0.1, 1.0, 2.0)
    for bad in ({"pacing_max_s": 0.05}, {"pacing_s": 0.0}, {"pacing_factor": 1.0}):
        with pytest.raises(ValueError):
            WsClientParams.model_validate(c | bad)


def test_silent_ack_is_counted_as_subscribed_after_timeout() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    quiet = Subscription("H0IOCNT0", "BC030")
    server.silent = {(quiet.tr_id, quiet.key)}

    async def body() -> None:
        async with rig(server=server, config=cfg_with(ack_timeout_s=0.2)) as r:
            want = desired(DAY)
            r.client.set_desired(want, DAY)
            await r.until_active(want)  # 응답 없는 1건도 자리를 차지한 것으로
            assert r.health.kinds().count("ws_ack_timeout") == 1

    run(body())


def test_unacked_unsubscribe_keeps_its_slot_and_reconnects_instead_of_overfilling() -> None:
    # KIS 가 해제에 답하지 않고 구독도 풀지 않는다 — 풀린 것으로 보고 등록하면 서버가 41건을 넘는다
    server = FakeKisWsServer(approval_key=WS_KEY)  # 앱키당 41건
    cap = DAY.futures + DAY.options

    async def body() -> None:
        async with rig(server=server, config=cfg_with(ack_timeout_s=0.2)) as r:
            a, b = desired(DAY, 30), desired(DAY, 34)  # 4행사가 이동 → 8건 교체
            r.client.set_desired(a, DAY)
            await r.until_active(a)
            server.silent = {(s.tr_id, s.key) for s in a - b}  # 해제 응답 없음, 서버에 남는다
            r.client.set_desired(b, DAY)
            await server.wait_for(lambda: len(server.connections) == 2 or server.over_limit > 0)
            assert server.over_limit == 0 and server.max_subs <= cap
            first = server.requests(conn=1)
            assert {Subscription(*x.sub) for x in first if x.tr_type == "1"} == a  # 새 등록 없음
            assert {Subscription(*x.sub) for x in first if x.tr_type == "2"} == a - b
            kinds = r.health.kinds()
            assert "ws_ack_timeout" in kinds and "ws_max_subscriptions" not in kinds
            full = r.health.of("ws_budget_full")
            assert len(full) == 1 and full[0].severity == "critical"
            # 새 세션은 구독이 없다 — 원하는 집합 전부를 예산 안에서 다시 건다
            await r.until_active(b)
            assert server.conn().subs == {(s.tr_id, s.key) for s in b}
            assert server.over_limit == 0 and server.max_subs <= cap
            assert r.client.max_occupancy <= cap

    run(body())


def test_unacked_unsubscribe_stays_counted_and_is_sent_again() -> None:
    server = FakeKisWsServer(approval_key=WS_KEY)
    fut, opt = Subscription("H0IFCNT0", "A01612"), Subscription("H0IOCNT0", "BC030")

    async def body() -> None:
        async with rig(server=server, config=cfg_with(ack_timeout_s=0.2)) as r:
            r.client.set_desired(frozenset({fut, opt}), DAY)
            await r.until_active(frozenset({fut, opt}))
            server.silent = {(opt.tr_id, opt.key)}
            r.client.set_desired(frozenset({fut}), DAY)
            await server.wait_for(lambda: bool(r.health.of("ws_ack_timeout")))
            assert r.client.status().active == {fut, opt}  # 해제 확인 전 — 자리를 차지한 채
            assert "풀리지 않은 것으로" in r.health.of("ws_ack_timeout")[0].detail
            server.silent = set()
            r.client.resync()  # 다음 맞추기에서 해제를 다시 보낸다
            await r.until_active(frozenset({fut}))
            assert [Subscription(*x.sub) for x in server.requests() if x.tr_type == "2"] == [
                opt,
                opt,
            ]
            assert server.conn().subs == {(fut.tr_id, fut.key)}
            assert len(server.connections) == 1  # 자리가 남아 있으면 끊지 않는다

    run(body())


def test_bad_frames_are_recorded_and_isolated() -> None:
    boom: list[int] = []

    def on_tick(ev: TickEvent) -> None:
        boom.append(ev.seq)
        if ev.seq == 1:
            raise RuntimeError("downstream down")

    async def body() -> None:
        async with rig(on_tick=on_tick) as r:
            await r.server.wait_for(lambda: len(r.server.connections) == 1)
            wide = synth_record("H0IFCNT0", futs_shrn_iscd="A01612", futs_prpr="1096.40")
            wide_frame = synth_frame("H0IFCNT0", [*wide, "EXTRA"])  # 필드 하나 더
            await r.server.send(wide_frame)
            await r.server.send("hello")  # 형식 불명
            enc = "1|H0IFCNI0|001|U2FsdGVkX19TWU5USEVUSUM="  # 암호화(체결통보) — 녹화만
            await r.server.send(enc)
            zero = synth_frame("H0IFCNT0", synth_record("H0IFCNT0", futs_shrn_iscd="A01612"))
            await r.server.send(zero)  # 체결가 0 — 검증 실패
            stray = json.dumps(
                {
                    "header": {"tr_id": "H0STCNT0", "tr_key": "005930", "encrypt": "N"},
                    "body": {"rt_cd": "1", "msg_cd": "OPSP9999", "msg1": "SYNTHETIC STRAY"},
                }
            )
            await r.server.send(stray)  # 보낸 적 없는 구독에 대한 오류 응답
            for hhmmss in ("093000", "093001"):
                await r.server.send_tick(
                    "H0IFCNT0", futs_shrn_iscd="A01612", bsop_hour=hhmmss, futs_prpr="1096.40"
                )
            await r.server.wait_for(lambda: len(boom) == 2)
            payloads = [e.payload for e in r.raws]
            assert wide_frame in payloads and "hello" in payloads and enc in payloads
            assert zero in payloads
            assert next(e for e in r.raws if e.payload == enc).tr_id == "H0IFCNI0"
            assert next(e for e in r.raws if e.payload == "hello").tr_id == ""
            kinds = r.health.kinds()
            assert kinds.count("ws_width_mismatch") == 1
            assert "ws_callback_error" in kinds
            assert kinds.count("ws_parse_error") == 2  # 'hello' · 체결가 0 (TR 별 1분에 1건)
            assert boom == [1, 2]  # 첫 콜백이 던져도 다음 틱은 온다
            stray_h = r.health.of("ws_subscribe_rejected")
            assert len(stray_h) == 1 and "SYNTHETIC STRAY" in stray_h[0].detail
            assert r.client.status().rejected == {}  # 상태는 건드리지 않는다
            assert (
                r.client.status().pacing_s == CFG.client.pacing_s
            )  # 보낸 적 없는 요청 — 감속 없음
            assert len(r.server.connections) == 1

    run(body())


# ── 접속키 ──


def test_approval_key_is_read_from_redis_through_auth_reader() -> None:
    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    seed_ws_key(r)

    async def body() -> None:
        async with rig(key_source=redis_key_source(r, settings())) as g:
            want = desired(DAY)
            g.client.set_desired(want, DAY)
            await g.until_active(want)
            assert {x.approval_key for x in g.server.requests()} == {WS_KEY}
            assert not any(WS_KEY in e.detail for e in g.health.events)

    run(body())


def test_missing_key_waits_with_health_and_never_issues() -> None:
    r = fakeredis.FakeRedis(server=fakeredis.FakeServer())

    async def body() -> None:
        async with rig(key_source=redis_key_source(r, settings())) as g:
            wait = CFG.client.key_wait_s
            await g.server.wait_for(lambda: g.sleep.calls.count(wait) >= 3)
            assert g.server.handshakes == 0  # 접속키 없이 붙지 않는다
            missing = g.health.of("ws_key_missing")
            assert len(missing) == 1 and missing[0].severity == "critical"  # 1분에 한 번
            assert r.keys("*") == []  # 발급·발급 간격 기록 등 아무것도 쓰지 않았다

            seed_ws_key(r)  # auth 가 발급하면
            want = desired(DAY)
            g.client.set_desired(want, DAY)
            await g.until_active(want)
            assert g.server.handshakes == 1
            assert r.keys("*") == [WS_KEY_KEY.encode()]

    run(body())


def test_rejected_key_closes_and_reconnects_with_a_fresh_key() -> None:
    keys = ["STALE-approval-key"]  # auth 가 갈아 끼우기 전 값

    async def body() -> None:
        async with rig(key_source=lambda: keys[0]) as g:
            want = desired(DAY)
            g.client.set_desired(want, DAY)
            await g.server.wait_for(lambda: bool(g.health.of("ws_key_rejected")))
            ev = g.health.of("ws_key_rejected")[0]
            assert ev.severity == "critical" and "STALE" not in ev.detail
            keys[0] = WS_KEY  # auth 가 새 접속키를 넣었다
            await g.server.wait_for(lambda: len(g.server.connections) >= 2)
            await g.until_active(want)
            last = len(g.server.connections)
            assert {x.approval_key for x in g.server.requests(conn=last)} == {WS_KEY}
            assert g.sleep.backoffs()[0] == 1.0  # 확인 전 끊긴 연결 — 백오프를 센다
            assert "ws_disconnected" in g.health.kinds()

    run(body())


def test_redis_error_while_reading_key_is_a_health_event() -> None:
    def broken() -> str:
        raise ConnectionError("redis down")

    async def body() -> None:
        async with rig(key_source=broken) as g:
            await g.server.wait_for(lambda: g.sleep.calls.count(CFG.client.key_wait_s) >= 2)
            assert [e.detail for e in g.health.of("ws_key_error")] == [
                "접속키를 읽지 못했다: ConnectionError — 5초 뒤 다시"
            ]
            assert g.server.handshakes == 0

    run(body())


# ── 컨트롤러: 세션 전환·만기 전환·ATM 회전 ──

KST = ZoneInfo("Asia/Seoul")


def kst(month: int, day: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(2026, month, day, hh, mm, ss, tzinfo=KST)


@dataclass
class FakeTarget:
    calls: list[tuple[frozenset[Subscription], Budget]] = field(
        default_factory=list[tuple[frozenset[Subscription], Budget]]
    )

    def set_desired(self, desired: frozenset[Subscription], budget: Budget) -> None:
        self.calls.append((desired, budget))


@dataclass
class StaticChain:
    views: dict[str, ChainView]
    pick: Callable[[datetime], str | None]

    def nearest(self, now: datetime) -> ChainView | None:
        name = self.pick(now)
        return None if name is None else self.views.get(name)


@dataclass
class Price:
    value: Decimal | None = None
    code: str | None = None  # 이 종목의 가격 (None 이면 어느 종목이든)

    def __call__(self, code: str) -> Decimal | None:
        return self.value if self.code in (None, code) else None


def view(series: str = "WKM:260904", futures: str = "A01612", prefix: str = "B") -> ChainView:
    k0 = Decimal("1000.0")
    rows = tuple(
        StrikeCodes(k0 + Decimal("2.5") * i, f"{prefix}C{i:03d}", f"{prefix}P{i:03d}")
        for i in range(60)
    )
    return ChainView(series, futures, rows)


def controller(
    price: PriceSource, chain: StaticChain | None = None, **kw: Any
) -> tuple[SubscriptionController, FakeTarget, MemoryHealthSink]:
    target, sink = FakeTarget(), MemoryHealthSink()
    src = chain if chain is not None else StaticChain({"a": view()}, lambda _t: "a")
    ctl = SubscriptionController(target, src, price, health=sink, calendar=CAL, **kw)
    return ctl, target, sink


def test_controller_plans_day_session_around_futures_atm() -> None:
    ctl, target, sink = controller(Price(Decimal("1075.9")))  # 1075.0 에 가장 가깝다 (30번째)
    d = ctl.step(kst(9, 28, 9, 30))
    assert (d.reason, d.session, d.series, d.center) == (
        "session",
        "day",
        "WKM:260904",
        Decimal("1075.0"),
    )
    assert d.desired == plan(DAY, "A01612", view().strikes, 30)
    assert {s.tr_id for s in d.desired} == {"H0IFCNT0", "H0IOCNT0"} and len(d.desired) == 39
    assert target.calls == [(d.desired, DAY)]
    assert sink.kinds() == ["ws_plan_session"]
    assert all(e.service == "ws-gateway" for e in sink.events)


def test_controller_subscribes_futures_only_until_a_price_arrives() -> None:
    price = Price()
    ctl, target, _ = controller(price)
    d = ctl.step(kst(9, 28, 8, 40))  # PRE_DAY → 주간 TR
    assert (d.reason, d.center) == ("session", None)
    assert d.desired == frozenset({Subscription("H0IFCNT0", "A01612")})
    assert ctl.step(kst(9, 28, 8, 40, 1)).reason == "hold"
    price.value = Decimal("1080.1")
    d = ctl.step(kst(9, 28, 8, 45, 2))
    assert (d.reason, d.center) == ("price", Decimal("1080.0"))
    assert d.desired == plan(DAY, "A01612", view().strikes, 32)
    price.value = None  # 가격이 사라져도 구독은 둔다
    assert ctl.step(kst(9, 28, 8, 46)).desired == d.desired
    assert len(target.calls) == 2


def test_controller_rotates_only_every_minute_and_two_strikes() -> None:
    price = Price(Decimal("1075.0"))
    ctl, target, sink = controller(price)
    t0 = kst(9, 28, 10, 0)
    first = ctl.step(t0)
    price.value = Decimal("1077.5")  # 1행사가 — 회전 안 함
    assert ctl.step(t0 + timedelta(seconds=60)).reason == "hold"
    price.value = Decimal("1080.0")  # 2행사가지만 직전 확인 뒤 30초 — 아직
    assert ctl.step(t0 + timedelta(seconds=90)).reason == "hold"
    d = ctl.step(t0 + timedelta(seconds=120))
    assert (d.reason, d.center) == ("rotate", Decimal("1080.0"))
    assert d.desired == plan(DAY, "A01612", view().strikes, 32)
    assert len(target.calls) == 2 and target.calls[0][0] == first.desired
    assert "ws_plan_rotate" in sink.kinds()
    assert "1075.0 → 1080.0" in sink.of("ws_plan_rotate")[0].detail


def test_controller_switches_sessions_day_post_night() -> None:
    ctl, target, _ = controller(Price(Decimal("1075.0")))
    day = ctl.step(kst(9, 28, 15, 0))
    assert day.budget == DAY
    closed = ctl.step(kst(9, 28, 15, 46))  # POST_DAY — 전부 해지
    assert (closed.reason, closed.session, closed.desired) == ("closed", None, frozenset())
    night = ctl.step(kst(9, 28, 17, 50))  # PRE_NIGHT — 야간 구독 전환
    assert (night.reason, night.session, night.budget) == ("session", "night", NIGHT)
    assert night.desired == plan(NIGHT, "A01612", view().strikes, 30)
    assert {s.tr_id for s in night.desired} == {"H0MFCNT0", "H0EUCNT0"} and len(night.desired) == 35
    assert [len(d) for d, _ in target.calls] == [39, 0, 35]
    # 휴장 전날 밤(09-23 수)은 야간장이 없다 → 구독 없음
    ctl.step(kst(9, 23, 15, 0))
    assert ctl.step(kst(9, 23, 18, 30)).reason == "closed"


def test_controller_keeps_subscriptions_through_the_closing_print() -> None:
    # 15:45:00 종가 단일가 체결은 15:45:00 직후에 온다 — 곧바로 해지하면(선물이 먼저) 놓친다.
    # 클라이언트 late_grace_s(60초) 동안 이전 세션 구독을 두고 그 뒤에 해지한다
    assert CFG.client.late_grace_s == 60.0
    ctl, target, sink = controller(Price(Decimal("1075.0")))
    day = ctl.step(kst(9, 28, 15, 44, 59))
    for t in (kst(9, 28, 15, 45), kst(9, 28, 15, 45, 59)):
        held = ctl.step(t)
        assert (held.reason, held.session, held.desired) == ("grace", "day", day.desired)
        assert Subscription("H0IFCNT0", "A01612") in held.desired
    assert len(target.calls) == 1  # 해지를 보내지 않았다
    assert ctl.step(kst(9, 28, 15, 46)).reason == "closed"
    assert [len(d) for d, _ in target.calls] == [39, 0]
    # 야간 06:00 도 같다
    night = ctl.step(kst(9, 28, 21, 0))
    held = ctl.step(kst(9, 29, 6, 0, 30))
    assert (held.reason, held.session, held.desired) == ("grace", "night", night.desired)
    assert ctl.step(kst(9, 29, 6, 1)).reason == "closed"
    assert "ws_plan_closed" in sink.kinds()
    # 여유 0 이면 곧바로 해지
    now_ctl, _, _ = controller(Price(Decimal("1075.0")), grace_s=0)
    now_ctl.step(kst(9, 28, 15, 44, 59))
    assert now_ctl.step(kst(9, 28, 15, 45)).reason == "closed"


def test_controller_switches_expiry_after_1520() -> None:
    chain = StaticChain(
        {"near": view("WKM:260904", prefix="N"), "next": view("WKI:261001", prefix="X")},
        lambda t: "near" if t < kst(9, 28, 15, 20) else "next",
    )
    ctl, target, sink = controller(Price(Decimal("1075.0")), chain)
    old = ctl.step(kst(9, 28, 15, 19, 59))
    assert old.series == "WKM:260904"
    # 만기 시리즈의 15:20 마감 체결을 받도록 여유(60초) 동안 옛 시리즈를 둔다
    for t in (kst(9, 28, 15, 20), kst(9, 28, 15, 20, 59)):
        held = ctl.step(t)
        assert (held.reason, held.series, held.desired) == ("grace", "WKM:260904", old.desired)
    assert len(target.calls) == 1
    d = ctl.step(kst(9, 28, 15, 21))
    assert (d.reason, d.series) == ("series", "WKI:261001")
    # 시각이 아니라 문맥(마스터·월물리스트 갱신 등)으로 바뀐 것이면 여유 없이 곧바로 바꾼다
    picks = {"now": "a"}
    by_ctx = StaticChain(
        {"a": view("WKM:260904"), "b": view("WKM:261001", prefix="Y")}, lambda _t: picks["now"]
    )
    ctl2, _, _ = controller(Price(Decimal("1075.0")), by_ctx)
    ctl2.step(kst(9, 28, 10, 0))
    picks["now"] = "b"
    assert ctl2.step(kst(9, 28, 10, 0, 1)).reason == "series"
    assert Subscription("H0IOCNT0", "XC030") in d.desired
    assert not any(s.key.startswith("N") for s in d.desired)
    assert "ws_plan_series" in sink.kinds()


def test_controller_uses_only_the_subscribed_futures_price() -> None:
    # 분기 만기 15:20 에 선물 종목이 바뀌면 옛 종목(만기 지난 12월물) 체결가로 새 ATM 을 잡지 않는다
    chain = StaticChain(
        {
            "dec": view("WKM:261202", futures="A01612", prefix="N"),
            "mar": view("WKM:261203", futures="A01703", prefix="X"),
        },
        lambda t: "dec" if t < kst(12, 10, 15, 20) else "mar",
    )
    price = LatestFuturesPrice()

    def tick(code: str, px: str) -> TickEvent:
        t = FuturesTick(
            tr_id="H0IFCNT0", session="day", hhmmss="151900", code=code, price=Decimal(px)
        )
        return TickEvent(t, 1, DAY_NOW, date(2026, 12, 10), "day")

    price.observe(tick("A01612", "1075.0"))
    assert price("A01612") == Decimal("1075.0") and price("A01703") is None
    ctl, _, _ = controller(price, chain)
    assert ctl.step(kst(12, 10, 15, 10)).center == Decimal("1075.0")
    d = ctl.step(kst(12, 10, 15, 21, 30))
    assert (d.reason, d.series, d.center) == ("series", "WKM:261203", None)
    assert d.desired == frozenset({Subscription("H0IFCNT0", "A01703")})  # 새 종목 체결 전 — 선물만
    price.observe(tick("A01703", "1080.0"))
    d = ctl.step(kst(12, 10, 15, 21, 31))
    assert (d.reason, d.center) == ("price", Decimal("1080.0"))
    assert d.desired == plan(DAY, "A01703", view(prefix="X").strikes, 32)


def test_controller_without_chain_warns_and_drops_old_session() -> None:
    picks: dict[str, str | None] = {"now": "a"}
    chain = StaticChain({"a": view()}, lambda _t: picks["now"])
    ctl, target, sink = controller(Price(Decimal("1075.0")), chain)
    ctl.step(kst(9, 28, 10, 0))
    picks["now"] = None
    held = ctl.step(kst(9, 28, 10, 1))
    assert held.reason == "no_chain" and len(held.desired) == 39  # 같은 세션이면 둔다
    assert sink.kinds().count("ws_chain_missing") == 1
    night = ctl.step(kst(9, 28, 17, 55))
    assert (night.reason, night.desired) == ("no_chain", frozenset())  # 주간 TR 을 내린다
    assert target.calls[-1] == (frozenset(), NIGHT)


def opt(series: str, token: str, cp: str, k: int, i: int) -> MasterRow:
    """SYNTHETIC 마스터 위클리(월) 옵션 한 줄."""
    return MasterRow(
        kind="N",
        code=f"{series}{cp}{i:03d}",
        isin="",
        name=f"위클리M {cp} {token} {k:,}.0",
        cp=cp,  # type: ignore[arg-type]
        strike=Decimal(k),
        moneyness="",
        underlying="KOSPI200",
    )


def fut(code: str, yyyymm: str) -> MasterRow:
    """SYNTHETIC 마스터 코스피200 선물 한 줄."""
    return MasterRow(
        kind="1",
        code=code,
        isin="",
        name=f"F {yyyymm}",
        cp="",
        strike=None,
        moneyness="",
        underlying="KOSPI200",
    )


def test_context_chain_source_uses_master_and_expiry_dates() -> None:
    rows = [fut("A01612", "202612")]
    for series, token in (("S1", "2609W4"), ("S2", "2610W1")):
        for i, k in enumerate(range(1050, 1101, 5)):
            rows += [opt(series, token, "C", k, i), opt(series, token, "P", k, i)]
    rows.remove(opt("S1", "2609W4", "P", 1100, 10))  # 풋이 없는 행사가는 뺀다
    ctx = ChainContext(rows)
    ctx.set_listed("WKM", ["260904", "261001"])
    ctx.set_expiry(Series("WKM", "260904"), ExpiryInfo(date(2026, 9, 28), "kis"))
    ctx.set_expiry(Series("WKM", "261001"), ExpiryInfo(date(2026, 10, 6), "kis"))
    src = ContextChainSource(ctx)

    v = src.nearest(kst(9, 28, 9, 30))
    assert v is not None and (v.series, v.futures_code) == ("WKM:260904", "A01612")
    assert [sc.strike for sc in v.strikes] == [Decimal(k) for k in range(1050, 1100, 5)]
    assert (v.strikes[0].call, v.strikes[0].put) == ("S1C000", "S1P000")
    after = src.nearest(kst(9, 28, 15, 20))
    assert after is not None and after.series == "WKM:261001" and len(after.strikes) == 11
    assert ContextChainSource(ChainContext(rows)).nearest(kst(9, 28, 9, 30)) is None  # 만기 모름


def test_context_chain_source_drops_futures_expired_at_1520() -> None:
    # 분기 선물 만기일(12월 둘째 목요일 12-10) 15:20 이 지나면 만기 지난 12월물을 구독하지 않는다
    rows = [fut("A01612", "202612"), fut("A01703", "202703")]
    for series, token in (("S1", "2612W2"), ("S2", "2612W3")):
        for i, k in enumerate(range(1050, 1101, 5)):
            rows += [opt(series, token, "C", k, i), opt(series, token, "P", k, i)]
    ctx = ChainContext(rows)
    ctx.set_listed("WKM", ["261202", "261203"])
    ctx.set_expiry(Series("WKM", "261202"), ExpiryInfo(date(2026, 12, 10), "kis"))
    ctx.set_expiry(Series("WKM", "261203"), ExpiryInfo(date(2026, 12, 17), "kis"))
    src = ContextChainSource(ctx, CAL)

    def pick(t: datetime) -> tuple[str, str]:
        v = src.nearest(t)
        assert v is not None
        return v.series, v.futures_code

    # 12월물 최종거래일은 월물리스트 값이 없으면 캘린더(둘째 목요일)로 본다
    assert pick(kst(12, 10, 15, 19, 59)) == ("WKM:261202", "A01612")
    assert pick(kst(12, 10, 15, 20)) == ("WKM:261203", "A01703")
    assert pick(kst(12, 10, 21, 0)) == ("WKM:261203", "A01703")  # 야간
    ctx.set_futures_codes(["A01612", "A01703"])  # 선물 전광판 순서(근월물부터)여도 같다
    assert pick(kst(12, 10, 15, 20))[1] == "A01703"
    assert pick(kst(12, 10, 15, 19))[1] == "A01612"
    # 월물리스트(KIS) 최종거래일이 있으면 그것이 먼저다 (합성: 하루 당겨진 경우)
    ctx.set_expiry(Series("", "202612"), ExpiryInfo(date(2026, 12, 9), "kis"))
    assert pick(kst(12, 9, 15, 20))[1] == "A01703"
    # 살아 있는 선물이 없으면(마스터에 차월물이 없다) 체인을 주지 않는다
    only_dec = ChainContext([fut("A01612", "202612"), *rows[2:]])
    only_dec.set_listed("WKM", ["261202", "261203"])
    only_dec.set_expiry(Series("WKM", "261203"), ExpiryInfo(date(2026, 12, 17), "kis"))
    assert ContextChainSource(only_dec, CAL).nearest(kst(12, 10, 15, 30)) is None


def test_context_chain_source_skips_expired_futures_dropped_from_reloaded_master() -> None:
    # 분기 만기일 밤 새 마스터에 12월물이 빠졌다 — poller 문맥의 선물 전광판 순서엔 12월물이 남아
    # 있어도(야간 B 는 전광판을 부르지 않는다) 결제월을 몰라 살아 있다고 보지 않는다
    fut_dec, fut_mar = fut("A01612", "202612"), fut("A01703", "202703")
    opts: list[MasterRow] = []
    for i, k in enumerate(range(1050, 1101, 5)):
        opts += [opt("S2", "2612W3", "C", k, i), opt("S2", "2612W3", "P", k, i)]
    ctx = ChainContext([fut_dec, fut_mar, *opts])
    ctx.set_listed("WKM", ["261203"])
    ctx.set_expiry(Series("WKM", "261203"), ExpiryInfo(date(2026, 12, 17), "kis"))
    ctx.set_futures_codes(["A01612", "A01703"])
    src = ContextChainSource(ctx, CAL)
    night = kst(12, 10, 21, 0)
    assert src.futures_code(night) == "A01703"
    ctx.set_master([fut_mar, *opts])  # 새 마스터: 12월물 없음
    assert src.futures_code(night) == "A01703"
    v = src.nearest(night)
    assert v is not None and v.futures_code == "A01703"
    # 마스터에 코스피200 선물이 하나도 없으면(모른다) 전광판 순서를 그대로 믿는다
    ctx.set_master(opts)
    assert src.futures_code(night) == "A01612"


def test_controller_drives_client_end_to_end_with_futures_price_from_ticks() -> None:
    price = LatestFuturesPrice()
    chain = StaticChain({"a": view()}, lambda _t: "a")

    async def body() -> None:
        ticks: list[TickEvent] = []

        def on_tick(ev: TickEvent) -> None:
            ticks.append(ev)
            price.observe(ev)

        async with rig(on_tick=on_tick) as r:
            ctl = SubscriptionController(r.client, chain, price, health=r.health, calendar=CAL)
            ctl.step(DAY_NOW)
            fut_only = frozenset({Subscription("H0IFCNT0", "A01612")})
            await r.until_active(fut_only)
            await r.server.send_tick(
                "H0IFCNT0", futs_shrn_iscd="A01612", bsop_hour="093000", futs_prpr="1080.30"
            )
            await r.server.wait_for(lambda: price("A01612") == Decimal("1080.30"))
            d = ctl.step(DAY_NOW + timedelta(seconds=1))
            assert (d.reason, d.center) == ("price", Decimal("1080.0"))
            await r.until_active(d.desired)
            assert len(r.server.conn().subs) == 39
            assert r.client.max_occupancy <= DAY.futures + DAY.options
            assert r.server.errors == []

            stop = asyncio.Event()
            task = asyncio.create_task(ctl.run(stop, now=lambda: DAY_NOW, tick_s=0.01))
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, 2)
            assert ctl.desired == d.desired

    run(body())
