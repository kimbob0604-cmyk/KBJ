"""core.black76 — 문헌값·독립 해석해와 vollib 가격을 맞춘다 (PLAN §6.3)."""

import math

import pytest

from core import black76
from core.black76 import Flag

MINUTE = 1 / (365 * 24 * 60)


def _cdf(x: float) -> float:
    # 아래 꼬리에서도 정확하도록 erfc 로 쓴다
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _ref_price(flag: Flag, f: float, k: float, t: float, sigma: float, r: float) -> float:
    """교과서 Black-76 식을 math 로 직접 — vollib 과 독립."""
    s = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + s * s / 2) / s
    d2 = d1 - s
    df = math.exp(-r * t)
    if flag == "c":
        return df * (f * _cdf(d1) - k * _cdf(d2))
    return df * (k * _cdf(-d2) - f * _cdf(-d1))


@pytest.mark.parametrize("flag", ["c", "p"])
def test_hull_futures_option_example(flag: Flag) -> None:
    # Hull, Options, Futures, and Other Derivatives 7판 예제 16.6 (원유 선물 풋):
    # F = K = 20, r = 9%, T = 4개월, σ = 25% → 1.12. ATM 이라 콜도 같다
    p = black76.price(flag, 20.0, 20.0, 4 / 12, 0.25, r=0.09)
    assert round(p, 2) == 1.12
    assert math.isclose(p, _ref_price(flag, 20.0, 20.0, 4 / 12, 0.25, 0.09), rel_tol=1e-12)


def test_vollib_reference_value() -> None:
    # vollib·py_vollib 문서 기준값 (PLAN §6.3 "py_vollib 기준값")
    p = black76.price("c", 100.0, 100.0, 0.5, 0.2, r=0.02)
    assert math.isclose(p, 5.5811067246048118, rel_tol=1e-12)


def test_price_is_builtin_float() -> None:
    assert type(black76.price("c", 1100.0, 1100.0, 7 / 365, 0.2)) is float


@pytest.mark.parametrize("flag", ["c", "p"])
@pytest.mark.parametrize("k", [1000.0, 1097.5, 1100.0, 1102.5, 1200.0])  # ITM·ATM 부근·OTM
@pytest.mark.parametrize("t", [5 * MINUTE, 1 / 365, 7 / 365, 0.25])
@pytest.mark.parametrize("sigma", [0.12, 0.35, 1.5])
@pytest.mark.parametrize("r", [0.0, 0.035])
def test_matches_closed_form(flag: Flag, k: float, t: float, sigma: float, r: float) -> None:
    f = 1100.0
    p = black76.price(flag, f, k, t, sigma, r=r)
    assert math.isclose(p, _ref_price(flag, f, k, t, sigma, r), rel_tol=1e-10, abs_tol=1e-10)
    assert black76.intrinsic(flag, f, k, t, r) <= p + 1e-12
    assert p < black76.upper_bound(flag, f, k, t, r)


def test_r_zero_is_default() -> None:
    assert black76.price("p", 1100.0, 1080.0, 7 / 365, 0.2) == black76.price(
        "p", 1100.0, 1080.0, 7 / 365, 0.2, r=0.0
    )


def test_intrinsic_and_upper_bound() -> None:
    t, r = 0.5, 0.04
    df = math.exp(-r * t)
    assert black76.intrinsic("c", 110.0, 100.0, t, r) == pytest.approx(10.0 * df)
    assert black76.intrinsic("p", 110.0, 100.0, t, r) == 0.0
    assert black76.intrinsic("p", 90.0, 100.0, t) == 10.0
    assert black76.intrinsic("c", 100.0, 100.0, t) == 0.0
    assert black76.upper_bound("c", 110.0, 100.0, t, r) == pytest.approx(110.0 * df)
    assert black76.upper_bound("p", 110.0, 100.0, t, r) == pytest.approx(100.0 * df)


@pytest.mark.parametrize(
    ("flag", "f", "k", "t", "sigma", "r", "match"),
    [
        ("x", 100.0, 100.0, 0.1, 0.2, 0.0, "flag"),
        ("C", 100.0, 100.0, 0.1, 0.2, 0.0, "flag"),
        ("c", 0.0, 100.0, 0.1, 0.2, 0.0, "F"),
        ("c", -1.0, 100.0, 0.1, 0.2, 0.0, "F"),
        ("c", math.nan, 100.0, 0.1, 0.2, 0.0, "F"),
        ("c", math.inf, 100.0, 0.1, 0.2, 0.0, "F"),
        ("p", 100.0, 0.0, 0.1, 0.2, 0.0, "K"),
        ("p", 100.0, 100.0, 0.0, 0.2, 0.0, "T"),
        ("p", 100.0, 100.0, -0.1, 0.2, 0.0, "T"),
        ("c", 100.0, 100.0, 0.1, 0.0, 0.0, "σ"),
        ("c", 100.0, 100.0, 0.1, -0.2, 0.0, "σ"),
        ("c", 100.0, 100.0, 0.1, math.nan, 0.0, "σ"),
        ("c", 100.0, 100.0, 0.1, 0.2, math.nan, "r"),
    ],
)
def test_rejects_bad_inputs(
    flag: str, f: float, k: float, t: float, sigma: float, r: float, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        black76.price(flag, f, k, t, sigma, r=r)  # pyright: ignore[reportArgumentType]


def test_bounds_reject_bad_inputs() -> None:
    with pytest.raises(ValueError, match="T"):
        black76.intrinsic("c", 100.0, 100.0, 0.0)
    with pytest.raises(ValueError, match="K"):
        black76.upper_bound("p", 100.0, -5.0, 0.1)
