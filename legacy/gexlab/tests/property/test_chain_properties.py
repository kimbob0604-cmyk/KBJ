from decimal import Context, localcontext
from decimal import Decimal as D

from hypothesis import given
from hypothesis import strategies as st

from core.chain import atm_strike, atm_window, by_distance, covers

strike_sets = st.lists(
    st.decimals(min_value="0.5", max_value=3000, places=1), min_size=1, max_size=40
)
refs = st.decimals(min_value="0.01", max_value=3200, places=2)
ns = st.integers(min_value=0, max_value=12)


@st.composite
def board(draw: st.DrawFn) -> tuple[list[D], list[D], D, int]:
    """(만기 전 행사가 격자, 그 이어진 부분 구간 = 전광판, 기준가, n)."""
    start = D(draw(st.integers(min_value=1, max_value=600))) * D("2.5")
    step = draw(st.sampled_from([D("2.5"), D("5"), D("0.5")]))
    count = draw(st.integers(min_value=1, max_value=60))
    full = [start + step * i for i in range(count)]
    lo = draw(st.integers(min_value=0, max_value=count - 1))
    hi = draw(st.integers(min_value=lo, max_value=count - 1))
    span = full[-1] - full[0]
    ref = draw(
        st.decimals(min_value=max(D("0.01"), full[0] - 20), max_value=full[0] + span + 20, places=2)
    )
    return full, full[lo : hi + 1], ref, draw(ns)


@given(strike_sets, refs)
def test_atm_is_nearest_and_lower_on_tie(ks: list[D], ref: D) -> None:
    a = atm_strike(ks, ref)
    assert a in ks
    d = abs(a - ref)
    assert all(abs(k - ref) >= d for k in ks)
    assert all(k > a for k in ks if abs(k - ref) == d and k != a)  # 동률이면 a 가 가장 낮다


@given(strike_sets, refs, ns)
def test_window_is_clipped_contiguous_slice_around_atm(ks: list[D], ref: D, n: int) -> None:
    uniq = sorted(set(ks))
    w = atm_window(ks, ref, n)
    i = uniq.index(atm_strike(ks, ref))
    lo, hi = max(0, i - n), min(len(uniq) - 1, i + n)
    assert w == uniq[lo : hi + 1]
    assert len(w) <= 2 * n + 1


@given(strike_sets, refs)
def test_by_distance_is_sorted_permutation_starting_at_atm(ks: list[D], ref: D) -> None:
    out = by_distance(ks, ref)
    assert sorted(out) == sorted(set(ks))
    assert out[0] == atm_strike(ks, ref)
    keys = [(abs(k - ref), k) for k in out]
    assert keys == sorted(keys)


@given(board())
def test_covers_iff_true_atm_window_is_inside_board(case: tuple[list[D], list[D], D, int]) -> None:
    full, part, ref, n = case
    inside = part[0] <= ref <= part[-1]
    want = atm_window(full, ref, n)
    ok = covers(part, ref, n)
    if not inside:
        assert ok is False  # 범위 밖은 보수적으로 False
        return
    # 범위 안: 잘린 구간 안 최근접 = 참 ATM
    assert atm_strike(part, ref) == atm_strike(full, ref)
    full_ok = len(want) == 2 * n + 1 and set(want) <= set(part)
    assert ok is full_ok
    if ok:
        assert atm_window(part, ref, n) == want


@given(strike_sets, refs, st.integers(min_value=1, max_value=12))
def test_covers_monotone_in_n(ks: list[D], ref: D, n: int) -> None:
    if covers(ks, ref, n):
        assert covers(ks, ref, n - 1)


# 기준가 자릿수가 기본 문맥(28자리)을 넘거나 호출 측이 문맥을 좁혀도 거리·동률은 정확해야 한다.
# 무작위 기준가는 가짜 동률에 거의 안 닿으므로 인접 두 행사가의 가운데에서 10^-k 만 비낀다
_ORACLE = Context(prec=200)  # 이 범위 입력의 합·차를 반올림 없이 담는다


@st.composite
def near_tie(draw: st.DrawFn) -> tuple[list[D], D, D]:
    """(인접 두 행사가, 가운데서 10^-k 비낀 기준가, 참 ATM)."""
    lo = D(draw(st.integers(min_value=1, max_value=1200))) * D("2.5")
    hi = lo + draw(st.sampled_from([D("0.5"), D("2.5"), D("5")]))
    eps = D(f"1E-{draw(st.integers(min_value=1, max_value=40))}")
    up = draw(st.booleans())
    with localcontext(_ORACLE):
        ref = (lo + hi) / 2 + (eps if up else -eps)
    return [lo, hi], ref, hi if up else lo


@given(near_tie(), st.integers(min_value=1, max_value=28))
def test_atm_exact_near_tie_regardless_of_context(case: tuple[list[D], D, D], prec: int) -> None:
    ks, ref, want = case
    with localcontext() as ctx:
        ctx.prec = prec
        assert atm_strike(ks, ref) == want
        assert by_distance(ks, ref) == [want, *(k for k in ks if k != want)]
        assert atm_window(ks, ref, 0) == [want]
