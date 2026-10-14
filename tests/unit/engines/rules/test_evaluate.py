"""평가기(§4.2-2·metrics §30.3): 연산자·None 은 거짓+사유·3값 논리(장중 수급 없음 = 판단 불가)."""

from __future__ import annotations

from kbj.engines.rules.evaluate import evaluate
from kbj.engines.rules.frame import build_frame
from tests.unit.engines.rules.rule_rows import ANCHOR, CODE, bars, days, flow, frame, rule, slot

LT30 = {"ind": "rsi", "p": {"n": 3}, "op": "<", "value": 30}


def test_rsi_true_false_and_insufficient() -> None:
    f = frame([100.0, 99.0, 98.0, 97.0, 96.0, 110.0])
    r = rule({"all": [LT30]})
    assert evaluate(r, f, 4).ok
    v = evaluate(r, f, 5)
    assert not v.ok and v.state == "false" and v.conds[0].reason is None
    w = evaluate(r, f, 1)
    assert w.state == "false" and w.conds[0].reason == "insufficient"


def test_cross_above_uses_previous_cell() -> None:
    f = frame([10.0, 10.0, 10.0, 9.0, 11.0])
    r = rule({"all": [{"ind": "close", "op": "cross_above", "ref": {"ind": "sma", "p": {"n": 3}}}]})
    v = evaluate(r, f, 4)
    assert v.ok
    c = v.conds[0]
    assert c.prev_value == 9.0 and c.prev_ref is not None and c.prev_ref > 9.0
    assert not evaluate(r, f, 2).ok


def test_between_turns_and_scale() -> None:
    f = frame([100.0, 102.0, 101.0, 103.0])
    assert evaluate(
        rule({"all": [{"ind": "change_pct", "op": "between", "value": [1, 3]}]}), f, 1
    ).ok
    assert evaluate(rule({"all": [{"ind": "change_pct", "op": "turns_positive"}]}), f, 3).ok
    near = rule(
        {
            "all": [
                {"ind": "close", "op": ">=", "ref": {"ind": "high_n", "p": {"n": 2}, "scale": 0.97}}
            ]
        }
    )
    assert evaluate(near, f, 3).ok


def test_all_does_not_short_circuit() -> None:
    f = frame([100.0, 99.0, 98.0, 97.0, 96.0])
    r = rule({"all": [{"ind": "close", "op": ">", "value": 1000}, LT30]})
    v = evaluate(r, f, 4)
    assert not v.ok and len(v.conds) == 2 and v.conds[1].ok is True


def test_any_or_logic() -> None:
    f = frame([100.0, 99.0, 98.0, 97.0, 96.0])
    r = rule({"any": [{"ind": "close", "op": ">", "value": 1000}, LT30]})
    assert evaluate(r, f, 4).ok


def test_skipped_cells() -> None:
    f = frame([100.0] * 5 + [40.0] * 3, split_window=10)
    v = evaluate(rule({"all": [LT30]}), f, 6)
    assert v.state == "skipped" and v.skipped_reason == "split_suspect" and not v.conds


def test_no_live_flow_is_unknown_not_sent() -> None:
    ds = days(4)
    fl = {d: flow(d, 10) for d in ds[:3]}
    bs = bars([100.0] * 3, ds[:3])
    r = rule(
        {
            "all": [
                {"ind": "close", "op": ">", "value": 1},
                {"ind": "foreign_streak", "op": ">=", "value": 1},
            ]
        },
        mode="intraday",
    )
    f = build_frame(
        CODE, bs, upto=ds[2], anchor=ANCHOR, split_ratio=0.31, flows=fl, partial=slot(ds[3], 101.0)
    )
    v = evaluate(r, f, f.last)
    assert not v.ok and v.state == "unknown" and v.skipped_reason == "no_live_flow"
    assert v.conds[1].ok is None and v.conds[1].reason == "no_live_flow"
    # OR 의 다른 가지가 참이면 참(판단 불가가 결과를 바꾸지 못한다)
    r2 = rule(
        {
            "any": [
                {"ind": "close", "op": ">", "value": 1},
                {"ind": "foreign_streak", "op": ">=", "value": 1},
            ]
        },
        mode="intraday",
    )
    assert evaluate(r2, f, f.last).ok
    # AND 의 다른 가지가 거짓이면 거짓
    r3 = rule(
        {
            "all": [
                {"ind": "close", "op": ">", "value": 1e9},
                {"ind": "foreign_streak", "op": ">=", "value": 1},
            ]
        },
        mode="intraday",
    )
    assert evaluate(r3, f, f.last).state == "false"
    live = build_frame(
        CODE,
        bs,
        upto=ds[2],
        anchor=ANCHOR,
        split_ratio=0.31,
        flows=fl,
        partial=slot(ds[3], 101.0),
        partial_flow=flow(ds[3], 3),
    )
    assert evaluate(r, live, live.last).ok


def test_halted_partial() -> None:
    ds = days(3)
    f = build_frame(
        CODE,
        bars([100.0, 100.0], ds[:2]),
        upto=ds[1],
        anchor=ANCHOR,
        split_ratio=0.31,
        partial=slot(ds[2], 100.0, flags=("halted",)),
    )
    assert evaluate(rule({"all": [LT30]}, mode="intraday"), f, f.last).skipped_reason == "halted"


def test_deterministic() -> None:
    f = frame([100.0, 99.0, 98.0, 97.0, 96.0])
    r = rule({"all": [LT30]})
    assert evaluate(r, f, 4) == evaluate(r, f, 4)


def test_live_row_without_that_investor_is_unknown_not_zero() -> None:
    """가집계 행이 있어도 그 구분(기관) 값이 비면(회차 전·그 구분 목록 밖) 판단 불가 — 0 으로 세지
    않는다."""
    ds = days(4)
    fl = {d: flow(d, 10, 10) for d in ds[:3]}
    bs = bars([100.0] * 3, ds[:3])
    f = build_frame(
        CODE, bs, upto=ds[2], anchor=ANCHOR, split_ratio=0.31, flows=fl,
        partial=slot(ds[3], 101.0), partial_flow=flow(ds[3], 3, None),
    )  # fmt: skip
    inst = rule({"all": [{"ind": "inst_streak", "op": ">=", "value": 0}]}, mode="intraday")
    v = evaluate(inst, f, f.last)
    assert v.state == "unknown" and v.conds[0].reason == "no_live_flow" and v.conds[0].value is None
    sell = rule(
        {"all": [{"ind": "inst_streak", "p": {"side": "sell"}, "op": "<", "value": 1}]},
        mode="intraday",
    )
    assert evaluate(sell, f, f.last).state == "unknown"
    total = rule(
        {"all": [{"ind": "inst_sum", "p": {"n": 2}, "op": "<", "value": 1e18}]}, "intraday"
    )
    assert evaluate(total, f, f.last).state == "unknown"
    # 값이 있는 구분(외국인)은 그대로 평가한다
    frg = rule({"all": [{"ind": "foreign_streak", "op": ">=", "value": 4}]}, mode="intraday")
    assert evaluate(frg, f, f.last).ok
    # 마감 칸(부분 봉이 아님)의 빈 값은 판단 불가가 아니라 기존 사유(결측)
    g = build_frame(CODE, bs, upto=ds[2], anchor=ANCHOR, split_ratio=0.31,
                    flows={**fl, ds[2]: flow(ds[2], 3, None)})  # fmt: skip
    assert evaluate(inst, g, g.last).state != "unknown"
