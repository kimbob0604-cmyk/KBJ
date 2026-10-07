"""선물 지표 (core/metrics/futures.py — docs/metrics.md §7).

- 시장 베이시스 = 자체 선물가 − 지수, 이론 베이시스 = 이론가 − 지수, KIS 값(베이시스·괴리율·OI
  증감·체결강도)은 그대로 표시
- 교차검증(2026-09-30 실측 반영): KIS `basis` 는 이론 베이시스다 — |이론가 − 지수 − KIS basis|
  0.05pt 경계(0.05 통과·0.051 실패), 입력이 모자라면 판정 없음, 없거나 0 이하 값은 없음(invalid
  `field_missing`)
- fixture 응답(14:01 분봉 조회 output1 형태의 합성 — KIS basis = 이론가 − 지수 관례는 실측에서
  확인): KIS basis 5.85 = 이론가 − 지수, 시장 베이시스 0.43
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.metrics.futures import BASIS_CHECK_TOLERANCE, FuturesQuote, futures_metrics

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kis" / "minute_day.json"
D = Decimal


def quote(**kw: Any) -> FuturesQuote:
    base: dict[str, Any] = {
        "code": "A01612",
        "price": D("1100.00"),
        "basis": D("2.00"),
        "divergence": D("-0.10"),
        "oi_change": -1655,
        "strength": D("97.10"),
        "index": D("1098.00"),
        "theory_price": D("1101.10"),
    }
    return FuturesQuote(**(base | kw))


def test_kis_values_are_shown_as_they_are() -> None:
    m = futures_metrics(quote())
    assert m.code == "A01612"
    assert (m.market_basis.value, m.kis_basis.value, m.divergence.value) == (2.0, 2.0, -0.1)
    assert (m.oi_change.value, m.strength.value) == (-1655.0, 97.1)
    shown = (m.market_basis, m.kis_basis, m.divergence, m.oi_change, m.strength, m.theory_basis)
    assert all(v.quality == "ok" and v.reasons == () for v in shown)
    assert m.theory_basis.value == pytest.approx(3.1)  # 1101.10 − 1098.00
    assert (m.price, m.index, m.self_basis) == (D("1100.00"), D("1098.00"), 2)
    assert m.basis_gap == D("1.10") and m.basis_check is False  # 이론 3.10 − KIS 2.00


@pytest.mark.parametrize(
    ("basis", "ok"),
    [("3.15", True), ("3.05", True), ("3.151", False), ("3.049", False), ("2.00", False)],
)
def test_the_theory_basis_check_boundary_is_five_hundredths(basis: str, ok: bool) -> None:
    """이론 베이시스 1101.10 − 1098.00 = 3.10 과 KIS basis 의 차."""
    assert BASIS_CHECK_TOLERANCE == D("0.05")
    m = futures_metrics(quote(basis=D(basis)))
    assert m.basis_check is ok and m.basis_gap == D("3.10") - D(basis)


def test_the_market_basis_is_ours_not_the_kis_field() -> None:
    m = futures_metrics(quote(price=D("1105.00"), basis=D("3.10")))
    assert m.market_basis.value == 7.0 and m.kis_basis.value == 3.1 and m.basis_check is True
    no_kis = futures_metrics(quote(basis=None))
    assert no_kis.market_basis.value == 2.0 and no_kis.kis_basis.quality == "invalid"
    assert no_kis.basis_check is None


def test_tolerance_is_a_parameter() -> None:
    assert futures_metrics(quote(basis=D("2.7")), tolerance=D("0.5")).basis_check is True
    with pytest.raises(ValueError):
        futures_metrics(quote(), tolerance=D(-1))


def test_missing_or_non_positive_inputs_give_null_values_and_no_check() -> None:
    m = futures_metrics(quote(basis=None, divergence=None, strength=None, index=D("0.00")))
    for v in (m.market_basis, m.kis_basis, m.divergence, m.strength, m.theory_basis):
        assert v.value is None and v.quality == "invalid" and v.reasons == ("field_missing",)
    assert m.index is None and m.self_basis is None and m.basis_check is None
    assert m.oi_change.value == -1655.0
    no_price = futures_metrics(quote(price=D(0)))
    assert no_price.price is None and no_price.market_basis.value is None
    assert no_price.basis_check is False  # 교차검증은 이론가·지수·KIS basis 만 쓴다
    no_theory = futures_metrics(quote(theory_price=None))
    assert no_theory.theory_basis.value is None and no_theory.basis_check is None
    assert futures_metrics(quote(oi_change=None)).oi_change.quality == "invalid"


@pytest.mark.parametrize(
    "bad",
    [
        {"code": ""},
        {"basis": D("NaN")},
        {"index": D("Infinity")},
        {"price": 1100.0},
        {"oi_change": 1.5},
        {"oi_change": True},
    ],
)
def test_bad_values_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        quote(**bad)


def test_measured_response_kis_basis_is_the_theoretical_basis() -> None:
    """합성 fixture(14:01 A01612 분봉 조회 output1 형태 — KIS basis = 이론 베이시스는 실측에서
    확인한 관례): KIS basis 5.85 = 이론가 1103.47 − 지수 1097.62 → 교차검증 통과. 시장 베이시스 =
    1098.05 − 1097.62 = 0.43 은 자체 계산."""
    out = json.loads(FIXTURE.read_text(encoding="utf-8"))["output1"]
    q = FuturesQuote(
        code=out["futs_shrn_iscd"],
        price=D(out["futs_prpr"]),
        basis=D(out["basis"]),
        divergence=D(out["dprt"]),
        oi_change=int(out["otst_stpl_qty_icdc"]),
        strength=D(out["tday_rltv"]),
        index=D(out["kospi200_nmix"]),
        theory_price=D(out["hts_thpr"]),
    )
    m = futures_metrics(q)
    assert m.kis_basis.value == 5.85 and m.theory_basis.value == pytest.approx(5.85)
    assert m.market_basis.value == pytest.approx(0.43) and m.self_basis == D("0.43")
    assert m.basis_gap == 0 and m.basis_check is True
    assert (m.divergence.value, m.oi_change.value, m.strength.value) == (-0.49, 1310.0, 97.37)
