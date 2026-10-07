"""core.greeks — 단위(metrics §0)를 독립 해석해·차분으로 고정한다.

감마 1/pt, 베가 IV 1%p 당 pt, 세타 달력 1일(1/365년) 당 pt. vollib 의 스케일(베가 ×0.01,
세타 ÷365)이 바뀌면 여기서 깨진다.

Vanna·Charm(metrics §4.1·§4.2): 해석식(−φ(d1)·d2/σ, φ(d1)·d2/(2T)/365)을 vollib 델타의 중앙 차분
(σ ± h, T ± h)과 대조하고, 콜·풋이 같음·부호·만기 가까울수록 커지는 Charm 을 본다.
"""

import math

import pytest

from core import black76
from core.black76 import Flag
from core.greeks import Greeks, charm, gamma, greeks, vanna

DAY = 1 / 365


def _cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _ref_greeks(flag: Flag, f: float, k: float, t: float, sigma: float, r: float) -> Greeks:
    """교과서 Black-76 그릭스를 math 로 직접 — vollib 과 독립. 세타는 −∂V/∂T."""
    s = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + s * s / 2) / s
    d2 = d1 - s
    df = math.exp(-r * t)
    decay = -f * df * _pdf(d1) * sigma / (2 * math.sqrt(t))  # 연 단위, r 항 제외
    if flag == "c":
        delta = df * _cdf(d1)
        theta_y = decay + r * f * df * _cdf(d1) - r * k * df * _cdf(d2)
    else:
        delta = -df * _cdf(-d1)
        theta_y = decay - r * f * df * _cdf(-d1) + r * k * df * _cdf(-d2)
    return Greeks(
        delta=delta,
        gamma=df * _pdf(d1) / (f * s),
        vega=f * df * _pdf(d1) * math.sqrt(t) * 0.01,  # σ 1%p 당
        theta=theta_y / 365,  # 달력 1일 당
    )


def test_vollib_reference_values() -> None:
    # vollib·py_vollib 문서 기준값 (PLAN §6.3 "py_vollib 기준값")
    c = greeks("c", 49.0, 50.0, 0.3846, 0.2, r=0.05)
    p = greeks("p", 49.0, 50.0, 0.3846, 0.2, r=0.05)
    assert math.isclose(c.delta, 0.45107017482201828, rel_tol=1e-9)
    assert math.isclose(c.gamma, 0.0640646705882, rel_tol=1e-9)
    assert math.isclose(c.vega, 0.118317785624, rel_tol=1e-9)
    assert math.isclose(c.theta, -0.00816236877462, rel_tol=1e-9)
    assert math.isclose(p.theta, -0.00802799155312, rel_tol=1e-9)
    assert p.gamma == c.gamma
    assert p.vega == c.vega


def test_atm_hand_value() -> None:
    # ATM·r=0: d1 = σ√T/2. 감마 = φ(d1)/(Fσ√T), 베가(1%p) = F·φ(d1)·√T·0.01
    f, t, sigma = 1100.0, 30 * DAY, 0.2
    d1 = sigma * math.sqrt(t) / 2
    g = greeks("c", f, f, t, sigma)
    assert g.gamma == pytest.approx(_pdf(d1) / (f * sigma * math.sqrt(t)), rel=1e-12)
    assert g.vega == pytest.approx(f * _pdf(d1) * math.sqrt(t) * 0.01, rel=1e-12)
    assert g.vega == pytest.approx(1.2576, abs=1e-4)  # IV 1%p 오르면 약 1.26pt


@pytest.mark.parametrize("flag", ["c", "p"])
@pytest.mark.parametrize("k", [900.0, 1050.0, 1100.0, 1102.5, 1150.0, 1300.0])
@pytest.mark.parametrize("t", [5 / (365 * 24 * 60), DAY, 7 * DAY, 0.5])
@pytest.mark.parametrize("sigma", [0.1, 0.3, 1.2])
@pytest.mark.parametrize("r", [0.0, 0.035])
def test_matches_closed_form(flag: Flag, k: float, t: float, sigma: float, r: float) -> None:
    got = greeks(flag, 1100.0, k, t, sigma, r=r)
    ref = _ref_greeks(flag, 1100.0, k, t, sigma, r)
    for name in ("delta", "gamma", "vega", "theta"):
        a, b = getattr(got, name), getattr(ref, name)
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12), (name, a, b)


