"""cis 신호 우위(순수 계산) — 매매 규칙 없이 '신호 뒤 n일 동안 어떻게 움직였나'만 본다.

손절·청산 규칙을 바꿔 가며 돌리기 전에 신호 자체가 아무 날 아무 종목보다 나은지 먼저 확인한다.
- 진입: open = 신호일 다음 날 시가 · confirm = 다음 날 고가가 신호일 고가 이상일 때
  max(시가, 신호일 고가) · close = 신호일 종가(장 마감 동시호가에 산다고 본다).
- n일 수익률: 진입가 → 신호일 뒤 n번째 거래일 종가(1일 = 다음 날 종가 — 진입 방식이 달라도 같은
  날 종가와 비교한다). 비용 빼기 전.
- 최고·최저: 신호일 뒤 가장 긴 n일 안 고가 최고·저가 최저(진입가 대비).
- 기준선: 같은 기간 대상 종목 전부·모든 날(다음 날 시가 → n일 뒤 종가) — 신호가 이걸 넘어야 우위다.
n일 뒤 봉이 없으면(기간 끝·정지) 그 n 에서만 뺀다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Final, Literal

from kbj.engines.screen.backtest import OhlcSeries, Signal

__all__ = ["HORIZONS", "EdgeRow", "Fwd", "baseline", "edge_row", "forward_of"]

HORIZONS: Final = (1, 5, 10, 20)


@dataclass(frozen=True)
class Fwd:
    """한 신호의 앞날(진입가 대비 %)."""

    code: str
    date: date
    vol_ratio: float
    rets: tuple[float | None, ...]  # HORIZONS 순
    mfe: float | None  # 가장 긴 n일 안 최고(고가)
    mae: float | None  # 가장 긴 n일 안 최저(저가)


@dataclass(frozen=True)
class EdgeRow:
    label: str
    n: int
    mean: tuple[float | None, ...]  # HORIZONS 순 평균 %
    up_pct: tuple[float | None, ...]  # HORIZONS 순 오른 비율 %
    median_last: float | None  # 가장 긴 n일 중앙값 %
    mfe: float | None  # 평균
    mae: float | None  # 평균


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


def forward_of(
    series: Mapping[str, OhlcSeries],
    signals: Mapping[date, Sequence[Signal]],
    *,
    entry: Literal["open", "confirm", "close"] = "open",
    horizons: Sequence[int] = HORIZONS,
) -> list[Fwd]:
    """신호마다 앞날. 다음 날 봉이 없거나(정지·기간 끝) 확인 실패면 뺀다."""
    out: list[Fwd] = []
    idx: dict[str, dict[date, int]] = {}
    far = max(horizons)
    for d in sorted(signals):
        for sig in signals[d]:
            s = series[sig.code]
            pos = idx.setdefault(sig.code, {x: i for i, x in enumerate(s.dates)})
            i = pos.get(d)
            if i is None or i + 1 >= len(s.dates):
                continue
            e = i + 1
            px = s.close[i] if entry == "close" else s.open[e]
            if entry == "confirm":
                if s.high[e] < sig.high:
                    continue
                px = max(px, sig.high)
            rets = tuple(_pct(s.close[i + h], px) if i + h < len(s.dates) else None
                         for h in horizons)  # fmt: skip
            end = min(e + far, len(s.dates))
            out.append(
                Fwd(sig.code, d, sig.vol_ratio, rets, _pct(max(s.high[e:end]), px),
                    _pct(min(s.low[e:end]), px))
            )  # fmt: skip
    return out


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def edge_row(label: str, fwd: Sequence[Fwd], keep: Callable[[Fwd], bool] | None = None) -> EdgeRow:
    rows = [f for f in fwd if keep is None or keep(f)]
    k = len(HORIZONS)
    cols = [[r for f in rows if (r := f.rets[j]) is not None] for j in range(k)]
    return EdgeRow(
        label=label, n=len(rows),
        mean=tuple(_mean(c) for c in cols),
        up_pct=tuple(sum(x > 0 for x in c) / len(c) * 100 if c else None for c in cols),
        median_last=median(cols[-1]) if cols[-1] else None,
        mfe=_mean([f.mfe for f in rows if f.mfe is not None]),
        mae=_mean([f.mae for f in rows if f.mae is not None]),
    )  # fmt: skip


def baseline(
    series: Mapping[str, OhlcSeries], codes: Sequence[str], *, start: date,
    horizons: Sequence[int] = HORIZONS,
) -> EdgeRow:  # fmt: skip
    """대상 종목 전부·start 이후 모든 날을 신호로 본 것(다음 날 시가 → n일 뒤 종가)."""
    k = len(horizons)
    sums, ups, cnt = [0.0] * k, [0] * k, [0] * k
    n = 0
    for code in codes:
        s = series.get(code)
        if s is None:
            continue
        o, c = s.open, s.close
        last = len(s.dates)
        for i in range(last - 1):
            if s.dates[i] < start:
                continue
            e = i + 1
            px = o[e]
            n += 1
            for j, h in enumerate(horizons):
                x = e + h - 1
                if x < last:
                    r = (c[x] / px - 1) * 100
                    sums[j] += r
                    ups[j] += r > 0
                    cnt[j] += 1
    return EdgeRow(
        label="기준: 전 종목·모든 날", n=n,
        mean=tuple(sums[j] / cnt[j] if cnt[j] else None for j in range(k)),
        up_pct=tuple(ups[j] / cnt[j] * 100 if cnt[j] else None for j in range(k)),
        median_last=None, mfe=None, mae=None,
    )  # fmt: skip
