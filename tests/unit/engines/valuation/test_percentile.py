"""선형 보간 백분위·경험적 순위 — numpy 'linear' 와 같은 값(시험 안에서만 numpy, §8.3 test_band)."""

from __future__ import annotations

import math
import random

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.engines.valuation.percentile import BAND_QS, percentile, percentiles, rank_pct

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


@settings(max_examples=200, deadline=None)
@given(st.lists(finite, min_size=1, max_size=60), st.floats(min_value=0, max_value=100))
def test_numpy_linear_과_같다(xs: list[float], q: float) -> None:
    want = float(np.percentile(np.array(xs), q, method="linear"))
    assert math.isclose(percentile(xs, q), want, rel_tol=1e-9, abs_tol=1e-9)


def test_밴드_다섯_줄_한번에() -> None:
    rng = random.Random(7)  # noqa: S311 — 고정 시드 합성 표본
    xs = [rng.uniform(5, 30) for _ in range(1240)]
    got = percentiles(xs)
    want = np.percentile(np.array(xs), list(BAND_QS))
    assert all(math.isclose(a, float(b), rel_tol=1e-12) for a, b in zip(got, want, strict=True))
    assert list(got) == sorted(got)


def test_손계산() -> None:
    xs = [10.0, 20.0, 30.0, 40.0]
    # h = (4 − 1)·0.25 = 0.75 → 10 + 0.75 × 10 = 17.5
    assert percentile(xs, 25) == pytest.approx(17.5)
    assert percentile(xs, 50) == pytest.approx(25.0)
    assert percentile(xs, 0) == 10.0
    assert percentile(xs, 100) == 40.0
    assert percentile([3.0], 90) == 3.0


def test_순위는_경험적() -> None:
    xs = [1.0, 2.0, 2.0, 3.0]
    assert rank_pct(xs, 0.5) == 0.0
    assert rank_pct(xs, 2.0) == 75.0  # 같은 값은 모두 센다(≤)
    assert rank_pct(xs, 3.0) == 100.0
    assert rank_pct(xs, 99.0) == 100.0


@pytest.mark.parametrize("bad", [[], [1.0, math.nan], [math.inf]])
def test_빈_표본_NaN_inf_는_거절(bad: list[float]) -> None:
    with pytest.raises(ValueError):
        percentile(bad, 50)


def test_q_범위() -> None:
    with pytest.raises(ValueError):
        percentile([1.0, 2.0], 101)
    with pytest.raises(ValueError):
        rank_pct([1.0], math.nan)
