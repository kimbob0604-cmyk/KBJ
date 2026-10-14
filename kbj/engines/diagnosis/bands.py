"""백분위·구간·상태 점수(docs/metrics.md §D0·§D1).

- 백분위 p: 창(그날 포함 최근 `window` 관측) 안에서 x 의 평균 순위 r(1부터, 동점은 평균)을
  `(r − 1) ÷ (n − 1) × 100` 으로 0~100 에 편다(최솟값 0·최댓값 100, n = 1 이면 50). 소수 둘째
  반올림.
- 다섯 칸(`bands` = [20, 40, 60, 80]): p < 20 극단 낮음 · < 40 낮음 · < 60 보통 · < 80 높음 · ≥ 80
  극단 높음.
- σ 세 칸(§D1 — 신용스프레드): z = (x − 창 평균) ÷ 창 모표준편차. z ≤ −k 좁음 · z ≥ +k 넓음 · 그
  사이
  보통(표준편차 0 이면 보통).
- 상태 점수(§D0): 위험 선호 방향 `high` 면 높음·극단 높음 +1, 낮음·극단 낮음 −1, 보통 0. `low` 면
  반대.
  σ 칸은 좁음 = 낮음 쪽, 넓음 = 높음 쪽으로 같은 규칙. `none` 은 점수 0(플래그만).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from kbj.core.p7_rows import DiagState
from kbj.core.quality import Quality

__all__ = [
    "Obs",
    "RiskOn",
    "pct_rank",
    "pct_state",
    "quantize",
    "sigma_state",
    "state_score",
    "worst",
]

RiskOn = Literal["high", "low", "none"]
_Q_ORDER: Final = {Quality.OK: 0, Quality.STALE: 1, Quality.ESTIMATED: 2, Quality.INVALID: 3}
_HUNDRED: Final = Decimal(100)
_HIGH: Final[frozenset[str]] = frozenset({"high", "extreme_high", "wide"})
_LOW: Final[frozenset[str]] = frozenset({"low", "extreme_low", "narrow"})


@dataclass(frozen=True)
class Obs:
    """관측 한 점 — 그날(day)의 값. value None 은 결측(0 으로 채우지 않는다)."""

    day: date
    value: Decimal | None
    quality: Quality
    source: str


def worst(qs: Iterable[Quality]) -> Quality | None:
    """가장 나쁜 품질(ok < stale < estimated < invalid — `kbj.engines.flows.ledger` 와 같은
    순서)."""
    out: Quality | None = None
    for q in qs:
        if out is None or _Q_ORDER[q] > _Q_ORDER[out]:
            out = q
    return out


def quantize(x: Decimal, places: str = "0.01") -> Decimal:
    return x.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def pct_rank(window: Sequence[Decimal], x: Decimal) -> Decimal:
    """창(x 를 포함해야 한다) 안 x 의 백분위(0~100, 동점 평균 순위)."""
    n = len(window)
    if n == 0:
        raise ValueError("빈 창")
    less = sum(1 for v in window if v < x)
    equal = sum(1 for v in window if v == x)
    if equal == 0:
        raise ValueError("창에 x 가 없다(그날 포함)")
    if n == 1:
        return Decimal(50)
    rank = Decimal(less) + Decimal(equal + 1) / 2  # 1부터
    return quantize((rank - 1) / Decimal(n - 1) * _HUNDRED)


def pct_state(p: Decimal, bands: Sequence[int]) -> DiagState:
    b1, b2, b3, b4 = bands
    if p < b1:
        return "extreme_low"
    if p < b2:
        return "low"
    if p < b3:
        return "normal"
    if p < b4:
        return "high"
    return "extreme_high"


def sigma_state(window: Sequence[Decimal], x: Decimal, k: float) -> tuple[DiagState, Decimal]:
    """(구간, z 소수 둘째). 창은 x 를 포함한다."""
    n = len(window)
    if n == 0:
        raise ValueError("빈 창")
    mean = sum(window, Decimal(0)) / n
    var = sum(((v - mean) ** 2 for v in window), Decimal(0)) / n
    if var == 0:
        return "normal", Decimal(0)
    z = (x - mean) / var.sqrt()
    kk = Decimal(str(k))
    if z <= -kk:
        return "narrow", quantize(z)
    if z >= kk:
        return "wide", quantize(z)
    return "normal", quantize(z)


def state_score(state: DiagState | None, risk_on: RiskOn) -> int | None:
    """상태 점수 −1·0·+1(§D0). 구간이 없으면 None(성분 빠짐)."""
    if state is None:
        return None
    if risk_on == "none" or state == "normal":
        return 0
    up = 1 if risk_on == "high" else -1
    if state in _HIGH:
        return up
    if state in _LOW:
        return -up
    raise ValueError(f"모르는 구간: {state!r}")
