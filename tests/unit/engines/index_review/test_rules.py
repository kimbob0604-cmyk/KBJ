"""MSCI 형(컷오프 배수)·KRX 형(순위·완충) 판정·패시브·백테스트 채점(metrics §24.5~§24.8).

2026-10-14: 배수를 GIMI 2026-08판 §3.1.5·§3.1.6 으로(비구성 1.0×·하위 세그먼트 이동 1.5×·유동 0.5×,
유동비율 < 15% 는 0.9×·0.6×, Light Rebalancing 1.8×·0.9× 는 꺼짐), KRX 는 산업군 안 순위 ÷ 산업군
구성종목 수·산업군 누적 시총 비중으로 바꿨다(기대값도 그 규칙으로)."""

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
    """컷오프 1조(사용자 값): 비구성 1.5조(세그먼트 모름 → 이동 1.5×)·유동 0.5조, 유지 0.6667조·
    유동 0.3333조, 유동비율 < 15% 는 유동 0.9조(신규)·0.6조(유지)."""
    stocks = [
        st("000001", 2.5, fif=0.5),  # 비구성: 2.5 ≥ 1.5, 유동 1.25 ≥ 0.5 → 편입(+66.7%·+150%)
        st("000002", 2.5, fif=0.15),  # 비구성: 유동 0.375 < 0.5 → 괴리 −25% → 후보 아님
        st("000003", 1.55, fif=0.5),  # 비구성: 괴리 +3.3% → 경계(±10%)
        st("000004", 0.5, member=True, fif=0.5),  # 구성: 0.5 < 0.6667 → 괴리 −25% → 편출
        st("000005", 3.0, member=True, fif=0.5),  # 유지
        st("000006", 10, fif=0.12),  # 비구성 유동비율 12% < 15%: 유동 1.2 ≥ 0.9 → 편입(예외 편입)
        st("000007", 10, fif=0.08),  # 유동 0.8 < 0.9 → 괴리 −11.1% → 후보 아님
        st("000008", 3.0, member=True, fif=0.12),  # 구성 유동비율 < 15%: 유동 0.36 < 0.6 → 편출
        st("000009", 1.2, fif=0.5, lower_segment=False),  # IMI 밖 신규: 1.0× → +20% 편입
        st("000010", 1.2, fif=0.5, lower_segment=True),  # Small Cap 이동: 1.5× → −20% 아님
    ]
    res = predict_msci(stocks, MSCI, NEW, CFG.fol, cutoff_override=JO)
    assert res.status == "ok" and res.cutoff is not None and res.cutoff.kind == "user"
    got = by_code(res)
    assert got["000001"].outcome == "add" and got["000001"].condition_met is True
    assert got["000001"].gap_mcap == pytest.approx(2.5 / 1.5 - 1)
    assert got["000001"].gap_ff == pytest.approx(1.25 / 0.5 - 1)
    assert got["000002"].outcome == "none" and got["000002"].required_basis == "ff"
    assert got["000002"].required_rise == pytest.approx(0.3334, abs=1e-4)  # 0.5 / 0.375 − 1
    assert got["000003"].outcome == "borderline"
    assert got["000004"].outcome == "delete" and got["000004"].required_rise == pytest.approx(
        0.3334, abs=1e-4
    )
    assert got["000005"].outcome == "keep"
    assert got["000006"].outcome == "add" and got["000006"].threshold_ff == round(0.9 * JO)
    assert any("< 15%" in r for r in got["000006"].reasons)
    assert got["000007"].outcome == "none" and got["000007"].gap_ff == pytest.approx(0.8 / 0.9 - 1)
    assert got["000008"].outcome == "delete" and got["000008"].threshold_ff == round(0.6 * JO)
    assert got["000009"].outcome == "add" and got["000009"].threshold_mcap == JO
    assert got["000010"].outcome == "none" and got["000010"].threshold_mcap == round(1.5 * JO)
    # 세그먼트를 모르는 비구성종목 수는 노트로 한 번
    assert any("하위 세그먼트 여부를 몰라" in n for n in res.notes)


def test_light_rebalancing_is_off_by_default_and_uses_1_8() -> None:
    lr = MSCI.light_rebalance
    assert lr is not None and lr.enabled is False
    on = MSCI.model_copy(update={"light_rebalance": lr.model_copy(update={"enabled": True})})
    stocks = [st("000001", 2.5, fif=0.5), st("000002", 1.7, fif=0.5), st("000099", 5, member=True)]
    got = by_code(predict_msci(stocks, on, NEW, CFG.fol, cutoff_override=JO))
    assert got["000001"].outcome == "add" and got["000001"].threshold_mcap == round(1.8 * JO)
    assert got["000001"].threshold_ff == round(0.9 * JO)
    assert got["000002"].outcome == "borderline"  # 1.7 / 1.8 − 1 = −5.6%
    off = by_code(predict_msci(stocks, MSCI, NEW, CFG.fol, cutoff_override=JO))
    assert off["000002"].outcome == "add"  # 꺼져 있으면 1.5×


