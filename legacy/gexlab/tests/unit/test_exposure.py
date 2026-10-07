"""core.metrics.exposure — Vanna·Charm 익스포저, GEX P/C 비율 (docs/metrics.md §4.1~§4.3).

- 단위: 한 종목 VEX = Vanna·OI·m·F·0.01(원 / IV 1%p), CEX = Charm·OI·m·F(원 / 달력 1일)
- 부호(§2.3 과 같다 — 딜러 콜 롱 +, 풋 숏 −): 콜만 있으면 종목 Vanna·Charm 부호 그대로, 풋만
  있으면 반대. 같은 행사가·같은 OI 의 콜·풋은 상쇄(Vanna·Charm 이 콜·풋 같다)
- VEX 는 DEX 의 σ 미분 × 0.01, CEX 는 −DEX 의 T 미분 ÷ 365 (범위 합 수준의 수치 미분 대조)
- Charm 익스포저는 만기가 가까울수록 커진다
- 예외·품질은 §2.2 순GEX 와 같다: 빈 범위 null·ok, F 없는 만기 invalid, 제외 OI 비율 > 10%
  estimated, OI 전부 제외 null·invalid. GEX P/C 는 Σ GEX_call = 0 이면 null
"""

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from core.calendar import KST, expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import (
    OPTION_MULTIPLIER,
    CallPut,
    ExpiryEval,
    OptionEval,
    OptionQuote,
    dex,
    net_gex,
    option_gex,
)
from core.greeks import charm, greeks, vanna
from core.iv import IvResult
from core.metrics.exposure import (
    cex,
    gex_put_call_ratio,
    option_cex,
    option_vex,
    vex,
)
from core.preprocess import Quality

D = Decimal
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
EXP = date(2026, 10, 8)  # 월물 202610
F0 = 1100.0
M = OPTION_MULTIPLIER

Leg = tuple[float, CallPut, int, float]  # 행사가, 콜/풋, OI, σ


def _forward(F: float | None, quality: Quality = "ok") -> ForwardResult:
    if F is None:
        return ForwardResult(None, "invalid", None, (), None, ("no_parity_strikes",))
    reasons = () if quality == "ok" else ("few_strikes",)
    return ForwardResult(F, quality, D(1100), (D(1100),), None, reasons)


def make(
    F: float | None,
    legs: Sequence[Leg],
    *,
    expiry: str = "202610",
    ed: date = EXP,
    now: datetime = NOW,
    excluded: Sequence[tuple[float, CallPut, int]] = (),
    fq: Quality = "ok",
) -> ExpiryEval:
    """만기 하나 — legs 는 GEX 에 드는 종목(자체 σ, Black-76 그릭스), excluded 는 가격 없는 종목."""
    t = time_to_expiry(now, expiry_at(ed))
    opts: list[OptionEval] = []
    for k, cp, oi, sigma in legs:
        q = OptionQuote(expiry=expiry, expiry_date=ed, strike=D(repr(k)), cp=cp, last=D(1), oi=oi)
        if F is None:
            opts.append(OptionEval(q, q.price(), None, None, True, "no_forward"))
            continue
        g = greeks("c" if cp == "C" else "p", F, k, t, sigma)
        opts.append(OptionEval(q, q.price(), IvResult(sigma, "ok", "model", None), g, False, None))
    for k, cp, oi in excluded:
        q = OptionQuote(expiry=expiry, expiry_date=ed, strike=D(repr(k)), cp=cp, oi=oi)
        opts.append(OptionEval(q, q.price(), None, None, True, "no_price"))
    opts.sort(key=lambda o: (o.quote.strike, o.quote.cp))
    return ExpiryEval(expiry, ed, _forward(F, fq), t, tuple(opts))


# ── 단위·부호 ──


def test_single_option_units() -> None:
    ev = make(F0, [(1100.0, "C", 1, 0.2)])
    t = ev.T
    x = vex([ev])
    assert x.value == pytest.approx(vanna(F0, 1100.0, t, 0.2) * M * F0 * 0.01, rel=1e-12)
    y = cex([ev])
    assert y.value == pytest.approx(charm(F0, 1100.0, t, 0.2) * M * F0, rel=1e-12)
    assert option_vex("C", 1.0, 1, F0) == M * F0 * 0.01
    assert option_cex("P", 1.0, 2, F0) == -2 * M * F0
    with pytest.raises(ValueError, match="cp"):
        option_vex("X", 1.0, 1, F0)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("k", [1080.0, 1100.0, 1112.5, 1130.0])
