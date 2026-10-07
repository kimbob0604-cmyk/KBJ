"""선물 지표 속성 (core/metrics/futures.py — docs/metrics.md §7).

- KIS 값(베이시스·괴리율·OI 증감·체결강도)은 float 로만 옮긴다 — 품질 ok, 없으면 null·invalid
- 이론 베이시스 = 이론가 − 지수, 시장 베이시스(자체) = 선물가 − 지수(둘 다 양수일 때만)
- 교차검증은 |이론 베이시스 − KIS basis| ≤ 허용과 정확히 같다(Decimal — 경계에서 부동소수 오차가
  없다), 입력이 모자라면 판정 없음. 허용을 넓히면 통과가 실패로 바뀌지 않는다
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from core.metrics.futures import BASIS_CHECK_TOLERANCE, FuturesQuote, futures_metrics

prices = st.decimals(Decimal("-5"), Decimal("3000"), places=2)
bases = st.decimals(Decimal("-50"), Decimal("50"), places=2)
maybe = st.one_of(st.none(), prices)


@given(
    maybe, maybe, maybe, st.one_of(st.none(), bases), st.one_of(st.none(), st.integers(-9999, 9999))
)
def test_kis_values_pass_through_and_the_bases_follow_the_formula(
    price: Decimal | None,
    index: Decimal | None,
    theory: Decimal | None,
    basis: Decimal | None,
    oi: int | None,
) -> None:
    q = FuturesQuote(
        code="A01612", price=price, basis=basis, oi_change=oi, index=index, theory_price=theory
    )
    m = futures_metrics(q)
    for shown, raw in ((m.kis_basis, basis), (m.oi_change, oi)):
        if raw is None:
            assert (shown.value, shown.quality, shown.reasons) == (
                None,
                "invalid",
                ("field_missing",),
            )
        else:
            assert (shown.value, shown.quality) == (float(raw), "ok")
    ok_price = price if price is not None and price > 0 else None
    ok_index = index if index is not None and index > 0 else None
    ok_theory = theory if theory is not None and theory > 0 else None
    assert m.price == ok_price and m.index == ok_index
    if ok_theory is not None and ok_index is not None:
        assert m.theory_basis.value == float(ok_theory - ok_index)
    else:
        assert m.theory_basis.value is None and m.theory_basis.quality == "invalid"
    if ok_price is not None and ok_index is not None:
        assert m.self_basis == ok_price - ok_index
        assert m.market_basis.value == float(ok_price - ok_index)
    else:
        assert m.self_basis is None and m.market_basis.quality == "invalid"
    if ok_theory is None or ok_index is None or basis is None:
        assert m.basis_check is None and m.basis_gap is None
    else:
        gap = ok_theory - ok_index - basis
        assert m.basis_gap == gap and m.basis_check == (abs(gap) <= BASIS_CHECK_TOLERANCE)


@given(prices, prices, bases, st.decimals(Decimal("0"), Decimal("1"), places=3))
def test_a_wider_tolerance_never_turns_a_pass_into_a_fail(
    theory: Decimal, index: Decimal, basis: Decimal, extra: Decimal
) -> None:
    q = FuturesQuote(code="A01612", theory_price=theory, basis=basis, index=index)
    narrow = futures_metrics(q).basis_check
    wide = futures_metrics(q, tolerance=BASIS_CHECK_TOLERANCE + extra).basis_check
    assert (narrow is None) == (wide is None)
    if narrow:
        assert wide
