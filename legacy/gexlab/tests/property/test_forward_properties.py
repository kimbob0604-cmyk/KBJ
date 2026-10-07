"""§1.3 합성 F 선물 교차 확인 속성 (docs/metrics.md §1.3, 2026-09-28 사용자 결정 — 검증 수정 2).

ATM±2 다섯 행사가 중 임의의 부분집합에 콜·풋 가격이 있고, 그 중 임의의 부분집합이 전 세션 가격이다.
남은(쓸) 행사가 수 n 과 기준가 유무·종류·거리로 결과가 정해진다:
- 기준가 없음: 선물 규칙 사유 없음, `no_futures_ref`, n = 0 이면 invalid
- 기준가 있고 n < 2: F = 기준가, `futures_fallback` 하나
- 기준가 있고 n ≥ 2: 패리티 중앙값 F, `futures_ref_gap` ⇔ |F − 기준가| > 2, `futures_gap` ⇔ 같은
  결제월이고 > 0.5, `few_strikes` ⇔ n < 3
- 확정 베이시스로 만든 기준가는 그 F 와 같다(왕복)
"""

from datetime import date
from decimal import Decimal as D

from hypothesis import given
from hypothesis import strategies as st

from core.forward import (
    FUTURES_GAP_TOLERANCE,
    FUTURES_REF_TOLERANCE,
    FuturesRef,
    confirm_basis,
    futures_reference,
    synthetic_forward,
)

WINDOW = [D(1095), D("1097.5"), D(1100), D("1102.5"), D(1105)]  # S_ref 1100 의 ATM±2
PUT = D("200.00")  # 콜 가격 level − K + PUT 이 늘 양수

levels = st.decimals(min_value=1000, max_value=1200, places=2)
offsets = st.decimals(min_value=-5, max_value=5, places=2)
subsets = st.lists(st.sampled_from(WINDOW), unique=True, max_size=len(WINDOW))


@given(
    priced=subsets,
    stale=subsets,
    level=levels,
    offset=offsets,
    kind=st.sampled_from([None, "same_month", "near_basis"]),
)
def test_reference_rules(
    priced: list[D], stale: list[D], level: D, offset: D, kind: str | None
) -> None:
    # 패리티 C − P + K 는 모두 level — 중앙값도 level
    calls = {k: level - k + PUT for k in priced}
    puts = {k: PUT for k in priced}
    ref_price = level + offset
    reference = None
    if kind == "same_month":
        reference = FuturesRef(ref_price, "same_month")
    elif kind == "near_basis":
        reference = FuturesRef(ref_price, "near_basis", D("-4.80"))
    res = synthetic_forward(
        calls, puts, 1100.0, strikes=WINDOW, reference=reference, prev_session_strikes=stale
    )
    used = sorted(set(priced) - set(stale))
    assert res.prev_session_skipped == tuple(sorted(set(priced) & set(stale)))

    if reference is None:
        assert res.notes == ("no_futures_ref",)
        assert not {"futures_gap", "futures_ref_gap", "futures_fallback"} & set(res.reasons)
        assert res.futures_gap is None
        if not used:
            assert (res.F, res.quality) == (None, "invalid")
        else:
            assert res.F == float(level) and res.strikes == tuple(used)
        return

    assert (res.reference, res.notes) == (reference, ())
    if len(used) < 2:
        assert (res.F, res.quality, res.reasons) == (
            float(ref_price),
            "estimated",
            ("futures_fallback",),
        )
        assert (res.strikes, res.futures_gap) == ((), None)
        return

    gap = level - ref_price
    assert res.F == float(level) and res.strikes == tuple(used)
    assert res.futures_gap == float(gap)
    assert ("few_strikes" in res.reasons) == (len(used) < 3)
    assert ("futures_ref_gap" in res.reasons) == (abs(gap) > FUTURES_REF_TOLERANCE)
    same = kind == "same_month"
    assert ("futures_gap" in res.reasons) == (same and abs(gap) > FUTURES_GAP_TOLERANCE)
    assert (res.quality == "ok") == (not res.reasons)


@given(level=levels, near=levels)
def test_confirmed_basis_round_trip(level: D, near: D) -> None:
    calls = {k: level - k + PUT for k in WINDOW}
    puts = dict.fromkeys(WINDOW, PUT)
    ok = synthetic_forward(calls, puts, 1100.0)
    assert ok.quality == "ok"
    basis = confirm_basis(ok, near)
    assert basis == level - near
    ref = futures_reference("202610", date(2026, 10, 8), near, basis=basis)
    assert ref is not None and ref.kind == "near_basis"
    assert float(ref.price) == ok.F
    again = synthetic_forward(calls, puts, 1100.0, reference=ref)
    assert (again.quality, again.futures_gap) == ("ok", 0.0)
