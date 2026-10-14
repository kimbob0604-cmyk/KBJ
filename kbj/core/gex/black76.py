"""Black-76 가격 (PLAN §2.4, docs/metrics.md §0).

기초자산은 같은 만기 합성선물 F(§1.3). 가격·F·K 단위 pt, T 는 년, σ 는 연율 소수(0.25 = 25%).
계산은 vollib(Let's Be Rational) — 타입 없는 라이브러리라 이 모듈이 경계에서 입력을 검증하고
결과를 float 로 되돌린다. 할인율 r 기본 0: PLAN "배당·금리 추정 불필요"의 해석 [확인 필요].
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal, cast

import vollib.black as _vb  # pyright: ignore[reportMissingTypeStubs]

# vollib 은 타입이 없다 — 쓰는 함수에만 시그니처를 붙인다: black(flag, F, K, t, r, sigma)
_vl_black = cast(
    Callable[[str, float, float, float, float, float], float],
    _vb.black,  # pyright: ignore[reportUnknownMemberType]
)

Flag = Literal["c", "p"]
FLAGS: tuple[Flag, ...] = ("c", "p")


def check_inputs(
    flag: str, F: float, K: float, T: float, r: float, sigma: float | None = None
) -> None:
    """flag 는 'c'|'p', F·K·T(·σ)는 유한한 양수, r 은 유한. 어기면 ValueError.

    vollib 은 잘못된 입력에도 조용히 0·nan 을 내거나(T=0, σ=0), 'p' 가 아니면 콜로 본다.
    """
    if flag not in FLAGS:
        raise ValueError(f"flag 는 'c' 또는 'p' 여야 한다: {flag!r}")
    positives = [("F", F), ("K", K), ("T", T)]
    if sigma is not None:
        positives.append(("σ", sigma))
    for name, value in positives:
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"{name} 는 유한한 양수여야 한다: {value!r}")
    if not math.isfinite(r):
        raise ValueError(f"r 은 유한해야 한다: {r!r}")


def price(flag: Flag, F: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """할인된 Black-76 옵션 가격(pt)."""
    check_inputs(flag, F, K, T, r, sigma)
    return float(_vl_black(flag, F, K, T, r, sigma))


def intrinsic(flag: Flag, F: float, K: float, T: float, r: float = 0.0) -> float:
    """무차익 하한 = 할인 내재가치 `e^(−rT)·max(±(F − K), 0)`."""
    check_inputs(flag, F, K, T, r)
    payoff = F - K if flag == "c" else K - F
    return math.exp(-r * T) * max(payoff, 0.0)


def upper_bound(flag: Flag, F: float, K: float, T: float, r: float = 0.0) -> float:
    """무차익 상한(σ → ∞ 극한) = 콜 `e^(−rT)·F`, 풋 `e^(−rT)·K`. 이 값 이상이면 IV 가 없다."""
    check_inputs(flag, F, K, T, r)
    return math.exp(-r * T) * (F if flag == "c" else K)
