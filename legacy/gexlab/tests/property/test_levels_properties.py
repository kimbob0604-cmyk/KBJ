"""핵심 레벨 속성 (PLAN §6.3 "Flip 이 격자 범위 안", docs/metrics.md §3).

- Flip 교차점은 모두 격자 [0.95F, 1.05F] 안이고, level 은 그중 F 에 가장 가까운 것
- 모든 OI 를 k 배 해도 콜월·풋월·절대감마·Flip 이 움직이지 않는다(GEX 가 OI 에 선형)
- 콜만 있으면 격자 전체가 양수, 풋만 있으면 음수 — Flip 없음
- sign_crossings: 교차점은 격자 안·오름차순, 기본 규칙은 0 아닌 값의 부호 변화 수만큼, every_zero 는
  0 인 격자점을 모두 담고 0 이 없으면 기본과 같다
"""

import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise

from hypothesis import given, settings
from hypothesis import strategies as st

from core.calendar import KST, expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import CallPut, ExpiryEval, OptionEval, OptionQuote
from core.greeks import greeks
from core.iv import IvResult
from core.levels import (
    ZeroRule,
    abs_gamma_strike,
    call_wall,
    gamma_flip,
    put_wall,
    sign_crossings,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
STRIKES = tuple(float(1050 + 5 * i) for i in range(21))  # 1050~1150

Leg = tuple[float, CallPut, int, float]  # 행사가, 콜/풋, OI, σ

sides: st.SearchStrategy[CallPut] = st.sampled_from(("C", "P"))
sigmas = st.floats(min_value=0.08, max_value=0.6)
forwards = st.floats(min_value=1060.0, max_value=1140.0)
zero_rules: st.SearchStrategy[ZeroRule] = st.sampled_from(("sign_change", "every_zero"))


def leg_lists(cp: st.SearchStrategy[CallPut] = sides) -> st.SearchStrategy[list[Leg]]:
    return st.lists(
        st.tuples(st.sampled_from(STRIKES), cp, st.integers(0, 50_000), sigmas),
        min_size=1,
        max_size=12,
        unique_by=lambda leg: (leg[0], leg[1]),
    )


def make(F: float, legs: Sequence[Leg], expiry: str = "202610", ed: date = date(2026, 10, 8)):
    t = time_to_expiry(NOW, expiry_at(ed))
    opts: list[OptionEval] = []
    for k, cp, oi, sigma in sorted(legs, key=lambda leg: (leg[0], leg[1])):
        q = OptionQuote(
            expiry=expiry, expiry_date=ed, strike=Decimal(repr(k)), cp=cp, last=Decimal(1), oi=oi
        )
        g = greeks("c" if cp == "C" else "p", F, k, t, sigma)
        opts.append(OptionEval(q, q.price(), IvResult(sigma, "ok", "model", None), g, False, None))
    fwd = ForwardResult(F, "ok", Decimal(1100), (Decimal(1100),), None, ())
    return ExpiryEval(expiry, ed, fwd, t, tuple(opts))


def _scale(legs: Sequence[Leg], k: int) -> list[Leg]:
    return [(s, cp, oi * k, sigma) for s, cp, oi, sigma in legs]


@settings(max_examples=40, deadline=None)
@given(
    forwards,
    leg_lists(),
    st.none() | st.tuples(st.floats(min_value=0.98, max_value=1.02), leg_lists()),
    zero_rules,
)
def test_flip_lies_inside_grid(
    f: float, legs: list[Leg], second: tuple[float, list[Leg]] | None, zero_rule: ZeroRule
) -> None:
    evs = [make(f, legs)]
    if second is not None:
        evs.append(make(f * second[0], second[1], "202611", date(2026, 11, 12)))
    fl = gamma_flip(evs, zero_rule=zero_rule)
    assert fl.f_ref == f  # 최근접 만기 F
    lo, hi = fl.profile[0][0], fl.profile[-1][0]
    # 끝점은 격자 간격 오차(1e-9pt) 안에서 0.95F·1.05F
    assert math.isclose(lo, 0.95 * f, rel_tol=1e-12)
    assert math.isclose(hi, 1.05 * f, rel_tol=1e-12)
    assert all(lo <= c <= hi for c in fl.crossings)
    assert list(fl.crossings) == sorted(fl.crossings)
    if fl.level is None:
        assert fl.crossings == ()
    else:
        assert lo <= fl.level <= hi
        assert all(abs(fl.level - f) <= abs(c - f) for c in fl.crossings)
        assert fl.multi_cross is (len(fl.crossings) >= 2)
        assert fl.distance_pct is not None and abs(fl.distance_pct) <= 5 + 1e-9


@settings(max_examples=40, deadline=None)
@given(forwards, leg_lists(), st.integers(min_value=2, max_value=1000))
def test_scaling_oi_does_not_move_levels(f: float, legs: list[Leg], k: int) -> None:
    base, scaled = [make(f, legs)], [make(f, _scale(legs, k))]
    for fn in (call_wall, put_wall, abs_gamma_strike):
        assert fn(scaled).strike == fn(base).strike
    fb, fs = gamma_flip(base), gamma_flip(scaled)
    assert len(fs.crossings) == len(fb.crossings)
    for a, b in zip(fb.crossings, fs.crossings, strict=True):
        assert math.isclose(a, b, rel_tol=0.0, abs_tol=1e-6)
    assert fs.multi_cross is fb.multi_cross
    assert (fs.level is None) is (fb.level is None)
    if fs.level is not None and fb.level is not None:
        assert math.isclose(fs.level, fb.level, rel_tol=0.0, abs_tol=1e-6)


@settings(max_examples=30, deadline=None)
@given(forwards, st.sampled_from(("C", "P")), st.data())
def test_one_sided_chain_has_no_flip(f: float, cp: CallPut, data: st.DataObject) -> None:
    legs = data.draw(leg_lists(st.just(cp)))
    fl = gamma_flip([make(f, legs)])
    assert (fl.level, fl.crossings) == (None, ())
    if cp == "C":
        assert all(g >= 0 for _, g in fl.profile)
    else:
        assert all(g <= 0 for _, g in fl.profile)


@given(st.lists(st.sampled_from((-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0)), min_size=1, max_size=30))
def test_sign_crossings_rules(values: list[float]) -> None:
    xs = [0.25 * i for i in range(len(values))]
    base = sign_crossings(xs, values)
    lit = sign_crossings(xs, values, zero_rule="every_zero")
    for found in (base, lit):
        assert list(found) == sorted(found)
        assert all(xs[0] <= c <= xs[-1] for c in found)
    nonzero = [v for v in values if v != 0]
    assert len(base) == sum((a > 0) != (b > 0) for a, b in pairwise(nonzero))
    zeros = {x for x, v in zip(xs, values, strict=True) if v == 0}
    assert zeros <= set(lit)
    if not zeros:
        assert lit == base
