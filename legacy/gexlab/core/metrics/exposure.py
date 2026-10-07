"""익스포저 확장 — Vanna·Charm 익스포저, GEX P/C 비율 (docs/metrics.md §4.1~§4.3, PLAN §5.3).

부호는 §2.3 DEX 와 같다(naive 딜러 — 콜 롱 `+`, 풋 숏 `−`, CLAUDE.md "Vanna·Charm 동일 부호"):
- `VEX = Σ OI_c·Vanna_c·m·F·0.01 − Σ OI_p·Vanna_p·m·F·0.01` — 원 / IV 1%p (표시 억원)
- `CEX = Σ OI_c·Charm_c·m·F − Σ OI_p·Charm_p·m·F` — 원 / 달력 1일 (표시 억원)
- `GEX P/C = |Σ GEX_put| ÷ Σ GEX_call` — 무차원. Σ GEX_call = 0 이면 null
m 은 `core.specs` 옵션 승수(250,000원), F 는 그 만기의 합성선물(§1.3). Vanna·Charm 은
`core.greeks.vanna`·`charm`(Black-76, r = 0 — 콜·풋 같은 값) 에 종목의 자체 σ(§1.5 — 자체 역산 또는
KIS 폴백을 자체 T 로 옮긴 값)·만기의 자체 T(§1.4, 하한 5분 그대로)를 넣는다. KIS 그릭스는 쓰지
않는다(2026-09-28 사용자 결정).

대상 종목·예외·품질은 §2.2 순GEX 와 같다(§4.1 "예외·품질: §2.2 와 같다", §4.3 "품질: §2.2"):
GEX 에 든 종목(자체 그릭스가 있는 종목)만 더하고, 범위 품질·제외 OI 비율은 `core.gex.net_gex` 의
것을 그대로 쓴다 — 제외 OI 비율 > 10% estimated, F 없는 만기 invalid, 범위에 OI 가 있는데 전부
제외면 값 null·invalid, 빈 범위는 null·ok(해당 없음).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from core.gex import (
    MAX_EXCLUDED_OI_RATIO,
    OPTION_MULTIPLIER,
    CallPut,
    ExpiryEval,
    Exposure,
    OptionEval,
    Scope,
    net_gex,
    option_gex,
    select_scope,
)
from core.greeks import charm, vanna
from core.preprocess import Quality

VANNA_SCALE = 0.01  # §4.1 — σ 1(100%p) 당 → IV 1%p 당


def _sign(cp: CallPut) -> float:
    if cp == "C":
        return 1.0
    if cp == "P":
        return -1.0
    raise ValueError(f"cp 는 'C' 또는 'P': {cp!r}")


def option_vex(
    cp: CallPut, vanna_value: float, oi: int, F: float, multiplier: int = OPTION_MULTIPLIER
) -> float:
    """§4.1 한 종목의 딜러 VEX(원 / IV 1%p) = ±OI·Vanna·m·F·0.01. 콜 +, 풋 −."""
    return _sign(cp) * vanna_value * oi * multiplier * F * VANNA_SCALE


def option_cex(
    cp: CallPut, charm_value: float, oi: int, F: float, multiplier: int = OPTION_MULTIPLIER
) -> float:
    """§4.2 한 종목의 딜러 CEX(원 / 달력 1일) = ±OI·Charm·m·F. 콜 +, 풋 −."""
    return _sign(cp) * charm_value * oi * multiplier * F


def _sigma(o: OptionEval) -> float:
    """GEX 에 든 종목의 자체 σ — 그릭스가 있으면 늘 있다(없으면 결함)."""
    if o.iv is None or o.iv.sigma is None:
        raise ValueError(f"그릭스가 있는데 IV 가 없다: {o.quote.strike} {o.quote.cp}")
    return o.iv.sigma


Term = Callable[[OptionEval, ExpiryEval, float], float]  # (종목, 만기, F) → 원


def _scope_sum(
    evals: Iterable[ExpiryEval],
    scope: Scope,
    trade_date: date | None,
    term: Term,
    max_excluded_ratio: float,
) -> Exposure:
    """범위 합 — 값은 GEX 에 든 종목의 term 합, 품질·제외 비율·null 은 §2.2 순GEX 그대로."""
    chosen = select_scope(evals, scope, trade_date)
    base = net_gex(chosen, max_excluded_ratio=max_excluded_ratio)
    if base.value is None:
        return base
    value = math.fsum(
        term(o, e, e.forward.F)
        for e in chosen
        if e.forward.F is not None
        for o in e.options
        if o.greeks is not None
    )
    return Exposure(value, base.quality, base.excluded_oi_ratio, base.expiries)


def vex(
    evals: Iterable[ExpiryEval],
    scope: Scope = "all",
    trade_date: date | None = None,
    *,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Exposure:
    """§4.1 딜러 Vanna 익스포저(원 / IV 1%p) — 범위(all·nearest·0dte)의 GEX 에 든 종목 합."""

    def term(o: OptionEval, e: ExpiryEval, F: float) -> float:
        v = vanna(F, float(o.quote.strike), e.T, _sigma(o))
        return option_vex(o.quote.cp, v, o.quote.oi, F)

    return _scope_sum(evals, scope, trade_date, term, max_excluded_ratio)


def cex(
    evals: Iterable[ExpiryEval],
    scope: Scope = "all",
    trade_date: date | None = None,
    *,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Exposure:
    """§4.2 딜러 Charm 익스포저(원 / 달력 1일) — T 는 만기의 자체 T(§1.4 하한 5분 그대로)."""

    def term(o: OptionEval, e: ExpiryEval, F: float) -> float:
        c = charm(F, float(o.quote.strike), e.T, _sigma(o))
        return option_cex(o.quote.cp, c, o.quote.oi, F)

    return _scope_sum(evals, scope, trade_date, term, max_excluded_ratio)


@dataclass(frozen=True, slots=True)
class GexRatio:
    """§4.3 결과. value = |Σ GEX_put| ÷ Σ GEX_call(무차원) — Σ GEX_call = 0 이거나 범위 순GEX 가
    null(빈 범위·F 없는 만기뿐·OI 전부 제외)이면 None. call_gex·put_gex 는 합(원/1%, 순GEX 가 null
    이면 None). quality·excluded_oi_ratio·expiries 는 §2.2 순GEX 것."""

    value: float | None
    quality: Quality
    excluded_oi_ratio: float
    expiries: tuple[str, ...]
    call_gex: float | None
    put_gex: float | None


def gex_put_call_ratio(
    evals: Iterable[ExpiryEval],
    scope: Scope = "all",
    trade_date: date | None = None,
    *,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> GexRatio:
    """§4.3 GEX P/C 비율 `|Σ GEX_put| ÷ Σ GEX_call` (범위: all·nearest·0dte)."""
    chosen = select_scope(evals, scope, trade_date)
    base = net_gex(chosen, max_excluded_ratio=max_excluded_ratio)
    if base.value is None:
        return GexRatio(None, base.quality, base.excluded_oi_ratio, base.expiries, None, None)
    sums: dict[CallPut, list[float]] = {"C": [], "P": []}
    for e in chosen:
        F = e.forward.F
        if F is None:
            continue
        for o in e.options:
            if o.greeks is not None:
                sums[o.quote.cp].append(option_gex(o.quote.cp, o.greeks.gamma, o.quote.oi, F))
    calls, puts = math.fsum(sums["C"]), math.fsum(sums["P"])
    value = None if calls == 0 else abs(puts) / calls
    return GexRatio(value, base.quality, base.excluded_oi_ratio, base.expiries, calls, puts)