@pytest.mark.parametrize("flag", ["c", "p"])
@pytest.mark.parametrize("k", [1050.0, 1100.0, 1150.0])
@pytest.mark.parametrize("r", [0.0, 0.03])
def test_units_match_finite_differences(flag: Flag, k: float, r: float) -> None:
    """vollib 스케일을 black76.price 차분으로 확인: 1pt·1%p·1일."""
    f, t, sigma = 1100.0, 30 * DAY, 0.2

    def p(ff: float = f, tt: float = t, ss: float = sigma) -> float:
        return black76.price(flag, ff, k, tt, ss, r=r)

    g = greeks(flag, f, k, t, sigma, r=r)
    h = 0.5
    assert g.delta == pytest.approx((p(ff=f + h) - p(ff=f - h)) / (2 * h), rel=1e-4)
    assert g.gamma == pytest.approx((p(ff=f + h) - 2 * p() + p(ff=f - h)) / h**2, rel=1e-4)
    # σ 가 0.01(1%p) 움직일 때의 가격 변화
    assert g.vega == pytest.approx(p(ss=sigma + 0.005) - p(ss=sigma - 0.005), rel=1e-3)
    # 하루가 지날 때(잔존 T 가 1/365 줄 때)의 가격 변화
    assert g.theta == pytest.approx(p(tt=t - DAY / 2) - p(tt=t + DAY / 2), rel=1e-3)


@pytest.mark.parametrize("k", [1000.0, 1100.0, 1200.0])
def test_signs_at_r_zero(k: float) -> None:
    c = greeks("c", 1100.0, k, 7 * DAY, 0.25)
    p = greeks("p", 1100.0, k, 7 * DAY, 0.25)
    assert 0 < c.delta < 1
    assert -1 < p.delta < 0
    assert c.gamma > 0 and c.gamma == p.gamma
    assert c.vega > 0 and c.vega == p.vega
    assert c.theta < 0 and c.theta == pytest.approx(p.theta, rel=1e-12)  # r=0 이면 같다


def test_gamma_only_matches_greeks() -> None:
    for flag in ("c", "p"):
        g = greeks(flag, 1100.0, 1112.5, 3 * DAY, 0.18, r=0.01)
        assert gamma(1100.0, 1112.5, 3 * DAY, 0.18, r=0.01) == g.gamma


def test_returns_builtin_floats() -> None:
    g = greeks("p", 1100.0, 1090.0, 7 * DAY, 0.2)
    assert all(type(v) is float for v in (g.delta, g.gamma, g.vega, g.theta))
    assert type(gamma(1100.0, 1090.0, 7 * DAY, 0.2)) is float


