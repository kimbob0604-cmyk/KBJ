"""시나리오 적정가(구 TAM) — SD `tam_modeler` 폴백 순서 표(컨센 → TTM → PBR → INSUFFICIENT)·적자·
자본잠식·신규상장·근거 문자열·업사이드(§4.9, metrics §15). SD 원본 시험은 없다(옮김 0)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.engines.consensus.forward import Ntm, ntm
from kbj.engines.valuation.pit import PitBook
from kbj.engines.valuation.scenario import ScenarioInputs, inputs_from, scenarios
from tests.unit.engines.valuation.val_rows import (
    CODE,
    band_row,
    estimate,
    share_count,
    year_of_quarters,
)

T = date(2026, 11, 13)
PER = band_row("per")  # p25 10 · p50 12 · p75 14
PBR = band_row("pbr", p=(0.8, 1.0, 1.2, 1.4, 1.6))  # p25 1.0 · p50 1.2 · p75 1.4


INP0 = ScenarioInputs(code=CODE, as_of=T, price=100.0, per_band=PER, pbr_band=PBR)


def _inp(**kw: Any) -> ScenarioInputs:
    return replace(INP0, **kw)


def test_컨센서스가_있으면_PER() -> None:
    bear, base, bull = scenarios(_inp(fy1_eps=10.0, ttm_eps=8.0, bps=50.0))
    assert [r.case for r in (bear, base, bull)] == ["bear", "base", "bull"]
    assert (base.method, base.eps, base.eps_source, base.multiple, base.multiple_source) == (
        "PER",
        10.0,
        "CONSENSUS",
        12.0,
        "5Y_P50",
    )
    assert base.tp == pytest.approx(120.0) and base.upside_pct == pytest.approx(20.0)
    assert (bear.eps, bear.eps_source, bear.multiple_source) == (
        pytest.approx(7.0),
        "CONSENSUS_×0.7",
        "5Y_P25",
    )
    assert bear.tp == pytest.approx(70.0)
    assert (bull.eps, bull.eps_source, bull.multiple_source) == (
        pytest.approx(12.0),
        "BASE_×1.2",
        "5Y_P75",
    )
    assert bull.tp == pytest.approx(168.0)
    assert all(r.quality is Quality.ESTIMATED for r in (bear, base, bull))  # KIS 단일 추정·폴백
    assert base.source.endswith("+KIS")


def test_컨센서스가_없거나_0_이하면_TTM() -> None:
    for fy1 in (None, 0.0, -3.0):
        bear, base, _ = scenarios(_inp(fy1_eps=fy1, ttm_eps=8.0, ttm_quality=Quality.OK))
        assert (base.method, base.eps, base.eps_source) == ("PER", 8.0, "TTM_EPS")
        assert bear.eps_source == "BASE_×0.7" and bear.eps == pytest.approx(5.6)
        assert base.quality is Quality.ESTIMATED  # TTM 폴백 → estimated
        assert "+KIS" not in base.source


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"fy1_eps": None, "ttm_eps": -2.0}, "적자 — Base EPS 없음"),
        ({"fy1_eps": 10.0, "per_band": band_row("per", p=None)}, "PER 밴드 없음"),
        ({"fy1_eps": 10.0, "per_band": None}, "PER 밴드 행 없음"),
    ],
)
def test_PBR_폴백(kw: dict[str, Any], why: str) -> None:
    bear, base, bull = scenarios(_inp(bps=50.0, **kw))
    assert base.method == "PBR", why
    assert (base.eps, base.eps_source, base.multiple, base.multiple_source) == (
        50.0,
        "BPS",
        1.2,
        "PBR_5Y_P50",
    )
    assert (bear.tp, base.tp, bull.tp) == (
        pytest.approx(50.0),
        pytest.approx(60.0),
        pytest.approx(70.0),
    )
    assert base.quality is Quality.ESTIMATED


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        (
            {"pbr_band": band_row("pbr", p=None, na=NaReason.SHORT_HISTORY), "bps": 50.0},
            NaReason.SHORT_HISTORY,
        ),
        ({"bps": -5.0}, NaReason.CAPITAL_IMPAIRMENT),
        ({"bps": None}, NaReason.MISSING_INPUT),
        ({"pbr_band": None, "bps": 5.0}, NaReason.MISSING_INPUT),
        (
            {"pbr_band": band_row("pbr", p=None, na=NaReason.SHORT_HISTORY), "bps": -1.0},
            NaReason.SHORT_HISTORY,
        ),
    ],
)
def test_산출_불가(kw: dict[str, Any], reason: NaReason) -> None:
    rows = scenarios(_inp(fy1_eps=None, ttm_eps=None, per_band=band_row("per", p=None), **kw))
    assert {r.method for r in rows} == {"INSUFFICIENT"}
    assert {r.na_reason for r in rows} == {reason}
    assert all(r.tp is None and r.upside_pct is None for r in rows)


def test_막는_사유가_있으면_계산하지_않는다() -> None:
    rows = scenarios(_inp(fy1_eps=10.0, blocked=NaReason.CURRENCY_MISMATCH))
    assert {(r.method, r.na_reason) for r in rows} == {("INSUFFICIENT", NaReason.CURRENCY_MISMATCH)}


def test_가격이_없으면_업사이드_None() -> None:
    rows = scenarios(_inp(fy1_eps=10.0, price=None))
    assert rows[1].tp == pytest.approx(120.0) and rows[1].upside_pct is None


def test_배수_설정() -> None:
    rows = scenarios(_inp(fy1_eps=10.0), bear_eps_mult=0.5, bull_eps_mult=1.5)
    assert rows[0].eps_source == "CONSENSUS_×0.5" and rows[2].eps_source == "BASE_×1.5"
    with pytest.raises(ValueError):
        scenarios(_inp(fy1_eps=10.0), bear_eps_mult=1.2)


# ── 입력 만들기(PIT 재무·NTM·그날 주식 수) ──


def _est(eps: float, ni: int) -> Ntm | None:
    rows = [
        estimate(CODE, T, "202612", eps=eps, ni_owner=ni),
        estimate(CODE, T, "202712", eps=eps * 1.1, ni_owner=round(ni * 1.1)),
    ]
    return ntm(rows, T)


def test_입력_분할_직후에는_그날_주식_수로_다시_센다() -> None:
    # 분기 EPS 7.5(분할 전 주식 10주 기준) — TTM 지배순익 300. 분할 5:1 뒤 KRX 상장주식수 50주
    book = PitBook(
        year_of_quarters(2025),
        [share_count(date(2025, 12, 31), rcept=date(2026, 3, 20), common=10)],
    )
    view = book.at(T)
    inp = inputs_from(CODE, T, view, _est(40.0, 400), [PER, PBR], price=10.0, krx_common_shares=50)
    assert (inp.fy1_eps, inp.fy1_eps_source) == (pytest.approx(8.0), "CONSENSUS_NI_PER_SHARE")
    assert (inp.ttm_eps, inp.ttm_eps_source) == (pytest.approx(6.0), "TTM_NI_PER_SHARE")
    assert inp.bps == pytest.approx(2_000 / 50)
    same = inputs_from(CODE, T, view, _est(40.0, 400), [PER, PBR], price=50.0, krx_common_shares=10)
    assert (same.fy1_eps, same.fy1_eps_source) == (40.0, "CONSENSUS")
    assert (same.ttm_eps, same.ttm_eps_source) == (pytest.approx(30.0), "TTM_EPS")
    assert same.bps == pytest.approx(200.0) and same.bps_quality is Quality.OK


def test_입력_주요계정_층은_지배순익_나누기_주식수() -> None:
    book = PitBook(year_of_quarters(2025, eps=None, tier="main"))
    inp = inputs_from(CODE, T, book.at(T), None, [PER, PBR], price=10.0, krx_common_shares=100)
    assert (inp.ttm_eps, inp.ttm_eps_source) == (pytest.approx(3.0), "TTM_NI_PER_SHARE")
    assert inp.ttm_quality is Quality.ESTIMATED and inp.bps_quality is Quality.ESTIMATED
    assert inp.fy1_eps is None and inp.blocked is None
    rows = scenarios(inp)
    assert rows[1].eps_source == "TTM_NI_PER_SHARE" and rows[1].method == "PER"


def test_입력_재무가_없으면_no_filings() -> None:
    inp = inputs_from(CODE, T, PitBook([]).at(T), None, [], price=10.0, krx_common_shares=100)
    assert inp.blocked is NaReason.NO_FILINGS
    assert {r.na_reason for r in scenarios(inp)} == {NaReason.NO_FILINGS}
