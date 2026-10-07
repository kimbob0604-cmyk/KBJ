"""GEX 속성 (PLAN §6.3 "순GEX 의 콜/풋 대칭성", docs/metrics.md §2).

- OI 선형성: 모든 OI 를 k 배 하면 행사가별·순GEX 도 k 배(실제 흐름 — 가격 → F → IV → 그릭스)
- 콜/풋 OI 맞바꿈: 감마가 같으면 행사가별 GEX 부호가 뒤집힌다
- 순GEX = 행사가별 GEX 합
"""

import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from core import black76
from core.calendar import KST, expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import (
    CallPut,
    ExpiryEval,
    OptionEval,
    OptionQuote,
    evaluate_expiry,
    net_gex,
    strike_gex,
)
from core.greeks import Greeks
from core.iv import IvResult

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
EXP = date(2026, 10, 8)
T = time_to_expiry(NOW, expiry_at(EXP))
STRIKES = tuple(range(1050, 1151, 10))  # 11 개

ois = st.integers(min_value=0, max_value=50_000)
oi_lists = st.lists(ois, min_size=len(STRIKES), max_size=len(STRIKES))
gammas = st.floats(min_value=0.0, max_value=0.05)
forwards = st.floats(min_value=1000.0, max_value=1200.0)


def _priced(f: float, sigma: float, oi_c: Sequence[int], oi_p: Sequence[int]) -> ExpiryEval:
    qs: list[OptionQuote] = []
    for i, k in enumerate(STRIKES):
        sides: tuple[tuple[CallPut, int], ...] = (("C", oi_c[i]), ("P", oi_p[i]))
        for cp, oi in sides:
            p = black76.price("c" if cp == "C" else "p", f, k, T, sigma)
            qs.append(
                OptionQuote(
                    expiry="202610",
                    expiry_date=EXP,
                    strike=Decimal(k),
                    cp=cp,
                    last=Decimal(repr(p)),
                    oi=oi,
                )
            )
    return evaluate_expiry(qs, NOW, f)


def _made(f: float, g: Sequence[float], oi_c: Sequence[int], oi_p: Sequence[int]) -> ExpiryEval:
    """행사가마다 콜·풋 감마가 같은 만기를 그릭스를 직접 넣어 만든다."""
    iv = IvResult(0.2, "ok", "model", None)
    opts: list[OptionEval] = []
    for i, k in enumerate(STRIKES):
        sides: tuple[tuple[CallPut, int, float], ...] = (("C", oi_c[i], 0.5), ("P", oi_p[i], -0.5))
        for cp, oi, delta in sides:
            q = OptionQuote(
                expiry="202610", expiry_date=EXP, strike=Decimal(k), cp=cp, last=Decimal(1), oi=oi
            )
            opts.append(OptionEval(q, q.price(), iv, Greeks(delta, g[i], 0.0, 0.0), False, None))
    fwd = ForwardResult(f, "ok", Decimal(1100), (Decimal(1100),), None, ())
    return ExpiryEval("202610", EXP, fwd, T, tuple(opts))


def _gross(ev: ExpiryEval) -> float:
    return math.fsum(abs(r.gex_call) + abs(r.gex_put) for r in strike_gex(ev).rows)


@settings(max_examples=40, deadline=None)
@given(
    st.floats(min_value=1080.0, max_value=1120.0),
    st.floats(min_value=0.08, max_value=0.6),
    oi_lists,
    oi_lists,
    st.integers(min_value=2, max_value=1000),
)
def test_gex_is_linear_in_oi(
    f: float, sigma: float, oi_c: list[int], oi_p: list[int], k: int
) -> None:
    base = _priced(f, sigma, oi_c, oi_p)
    scaled = _priced(f, sigma, [k * x for x in oi_c], [k * x for x in oi_p])
    tol = 1e-12 * k * _gross(base)
    for r1, rk in zip(strike_gex(base).rows, strike_gex(scaled).rows, strict=True):
        assert math.isclose(rk.gex_call, k * r1.gex_call, rel_tol=1e-12, abs_tol=tol)
        assert math.isclose(rk.gex_put, k * r1.gex_put, rel_tol=1e-12, abs_tol=tol)
    n1, nk = net_gex([base]), net_gex([scaled])
    assert (nk.quality, nk.excluded_oi_ratio) == (n1.quality, n1.excluded_oi_ratio)
    # 행사가별 표의 품질·제외 비율은 같은 만기의 순GEX 와 같다(§2.1·§2.2)
    t1 = strike_gex(base)
    assert (t1.quality, t1.excluded_oi_ratio) == (n1.quality, n1.excluded_oi_ratio)
    # 포함 OI 가 0 인데 제외(§1.2 하한 등) OI 만 있으면 둘 다 None·invalid
    assert (n1.value is None) == (nk.value is None)
    if n1.value is not None and nk.value is not None:
        assert math.isclose(nk.value, k * n1.value, rel_tol=1e-12, abs_tol=tol)


@given(forwards, st.lists(gammas, min_size=len(STRIKES), max_size=len(STRIKES)), oi_lists, oi_lists)
def test_swapping_call_put_oi_flips_strike_gex(
    f: float, g: list[float], oi_c: list[int], oi_p: list[int]
) -> None:
    before, after = _made(f, g, oi_c, oi_p), _made(f, g, oi_p, oi_c)
    for r, s in zip(strike_gex(before).rows, strike_gex(after).rows, strict=True):
        assert s.strike == r.strike
        assert s.gex == -r.gex
        assert (s.gex_call, s.gex_put) == (-r.gex_put, -r.gex_call)
    nb, na = net_gex([before]).value, net_gex([after]).value
    assert nb is not None and na is not None
    assert na == -nb


@given(forwards, st.lists(gammas, min_size=len(STRIKES), max_size=len(STRIKES)), oi_lists, oi_lists)
def test_net_is_sum_of_strikes(f: float, g: list[float], oi_c: list[int], oi_p: list[int]) -> None:
    ev = _made(f, g, oi_c, oi_p)
    net = net_gex([ev]).value
    assert net is not None
    rows = math.fsum(r.gex for r in strike_gex(ev).rows)
    assert math.isclose(net, rows, rel_tol=1e-12, abs_tol=1e-12 * _gross(ev))


@given(forwards, st.lists(gammas, min_size=len(STRIKES), max_size=len(STRIKES)), oi_lists)
def test_call_side_non_negative_put_side_non_positive(
    f: float, g: list[float], oi: list[int]
) -> None:
    for r in strike_gex(_made(f, g, oi, oi)).rows:
        assert r.gex_call >= 0 >= r.gex_put
