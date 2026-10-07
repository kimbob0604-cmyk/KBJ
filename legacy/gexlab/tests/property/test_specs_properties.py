from bisect import bisect_left, bisect_right
from decimal import Decimal as D

from hypothesis import given
from hypothesis import strategies as st

from core.specs import Product, RoundMode, is_on_tick, round_to_tick, spec

MODES: tuple[RoundMode, ...] = ("nearest", "down", "up")

prices = st.decimals(min_value=0, max_value=3000, places=4, allow_nan=False, allow_infinity=False)
products = st.sampled_from(list(Product))
modes = st.sampled_from(MODES)

# 옵션 격자를 직접 늘어놓은 독립 기준값(구현의 divmod 와 다른 방법): 10pt 미만 0.01, 이상 0.05
OPT_MAX = D(30)
OPT_GRID: list[D] = [D(k) / 100 for k in range(1000)] + [
    D(10) + D(k) * D("0.05") for k in range(int((OPT_MAX - 10) / D("0.05")) + 1)
]
opt_prices = st.decimals(min_value=0, max_value=OPT_MAX, places=5)


@given(products, prices, modes)
def test_result_is_multiple_of_its_own_tick(product: Product, p: D, mode: RoundMode) -> None:
    r = round_to_tick(product, p, mode)
    assert r % spec(product).tick_size(r) == 0
    assert is_on_tick(product, r)


@given(products, prices, modes, modes)
def test_idempotent(product: Product, p: D, m1: RoundMode, m2: RoundMode) -> None:
    r = round_to_tick(product, p, m1)
    assert round_to_tick(product, r, m2) == r


@given(products, prices)
def test_down_le_price_le_up_and_nearest_is_one_of_them(product: Product, p: D) -> None:
    down = round_to_tick(product, p, "down")
    up = round_to_tick(product, p, "up")
    near = round_to_tick(product, p)
    assert down <= p <= up
    assert near in (down, up)
    assert (down == up) == is_on_tick(product, p)


@given(products, prices)
def test_nearest_moves_at_most_half_a_tick(product: Product, p: D) -> None:
    # 경계 근처(9.996 → 10.00)에서도 가격 자신의 구간 틱의 절반 이내다
    assert abs(round_to_tick(product, p) - p) * 2 <= spec(product).tick_size(p)


@given(products, prices, prices, modes)
def test_monotone(product: Product, a: D, b: D, mode: RoundMode) -> None:
    lo, hi = min(a, b), max(a, b)
    assert round_to_tick(product, lo, mode) <= round_to_tick(product, hi, mode)


@given(opt_prices)
def test_option_rounding_matches_enumerated_grid(p: D) -> None:
    down = OPT_GRID[bisect_right(OPT_GRID, p) - 1]  # p 이하 최대 격자점
    up = OPT_GRID[bisect_left(OPT_GRID, p)]  # p 이상 최소 격자점
    assert round_to_tick(Product.KOSPI200_OPTIONS, p, "down") == down
    assert round_to_tick(Product.KOSPI200_OPTIONS, p, "up") == up
    near = up if (up - p) <= (p - down) else down  # 절반은 위로
    assert round_to_tick(Product.KOSPI200_OPTIONS, p) == near
