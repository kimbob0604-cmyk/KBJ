"""일간 수익률 피어슨 상관(docs/p8_design.md §4.7, ADR 0033).

- 창: 기준 달력(거래일 오름차순)의 마지막 `window`(60·120) 거래일.
- 수익률 r_t = close_t ÷ close_{t−1} − 1 은 달력에서 **바로 앞 거래일**에 종가가 있을 때만 만든다
  (빈 날을 건너뛰어 잇지 않는다 — 정지 뒤 첫날이 여러 날 수익률이 되지 않게).
- 쌍마다 둘 다 수익률이 있는 날만 쓴다. 겹치는 날 < `MIN_OVERLAP`(40) 이거나 분산이 0 이면 None.
- 대각선은 자기 수익률이 40 일 이상이고 분산이 있으면 1.0, 아니면 None.
- 종목 수 상한 `MAX_CODES`(50) — 넘는 쪽은 부르는 쪽이 잘라서 넘긴다(넘기면 ValueError).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import pairwise
from typing import Final

__all__ = ["MAX_CODES", "MIN_OVERLAP", "WINDOWS", "corr_matrix", "daily_returns", "pearson"]

MIN_OVERLAP: Final = 40
MAX_CODES: Final = 50
WINDOWS: Final = (60, 120)


def daily_returns(
    closes: Mapping[date, float | None], calendar: Sequence[date]
) -> dict[date, float]:
    """달력(오름차순)에서 앞 거래일·오늘 종가가 둘 다 양수일 때만 수익률."""
    out: dict[date, float] = {}
    for prev, cur in pairwise(calendar):
        a, b = closes.get(prev), closes.get(cur)
        if a is None or b is None or a <= 0 or b <= 0:
            continue
        out[cur] = b / a - 1.0
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, r))


def corr_matrix(
    closes: Mapping[str, Mapping[date, float | None]],
    calendar: Sequence[date],
    codes: Sequence[str],
    *,
    window: int,
    min_overlap: int = MIN_OVERLAP,
) -> list[list[float | None]]:
    """codes 순서의 정방 행렬. 창은 달력 마지막 window 거래일(첫 날 수익률을 위해 한 날 더 본다)."""
    if len(codes) > MAX_CODES:
        raise ValueError(f"상관 종목은 {MAX_CODES}개까지")
    if window < 2:
        raise ValueError("window 는 2 이상")
    cal = list(calendar)[-(window + 1) :]
    rets = [daily_returns(closes.get(c, {}), cal) for c in codes]
    n = len(codes)
    out: list[list[float | None]] = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            common = sorted(set(rets[i]) & set(rets[j]))
            if len(common) < min_overlap:
                continue
            xs = [rets[i][d] for d in common]
            ys = [rets[j][d] for d in common]
            r = pearson(xs, ys)
            if r is None:
                continue
            if i == j:
                r = 1.0
            out[i][j] = out[j][i] = r
    return out
