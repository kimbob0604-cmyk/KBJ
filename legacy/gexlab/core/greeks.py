"""Black-76 해석적 그릭스 (docs/metrics.md §0·§1.6).

vollib.black.greeks.analytical 을 쓴다. 단위는 metrics §0 그대로이며 vollib 의 스케일과 같다:
- delta: 무차원. 풋은 음수
- gamma: 1/pt — 기초(F) 1pt 당 델타 변화. 콜·풋 같다
- vega: IV 1%p(σ 0.01) 당 가격 변화(pt) — vollib 이 교과서 식에 0.01 을 곱해 둔다
- theta: 달력 1일(1/365년) 경과당 가격 변화(pt) — vollib 이 연 단위 식을 365 로 나눠 둔다

IV 가 invalid 이거나 §1.2 로 제외된 종목(`IvResult.below_min_premium`)이면 부르지 않는다
(§1.6) — KIS 폴백으로 sigma 가 있어도 그렇다. 그릭스 품질은 IV 품질을 그대로 따른다.

Vanna·Charm(§4.1·§4.2 익스포저 입력)은 Black-76(r = 0) 해석식을 직접 계산한다(vollib 에 없다).
콜·풋이 같다(Δ_c − Δ_p = 1 이 σ·T 와 무관):
- vanna = ∂Δ/∂σ = −φ(d1)·d2/σ — σ 1(= 100%p) 당 델타 변화. VEX 가 0.01 을 곱해 IV 1%p 당으로
- charm = −∂Δ/∂T 를 달력 1일로 = φ(d1)·d2 / (2T) / 365 — 달력 1일 경과당 델타 변화
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import vollib.black.greeks.analytical as _vga  # pyright: ignore[reportMissingTypeStubs]

from core.black76 import Flag, check_inputs

DAYS_PER_YEAR = 365  # charm 을 달력 1일로 (§4.2 — 세타와 같은 기준)

# vollib 은 타입이 없다 — 쓰는 함수에만 시그니처를 붙인다: f(flag, F, K, t, r, sigma)
_Fn = Callable[[str, float, float, float, float, float], float]
_vl_delta = cast(_Fn, _vga.delta)  # pyright: ignore[reportUnknownMemberType]
_vl_gamma = cast(_Fn, _vga.gamma)  # pyright: ignore[reportUnknownMemberType]
_vl_vega = cast(_Fn, _vga.vega)  # pyright: ignore[reportUnknownMemberType]
_vl_theta = cast(_Fn, _vga.theta)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True, slots=True)
class Greeks:
    delta: float  # 무차원
    gamma: float  # 1/pt
    vega: float  # pt / IV 1%p
    theta: float  # pt / 1일(365일 기준)


def greeks(flag: Flag, F: float, K: float, T: float, sigma: float, r: float = 0.0) -> Greeks:
    """한 종목의 delta·gamma·vega·theta. 입력이 양수·유한이 아니면 ValueError."""
    check_inputs(flag, F, K, T, r, sigma)
    return Greeks(
        delta=float(_vl_delta(flag, F, K, T, r, sigma)),
        gamma=float(_vl_gamma(flag, F, K, T, r, sigma)),
        vega=float(_vl_vega(flag, F, K, T, r, sigma)),
        theta=float(_vl_theta(flag, F, K, T, r, sigma)),
    )


def gamma(F: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """감마만(1/pt). 콜·풋이 같아 flag 를 받지 않는다 — Flip 격자 재계산(§3.4)용."""
    check_inputs("c", F, K, T, r, sigma)
    return float(_vl_gamma("c", F, K, T, r, sigma))


def _d1_d2(F: float, K: float, T: float, sigma: float) -> tuple[float, float]:
    """Black-76(r = 0) d1·d2. 입력은 `check_inputs` 로 먼저 본다."""
    check_inputs("c", F, K, T, 0.0, sigma)
    s = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + s * s / 2) / s
    return d1, d1 - s


def _pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def vanna(F: float, K: float, T: float, sigma: float) -> float:
    """§4.1 Vanna = ∂Δ/∂σ = −φ(d1)·d2/σ (r = 0, 콜·풋 같다) — σ 1(= 100%p) 당 델타 변화.

    입력이 양수·유한이 아니면 ValueError.
    """
    d1, d2 = _d1_d2(F, K, T, sigma)
    return -_pdf(d1) * d2 / sigma


def charm(F: float, K: float, T: float, sigma: float) -> float:
    """§4.2 Charm = −∂Δ/∂T 를 달력 1일로 = φ(d1)·d2 / (2T) / 365 (r = 0, 콜·풋 같다).

    T 는 §1.4 자체 잔존기간(하한 5분 그대로 — 호출 쪽이 넘긴 값). 입력이 양수·유한이 아니면
    ValueError.
    """
    d1, d2 = _d1_d2(F, K, T, sigma)
    return _pdf(d1) * d2 / (2 * T) / DAYS_PER_YEAR
