"""익스포저 확장 속성 (docs/metrics.md §4.1~§4.3).

임의 체인(행사가 1050~1150, OI 0~50,000, σ 0.08~0.6, 만기 1~30일 앞)에서:

- VEX = ∂DEX/∂σ × 0.01, CEX = −∂DEX/∂T ÷ 365 — 모든 종목 σ·T 를 같이 옮긴 중앙 차분과 같다
- 같은 행사가·같은 OI 의 콜·풋 짝만 있으면 VEX·CEX 가 0(상쇄), 콜만 있으면 종목 합의 부호
- GEX P/C 는 0 이상이고, 콜 GEX 합이 0 이 아니면 |풋 합| ÷ 콜 합
"""

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from core.calendar import expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import OPTION_MULTIPLIER, CallPut, ExpiryEval, OptionEval, OptionQuote, dex
from core.greeks import greeks, vanna
from core.iv import IvResult
from core.metrics.exposure import cex, gex_put_call_ratio, vex

EXP = date(2026, 10, 8)
STRIKES = tuple(float(1050 + 5 * i) for i in range(21))
Leg = tuple[float, CallPut, int, float]

sides: st.SearchStrategy[CallPut] = st.sampled_from(("C", "P"))
sigmas = st.floats(min_value=0.08, max_value=0.6)
forwards = st.floats(min_value=1060.0, max_value=1140.0)
days_left = st.floats(min_value=1.0, max_value=30.0)


def leg_lists(cp: st.SearchStrategy[CallPut] = sides) -> st.SearchStrategy[list[Leg]]:
    return st.lists(
        st.tuples(st.sampled_from(STRIKES), cp, st.integers(0, 50_000), sigmas),
        min_size=1,
        max_size=12,
        unique_by=lambda leg: (leg[0], leg[1]),
    )


def make(F: float, legs: Sequence[Leg], days: float, *, dsigma: float = 0.0) -> ExpiryEval:
    now = expiry_at(EXP) - timedelta(days=days)
    t = time_to_expiry(now, expiry_at(EXP))
    opts: list[OptionEval] = []
    for k, cp, oi, sigma in sorted(legs, key=lambda leg: (leg[0], leg[1])):
        q = OptionQuote(
            expiry="202610", expiry_date=EXP, strike=Decimal(repr(k)), cp=cp, last=Decimal(1), oi=oi
        )
        s = sigma + dsigma
        g = greeks("c" if cp == "C" else "p", F, k, t, s)
        opts.append(OptionEval(q, q.price(), IvResult(s, "ok", "model", None), g, False, None))
    fwd = ForwardResult(F, "ok", Decimal(1100), (Decimal(1100),), None, ())
    return ExpiryEval("202610", EXP, fwd, t, tuple(opts))


def _with_t(ev: ExpiryEval, t: float) -> ExpiryEval:
    F = ev.forward.F
    assert F is not None
    opts: list[OptionEval] = []
    for o in ev.options:
        assert o.iv is not None and o.iv.sigma is not None
        g = greeks("c" if o.quote.cp == "C" else "p", F, float(o.quote.strike), t, o.iv.sigma)
        opts.append(replace(o, greeks=g))
    return replace(ev, T=t, options=tuple(opts))


def _scale(legs: Sequence[Leg], F: float) -> float:
    return sum(oi for _, _, oi, _ in legs) * OPTION_MULTIPLIER * F


@given(forwards, leg_lists(), days_left)
def test_vex_is_the_sigma_derivative_of_dex(F: float, legs: list[Leg], days: float) -> None:
    h = 1e-5
    up = dex([make(F, legs, days, dsigma=h)]).value
    down = dex([make(F, legs, days, dsigma=-h)]).value
    got = vex([make(F, legs, days)]).value
    assert up is not None and down is not None and got is not None
    num = (up - down) / (2 * h) * 0.01
    assert math.isclose(got, num, rel_tol=1e-5, abs_tol=1e-9 * _scale(legs, F))


@given(forwards, leg_lists(), days_left)
def test_cex_is_minus_the_time_derivative_of_dex_per_day(
    F: float, legs: list[Leg], days: float
) -> None:
    ev = make(F, legs, days)
    h = ev.T * 1e-5
    up, down = dex([_with_t(ev, ev.T + h)]).value, dex([_with_t(ev, ev.T - h)]).value
    got = cex([ev]).value
    assert up is not None and down is not None and got is not None
    num = -(up - down) / (2 * h) / 365
    assert math.isclose(got, num, rel_tol=1e-5, abs_tol=1e-9 * _scale(legs, F))


pair_lists = st.lists(
    st.tuples(st.sampled_from(STRIKES), st.integers(0, 50_000), sigmas),
    min_size=1,
    max_size=10,
    unique_by=lambda x: x[0],
)


@given(forwards, pair_lists, days_left)
def test_call_put_pairs_with_the_same_oi_cancel(
    F: float, pairs: list[tuple[float, int, float]], days: float
) -> None:
    legs: list[Leg] = [(k, cp, oi, s) for k, oi, s in pairs for cp in ("C", "P")]
    ev = make(F, legs, days)
    for fn in (vex, cex):
        v = fn([ev]).value
        assert v is not None and abs(v) <= 1e-9 * _scale(legs, F)


@given(forwards, leg_lists(st.just("C")), days_left)
def test_calls_only_carry_the_sign_of_their_vanna(F: float, legs: list[Leg], days: float) -> None:
    ev = make(F, legs, days)
    total = math.fsum(oi * vanna(F, k, ev.T, s) for k, _, oi, s in legs)
    got = vex([ev]).value
    assert got is not None
    assert math.isclose(got, total * OPTION_MULTIPLIER * F * 0.01, rel_tol=1e-9, abs_tol=1e-6)


@given(forwards, leg_lists(), days_left)
def test_gex_put_call_ratio_is_non_negative(F: float, legs: list[Leg], days: float) -> None:
    r = gex_put_call_ratio([make(F, legs, days)])
    assert r.call_gex is not None and r.put_gex is not None
    assert r.call_gex >= 0 >= r.put_gex
    if r.call_gex == 0:
        assert r.value is None
    else:
        assert r.value is not None and r.value >= 0
        assert math.isclose(r.value, abs(r.put_gex) / r.call_gex, rel_tol=1e-12)
