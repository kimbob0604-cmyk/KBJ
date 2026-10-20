"""프로그램매매 엔진(metrics §22) — 검산 ④ 손계산·일별 막대·상위 순위·범위 꼬리표·훑기 범위."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.core.p5_rows import ProgramMarketRow, ProgramStockRow
from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow
from kbj.engines.flows.program import (
    CHECK4_TOL,
    apply_program_checks,
    check4,
    day_bars,
    program_report,
    rank_top,
    scan_codes,
    scope_label,
    sweep_universe,
)
from tests.fixtures.synthetic.p5_world import build_world

DAY = date(2026, 11, 17)


def group(d: date = DAY, nudge: int = 0) -> list[ProgramMarketRow]:
    """손계산 묶음 — (매도, 매수) 대금(원): 차익 위탁 (5, 15)·자기 (8, 4), 비차익 위탁 (30, 10)·
    자기 (0, 30) → 순매수 10·-4·-20·30. 합계·전체는 칸마다 더한다. nudge 는 전체 합계 순매수만."""
    base = {
        ("arbt", "entm"): (5, 15),
        ("arbt", "onsl"): (8, 4),
        ("nabt", "entm"): (30, 10),
        ("nabt", "onsl"): (0, 30),
    }
    for kind in ("arbt", "nabt"):
        e, o = base[(kind, "entm")], base[(kind, "onsl")]
        base[(kind, "smtn")] = (e[0] + o[0], e[1] + o[1])
    for side in ("entm", "onsl", "smtn"):
        a, n = base[("arbt", side)], base[("nabt", side)]
        base[("whol", side)] = (a[0] + n[0], a[1] + n[1])
    out = []
    for (kind, side), (sv, bv) in base.items():
        net = bv - sv + (nudge if (kind, side) == ("whol", "smtn") else 0)
        out.append(ProgramMarketRow(d, "K", "KRX", kind, side, 0, 0, 0, sv, bv, net, 2500.0, "KIS",  # pyright: ignore[reportArgumentType]
                                    Quality.OK))  # fmt: skip
    return out


def test_check4_hand_computed() -> None:
    assert check4(group()) == []
    over = CHECK4_TOL["net_value"] + 1
    fails = check4(group(nudge=over))
    assert {(f.rule, f.at, f.measure, f.residual) for f in fails} == {
        ("whol=arbt+nabt", "smtn", "net_value", over),
        ("smtn=entm+onsl", "whol", "net_value", over),
    }


def test_check4_allows_one_unit_of_kis_rounding() -> None:
    """KIS 는 칸마다 백만원으로 반올림한다 — 잔차 ±1 단위는 통과(실측 2026-10-20: 72,944 +
    (-280,794) = -207,850 대 전체 -207,849)."""
    assert check4(group(nudge=CHECK4_TOL["net_value"])) == []
    assert check4(group(nudge=-CHECK4_TOL["net_value"])) == []
    # buy_value 도 바뀌었으면 그 칸도 센다 — 여기선 net 만 바꿨다


def test_apply_checks_marks_whole_group_invalid_and_bar_has_no_value() -> None:
    rows = group(date(2026, 11, 16)) + group(nudge=5 * CHECK4_TOL["net_value"])
    checked, fails = apply_program_checks(rows)
    assert fails and {r.quality for r in checked if r.trade_date == DAY} == {Quality.INVALID}
    bars = day_bars(checked)
    assert [b.date for b in bars] == [date(2026, 11, 16), DAY]
    good, bad = bars
    assert (good.net, good.arbt_net, good.nabt_net, good.entm_net, good.onsl_net) == (
        16,
        6,
        10,
        -10,
        26,
    )
    assert good.index_close == 2500.0 and good.quality == Quality.OK
    assert bad.net is None and bad.quality == Quality.INVALID


def test_missing_member_is_not_possible_not_zero() -> None:
    rows = [r for r in group() if (r.kind, r.side) != ("arbt", "onsl")]
    assert check4(rows) == []
    rep = program_report(rows)
    assert rep["failed"] == 0 and rep["na"] > 0


def stock(code: str, net: int | None, q: Quality = Quality.OK) -> ProgramStockRow:
    return ProgramStockRow(
        DAY, code, "K", "KRX", "sweep", 1000.0, 0.1, None, None, None, None, None, net, "KIS", q
    )


def test_rank_top_buy_sell_ties_and_exclusions() -> None:
    rows = [
        stock("000010", 5),
        stock("000020", 9),
        stock("000030", 9),
        stock("000040", -7),
        stock("000050", None),
        stock("000060", 100, Quality.INVALID),
        stock("000070", 0),
    ]
    assert [r.code for r in rank_top(rows, "buy", 20)] == ["000020", "000030", "000010"]
    assert [r.code for r in rank_top(rows, "sell", 20)] == ["000040"]
    assert [r.code for r in rank_top(rows, "buy", 1)] == ["000020"]
    with pytest.raises(ValueError):
        rank_top(rows, "buy", 0)


def test_scope_labels_say_the_range() -> None:
    assert scope_label("intraday", top_n=30) == "거래대금 상위 30 + 관심종목"
    assert scope_label("close", top_n=30) == "전 종목(마감 확정)"
    assert scope_label("close", top_n=30, swept=10, total=10) == "전 종목(마감 확정)"
    assert "훑은 종목 7/10" in scope_label("close", top_n=30, swept=7, total=10)


def test_scan_codes_and_sweep_universe() -> None:
    assert scan_codes(["a", "b", "c"], ["c", "d", "e"], top_n=2, watch_max=2) == [
        "a",
        "b",
        "c",
        "d",
    ]
    assert scan_codes([], ["x"], top_n=5, watch_max=0) == []
    uni = [
        UniverseRow("000010", DAY, "가", "KOSPI", "common", None, "krx", Quality.OK),
        UniverseRow("000020", DAY, "나", "KOSDAQ", "pref", None, "krx", Quality.OK),
        UniverseRow("000030", DAY, "다", "KOSPI", "etf", None, "krx", Quality.OK),
        UniverseRow("000040", DAY, "라", "KONEX", "common", None, "krx", Quality.OK),
    ]
    assert sweep_universe(uni, ["K", "Q"]) == [("000010", "K"), ("000020", "Q")]
    assert sweep_universe(uni, ["Q"]) == [("000020", "Q")]


def test_world_program_ledger_has_zero_check4_differences() -> None:
    w = build_world()
    assert w.program_checks() == {"c4": 0}
    assert check4(list(w.program_days)) == [] and check4(list(w.program_intraday)) == []
    rep = program_report(list(w.program_intraday))
    assert rep["failed"] == 0 and rep["na"] == 0 and rep["groups"] == 391 * 2
    top = [r.code for r in rank_top([r for r in w.program_stocks if r.scope == "sweep"], "buy", 5)]
    assert top == w.expect_program_top(5)
