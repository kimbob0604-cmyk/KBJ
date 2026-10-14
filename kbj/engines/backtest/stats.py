"""통계(metrics §30.4) — 거래 통계(board `backtest.stats`:236 승격)와 표본 기술 통계(알림 후 성과).

- 승 = 수익 > 0, 패 = 수익 ≤ 0(board 그대로). PF = 이익 합 ÷ |손실 합|(손실 0 이면 None).
- 분위수는 선형 보간(`quantile`). 중앙값은 짝수면 가운데 둘의 평균(board 와 같다).
- MDD = 거래 순서대로 복리 누적한 곡선의 최고점 대비 최저 낙폭(0 이하, 비율). 연환산 = (1 + 총수익)^
  (252 / 보유 칸 합) − 1(보유 칸 합 0 이면 None).
- 표본이 없으면 값은 None(n = 0). 반올림하지 않는다(표시 쪽이 한다). 수익을 약속하는 문구 없음.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "Describe",
    "TradeResult",
    "TradeStats",
    "describe",
    "max_drawdown",
    "quantile",
    "trade_stats",
]

TRADING_DAYS: Final = 252


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """선형 보간 분위수(0 ≤ q ≤ 1). 입력은 오름차순."""
    if not sorted_vals:
        raise ValueError("빈 표본")
    if not 0 <= q <= 1:
        raise ValueError("q 는 0~1")
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _median(r: Sequence[float]) -> float:
    n = len(r)
    return r[n // 2] if n % 2 else (r[n // 2 - 1] + r[n // 2]) / 2


@dataclass(frozen=True)
class Describe:
    """표본 기술 통계(알림 후 성과 — 규칙 × 기간 칸)."""

    n: int
    win_rate: float | None  # % (값 > 0 비율)
    mean: float | None
    median: float | None
    q25: float | None
    q75: float | None
    small: bool  # n < min_samples — '표본 적음'


def describe(values: Sequence[float], *, min_samples: int = 20) -> Describe:
    n = len(values)
    if n == 0:
        return Describe(0, None, None, None, None, None, True)
    r = sorted(values)
    return Describe(
        n=n,
        win_rate=sum(1 for x in r if x > 0) / n * 100,
        mean=sum(r) / n,
        median=_median(r),
        q25=quantile(r, 0.25),
        q75=quantile(r, 0.75),
        small=n < min_samples,
    )


@dataclass(frozen=True)
class TradeResult:
    """거래 한 건(BKT 가 만든다). ret_pct = 비용 반영 순수익(%)."""

    ret_pct: float
    bars: int
    reason: str
    hit_target: bool = False


def max_drawdown(rets_pct: Sequence[float]) -> float:
    """거래 순서대로 복리 누적한 곡선의 최고점 대비 최저 낙폭(비율, 0 이하)."""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for x in rets_pct:
        eq *= 1 + x / 100
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return mdd


@dataclass(frozen=True)
class TradeStats:
    n: int
    win_rate: float | None
    mean: float | None
    median: float | None
    p10: float | None
    p90: float | None
    profit_factor: float | None
    avg_bars: float | None
    target_hit: float | None
    mdd: float | None
    total: float | None  # 복리 총수익(비율)
    annualized: float | None
    reasons: Mapping[str, int] = field(default_factory=dict[str, int])


def trade_stats(trades: Sequence[TradeResult]) -> TradeStats:
    """거래 통계 — board `backtest.stats` 의 칸(n·승률·평균·중앙·p10·p90·PF·평균 보유·목표 도달) +
    MDD·
    총수익·연환산. p10·p90 은 board 와 같은 최근접 순위(r[int(n·0.1)], r[min(n−1, int(n·0.9))])."""
    if not trades:
        return TradeStats(0, None, None, None, None, None, None, None, None, None, None, None, {})
    r = sorted(t.ret_pct for t in trades)
    n = len(r)
    wins = [x for x in r if x > 0]
    loss = [x for x in r if x <= 0]
    gl = -sum(loss)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    seq = [t.ret_pct for t in trades]
    total = 1.0
    for x in seq:
        total *= 1 + x / 100
    total -= 1
    bars = sum(t.bars for t in trades)
    ann = None if bars <= 0 or total <= -1 else (1 + total) ** (TRADING_DAYS / bars) - 1
    return TradeStats(
        n=n,
        win_rate=len(wins) / n * 100,
        mean=sum(r) / n,
        median=_median(r),
        p10=r[int(n * 0.1)],
        p90=r[min(n - 1, int(n * 0.9))],
        profit_factor=None if gl == 0 else sum(wins) / gl,
        avg_bars=bars / n,
        target_hit=sum(1 for t in trades if t.hit_target) / n * 100,
        mdd=max_drawdown(seq),
        total=total,
        annualized=ann,
        reasons=reasons,
    )