@pytest.mark.parametrize(
    ("f", "k", "t", "sigma", "match"),
    [
        (1100.0, 1100.0, 0.0, 0.2, "T"),
        (1100.0, 1100.0, 0.1, 0.0, "σ"),
        (1100.0, 0.0, 0.1, 0.2, "K"),
        (math.nan, 1100.0, 0.1, 0.2, "F"),
    ],
)
def test_rejects_bad_inputs(f: float, k: float, t: float, sigma: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        greeks("c", f, k, t, sigma)
    with pytest.raises(ValueError, match=match):
        gamma(f, k, t, sigma)


def test_rejects_bad_flag() -> None:
    with pytest.raises(ValueError, match="flag"):
        greeks("put", 1100.0, 1100.0, 0.1, 0.2)  # pyright: ignore[reportArgumentType]


# ── Vanna·Charm (metrics §4.1·§4.2) ──


def _vanna_numeric(flag: Flag, f: float, k: float, t: float, sigma: float) -> float:
    h = sigma * 1e-4
    up, down = greeks(flag, f, k, t, sigma + h).delta, greeks(flag, f, k, t, sigma - h).delta
    return (up - down) / (2 * h)


def _charm_numeric(flag: Flag, f: float, k: float, t: float, sigma: float) -> float:
    """−∂Δ/∂T 를 달력 1일로 — T 가 줄어드는(시간이 가는) 쪽의 델타 변화."""
    h = t * 1e-4
    up, down = greeks(flag, f, k, t + h, sigma).delta, greeks(flag, f, k, t - h, sigma).delta
    return -(up - down) / (2 * h) / 365


def test_vanna_charm_atm_hand_values() -> None:
    # ATM·r=0: d1 = σ√T/2, d2 = −σ√T/2 → vanna = φ(d1)·√T/2, charm = −φ(d1)·σ/(4√T)/365
    f, t, sigma = 1100.0, 30 * DAY, 0.2
    d1 = sigma * math.sqrt(t) / 2
    assert vanna(f, f, t, sigma) == pytest.approx(_pdf(d1) * math.sqrt(t) / 2, rel=1e-12)
    assert charm(f, f, t, sigma) == pytest.approx(
        -_pdf(d1) * sigma / (4 * math.sqrt(t)) / 365, rel=1e-12
    )


@pytest.mark.parametrize("flag", ["c", "p"])
@pytest.mark.parametrize("k", [900.0, 1050.0, 1100.0, 1102.5, 1150.0, 1300.0])
@pytest.mark.parametrize("t", [5 / (365 * 24 * 60), 30 / (365 * 24 * 60), DAY, 7 * DAY, 0.5])
@pytest.mark.parametrize("sigma", [0.1, 0.3, 1.2])
def test_vanna_charm_match_numerical_derivatives(
    flag: Flag, k: float, t: float, sigma: float
) -> None:
    """해석식 대 수치 미분 — Vanna 는 σ ± h, Charm 은 T ± h. 콜·풋 델타 어느 쪽 차분과도 같다."""
    f = 1100.0
    assert math.isclose(
        vanna(f, k, t, sigma), _vanna_numeric(flag, f, k, t, sigma), rel_tol=1e-6, abs_tol=1e-9
    )
    assert math.isclose(
        charm(f, k, t, sigma), _charm_numeric(flag, f, k, t, sigma), rel_tol=1e-6, abs_tol=1e-9
    )


@pytest.mark.parametrize("k", [1000.0, 1080.0])
def test_charm_and_vanna_signs(k: float) -> None:
    """ITM 콜(K < F, d2 > 0): 시간이 가면 델타가 1 쪽으로 — charm > 0, IV 가 오르면 델타가 0.5
    쪽으로 — vanna < 0. OTM 콜(K > F)은 둘 다 반대."""
    f, t, sigma = 1100.0, 7 * DAY, 0.2
    assert charm(f, k, t, sigma) > 0 and vanna(f, k, t, sigma) < 0
    otm = 2 * f - k
    assert charm(f, otm, t, sigma) < 0 and vanna(f, otm, t, sigma) > 0


def test_charm_grows_toward_expiry() -> None:
    """ATM 가까운 행사가(K/F 1.005)는 만기가 가까울수록 |charm| 이 커진다(30일 → 1일 → 1시간)."""
    f, k, sigma = 1100.0, 1105.5, 0.2
    ts = [30 * DAY, 10 * DAY, 3 * DAY, DAY, DAY / 24]
    sizes = [abs(charm(f, k, t, sigma)) for t in ts]
    assert sizes == sorted(sizes) and sizes[-1] > 100 * sizes[0]


def test_vanna_charm_return_builtin_floats_and_reject_bad_inputs() -> None:
    assert type(vanna(1100.0, 1090.0, 7 * DAY, 0.2)) is float
    assert type(charm(1100.0, 1090.0, 7 * DAY, 0.2)) is float
    for fn in (vanna, charm):
        with pytest.raises(ValueError, match="T"):
            fn(1100.0, 1100.0, 0.0, 0.2)
        with pytest.raises(ValueError, match="σ"):
            fn(1100.0, 1100.0, 0.1, 0.0)
        with pytest.raises(ValueError, match="F"):
            fn(math.nan, 1100.0, 0.1, 0.2)
