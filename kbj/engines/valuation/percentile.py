"""백분위 — 선형 보간 백분위·경험적 순위(docs/p4_design.md §4.8, docs/metrics.md §11).

numpy 를 쓰지 않는 순수 함수다(D-P4-20 — 엔진 순수성). 정의는 numpy `percentile(…, method='linear')`
(기본값)와 같다 — 시험이 시험 안에서만 numpy 로 대조한다.

- `percentile(xs, q)`: q 는 **백분율**(0~100 — numpy 와 같은 단위). x 를 오름차순으로, 0 부터 센
  순서통계량 x_(k), h = (n − 1)·q/100, 값 = x_⌊h⌋ + (h − ⌊h⌋)(x_⌈h⌉ − x_⌊h⌋).
- `rank_pct(xs, x)`: 경험적 순위 100 × #{x_i ≤ x} ÷ n. SD
  `valuation_calculator.percentile_rank`:334 (조각 선형 근사)를 바꾼다 — legacy 와 값이 다르다(ADR
  0016 §3 차이 표).

빈 표본·NaN·inf 는 받지 않는다(`ValueError` — 지어낸 값으로 메우지 않는다).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Final

__all__ = ["BAND_QS", "percentile", "percentiles", "rank_pct"]

BAND_QS: Final[tuple[float, ...]] = (10.0, 25.0, 50.0, 75.0, 90.0)  # 밴드 다섯 줄(p10…p90)


def _clean(xs: Iterable[float]) -> list[float]:
    out: list[float] = []
    for x in xs:
        v = float(x)
        if not math.isfinite(v):
            raise ValueError(f"백분위 표본은 유한한 수만: {x!r}")
        out.append(v)
    if not out:
        raise ValueError("빈 표본의 백분위는 없다")
    return out


def _at(sorted_xs: Sequence[float], q: float) -> float:
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"q 는 0~100(백분율): {q!r}")
    n = len(sorted_xs)
    h = (n - 1) * q / 100.0
    lo = math.floor(h)
    hi = math.ceil(h)
    a, b = sorted_xs[lo], sorted_xs[hi]
    return a + (h - lo) * (b - a)


def percentile(xs: Iterable[float], q: float) -> float:
    """선형 보간 백분위(q = 0~100). numpy `percentile(xs, q)` 와 같은 값."""
    return _at(sorted(_clean(xs)), q)


def percentiles(xs: Iterable[float], qs: Sequence[float] = BAND_QS) -> tuple[float, ...]:
    """여러 백분위를 한 번 정렬로."""
    s = sorted(_clean(xs))
    return tuple(_at(s, q) for q in qs)


def rank_pct(xs: Iterable[float], x: float) -> float:
    """경험적 순위(%) = 100 × #{x_i ≤ x} ÷ n — 0~100."""
    vals = _clean(xs)
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"순위를 매길 값은 유한한 수만: {x!r}")
    return 100.0 * sum(1 for e in vals if e <= v) / len(vals)
