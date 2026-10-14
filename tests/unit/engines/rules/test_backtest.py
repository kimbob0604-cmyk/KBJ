"""규칙 백테스트(D-P8-6·13, metrics §30.4) — 손계산 합성 입력(실데이터 아님)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pytest

from kbj.engines.backtest.costs import Costs
from kbj.engines.rules.backtest import (
    BacktestTimeout,
    CodeData,
    FrameInputs,
    TradeParams,
    modes_of,
    run_backtest,
)
from kbj.engines.rules.evaluate import evaluate
from kbj.engines.rules.live import close_eval_ts
from tests.unit.engines.rules.rule_rows import ANCHOR, CODE, bars, days, flow, rule, slot

ZERO = Costs(0, 0, 0)
JUMP = {"ind": "change_pct", "op": ">", "value": 3}
DS = days(45)
RID = "r_000000000001"


def _closes() -> list[float | None]:
    c: list[float | None] = [100.0] * 45
    for i in range(20, 45):
        c[i] = 105.0
    for i in range(30, 45):
        c[i] = 110.25  # 칸 20·30 에서 +5%
    return c


def _data(closes: Sequence[float | None] | None = None, **kw: object) -> dict[str, CodeData]:
    fi = FrameInputs(CODE, bars(closes or _closes(), DS), anchor=ANCHOR, split_ratio=0.31,
                     calendar=DS)  # fmt: skip
    return {CODE: CodeData(fi, **kw)}  # type: ignore[arg-type]


def _run(r: object, data: dict[str, CodeData] | None = None, **kw: object):
    args: dict[str, object] = {
        "rule_id": RID, "start": DS[0], "end": DS[-1], "modes": ("close",), "cooldown_min": 1440,
        "params": TradeParams(hold=2, costs=ZERO),
    }  # fmt: skip
    args.update(kw)
    return run_backtest(r, data or _data(), **args)  # type: ignore[arg-type]


def test_close_mode_next_open_and_hold() -> None:
    out = _run(rule({"all": [JUMP]}))
    assert [(s.trade_date, s.status) for s in out.signals] == [(DS[20], "dry"), (DS[30], "dry")]
    assert out.signals[0].eval_ts == close_eval_ts(DS[20])  # 실시간 마감 신호와 같은 시각
    assert out.signals[0].entry_price == pytest.approx(105.0)  # 마감 신호 진입가 = 그날 종가
    t = out.trades[0]
    assert (t.signal_date, t.entry_date, t.exit_date) == (DS[20], DS[21], DS[22])
    assert t.entry_price == pytest.approx(105 * 0.99)  # d+1 시가(합성 봉 시가 = 종가 × 0.99)
    assert t.exit_price == pytest.approx(105.0) and t.reason == "hold" and t.bars == 2
    assert t.ret_pct == pytest.approx((105 / (105 * 0.99) - 1) * 100)
    assert out.stats.n == 2 and out.stats.win_rate == pytest.approx(100.0)
    assert [p[0] for p in out.curve] == [DS[22], DS[32]]
    assert out.curve[-1][1] == pytest.approx((1 + t.ret_pct / 100) ** 2)


def test_signal_matches_direct_evaluate() -> None:
    """같은 평가기 — 백테스트 신호 칸은 evaluate 가 참인 칸과 같다(D-P8-6)."""
    r = rule({"all": [JUMP]})
    fr = _data()[CODE].inputs.close_frame(DS[-1])
    want = [fr.dates[i] for i in range(fr.n) if evaluate(r, fr, i).ok]
    out = _run(r, cooldown_min=1)
    assert [s.trade_date for s in out.signals] == want
    assert all(s.input_fp == evaluate(r, fr, fr.index_of(s.trade_date) or 0).input_fp
               for s in out.signals)  # fmt: skip


def test_cooldown_blocks_and_records() -> None:
    out = _run(rule({"all": [JUMP]}), cooldown_min=60 * 24 * 20)
    assert [s.status for s in out.signals] == ["dry", "cooldown"]
    assert len(out.trades) == 1  # 막힌 신호는 거래 없음


def test_stop_first_when_both_touch() -> None:
    p = TradeParams(hold=5, stop_pct=-1.0, target_pct=1.0, costs=ZERO)
    out = _run(rule({"all": [JUMP]}), params=p)
    t = out.trades[0]
    # 진입 칸(d+1) 저가 105×0.98 ≤ 손절 103.95×0.99, 고가 105×1.01 ≥ 익절 — 둘 다면 손절
    assert t.reason == "stop" and t.exit_date == DS[21]
    assert t.exit_price == pytest.approx(105 * 0.99 * 0.99)


def test_costs_reduce_return() -> None:
    c = Costs(fee_bps=1.5, sell_tax_pct=0.2, slippage_bps=0)
    a = _run(rule({"all": [JUMP]})).trades[0]
    b = _run(rule({"all": [JUMP]}), params=TradeParams(hold=2, costs=c)).trades[0]
    want = (105 * (1 - 0.00015 - 0.002)) / (105 * 0.99 * (1 + 0.00015)) - 1
    assert b.ret_pct == pytest.approx(want * 100) and b.ret_pct < a.ret_pct


def test_halted_cells_are_skipped_not_signals() -> None:
    c = _closes()
    c[25] = None
    out = _run(rule({"all": [JUMP]}), _data(c))
    assert out.skipped.get("halted") == 1
    assert DS[25] not in [s.trade_date for s in out.signals]


def test_window_limits_evaluation() -> None:
    out = _run(rule({"all": [JUMP]}), start=DS[25], end=DS[35])
    assert [s.trade_date for s in out.signals] == [DS[30]]


def test_outcome_cells_use_same_function() -> None:
    out = _run(rule({"all": [JUMP]}), horizons=(1, 5))
    s0 = out.signals[0]
    assert s0.rets["h1"] == pytest.approx(0.0) and s0.rets["h5"] == pytest.approx(0.0)
    s1 = out.signals[1]
    assert s1.rets == {"h1": pytest.approx(0.0), "h5": pytest.approx(0.0)}
    c1 = out.outcome[0]
    assert c1.horizon == 1 and c1.n == 2 and c1.small


def _intraday_data(live: bool = True) -> dict[str, CodeData]:
    c: list[float | None] = [100.0] * 45
    fi = FrameInputs(CODE, bars(c, DS), anchor=ANCHOR, split_ratio=0.31, calendar=DS,
                     flows={d: flow(d, 10) for d in DS})  # fmt: skip
    d = DS[40]
    ss = [slot(d, 101.0, hhmm=(9, 50)), slot(d, 104.0, hhmm=(10, 0)),
          slot(d, 105.0, hhmm=(10, 10))]  # fmt: skip
    lf = {s.slot_ts: flow(d, 5) for s in ss} if live else {}
    return {CODE: CodeData(fi, slots=ss, live_flows=lf)}


def test_intraday_replay_slot_entry_and_cooldown() -> None:
    r = rule({"all": [JUMP]}, mode="intraday")
    out = _run(r, _intraday_data(), modes=modes_of(r))
    assert [(s.eval_ts.hour, s.eval_ts.minute, s.status) for s in out.signals] == [
        (10, 0, "dry"),
        (10, 10, "cooldown"),
    ]
    assert all(s.mode == "intraday_replay" for s in out.signals)
    t = out.trades[0]
    assert t.entry_price == pytest.approx(104.0) and t.entry_date == DS[40]
    # 진입 칸 장중은 보지 않고(슬롯 뒤 고저 모름) 보유 2 = 진입 칸 + 다음 칸 종가
    assert t.exit_date == DS[41] and t.exit_price == pytest.approx(100.0)


def test_intraday_flow_condition_unknown_without_live_flow() -> None:
    r = rule({"all": [JUMP, {"ind": "foreign_streak", "op": ">=", "value": 1}]}, mode="intraday")
    with_live = _run(r, _intraday_data(True), modes=modes_of(r))
    assert len(with_live.signals) == 2
    no_live = _run(r, _intraday_data(False), modes=modes_of(r))
    # 09:50 은 등락률 조건이 거짓(AND → 거짓), 10:00·10:10 은 판단 불가
    assert no_live.signals == () and no_live.skipped.get("no_live_flow") == 2


def test_both_mode_shares_cooldown() -> None:
    data = _intraday_data()
    fi = data[CODE].inputs
    c = [b.close for b in fi.bars]
    c[40] = 105.0  # 그날 마감도 +5%
    fi2 = FrameInputs(CODE, bars(c, DS), anchor=ANCHOR, split_ratio=0.31, calendar=DS,
                      flows=fi.flows)  # fmt: skip
    d2 = {CODE: CodeData(fi2, slots=data[CODE].slots, live_flows=data[CODE].live_flows)}
    r = rule({"all": [JUMP]}, mode="both")
    out = _run(r, d2, modes=modes_of(r))
    assert [(s.mode, s.status) for s in out.signals] == [
        ("intraday_replay", "dry"),
        ("intraday_replay", "cooldown"),
        ("close", "cooldown"),  # 같은 날 마감은 장중 신호의 쿨다운에 막힌다
    ]


def test_timeout_and_bad_args() -> None:
    with pytest.raises(BacktestTimeout):
        _run(rule({"all": [JUMP]}), should_stop=lambda: True)
    with pytest.raises(ValueError):
        _run(rule({"all": [JUMP]}), start=DS[5], end=DS[1])
    with pytest.raises(ValueError):
        TradeParams(hold=0)
    with pytest.raises(ValueError):
        TradeParams(stop_pct=1.0)


def test_empty_result_has_no_values() -> None:
    out = _run(rule({"all": [{"ind": "change_pct", "op": ">", "value": 50}]}))
    assert out.signals == () and out.trades == () and out.stats.n == 0
    assert out.stats.mean is None and out.curve == ()
    assert out.evaluated == 45


def test_modes_of() -> None:
    assert modes_of(rule({"all": [JUMP]})) == ("close",)
    assert modes_of(rule({"all": [JUMP]}, mode="both")) == ("close", "intraday_replay")


def test_dates_are_trading_days() -> None:
    assert all(isinstance(d, date) for d in DS)