def test_sign_follows_the_dealer_side(k: float) -> None:
    """콜만 있으면 종목 Vanna·Charm 부호 그대로(딜러 콜 롱), 풋만 있으면 반대(딜러 풋 숏)."""
    calls = make(F0, [(k, "C", 300, 0.2)])
    puts = make(F0, [(k, "P", 300, 0.2)])
    va, ch = vanna(F0, k, calls.T, 0.2), charm(F0, k, calls.T, 0.2)
    for fn, g in ((vex, va), (cex, ch)):
        c, p = fn([calls]).value, fn([puts]).value
        assert c is not None and p is not None
        assert math.copysign(1, c) == math.copysign(1, g) and c == pytest.approx(-p, rel=1e-12)


def test_otm_wings_have_the_textbook_signs() -> None:
    """OTM 콜(K > F)만: VEX > 0·CEX < 0. OTM 풋(K < F)만(딜러 숏): VEX > 0·CEX < 0 — 같은 부호."""
    otm_calls = make(F0, [(1130.0, "C", 500, 0.2), (1150.0, "C", 500, 0.2)])
    otm_puts = make(F0, [(1070.0, "P", 500, 0.2), (1050.0, "P", 500, 0.2)])
    for ev in (otm_calls, otm_puts):
        v, c = vex([ev]).value, cex([ev]).value
        assert v is not None and v > 0
        assert c is not None and c < 0


def test_call_and_put_with_the_same_oi_cancel() -> None:
    legs: list[Leg] = [(k, cp, 700, 0.22) for k in (1080.0, 1100.0, 1125.0) for cp in ("C", "P")]
    ev = make(F0, legs)
    for fn in (vex, cex):
        v = fn([ev]).value
        assert v is not None and v == pytest.approx(0.0, abs=1e-6 * M)


# ── 수치 미분(범위 합) ──


def _bump(ev: ExpiryEval, *, dsigma: float = 0.0, dt: float = 0.0) -> ExpiryEval:
    """모든 종목 σ 를 dsigma, T 를 dt 만큼 옮겨 그릭스를 다시 계산한 만기."""
    F = ev.forward.F
    assert F is not None
    t = ev.T + dt
    opts: list[OptionEval] = []
    for o in ev.options:
        if o.greeks is None or o.iv is None or o.iv.sigma is None:
            opts.append(o)
            continue
        s = o.iv.sigma + dsigma
        g = greeks("c" if o.quote.cp == "C" else "p", F, float(o.quote.strike), t, s)
        opts.append(replace(o, iv=IvResult(s, "ok", "model", None), greeks=g))
    return replace(ev, T=t, options=tuple(opts))


def test_vex_is_the_sigma_derivative_of_dex_and_cex_the_time_derivative() -> None:
    legs: list[Leg] = [
        (1050.0, "P", 1200, 0.28),
        (1080.0, "P", 900, 0.24),
        (1100.0, "C", 800, 0.21),
        (1100.0, "P", 400, 0.21),
        (1125.0, "C", 1500, 0.19),
        (1160.0, "C", 600, 0.18),
    ]
    ev = make(F0, legs)
    h = 1e-5
    up, down = dex([_bump(ev, dsigma=h)]).value, dex([_bump(ev, dsigma=-h)]).value
    assert up is not None and down is not None
    assert vex([ev]).value == pytest.approx((up - down) / (2 * h) * 0.01, rel=1e-6)
    ht = ev.T * 1e-5
    up, down = dex([_bump(ev, dt=ht)]).value, dex([_bump(ev, dt=-ht)]).value
    assert up is not None and down is not None
    assert cex([ev]).value == pytest.approx(-(up - down) / (2 * ht) / 365, rel=1e-6)


def test_charm_exposure_grows_toward_expiry() -> None:
    legs: list[Leg] = [(1105.0, "C", 1000, 0.2), (1095.0, "P", 800, 0.2)]
    sizes = []
    for when in (
        datetime(2026, 10, 1, 9, 0, tzinfo=KST),
        datetime(2026, 10, 5, 9, 0, tzinfo=KST),
        datetime(2026, 10, 7, 15, 0, tzinfo=KST),
        datetime(2026, 10, 8, 14, 0, tzinfo=KST),
    ):
        v = cex([make(F0, legs, now=when)]).value
        assert v is not None
        sizes.append(abs(v))
    assert sizes == sorted(sizes) and sizes[-1] > 10 * sizes[0]