def test_fol_room_factor_in_verdicts() -> None:
    """FOL 0.49 종목: 신규 room 10% → 편입 불가(경계), 15~25% → 유동 × 0.5, 기존은 계수 전 FIF 로
    유동 요건을 보고 지수 유동시총은 계수 뒤 값."""
    from kbj.config.index_reviews import FolLimit

    fol = CFG.fol.model_copy(update={"limits": tuple(
        FolLimit(code=c, limit=0.49, why="합성") for c in ("000001", "000002", "000003")
    )})  # fmt: skip
    stocks = [
        st("000001", 5, fif=0.5, foreign_held=0.45),  # room 8.2% < 15% → 편입 불가
        st("000002", 5, fif=0.5, foreign_held=0.40),  # room 18.4% → 계수 0.5
        st("000003", 5, member=True, fif=0.5, foreign_held=0.48),  # room 2% → 계수 0.25
    ]
    got = by_code(predict_msci(stocks, MSCI, NEW, fol, cutoff_override=JO))
    assert got["000001"].outcome == "borderline" and any(
        "편입 불가" in r for r in got["000001"].reasons
    )
    assert got["000002"].fif == pytest.approx(0.49 * 0.5)  # FIF = min(0.5, FOL 0.49) × 0.5
    assert got["000002"].outcome == "add" and got["000002"].estimated
    m = got["000003"]
    assert m.fif == pytest.approx(0.49 * 0.25) and m.ff_mcap == round(5 * JO * 0.49 * 0.25)
    assert m.gap_ff == pytest.approx(5 * 0.49 / 0.3333 - 1)  # 계수 전 값으로 유지 판정
    assert m.outcome == "keep"


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


def test_krx_rank_is_within_sector_and_cum_mcap_caps_new_entries() -> None:
    """산업군 A: 구성 5(순위 1·2·4·5·6) → 신규 한도 floor(5 × 0.9) = 4, 유지 floor(5 × 1.1) = 5.
    누적 시총 85%: 자기보다 큰 종목 비중 < 85% 인 종목까지(문턱에 처음 닿는 종목 포함)."""
    rules = CFG.krx_rules("kospi200").model_copy(update={
        "size": CFG.krx_rules("kospi200").size.model_copy(update={"v": 8}),
    })  # fmt: skip
    base = date(2026, 10, 30)
    a = [("A01", 50, True), ("A02", 20, True), ("A03", 9, False), ("A04", 8, True),
         ("A05", 7, True), ("A06", 6, True), ("A07", 5.5, False)]  # fmt: skip
    b = [("B01", 30, True), ("B02", 3, True), ("B03", 2.9, True), ("B04", 2.0, False)]
    stocks = [st(f"{c}000"[:6].replace("A", "1").replace("B", "2"), cap, member=m, sector=sec)
              for sec, rows in (("A", a), ("B", b)) for c, cap, m in rows]  # fmt: skip
    res = predict_krx(stocks, rules, ("KOSPI",), base)
    got = by_code(res)
    # A: 합 105.5 — A03 위로 70/105.5 = 66% < 85% → 누적 안. 산업군 안 3위 ≤ 4 → 편입
    assert got["103000"].outcome == "add"
    assert any("산업군 A 안 3위/구성 5" in r for r in got["103000"].reasons)
    # A07: 산업군 안 7위 > 신규 4, 자기보다 큰 비중 94.8% ≥ 85% → 아님
    assert got["107000"].outcome == "none"
    # A06: 구성종목, 산업군 안 6위 > 유지 5 → 편출(전체 순위로는 상위 8 안이지만)
    assert got["106000"].outcome == "delete"
    # B: 구성 3 → 신규 floor(2.7) = 2, B04(4위 — 문턱 B02 3조 대비 −33%) 아님
    assert got["204000"].outcome == "none"
    assert res.cutoff is not None and res.cutoff.method.startswith("sector rank 90%/110% cum 85%")


def test_krx_review_mcap_uses_average_and_falls_back() -> None:
    rules = CFG.krx_rules("kospi200").model_copy(update={
        "size": CFG.krx_rules("kospi200").size.model_copy(update={"v": 2}),
    })  # fmt: skip
    stocks = [
        st("000001", 10, member=True),
        st("000002", 9, member=True),
        st("000003", 1, mktcap_avg=int(20 * JO), adv_won=1e12),
    ]  # 평균은 1위
    res = predict_krx(stocks, rules, ("KOSPI",), date(2026, 10, 30))
    got = by_code(res)
    assert got["000003"].rank == 1 and got["000003"].outcome == "add"
    assert any("기준일 시총으로 대체" in n for n in res.notes)  # 나머지 2종목은 평균 없음


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
