"""Black-76 가격·그릭스·IV 속성 (PLAN §6.3, docs/metrics.md §1.5·§1.6).

영역: F 200~2,000pt, K/F 0.7~1.3, T 5분~1년, σ 0.05~2, r 0~10%. KIS IV 폴백 T 환산(§1.5)의 T_KIS 는
0.5일~1년(§1.7 — 만기일 0.5일이 가장 짧다).
"""

import math

from hypothesis import assume, given
from hypothesis import strategies as st

from core import black76
from core.black76 import Flag
from core.greeks import charm, greeks, vanna
from core.iv import IV_MAX, MIN_PREMIUM, implied_vol, rescale_sigma

MINUTE = 1 / (365 * 24 * 60)

forwards = st.floats(min_value=200.0, max_value=2000.0)
moneyness = st.floats(min_value=0.7, max_value=1.3)
times = st.floats(min_value=5 * MINUTE, max_value=1.0)
vols = st.floats(min_value=0.05, max_value=2.0)
rates = st.floats(min_value=0.0, max_value=0.1)
flags = st.sampled_from(["c", "p"])
kis_times = st.floats(min_value=0.5 / 365, max_value=1.0)


@given(flags, forwards, moneyness, times, vols, rates)
def test_gamma_non_negative(flag: Flag, f: float, m: float, t: float, s: float, r: float) -> None:
    assert greeks(flag, f, f * m, t, s, r=r).gamma >= 0


@given(forwards, moneyness, times, vols, rates)
def test_call_put_gamma_and_vega_equal(f: float, m: float, t: float, s: float, r: float) -> None:
    c, p = greeks("c", f, f * m, t, s, r=r), greeks("p", f, f * m, t, s, r=r)
    assert c.gamma == p.gamma
    assert c.vega == p.vega


@given(forwards, moneyness, times, vols, rates)
def test_put_call_parity(f: float, m: float, t: float, s: float, r: float) -> None:
    # C − P = e^(−rT)·(F − K). r = 0 이면 C − P = F − K (metrics §1.6)
    k = f * m
    c, p = black76.price("c", f, k, t, s, r=r), black76.price("p", f, k, t, s, r=r)
    assert math.isclose(c - p, math.exp(-r * t) * (f - k), rel_tol=1e-12, abs_tol=1e-10 * f)


@given(forwards, moneyness, times, vols, rates)
def test_delta_call_minus_put(f: float, m: float, t: float, s: float, r: float) -> None:
    # Δ_c − Δ_p = e^(−rT). r = 0 이면 1
    c, p = greeks("c", f, f * m, t, s, r=r), greeks("p", f, f * m, t, s, r=r)
    assert math.isclose(c.delta - p.delta, math.exp(-r * t), abs_tol=1e-12)
    assert 0 <= c.delta <= 1
    assert -1 <= p.delta <= 0


@given(flags, forwards, moneyness, times, vols, rates)
def test_price_within_no_arbitrage_bounds(
    flag: Flag, f: float, m: float, t: float, s: float, r: float
) -> None:
    k = f * m
    p = black76.price(flag, f, k, t, s, r=r)
    assert (
        black76.intrinsic(flag, f, k, t, r) - 1e-12 * f <= p < black76.upper_bound(flag, f, k, t, r)
    )


@given(flags, forwards, moneyness, times, vols, rates)
def test_iv_round_trip(flag: Flag, f: float, m: float, t: float, s: float, r: float) -> None:
    """가격 → IV → σ. 시간가치가 0.02pt 이상인 종목(ITM 도 OTM 짝과 같은 조건)."""
    k = f * m
    p = black76.price(flag, f, k, t, s, r=r)
    assume(p - black76.intrinsic(flag, f, k, t, r) >= MIN_PREMIUM)
    res = implied_vol(p, f, k, t, flag, r=r)
    assert (res.quality, res.source) == ("ok", "model")
    assert res.sigma is not None
    assert math.isclose(res.sigma, s, rel_tol=1e-9)


