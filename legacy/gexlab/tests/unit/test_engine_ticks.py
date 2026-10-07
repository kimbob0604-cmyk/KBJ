"""engine 틱 플로우 — HIRO-lite·대량 체결 (services/engine/flow.py `TickFlow`·service.py, metrics
§6.1·§6.4, phase3_design §3).

`TickFlow`:

- Δ·F 는 마지막 사이클의 그 종목 자체 델타·그 만기 F(`option_iv`) — 첫 틱은 기준만, 다음 틱부터
  딜러 헤지 = −signed × Δ × m × F, 품질은 늘 estimated. 다른 세션의 틱엔 쓰지 않는다(Δ 모름)
- 시리즈를 모르는 틱은 건너뛰고, 역행 틱은 버리며 health
- 리셋: 시퀀스 공백(선물·옵션 공통 순번)·세션 전환·웹소켓 끊김 — 리셋 직전 상태도 행으로 남는다
- 대량 체결: 기준이 활성이고 그 머니니스 구간 p99 이상이면 block_trade 행

서비스:

- ticks.opt·ticks.fut 메시지 → HIRO 누적, 10초마다 hiro 행(shadow 는 저장만, visible 은 발행)
- ws-gateway 연결 사건 → HIRO 리셋(ws_disconnect) — 그 사건 뒤에 재연결 뒤 첫 틱의 순번 공백이
  이미 리셋했으면 사유만(끊긴 동안 몰린 체결은 어느 행에도 없다), 형식이 틀린 틱은 센다, 한 틱의
  예외는 그것만
- 대량 체결 기준은 거래일마다 한 번 — opt_ticks 20거래일 전엔 비활성, 20거래일이면 활성
- 플래그 off 면 틱을 보지 않는다 — hiro 만 off 면 순번·대량 체결만(누적·역행 health 없음, 누적을
  버려 다시 켜면 처음부터), 구독 루프가 틱 채널을 받는다
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.gex import OPTION_MULTIPLIER
from core.metrics.flow import BLOCK_DAYS, BlockThresholds, HiroState
from data.store import FuturesTickRecord, OptionTickRecord
from services.bus import TICKS_FUT, TICKS_OPT, BasisBook, EngineMetrics
from services.engine.evaluate import CycleResult, evaluate_cycle
from services.engine.flow import TickFlow, hiro_record
from services.engine.records import MetricRecord, OptionIvRecord
from services.engine.registry import Flag
from tests.fakes.engine_inputs import CAL, kst
from tests.unit.test_engine_extended import cycle_input
from tests.unit.test_engine_service import TUE, Rig, T

K = Decimal("1095")
CODE = "B01611C1095"


def opt_tick(
    at: datetime,
    seq: int,
    buy: int | None,
    sell: int | None,
    *,
    strike: Decimal = K,
    cp: str = "C",
    code: str = CODE,
    qty: int | None = 1,
    mrkt_cls: str | None = "",
    expiry: str = "202611",
    trade_date: date = TUE,
    session: str = "day",
    received: datetime | None = None,
) -> OptionTickRecord:
    return OptionTickRecord.model_validate(
        {
            "ts": at,
            "trade_date": trade_date,
            "session": session,
            "received_at": received or at + timedelta(milliseconds=250),
            "tr_id": "H0IOCNT0" if session == "day" else "H0EUCNT0",
            "code": code,
            "seq": seq,
            "price": Decimal("12.5"),
            "qty": qty,
            "cum_buy_qty": buy,
            "cum_sell_qty": sell,
            "mrkt_cls": mrkt_cls,
            "expiry": expiry if mrkt_cls is not None else None,
            "strike": strike if mrkt_cls is not None else None,
            "cp": cp if mrkt_cls is not None else None,
        }
    )


def fut_tick(at: datetime, seq: int) -> FuturesTickRecord:
    return FuturesTickRecord.model_validate(
        {
            "ts": at,
            "trade_date": TUE,
            "session": "day",
            "received_at": at,
            "tr_id": "H0IFCNT0",
            "code": "A01612",
            "seq": seq,
            "price": Decimal("1095.10"),
        }
    )


def learned(flow: TickFlow) -> tuple[CycleResult, float, float]:
    """합성 사이클(TUE 10:00 주간)을 배우고 월물 202611 1095 콜의 자체 델타·F."""
    r = evaluate_cycle(cycle_input(), BasisBook(), cal=CAL)
    flow.learn(r)
    row = next(
        o for o in r.option_iv if (o.mrkt_cls, o.expiry, o.strike, o.cp) == ("", "202611", K, "C")
    )
    assert row.delta is not None and row.forward is not None
    return r, row.delta, row.forward


# ── TickFlow ──


def test_the_flow_is_signed_quantity_times_the_last_cycle_delta_and_forward() -> None:
    flow = TickFlow()
    _, delta, F = learned(flow)
    t0 = kst(TUE, 10, 0, 5)
    assert flow.on_option(opt_tick(t0, 1, 10, 5)) == "baseline"
    assert flow.on_option(opt_tick(t0 + timedelta(seconds=1), 2, 13, 5)) == "applied"
    assert flow.on_option(opt_tick(t0 + timedelta(seconds=2), 3, 13, 9)) == "applied"
    customer = (3 - 4) * delta * OPTION_MULTIPLIER * F
    assert flow.hiro.customer_flow == pytest.approx(customer)
    (row,) = flow.rows("shadow")
    assert row.metric == "hiro" and row.scope == "all" and row.key == ""
    assert row.value == pytest.approx(-customer) and row.quality == "estimated"
    assert (row.ts, row.trade_date, row.session, row.flag) == (
        t0 + timedelta(seconds=2),
        TUE,
        "day",
        "shadow",
    )
    assert row.payload["signed_qty"] == -1 and row.payload["ticks"] == 2
    assert row.payload["reset_reason"] == "start"
    assert flow.rows("shadow") == []  # 바뀐 것이 없으면 행이 없다


def test_ticks_of_another_session_or_without_a_series_are_not_priced() -> None:
    flow = TickFlow()
    learned(flow)  # 주간 사이클
    night = kst(TUE, 19, 0)
    nd = TUE + timedelta(days=1)
    kw: dict[str, Any] = {"trade_date": nd, "session": "night"}
    flow.on_option(opt_tick(night, 1, 10, 0, **kw))
    ev = flow.on_option(opt_tick(night + timedelta(seconds=1), 2, 14, 0, **kw))
    assert ev == "no_price" and flow.hiro.unpriced_qty == 4 and flow.hiro.customer_flow == 0
    # 마스터 밖(미니 등) 틱은 순번만 — 누적에 들지 않는다
    assert flow.on_option(opt_tick(night, 3, 99, 0, mrkt_cls=None, **kw)) is None
    assert flow.events["no_series"] == 1 and len(flow.hiro.cums) == 1


def test_a_reversal_is_dropped_with_health() -> None:
    flow = TickFlow()
    learned(flow)
    t0 = kst(TUE, 10, 0, 5)
    flow.on_option(opt_tick(t0, 1, 10, 5))
    assert flow.on_option(opt_tick(t0, 2, 9, 5)) == "reversal"
    (h,) = flow.drain_health()
    assert h.kind == "engine_hiro_reversal" and h.subject == CODE
    assert flow.hiro.cums[CODE] == (10, 5) and flow.drain_health() == []


def test_a_sequence_gap_resets_and_keeps_the_state_before_it() -> None:
    """선물 틱이 끼어도 순번이 이어지면 공백이 아니다. 공백이면 리셋(시각 = 그 틱 수신 시각) —
    직전 상태는 한 행으로 남고, 그 틱은 새 기준이 된다."""
    flow = TickFlow()
    learned(flow)
    t0 = kst(TUE, 10, 0, 5)
    flow.on_option(opt_tick(t0, 1, 10, 0))
    flow.on_futures(fut_tick(t0, 2))
    assert flow.on_option(opt_tick(t0 + timedelta(seconds=1), 3, 12, 0)) == "applied"
    before = flow.hiro.dealer_hedge
    assert before != 0
    got = kst(TUE, 10, 0, 9).astimezone(UTC) + timedelta(microseconds=123)
    assert flow.on_option(opt_tick(t0 + timedelta(seconds=4), 7, 20, 0, received=got)) == "baseline"
    kept, now = flow.rows("shadow")
    assert kept.value == pytest.approx(before) and kept.payload["reset_reason"] == "start"
    assert kept.ts == t0 + timedelta(seconds=1)
    assert now.value == 0 and now.payload["reset_reason"] == "seq_gap"
    assert now.payload["reset_at"] == got.isoformat() and now.ts == got


def test_a_session_change_resets_and_keeps_the_last_state_of_the_old_session() -> None:
    flow = TickFlow()
    learned(flow)
    t0 = kst(TUE, 15, 44)
    flow.on_option(opt_tick(t0, 1, 10, 0))
    flow.on_option(opt_tick(t0 + timedelta(seconds=1), 2, 11, 0))
    nd = TUE + timedelta(days=1)
    night = kst(TUE, 18, 0, 1)
    flow.on_option(opt_tick(night, 3, 1, 0, trade_date=nd, session="night"))
    day_row, night_row = flow.rows("visible")
    assert (day_row.trade_date, day_row.session, day_row.payload["ticks"]) == (TUE, "day", 1)
    assert (night_row.trade_date, night_row.session) == (nd, "night")
    assert night_row.payload["reset_reason"] == "session_change" and night_row.value == 0


def test_a_reset_before_any_tick_does_nothing_and_a_ws_reset_is_marked() -> None:
    flow = TickFlow()
    assert flow.reset("ws_disconnect", T) is False and flow.rows("shadow") == []
    learned(flow)
    flow.on_option(opt_tick(kst(TUE, 10, 0, 5), 1, 10, 0))
    flow.rows("shadow")
    at = kst(TUE, 10, 1)
    assert flow.reset("ws_disconnect", at) is True
    (row,) = flow.rows("shadow")  # 직전 행 뒤 바뀐 것이 없어 리셋 행 하나
    assert row.payload["reset_reason"] == "ws_disconnect" and row.ts == at
    assert flow.hiro.cums == {}


def test_a_connection_event_resets_unless_a_later_seq_gap_or_ws_reset_already_did() -> None:
    """연결 사건(가장 늦은 시각 e): 마지막 리셋이 e 이후의 순번 공백이면 사유만 ws_disconnect 로,
    e 이후의 끊김 리셋이면 할 일 없음, 그 밖(e 앞의 리셋·체결 시각의 start·세션 전환)은 리셋."""
    flow = TickFlow()
    t0 = kst(TUE, 10, 0, 5)
    assert flow.disconnected(t0, t0) is None  # 아직 틱이 없다
    learned(flow)
    flow.on_option(opt_tick(t0, 1, 10, 0))
    at = t0 + timedelta(seconds=3)
    assert flow.disconnected(t0 - timedelta(seconds=1), at) == "reset"  # start 는 늘 리셋
    assert (flow.hiro.reset_reason, flow.hiro.reset_at) == ("ws_disconnect", at)
    assert flow.disconnected(at - timedelta(seconds=1), at + timedelta(seconds=5)) is None
    got = at + timedelta(seconds=2)
    flow.on_option(opt_tick(at, 3, 20, 0, received=got))  # 순번 2 없음 — 공백 리셋
    flow.on_option(opt_tick(at, 4, 22, 0, received=got))
    assert flow.hiro.reset_reason == "seq_gap"
    flow.rows("shadow")
    assert flow.disconnected(got, got + timedelta(seconds=5)) == "relabel"
    (row,) = flow.rows("shadow")
    assert row.payload["reset_reason"] == "ws_disconnect" and row.payload["signed_qty"] == 2
    later = got + timedelta(seconds=1)
    assert flow.disconnected(later, later) == "reset" and flow.hiro.signed_qty == 0


def test_block_trades_need_active_thresholds_and_a_forward() -> None:
    flow = TickFlow()
    _, _, F = learned(flow)
    t0 = kst(TUE, 10, 0, 5)
    flow.on_option(opt_tick(t0, 1, 10, 0, qty=500))
    assert flow.drain_blocks("shadow") == []  # 비활성
    bucket = 0 if K >= Decimal(str(F)) else -1  # K/F − 1 의 1% 구간
    days = tuple(CAL.prev_trading_day(TUE) - timedelta(days=i) for i in range(BLOCK_DAYS))
    flow.thresholds = BlockThresholds(days, True, {("C", bucket): 100}, 2000)
    flow.on_option(opt_tick(t0, 2, 11, 0, qty=99))
    flow.on_option(opt_tick(t0 + timedelta(seconds=1), 3, 111, 0, qty=100))
    flow.on_option(opt_tick(t0, 4, 12, 0, qty=500, cp="P", code="B01611P1095"))  # 풋 구간 기록 없음
    (row,) = flow.drain_blocks("shadow")
    assert (row.metric, row.scope, row.key, row.value) == (
        "block_trade",
        "series",
        "M:202611:1095:C:3",
        100.0,
    )
    assert row.payload["threshold"] == 100 and row.payload["bucket"] == bucket
    assert row.payload["forward"] == F and row.quality == "ok"
    assert flow.drain_blocks("shadow") == []


def test_with_hiro_off_only_the_sequence_and_block_trades_are_seen() -> None:
    """hiro 가 꺼졌으면(대량 체결만 켜짐) HIRO 누적·역행 health 없이 순번과 대량 체결 판정만 —
    켜져 있던 누적은 버린다. 다시 켜면 처음부터(start — 재기동과 같다): 종목의 첫 틱은 기준만이라
    끈 동안 늘어난 누적이 흐름에 들지 않는다(검토 F2)."""
    flow = TickFlow()
    _, _, F = learned(flow)
    t0 = kst(TUE, 10, 0, 5)
    flow.on_option(opt_tick(t0, 1, 10, 0))
    flow.on_option(opt_tick(t0, 2, 12, 0))
    assert flow.hiro.signed_qty == 2
    bucket = 0 if K >= Decimal(str(F)) else -1
    days = tuple(CAL.prev_trading_day(TUE) - timedelta(days=i) for i in range(BLOCK_DAYS))
    flow.thresholds = BlockThresholds(days, True, {("C", bucket): 100}, 2000)
    assert flow.on_option(opt_tick(t0, 3, 112, 0, qty=100), hiro=False) is None
    assert flow.on_option(opt_tick(t0, 4, 111, 0), hiro=False) is None  # 역행 — 보지 않는다
    assert flow.hiro == HiroState() and flow.rows("shadow") == [] and flow.drain_health() == []
    assert flow.last_seq == 4
    assert [r.key for r in flow.drain_blocks("shadow")] == ["M:202611:1095:C:3"]
    t1 = t0 + timedelta(seconds=1)
    assert flow.on_option(opt_tick(t1, 5, 120, 0)) == "baseline"
    assert flow.on_option(opt_tick(t1, 6, 121, 0)) == "applied"
    st = flow.hiro
    assert (st.reset_reason, st.reset_at, st.signed_qty, st.reversals) == ("start", t1, 1, 0)


def test_a_hiro_record_needs_a_session() -> None:
    with pytest.raises(ValueError, match="세션"):
        hiro_record(HiroState(), "shadow")


# ── 서비스 ──


def msg(channel: str, rec: OptionTickRecord | FuturesTickRecord | bytes) -> dict[str, Any]:
    data = rec if isinstance(rec, bytes) else rec.model_dump_json().encode()
    return {"type": "message", "channel": channel.encode(), "data": data}


def hiro_rows(rig: Rig) -> list[MetricRecord]:
    return sorted((m for m in rig.store.metrics.values() if m.metric == "hiro"), key=lambda m: m.ts)


def test_the_service_accumulates_ticks_and_writes_hiro_rows_every_ten_seconds() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))  # 자체 델타·F 를 배운다
    t0 = T + timedelta(seconds=5)
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t0, 1, 10, 0)))
    rig.svc.on_message(msg(TICKS_FUT, fut_tick(t0, 2)))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t0, 3, 15, 0)))
    rig.svc.on_message(msg(TICKS_OPT, b"{not json"))
    assert (rig.svc.stats.ticks, rig.svc.stats.bad_ticks) == (3, 1)
    rig.svc.tick()
    (row,) = hiro_rows(rig)
    assert row.flag == "shadow" and row.quality == "estimated" and row.value is not None
    assert row.value < 0  # 콜 매수 주도 — 딜러 헤지는 매도(음수)
    sent = [x.metric for m in rig.published() if isinstance(m, EngineMetrics) for x in m.metrics]
    assert sent and "hiro" not in sent  # shadow 는 저장만(사이클의 visible 지표는 나갔다)
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t0 + timedelta(seconds=1), 4, 16, 0)))
    rig.clock.advance(5)
    rig.svc.tick()
    assert len(hiro_rows(rig)) == 1  # 10초가 안 됐다
    rig.clock.advance(5)
    rig.svc.tick()
    assert len(hiro_rows(rig)) == 2 and rig.svc.stats.hiro_rows == 2


def test_visible_hiro_rows_are_published() -> None:
    rig = Rig()
    flags: dict[str, Flag] = {"hiro": "visible"}
    rig.svc.flags = flags
    rig.svc.on_ready(rig.add_cycle(T))
    rig.published()
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T + timedelta(seconds=5), 1, 10, 0)))
    rig.svc.tick()
    got = [m for m in rig.published() if any(x.metric == "hiro" for x in m.metrics)]
    (m,) = got
    assert m.metrics[0].quality == "estimated" and m.quality == "estimated"
    assert (m.trade_date, m.session) == (TUE, "day")


def test_a_ws_gateway_connection_event_resets_the_flow() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 1, 10, 0)))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 2, 12, 0)))
    rig.svc.tick()
    assert rig.svc.flow.hiro.customer_flow != 0
    rig.store.ws_events.append((T - timedelta(hours=1), "ws_disconnected"))  # 기동 전 — 보지 않는다
    rig.clock.advance(5)
    rig.svc.tick()
    assert rig.svc.flow.hiro.reset_reason == "start"
    rig.store.ws_events.append((rig.clock.now(), "ws_disconnected"))
    rig.clock.advance(5)
    rig.svc.tick()
    assert rig.svc.flow.hiro.reset_reason == "ws_disconnect"
    assert rig.svc.flow.hiro.customer_flow == 0 and rig.svc.flow.hiro.cums == {}
    rig.clock.advance(10)
    rig.svc.tick()
    assert hiro_rows(rig)[-1].payload["reset_reason"] == "ws_disconnect"


def test_a_reconnect_jump_is_never_applied_and_the_event_poll_keeps_the_new_flow() -> None:
    """ws-gateway 재연결 — 끊긴 동안 500 계약이 재연결 뒤 첫 틱의 누적에 몰린다. ws-gateway 가
    재연결마다 순번 하나를 건너뛰어(2 → 4) 그 틱은 반영 전에 리셋되고 새 기준이 된다 — hiro 행에
    몰린 흐름이 남지 않는다(검토 F2). 5초 주기의 연결 사건 조회는 그 사건 뒤에 이미 리셋했으니 다시
    비우지 않고 사유만 ws_disconnect 로(재연결 뒤 흐름은 그대로). 그 뒤 새 사건이면 다시 리셋."""
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 1, 10, 0)))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 2, 12, 0)))
    rig.svc.tick()  # 연결 사건 없음, hiro 행 하나
    down, up = T + timedelta(seconds=1), T + timedelta(seconds=2)
    rig.store.ws_events += [(down, "ws_disconnected"), (up, "ws_connected")]
    t3 = T + timedelta(seconds=3)
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t3, 4, 512, 0)))  # 재연결 뒤 첫 틱 — 순번 3 없음
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t3 + timedelta(seconds=1), 5, 515, 0)))
    st = rig.svc.flow.hiro
    assert (st.reset_reason, st.signed_qty, st.ticks) == ("seq_gap", 3, 1)
    kept = st.customer_flow
    rig.clock.advance(5)
    rig.svc.tick()  # 연결 사건 조회 — 이미 리셋했다
    st = rig.svc.flow.hiro
    assert (st.reset_reason, st.signed_qty, st.customer_flow) == ("ws_disconnect", 3, kept)
    assert st.reset_at == t3 + timedelta(milliseconds=250)  # 그 틱 수신 시각
    rig.clock.advance(5)
    rig.svc.tick()
    rows = hiro_rows(rig)
    assert [r.payload["signed_qty"] for r in rows] == [2, 3]  # 몰린 500 은 어느 행에도 없다
    assert rows[-1].payload["reset_reason"] == "ws_disconnect"
    rig.store.ws_events.append((rig.clock.now(), "ws_disconnected"))  # 그 뒤 새 끊김
    rig.clock.advance(5)
    rig.svc.tick()
    st = rig.svc.flow.hiro
    assert (st.reset_reason, st.signed_qty, st.reset_at) == ("ws_disconnect", 0, rig.clock.now())


def test_one_bad_tick_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(rig.svc.flow, "on_option", boom)
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 1, 10, 0)))
    assert rig.svc.stats.bad_ticks == 1 and rig.health.kinds() == ["engine_flow_failed"]
    assert rig.svc.on_ready(rig.add_cycle(T)) is not None and rig.store.levels


def test_flags_off_skip_the_ticks() -> None:
    rig = Rig()
    off: dict[str, Flag] = {"hiro": "off", "block_trades": "off"}
    rig.svc.flags = off
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 1, 10, 0)))
    rig.svc.tick()
    assert rig.svc.stats.ticks == 0 and rig.svc.flow.hiro.trade_date is None
    assert not [m for m in rig.store.metrics.values() if m.metric in ("hiro", "block_trades")]


def test_hiro_off_with_block_trades_on_computes_no_hiro_and_starts_over_when_turned_on() -> None:
    """검토 F2 재현 — hiro off·block_trades shadow: 틱은 순번·대량 체결만 보고 HIRO 누적·역행
    health·hiro 행이 없다. shadow 로 켜면 처음부터(start) — 첫 행에 끈 동안의 흐름·역행이 없다."""
    rig = Rig()
    flags: dict[str, Flag] = {"hiro": "off", "block_trades": "shadow"}
    rig.svc.flags = flags
    rig.svc.on_ready(rig.add_cycle(T))
    t0 = T + timedelta(seconds=5)
    for seq, buy in ((1, 10), (2, 15), (3, 12)):  # 셋째는 역행
        rig.svc.on_message(msg(TICKS_OPT, opt_tick(t0, seq, buy, 0)))
    rig.svc.tick()
    assert rig.svc.stats.ticks == 3 and rig.svc.flow.hiro == HiroState()
    assert "engine_hiro_reversal" not in rig.health.kinds() and not hiro_rows(rig)
    on: dict[str, Flag] = {"hiro": "shadow", "block_trades": "shadow"}
    rig.svc.flags = on
    rig.clock.advance(11)
    rig.svc.tick()
    assert not hiro_rows(rig)  # 켠 뒤 틱이 아직 없다
    t1 = t0 + timedelta(seconds=11)
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t1, 4, 20, 0)))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t1, 5, 21, 0)))
    rig.clock.advance(10)
    rig.svc.tick()
    (row,) = hiro_rows(rig)
    p = row.payload
    assert (p["signed_qty"], p["ticks"], p["reversals"], p["reset_reason"]) == (1, 1, 0, "start")
    assert datetime.fromisoformat(p["reset_at"]) == t1 and row.flag == "shadow"


def test_turning_hiro_off_drops_the_flow() -> None:
    """켜져 있던 HIRO 를 끄면 누적과 아직 행으로 내지 않은 상태를 버린다 — 틱이 오지 않아도(10초
    곁일) 버리고, 끈 뒤엔 hiro 행이 없다."""
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 1, 10, 0)))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 2, 12, 0)))
    assert rig.svc.flow.hiro.signed_qty == 2
    off: dict[str, Flag] = {"hiro": "off"}
    rig.svc.flags = off
    rig.svc.tick()
    assert rig.svc.flow.hiro == HiroState() and rig.svc.flow.pending == []
    assert not rig.svc.flow.dirty and not hiro_rows(rig)


def _iv_row(at: datetime, d: date, forward: float) -> OptionIvRecord:
    return OptionIvRecord(
        ts=at,
        trade_date=d,
        session="day",
        mrkt_cls="",
        expiry="202611",
        strike=K,
        cp="C",
        quote_source="board",
        price=Decimal("20"),
        price_kind="mid",
        prev_session=False,
        oi=500,
        iv=0.22,
        source="self",
        rescaled=False,
        t_kis=None,
        reason=None,
        excluded=None,
        delta=0.5,
        gamma=0.01,
        forward=forward,
        t_years=0.1,
        quality="ok",
    )


def _history(rig: Rig, n: int) -> list[date]:
    """TUE 앞 n 거래일 — 날마다 1095 콜 틱 100건(체결량 1~100)과 그 앞 engine F 1095."""
    days: list[date] = []
    d = TUE
    for _ in range(n):
        d = CAL.prev_trading_day(d)
        days.append(d)
        at = kst(d, 11, 0)
        rig.store.option_iv[("iv", d)] = _iv_row(at - timedelta(minutes=1), d, 1095.0)
        for q in range(1, 101):
            rig.store.opt_ticks.append(opt_tick(at, q, None, None, qty=q, trade_date=d))
    return sorted(days)


def blocks_status(rig: Rig) -> list[MetricRecord]:
    return [m for m in rig.store.metrics.values() if m.metric == "block_trades"]


def test_block_trades_stay_inactive_until_twenty_trading_days_of_ticks() -> None:
    rig = Rig()
    _history(rig, BLOCK_DAYS - 1)
    rig.svc.tick()
    (st,) = blocks_status(rig)
    assert st.payload["active"] is False and st.value == BLOCK_DAYS - 1
    assert (st.trade_date, st.session, st.flag) == (TUE, "day", "shadow")
    rig.svc.on_ready(rig.add_cycle(T))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(T, 1, 10, 0, qty=10_000)))
    rig.clock.advance(1)
    rig.svc.tick()
    assert not [m for m in rig.store.metrics.values() if m.metric == "block_trade"]
    assert len(blocks_status(rig)) == 1  # 그 거래일 한 번


def test_block_trades_turn_on_with_twenty_days_and_flag_big_ticks() -> None:
    rig = Rig()
    days = _history(rig, BLOCK_DAYS)
    rig.svc.on_ready(rig.add_cycle(T))
    rig.svc.tick()
    (st,) = blocks_status(rig)
    assert st.payload["active"] is True and st.payload["first_day"] == days[0].isoformat()
    assert rig.svc.flow.thresholds.table == {("C", 0): 99}
    t1 = T + timedelta(seconds=7)
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t1, 1, 10, 0, qty=98)))
    rig.svc.on_message(msg(TICKS_OPT, opt_tick(t1, 2, 110, 0, qty=100)))
    rig.clock.advance(1)
    rig.svc.tick()
    (row,) = [m for m in rig.store.metrics.values() if m.metric == "block_trade"]
    assert row.value == 100.0 and row.ts == t1 and row.key == "M:202611:1095:C:2"
    assert rig.svc.stats.block_trades == 1


def test_a_failed_threshold_read_is_retried_a_minute_later(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()
    calls: list[int] = []

    def boom(*_a: Any) -> Any:
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(rig.store, "opt_tick_days", boom)
    rig.svc.tick()
    assert calls == [1] and "engine_flow_failed" in rig.health.kinds()
    rig.clock.advance(30)
    rig.svc.tick()
    assert calls == [1]
    rig.clock.advance(31)
    rig.svc.tick()
    assert calls == [1, 1]


def test_the_run_loop_takes_ticks_from_redis() -> None:
    rig = Rig(kst(TUE, 16, 30))
    stop = threading.Event()
    th = threading.Thread(target=rig.svc.run, args=(stop, lambda: rig.redis), daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    seq = 0
    while rig.svc.stats.ticks < 2 and time.monotonic() < deadline:
        seq += 1
        rig.redis.publish(TICKS_OPT, opt_tick(T, seq, 10 + seq, 0).model_dump_json())
        seq += 1
        rig.redis.publish(TICKS_FUT, fut_tick(T, seq).model_dump_json())
        time.sleep(0.05)
    stop.set()
    th.join(5)
    assert not th.is_alive() and rig.svc.stats.ticks >= 2
