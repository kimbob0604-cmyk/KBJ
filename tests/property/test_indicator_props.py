"""지표 정본 속성(metrics §30 — hypothesis).

- 미래 불변(창 밖 불변): 칸 i 이후의 값을 바꿔도 칸 ≤ i 의 출력은 그대로(룩어헤드 없음).
- SMA 선형성: sma(a·x + b) = a·sma(x) + b.
- RSI 는 0~100, 볼린저 상·하단은 가운데 대칭, EMA 는 상수열의 고정점.
- 결측 칸은 재귀 지표에서 건너뛴다(그 칸을 지운 배열과 같은 값).
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core import indicators as ind

prices = st.lists(
    st.floats(min_value=1.0, max_value=1_000.0, allow_nan=False, allow_infinity=False),
    min_size=5,
    max_size=80,
)


def _ohlc(c: list[float]) -> tuple[list[float], list[float], list[float]]:
    return [x * 1.02 for x in c], [x * 0.97 for x in c], c


def _all_outputs(c: list[float]) -> list[list[float | None]]:
    h, lo, _ = _ohlc(c)
    m = ind.macd(c, 3, 6, 2)
    b = ind.bollinger(c, 4)
    a = ind.adx(h, lo, c, 3)
    return [
        ind.sma(c, 3), ind.ema(c, 3), ind.rsi(c, 3), m.line, m.signal, m.hist,
        b.upper, b.lower, b.pctb, b.width, ind.width_rank(b.width, 3), a.adx, a.pdi, a.mdi,
        ind.atr(h, lo, c, 3), ind.vol_ratio(c, 3), ind.high_n(h, 3), ind.low_n(lo, 3),
        ind.change_pct(c), ind.true_range(h, lo, c),
    ]  # fmt: skip


def _eq(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


@settings(max_examples=60, deadline=None)
@given(prices, st.data())
def test_no_lookahead(c: list[float], data: st.DataObject) -> None:
    i = data.draw(st.integers(min_value=0, max_value=len(c) - 1))
    tail = data.draw(
        st.lists(
            st.floats(min_value=1.0, max_value=1_000.0),
            min_size=len(c) - i - 1,
            max_size=len(c) - i - 1,
        )
    )
    c2 = c[: i + 1] + tail
    for a, b in zip(_all_outputs(c), _all_outputs(c2), strict=True):
        for j in range(i + 1):
            assert _eq(a[j], b[j]), (j, a[j], b[j])


@settings(max_examples=60, deadline=None)
@given(prices, st.floats(min_value=0.5, max_value=3.0), st.floats(min_value=-50, max_value=50))
def test_sma_linear(c: list[float], a: float, b: float) -> None:
    lhs = ind.sma([a * x + b for x in c], 3)
    rhs = ind.sma(c, 3)
    for u, v in zip(lhs, rhs, strict=True):
        assert (u is None) == (v is None)
        if u is not None and v is not None:
            assert math.isclose(u, a * v + b, rel_tol=1e-9, abs_tol=1e-6)


@settings(max_examples=60, deadline=None)
@given(prices)
def test_rsi_bounds_and_bollinger_symmetric(c: list[float]) -> None:
    assert all(0 <= v <= 100 for v in ind.rsi(c, 3) if v is not None)
    b = ind.bollinger(c, 4)
    for up, mid, lo in zip(b.upper, b.mid, b.lower, strict=True):
        if mid is not None:
            assert up is not None and lo is not None
            assert math.isclose(up - mid, mid - lo, rel_tol=1e-9, abs_tol=1e-9)


@settings(max_examples=30, deadline=None)
@given(st.floats(min_value=1.0, max_value=1_000.0), st.integers(min_value=2, max_value=20))
def test_ema_constant_fixed_point(v: float, n: int) -> None:
    out = ind.ema([v] * (n + 10), n)
    assert all(math.isclose(x, v, rel_tol=1e-12) for x in out[n - 1 :] if x is not None)
    assert out[: n - 1] == [None] * (n - 1)


@settings(max_examples=40, deadline=None)
@given(prices, st.data())
def test_recursive_skip_missing(c: list[float], data: st.DataObject) -> None:
    k = data.draw(st.integers(min_value=0, max_value=len(c)))
    gapped: list[float | None] = [*c[:k], None, *c[k:]]
    for fn in (lambda x: ind.ema(x, 3), lambda x: ind.rsi(x, 3)):
        dense = fn(c)
        out = fn(gapped)
        assert out[k] is None
        assert [*out[:k], *out[k + 1 :]] == dense
