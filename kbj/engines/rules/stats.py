"""알림 후 성과 통계 — 규칙 × 기간 칸·분포(P8 묶음 BKT, docs/p8_design.md §4.5, metrics §30.4).

순수 계산(읽을 때 — 표 없음). 실제 알림 성과(`/api/rules/stats`)와 백테스트 '기대' 칸(`backtest`)이
같은 함수로 만든다(페이지 12 '백테스트 대 실제' 가 같은 칸으로 나란히).

- 입력 = 성과 행(`OutcomeRow` 또는 같은 칸을 가진 것 — ret·excess·mfe·mae 는 비율). 출력은 **%**
  (×100 — 승률과 같은 단위, 표시 쪽이 반올림).
- n = ret 이 있는 행 수, excluded = ret 이 없는 행 수(정지·상폐·분할 의심 — 뺀 수를 같이 보인다).
- 승률 = ret > 0 비율, 평균·중앙·25/75%(선형 보간 — `kbj.engines.backtest.stats.describe`),
  평균 초과(excess 가 있는 행만), MFE·MAE 중앙. n < min_samples 면 `small`('표본 적음').
- 분포 = 같은 너비 구간(기본 2%p, 구간 수 상한 — 넘으면 너비를 넓힌다). 수익을 약속하는 문구 없음.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from kbj.engines.backtest.stats import describe

__all__ = ["Bin", "OutcomeLike", "StatsCellCalc", "histogram", "stats_cells"]

PCT: Final = 100.0
DEFAULT_STEP: Final = 2.0
MAX_BINS: Final = 20


class OutcomeLike(Protocol):
    @property
    def horizon(self) -> int: ...
    @property
    def ret(self) -> float | None: ...
    @property
    def excess(self) -> float | None: ...
    @property
    def mfe(self) -> float | None: ...
    @property
    def mae(self) -> float | None: ...


@dataclass(frozen=True)
class StatsCellCalc:
    horizon: int
    n: int
    excluded: int
    win_rate: float | None
    mean: float | None
    median: float | None
    q25: float | None
    q75: float | None
    excess_mean: float | None
    mfe_median: float | None
    mae_median: float | None
    small: bool


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    n: int


def _median(vals: Sequence[float]) -> float | None:
    return describe(vals).median if vals else None


def stats_cells(
    rows: Iterable[OutcomeLike], horizons: Sequence[int], *, min_samples: int = 20
) -> list[StatsCellCalc]:
    """기간마다 한 칸(기간 순). 그 기간 행이 없으면 n = 0·값 None."""
    by_h: dict[int, list[OutcomeLike]] = {h: [] for h in horizons}
    for r in rows:
        if r.horizon in by_h:
            by_h[r.horizon].append(r)
    out: list[StatsCellCalc] = []
    for h in sorted(by_h):
        rs = by_h[h]
        rets = [r.ret * PCT for r in rs if r.ret is not None]
        d = describe(rets, min_samples=min_samples)
        exc = [r.excess * PCT for r in rs if r.ret is not None and r.excess is not None]
        mfe = [r.mfe * PCT for r in rs if r.ret is not None and r.mfe is not None]
        mae = [r.mae * PCT for r in rs if r.ret is not None and r.mae is not None]
        out.append(
            StatsCellCalc(
                horizon=h,
                n=d.n,
                excluded=len(rs) - d.n,
                win_rate=d.win_rate,
                mean=d.mean,
                median=d.median,
                q25=d.q25,
                q75=d.q75,
                excess_mean=sum(exc) / len(exc) if exc else None,
                mfe_median=_median(mfe),
                mae_median=_median(mae),
                small=d.small,
            )
        )
    return out


def histogram(
    values_pct: Sequence[float], *, step: float = DEFAULT_STEP, max_bins: int = MAX_BINS
) -> list[Bin]:
    """같은 너비 구간 분포. 구간 [lo, hi) — 마지막 구간은 hi 포함. 값이 없으면 빈 목록."""
    if step <= 0 or max_bins < 1:
        raise ValueError("step > 0, max_bins ≥ 1")
    vals = [v for v in values_pct if math.isfinite(v)]
    if not vals:
        return []
    lo = math.floor(min(vals) / step) * step
    hi = math.ceil(max(vals) / step) * step
    if hi <= lo:
        hi = lo + step
    width = step
    while (hi - lo) / width > max_bins:
        width += step
    k = max(1, math.ceil((hi - lo) / width - 1e-9))
    counts = [0] * k
    for v in vals:
        j = min(k - 1, int((v - lo) // width))
        counts[j] += 1
    return [Bin(lo + j * width, lo + (j + 1) * width, counts[j]) for j in range(k)]
