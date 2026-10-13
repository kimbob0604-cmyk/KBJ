"""MSCI 형(컷오프 배수)·KRX 형(순위·완충) 판정·패시브·백테스트 채점(metrics §24.5~§24.8)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kbj.config.index_reviews import load_index_reviews
from kbj.engines.index_review.backtest import score
from kbj.engines.index_review.krx import liquidity, predict_krx
from kbj.engines.index_review.msci import predict_msci
from kbj.engines.index_review.types import StockIn

CFG = load_index_reviews()
MSCI = CFG.msci_rules("msci_korea")
NEW = CFG.fif_rule_for(date(2026, 11, 11))
JO = 1_000_000_000_000  # 1조


def st(
    code: str, cap: float, *, member: bool = False, fif: float | None = 0.5, **kw: Any
) -> StockIn:
    base: dict[str, Any] = {
        "code": code, "name": None, "market": "KOSPI", "kind": "common", "sector": "IT",
        "listed_on": date(2010, 1, 1), "mktcap": int(cap * JO), "adv_won": 1e10,
        "fif_raw": fif, "fif_prev": None, "fif_source": "DART", "flags": (), "member": member,
    }  # fmt: skip
    base.update(kw)
    return StockIn(**base)


def by_code(res: Any) -> dict[str, Any]:
    return {o.code: o for o in res.stocks}


def test_no_constituents_makes_no_prediction() -> None:
    res = predict_msci([st("000001", 5)], MSCI, NEW, CFG.fol)
    assert res.status == "no_constituents" and res.stocks == () and res.cutoff is None
    res = predict_krx([st("000001", 5)], CFG.krx_rules("kospi200"), ("KOSPI",), date(2026, 10, 30))
    assert res.status == "no_constituents" and res.stocks == ()


def test_user_cutoff_multiples_and_verdicts() -> None:
    """컷오프 1조(사용자 값): 신규 1.8조·유동 0.9조, 유지 0.6667조·유동 0.3333조."""
    stocks = [
        st("000001", 2.5, fif=0.5),  # 비구성: 2.5 ≥ 1.8, 유동 1.25 ≥ 0.9 → 편입(괴리 +25%·+38.9%)
        st("000002", 2.5, fif=0.3),  # 비구성: 유동 0.75 < 0.9 → 괴리 −16.7% → 후보 아님
        st("000003", 1.85, fif=0.5),  # 비구성: 괴리 +2.8% → 경계(±10%)
        st("000004", 0.5, member=True, fif=0.5),  # 구성: 0.5 < 0.6667 → 괴리 −25% → 편출
        st("000005", 3.0, member=True, fif=0.5),  # 유지
        st("000006", 10, fif=0.12),  # 비구성: 조건은 되지만 유동비율 12% < 15% → 경계
    ]
    res = predict_msci(stocks, MSCI, NEW, CFG.fol, cutoff_override=JO)
    assert res.status == "ok" and res.cutoff is not None and res.cutoff.kind == "user"
    got = by_code(res)
    assert got["000001"].outcome == "add" and got["000001"].condition_met is True
    assert got["000001"].gap_mcap == pytest.approx(2.5 / 1.8 - 1)
    assert got["000001"].gap_ff == pytest.approx(1.25 / 0.9 - 1)
    assert got["000002"].outcome == "none" and got["000002"].required_basis == "ff"
    assert got["000002"].required_rise == pytest.approx(0.2, abs=1e-4)  # 0.9 / 0.75 − 1
    assert got["000003"].outcome == "borderline"
    assert got["000004"].outcome == "delete" and got["000004"].required_rise == pytest.approx(
        0.3334, abs=1e-4
    )
    assert got["000005"].outcome == "keep"
    assert got["000006"].outcome == "borderline" and "주가로 풀 수 없음" in got["000006"].reasons[0]


def test_unknown_fif_is_never_add_or_keep() -> None:
    stocks = [st("000001", 9, fif=None), st("000002", 9, member=True, fif=None),
              st("000003", 9, member=True)]  # fmt: skip
    res = predict_msci(stocks, MSCI, NEW, CFG.fol, cutoff_override=JO)
    got = by_code(res)
    assert got["000001"].outcome == "borderline" and got["000001"].condition_met is None
    assert got["000002"].outcome == "borderline" and got["000002"].estimated


def test_ineligible_member_is_delete_candidate_and_missing_price_is_borderline() -> None:
    stocks = [st("000001", 9, member=True, flags=("halted",)),
              st("000002", 0, member=True), st("000003", 9, member=True)]  # fmt: skip
    got = by_code(predict_msci(stocks, MSCI, NEW, CFG.fol, cutoff_override=JO))
    assert got["000001"].outcome == "delete" and "halted" in got["000001"].reasons[0]
    assert got["000002"].outcome == "borderline" and got["000002"].condition_met is None


def test_model_cutoff_is_estimated_and_passive_flows_signed() -> None:
    stocks = [st(f"{i:06d}", 10 / (i + 1), member=i < 8, fif=0.5) for i in range(40)]
    res = predict_msci(stocks, MSCI, NEW, CFG.fol, aum_won=10 * JO)
    assert res.status == "ok" and res.cutoff is not None and res.cutoff.kind == "model"
    assert res.estimated
    for o in res.stocks:
        if o.outcome == "add":
            assert o.passive_won is not None and o.passive_won > 0
            assert o.adv_multiple == pytest.approx(o.passive_won / 1e10)
        if o.outcome == "delete":
            assert o.passive_won is not None and o.passive_won < 0
        if o.outcome == "keep":
            assert o.passive_won is None


def test_no_aum_means_no_passive() -> None:
    res = predict_msci([st("000001", 3, member=True)], MSCI, NEW, CFG.fol, cutoff_override=JO)
    assert all(o.passive_won is None for o in res.stocks)
    assert any("추종 자금 없음" in n for n in res.notes)


def test_krx_rank_buffer_liquidity_listing_and_size() -> None:
    rules = CFG.krx_rules("kospi200").model_copy(update={
        "size": CFG.krx_rules("kospi200").size.model_copy(update={"v": 10}),
    })  # fmt: skip
    base = date(2026, 10, 30)
    stocks = [st(f"{i:06d}", 20 - i, member=i < 10, adv_won=1e10 * (30 - i)) for i in range(20)]
    # 9위 구성종목은 유지(유지 문턱 11위), 순위 밖(12위 이하) 구성종목 편출
    stocks[11] = st("000011", 20 - 11, member=True, adv_won=1e11)
    # 신규 상장(30일) 3위 — 특례(fast 50) / 거래대금 꼴찌인 2위 비구성 — 유동성 탈락
    stocks[2] = st("000002", 18, listed_on=date(2026, 10, 1), adv_won=1e12)
    stocks[1] = st("000001", 19, adv_won=1.0)
    res = predict_krx(stocks, rules, ("KOSPI",), base)
    got = by_code(res)
    assert got["000002"].outcome == "add" and any("특례" in r for r in got["000002"].reasons)
    assert got["000001"].outcome != "add" and any("거래대금" in r for r in got["000001"].reasons)
    assert got["000011"].outcome == "delete"
    after = sum(1 for o in res.stocks if (o.member and o.outcome != "delete") or o.outcome == "add")
    assert after == 10  # 정원 맞추기


def test_liquidity_percentile_by_sector() -> None:
    stocks = [st(f"{i:06d}", 1, adv_won=float(100 - i)) for i in range(20)]
    liq = liquidity(stocks, 0.85)
    assert sum(1 for v in liq.values() if v) == 17 and liq["000019"] is False
    assert liquidity([st("000001", 1, adv_won=None)], 0.85) == {"000001": None}


def test_backtest_score() -> None:
    s = score("2026-05", "add", {"A", "B", "C"}, {"B", "C", "D"}, {"D"})
    assert s.hits == ("B", "C") and s.misses == ("D",) and s.false_pos == ("A",)
    assert s.borderline_actual == ("D",)
    assert s.precision == pytest.approx(2 / 3) and s.recall == pytest.approx(2 / 3)
    assert score("x", "add", set(), set()).precision is None