def test_charm_uses_the_t_floor_as_given() -> None:
    """15:20 뒤(T 하한 5분)에도 유한 — T 는 만기의 자체 T 그대로."""
    late = datetime(2026, 10, 8, 15, 30, tzinfo=KST)
    ev = make(F0, [(1100.5, "C", 10, 0.2)], now=late)
    assert ev.T == pytest.approx(5 / (365 * 24 * 60))
    v = cex([ev]).value
    assert v is not None and math.isfinite(v)
    assert v == pytest.approx(option_cex("C", charm(F0, 1100.5, ev.T, 0.2), 10, F0))


# ── 범위·예외·품질 (§2.2 와 같다) ──


def test_scopes_follow_net_gex() -> None:
    near = make(1100.0, [(1100.0, "C", 100, 0.2)], expiry="261001", ed=date(2026, 10, 1))
    month = make(1101.0, [(1100.0, "P", 200, 0.2)])
    for fn in (vex, cex):
        allv = fn([near, month]).value
        n = fn([near, month], "nearest").value
        z = fn([near, month], "0dte", date(2026, 10, 1)).value
        m = fn([month]).value
        assert n is not None and m is not None and allv == pytest.approx(n + m, rel=1e-12)
        assert z == n
        empty = fn([near, month], "0dte", date(2026, 10, 2))
        assert (empty.value, empty.quality, empty.expiries) == (None, "ok", ())


def test_quality_and_nulls_are_those_of_net_gex() -> None:
    few = make(F0, [(1100.0, "C", 100, 0.2)], excluded=[(1150.0, "C", 50)])  # 제외 33%
    none_f = make(None, [(1100.0, "C", 100, 0.2)], expiry="202611", ed=date(2026, 11, 12))
    all_out = make(F0, [], excluded=[(1100.0, "C", 100)])
    est = make(F0, [(1100.0, "C", 100, 0.2)], fq="estimated")
    for fn in (vex, cex):
        for evs in ([few], [few, none_f], [all_out], [est], [none_f]):
            got, base = fn(evs), net_gex(evs)
            assert (got.quality, got.excluded_oi_ratio, got.expiries) == (
                base.quality,
                base.excluded_oi_ratio,
                base.expiries,
            )
            assert (got.value is None) == (base.value is None)
    assert vex([few]).quality == "estimated"
    assert vex([few, none_f]).quality == "invalid"
    assert (vex([all_out]).value, vex([all_out]).quality) == (None, "invalid")


# ── GEX P/C (§4.3) ──


def test_gex_put_call_ratio_value() -> None:
    ev = make(F0, [(1100.0, "C", 100, 0.2), (1100.0, "P", 300, 0.2), (1120.0, "C", 100, 0.2)])
    r = gex_put_call_ratio([ev])
    calls = math.fsum(
        option_gex(o.quote.cp, o.greeks.gamma, o.quote.oi, F0)
        for o in ev.options
        if o.greeks is not None and o.quote.cp == "C"
    )
    puts = math.fsum(
        option_gex(o.quote.cp, o.greeks.gamma, o.quote.oi, F0)
        for o in ev.options
        if o.greeks is not None and o.quote.cp == "P"
    )
    assert puts < 0 < calls
    assert r.value == pytest.approx(abs(puts) / calls, rel=1e-12)
    assert (r.call_gex, r.put_gex, r.quality) == (calls, puts, "ok")


def test_gex_put_call_ratio_nulls() -> None:
    puts_only = gex_put_call_ratio([make(F0, [(1100.0, "P", 300, 0.2)])])
    assert puts_only.value is None and puts_only.call_gex == 0.0 and puts_only.quality == "ok"
    zero_oi = gex_put_call_ratio([make(F0, [(1100.0, "C", 0, 0.2), (1100.0, "P", 5, 0.2)])])
    assert zero_oi.value is None  # Σ GEX_call = 0
    calls_only = gex_put_call_ratio([make(F0, [(1100.0, "C", 300, 0.2)])])
    assert calls_only.value == 0.0
    empty = gex_put_call_ratio([], "0dte", date(2026, 10, 2))
    assert (empty.value, empty.quality, empty.call_gex) == (None, "ok", None)
    all_out = gex_put_call_ratio([make(F0, [], excluded=[(1100.0, "C", 100)])])
    assert (all_out.value, all_out.quality) == (None, "invalid")
    few = make(F0, [(1100.0, "C", 100, 0.2)], excluded=[(1150.0, "C", 50)])
    assert gex_put_call_ratio([few]).quality == "estimated"