@given(
    flags,
    forwards,
    moneyness,
    times,
    st.floats(min_value=-1.0, max_value=2500.0),
    st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=True)),
    rates,
    st.one_of(st.none(), kis_times),
)
def test_iv_result_is_always_usable_or_invalid(
    flag: Flag,
    f: float,
    m: float,
    t: float,
    p: float,
    kis: float | None,
    r: float,
    t_kis: float | None,
) -> None:
    """임의 가격·KIS 값·T_KIS 에도 예외 없이, 쓰는 IV 는 항상 0 < σ ≤ 300%(§6.1 — KIS 폴백은 T 로
    옮긴 뒤 σ). ok 면 가격을 재현한다.

    §1.2 제외 플래그는 KIS 값·품질과 무관하게 가격 < 0.02pt 일 때만, 항상 선다. T 환산 기록은
    KIS 값을 옮긴 폴백에만 — 쓴 폴백(source kis)과 이상치로 invalid 가 된 폴백
    (`kis_out_of_range*`) 둘 다. t_kis 는 준 값, rescaled 는 T_KIS ≠ 자체 T 일 때. 옮긴 탓의
    invalid(`…_rescaled`)는 옮기기 전 KIS σ 가 범위 안이었다.
    """
    k = f * m
    res = implied_vol(p, f, k, t, flag, r=r, kis_iv_pct=kis, t_kis=t_kis)
    assert res.below_min_premium is (p < MIN_PREMIUM)
    kis_sigma = None if kis is None or math.isnan(kis) else kis / 100
    movable = kis_sigma is not None and math.isfinite(kis_sigma) and kis_sigma > 0
    kis_part = (res.reason or "").split("/")[-1]
    tried = res.source == "kis" or kis_part.startswith("kis_out_of_range")
    if tried and movable and t_kis is not None:
        assert (res.t_kis, res.rescaled) == (t_kis, t_kis != t)
    else:
        assert (res.t_kis, res.rescaled) == (None, False)
    if kis_part == "kis_out_of_range_rescaled":
        assert res.rescaled and kis_sigma is not None and 0 < kis_sigma <= IV_MAX
    if res.quality == "invalid":
        assert res.sigma is None
        return
    assert res.sigma is not None
    assert 0 < res.sigma <= IV_MAX
    if res.quality == "ok":
        assert math.isclose(black76.price(flag, f, k, t, res.sigma, r=r), p, rel_tol=1e-9)


@given(flags, forwards, moneyness, times, kis_times, st.floats(min_value=0.5, max_value=300.0))
def test_rescaled_fallback_preserves_kis_total_variance(
    flag: Flag, f: float, m: float, t: float, t_kis: float, kis_pct: float
) -> None:
    """§1.5 KIS IV 폴백: 옮긴 σ 는 KIS 총분산을 보존한다 — σ²·T = σ_KIS²·T_KIS. 그래서 r = 0 에서
    델타·감마·가격(σ√T 로만 정해진다)이 (σ_KIS, T_KIS) 로 낸 값과 같다. 옮긴 σ 가 300% 를 넘으면
    §6.1 이상치로 invalid(`kis_out_of_range_rescaled` — KIS 값은 범위 안이었다) — 판정은 옮긴 뒤 σ
    로 하고, 옮긴 기록(t_kis·rescaled)은 남는다.
    """
    k = f * m
    price = math.nan  # 역산 실패(invalid_price) — 폴백 경로만 본다
    res = implied_vol(price, f, k, t, flag, kis_iv_pct=kis_pct, t_kis=t_kis)
    kis = kis_pct / 100
    if rescale_sigma(kis, t_kis, t) > IV_MAX:
        assert (res.quality, res.reason) == ("invalid", "invalid_price/kis_out_of_range_rescaled")
        assert (res.t_kis, res.rescaled) == (t_kis, True)
        return
    assert (res.quality, res.source, res.t_kis) == ("estimated", "kis", t_kis)
    assert res.sigma is not None
    assert math.isclose(res.sigma**2 * t, kis**2 * t_kis, rel_tol=1e-12)
    ours, at_kis_t = greeks(flag, f, k, t, res.sigma), greeks(flag, f, k, t_kis, kis)
    assert math.isclose(ours.gamma, at_kis_t.gamma, rel_tol=1e-9, abs_tol=1e-300)
    assert math.isclose(ours.delta, at_kis_t.delta, rel_tol=1e-9, abs_tol=1e-12)
    assert math.isclose(
        black76.price(flag, f, k, t, res.sigma),
        black76.price(flag, f, k, t_kis, kis),
        rel_tol=1e-9,
        abs_tol=1e-9 * f,
    )


@given(flags, forwards, moneyness, times, vols)
def test_vanna_is_the_sigma_derivative_of_delta(
    flag: Flag, f: float, m: float, t: float, s: float
) -> None:
    """metrics §4.1 — Vanna = ∂Δ/∂σ(r = 0): 해석식 대 중앙 차분(σ ± h), 콜·풋 같다."""
    k, h = f * m, s * 1e-4
    num = (greeks(flag, f, k, t, s + h).delta - greeks(flag, f, k, t, s - h).delta) / (2 * h)
    assert math.isclose(vanna(f, k, t, s), num, rel_tol=1e-5, abs_tol=1e-8)


@given(flags, forwards, moneyness, times, vols)
def test_charm_is_minus_the_time_derivative_of_delta_per_day(
    flag: Flag, f: float, m: float, t: float, s: float
) -> None:
    """metrics §4.2 — Charm = −∂Δ/∂T ÷ 365(r = 0): 해석식 대 중앙 차분(T ± h), 콜·풋 같다."""
    k, h = f * m, t * 1e-4
    num = -(greeks(flag, f, k, t + h, s).delta - greeks(flag, f, k, t - h, s).delta) / (2 * h)
    assert math.isclose(charm(f, k, t, s), num / 365, rel_tol=1e-5, abs_tol=1e-8)
