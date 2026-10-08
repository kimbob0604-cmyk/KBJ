"""구성종목 변동(ET tracker.py:fund_pairs·analyze 승격 — 원본 시험 0, conflict_map §1.16).

CU 재산정 보정·5종목 미만 보정 없음·TOP10 깊이·ETF 제외·수량 하한·경계(±2%p 정확히)를 고정한다.
"""

from __future__ import annotations

from datetime import date

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Change, HoldingRow
from kbj.engines.etf.holdings import FundPair, analyze, fund_pairs, sort_key

RUN = date(2026, 10, 8)
PAIR = FundPair("kodex:F1", date(2026, 10, 7), date(2026, 10, 6), 1)


def h(code: str, qty: float | None, wt: float | None = 1.0) -> HoldingRow:
    return HoldingRow(code, f"합성{code}", qty, wt, None, "ETF_ISSUERS:kodex", Quality.OK)


def snap(**qty: float) -> dict[str, HoldingRow]:
    return {k.removeprefix("c"): h(k.removeprefix("c"), v) for k, v in qty.items()}


def kinds(changes: list[Change]) -> list[tuple[str, str]]:
    return [(c.kind, c.code) for c in changes]


def test_fund_pairs_latest_two_within_gap() -> None:
    d = date(2026, 10, 1)
    pairs = fund_pairs({
        "a": [d, date(2026, 10, 6), date(2026, 10, 7)],
        "b": [date(2026, 10, 7)],  # 하나뿐
        "c": [date(2026, 9, 1), date(2026, 10, 7)],  # 36일 공백
        "d": [date(2026, 9, 23), date(2026, 10, 7)],  # 14일 — 경계는 넣는다
    })  # fmt: skip
    assert pairs == [
        FundPair("a", date(2026, 10, 7), date(2026, 10, 6), 1),
        FundPair("d", date(2026, 10, 7), date(2026, 9, 23), 14),
    ]
    with pytest.raises(ValueError):
        fund_pairs({}, 0)


def test_new_and_drop() -> None:
    prev = snap(c100010=1000, c100020=1000)
    cur = snap(c100010=1000, c100030=500)
    out = analyze(PAIR, cur, prev, run_date=RUN)
    assert kinds(out) == [("NEW", "100030"), ("DROP", "100020")]
    new, drop = out
    assert (new.prev_qty, new.cur_qty, new.qty_pct) == (None, 500, None)
    assert (drop.prev_qty, drop.cur_qty) == (1000, None)
    assert (new.run_date, new.fund_id, new.asof, new.prev_asof, new.gap_days) == (
        RUN,
        "kodex:F1",
        PAIR.asof,
        PAIR.prev_asof,
        1,
    )


def test_cu_recalc_is_removed_by_median() -> None:
    codes = [f"10{i:03d}0" for i in range(8)]
    prev = {c: h(c, 1000) for c in codes}
    cur = {c: h(c, 1100) for c in codes}  # 설정단위 변경 — 모두 +10%
    assert analyze(PAIR, cur, prev, run_date=RUN) == []
    cur[codes[0]] = h(codes[0], 1150)  # +15% → 보정 뒤 +5%p
    cur[codes[1]] = h(codes[1], 1000)  # 0% → 보정 뒤 −10%p
    out = analyze(PAIR, cur, prev, run_date=RUN)
    assert kinds(out) == [("ADD", codes[0]), ("CUT", codes[1])]
    assert out[0].qty_pct == 15.0 and out[0].qty_pct_adj == 5.0
    assert out[1].qty_pct_adj == -10.0


def test_less_than_five_base_means_no_correction() -> None:
    codes = [f"10{i:03d}0" for i in range(4)]
    prev = {c: h(c, 1000) for c in codes}
    cur = {c: h(c, 1100) for c in codes}
    out = analyze(PAIR, cur, prev, run_date=RUN)
    assert [c.kind for c in out] == ["ADD"] * 4
    assert analyze(PAIR, cur, prev, run_date=RUN, min_base=4) == []


def test_top10_depth_uses_in_out_and_no_correction() -> None:
    codes = [f"10{i:03d}0" for i in range(8)]
    prev = {c: h(c, 1000) for c in codes}
    cur = {c: h(c, 1100) for c in codes[1:]} | {"200010": h("200010", 10)}
    out = analyze(PAIR, cur, prev, run_date=RUN, depth="top10")
    ks = [c.kind for c in out]
    assert ks.count("IN10") == 1 and ks.count("OUT10") == 1 and ks.count("ADD") == 7


def test_etf_holdings_and_small_qty_are_ignored() -> None:
    prev = snap(c100010=50, c995010=1000, c100020=1000)
    cur = snap(c100010=500, c100020=1000)  # 100010 은 수량 하한(100) 밑 — 비중 변동 안 봄
    assert analyze(PAIR, cur, prev, run_date=RUN, etf_codes={"995010"}) == []
    assert analyze(PAIR, {}, prev, run_date=RUN) == []
    assert analyze(PAIR, cur, {}, run_date=RUN) == []


def test_action_pp_boundary_is_inclusive() -> None:
    prev = snap(c100010=1000)
    assert kinds(analyze(PAIR, snap(c100010=1020), prev, run_date=RUN)) == [("ADD", "100010")]
    assert analyze(PAIR, snap(c100010=1019), prev, run_date=RUN) == []
    with pytest.raises(ValueError):
        analyze(PAIR, prev, prev, run_date=RUN, action_pp=0)


def test_unknown_qty_is_not_a_change() -> None:
    """수량을 모르면(None) 변동률을 내지 않는다 — 0 으로 바꿔 −100% 가짜 CUT 을 만들지 않는다.
    수량 0(운용사가 0 으로 공시)은 그대로 CUT 이다."""
    prev = snap(c100010=1000, c100020=1000)
    cur = {"100010": h("100010", None), "100020": h("100020", 0)}
    assert kinds(analyze(PAIR, cur, prev, run_date=RUN)) == [("CUT", "100020")]
    assert analyze(PAIR, prev, {"100010": h("100010", None), "100020": h("100020", 1000)},
                   run_date=RUN) == []  # fmt: skip


def test_screen_sort_key() -> None:
    keys = {
        "active_theme": sort_key("반도체", True, 1),
        "passive_index": sort_key("시장대표", False, 5),
        "theme": sort_key("반도체", False, 3),
    }
    order = sorted(keys, key=lambda k: keys[k])
    assert order == ["active_theme", "theme", "passive_index"]
