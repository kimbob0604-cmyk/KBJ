"""배수 밴드 — 5년 일별 PIT 배수의 선형 백분위·현재 백분위(docs/p4_design.md §4.8, docs/metrics.md
§11).

승격 원본: SD `valuation_calculator.calculate_per_band`:122(분기말 점 8개 이상, 0 < PER < 200)·
`percentile_rank`:334(조각 선형) — 둘 다 **바꿨다**(ADR 0016 §3): 표본은 매 거래일(최근 `band_days`
= 1,240거래일), 백분위는 선형 보간(numpy 'linear'), 현재 백분위는 경험적 순위.

- 창 = 그 종목의 일별 행 중 as_of 이하 마지막 `window_days` 개(행이 있는 거래일 — 상장 전·시세 없는
  날은 행이 없다). 유효 값 = None 아님·거름값(`valuation.caps` — 열린 구간) 안·품질 invalid 아님.
  `n_excluded` = 창의 행 − 유효 값.
- 유효 일 ≥ `min_days`(250) → p10·p25·p50·p75·p90·최소·최대, 현재(as_of 행의 값 — 거름값 밖이어도
  그대로)와 현재 백분위(유효 표본 대비 경험적 순위).
- 유효 일 < `min_days` → 백분위 None + 사유 하나(`NA_PRIORITY`): 창의 행이 `min_days` 보다 적으면
  `short_history`(신규상장), as_of 행의 그 칸 사유(통화·재무 없음·스팩·금융업·층·적자 …)와 견줘 앞의
  것. 그래도 없으면 창에서 가장 많은 사유(같으면 순위), 그마저 없으면(거름값 밖뿐) `short_history`.
- 품질: 유효 값을 낸 행 중 가장 나쁜 것.
- `band_lines`(화면 — 저장하지 않는다): 밴드가(t) = 배수_p × 그날 주당 기준값(= 종가 ÷ 그날 배수).

I/O 를 하지 않는다(계약 ⑨).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.core.fin_rows import BandRow, NaReason, ValuationDay
from kbj.core.quality import Quality
from kbj.engines.valuation.multiples import (
    BAND_METRICS_T,
    NA_PRIORITY,
    Metric,
    cells_of,
    first_reason,
    worst,
)
from kbj.engines.valuation.percentile import BAND_QS, percentiles, rank_pct

__all__ = ["BAND_SOURCE", "BandLine", "band", "band_lines", "bands"]

BAND_SOURCE: Final = "krx+DART"
_RANK_OF: Final = {r: i for i, r in enumerate(NA_PRIORITY)}


def _window(series: Sequence[ValuationDay], as_of: date, window_days: int) -> list[ValuationDay]:
    rows = sorted((r for r in series if r.trade_date <= as_of), key=lambda r: r.trade_date)
    return rows[-window_days:] if window_days > 0 else []


def _reason_of(r: ValuationDay, metric: Metric) -> NaReason | None:
    return cells_of(r)[metric].na_reason


def band(
    series: Sequence[ValuationDay],
    metric: Metric,
    *,
    code: str,
    as_of: date,
    cap: tuple[float, float],
    window_days: int = 1240,
    min_days: int = 250,
) -> BandRow:
    """한 지표의 밴드 행. `series` 는 그 종목의 일별 행(순서 무관)."""
    if min_days < 1 or window_days < min_days:
        raise ValueError("1 ≤ min_days ≤ window_days")
    rows = _window(series, as_of, window_days)
    lo, hi = cap
    valid: list[float] = []
    used_q: list[Quality] = []
    for r in rows:
        v = getattr(r, metric)
        if v is None or r.quality is Quality.INVALID or not lo < v < hi:
            continue
        valid.append(float(v))
        used_q.append(r.quality)
    cur_row = rows[-1] if rows and rows[-1].trade_date == as_of else None
    current: float | None = None
    if cur_row is not None:
        cv = getattr(cur_row, metric)
        current = None if cv is None else float(cv)
    window_from = rows[0].trade_date if rows else None
    n_days, n_excluded = len(valid), len(rows) - len(valid)
    if n_days >= min_days:
        p10, p25, p50, p75, p90 = percentiles(valid, BAND_QS)
        return BandRow(
            code=code,
            as_of=as_of,
            metric=metric,
            p10=p10,
            p25=p25,
            p50=p50,
            p75=p75,
            p90=p90,
            min=min(valid),
            max=max(valid),
            n_days=n_days,
            n_excluded=n_excluded,
            current=current,
            current_rank_pct=None if current is None else rank_pct(valid, current),
            window_from=window_from,
            source=BAND_SOURCE,
            quality=worst(*used_q),
        )
    cands: list[NaReason | None] = [None if cur_row is None else _reason_of(cur_row, metric)]
    if len(rows) < min_days:
        cands.append(NaReason.SHORT_HISTORY)
    reason = first_reason(cands)
    if reason is None:
        counts = Counter(x for r in rows if (x := _reason_of(r, metric)) is not None)
        if counts:
            reason = min(counts, key=lambda x: (-counts[x], _RANK_OF.get(x, len(NA_PRIORITY))))
        else:
            reason = NaReason.SHORT_HISTORY
    return BandRow(
        code=code,
        as_of=as_of,
        metric=metric,
        p10=None,
        p25=None,
        p50=None,
        p75=None,
        p90=None,
        min=None,
        max=None,
        n_days=n_days,
        n_excluded=n_excluded,
        current=current,
        current_rank_pct=None,
        window_from=window_from,
        na_reason=reason,
        source=BAND_SOURCE,
        quality=worst(*used_q) if used_q else Quality.OK,
    )


def bands(
    series: Sequence[ValuationDay],
    *,
    code: str,
    as_of: date,
    caps: Mapping[Metric, tuple[float, float]],
    window_days: int = 1240,
    min_days: int = 250,
) -> list[BandRow]:
    """네 지표(per·pbr·psr·ev_ebitda)의 밴드 행."""
    return [
        band(
            series,
            m,
            code=code,
            as_of=as_of,
            cap=caps[m],
            window_days=window_days,
            min_days=min_days,
        )
        for m in BAND_METRICS_T
    ]


@dataclass(frozen=True)
class BandLine:
    """밴드 차트 한 날(화면) — 그날 종가와 백분위별 밴드가(원)."""

    day: date
    price: float
    lines: Mapping[str, float]  # 'p10'…'p90' → 원


def band_lines(series: Sequence[ValuationDay], b: BandRow) -> list[BandLine]:
    """가격 공간 밴드(저장하지 않는다). 그날 배수가 없거나 ≤ 0 인 날은 뺀다(주당 기준값을
    모른다)."""
    ps = {"p10": b.p10, "p25": b.p25, "p50": b.p50, "p75": b.p75, "p90": b.p90}
    if any(v is None for v in ps.values()):
        return []
    out: list[BandLine] = []
    for r in _window(series, b.as_of, len(series)):
        if b.window_from is not None and r.trade_date < b.window_from:
            continue
        m = getattr(r, b.metric)
        if r.price is None or m is None or m <= 0:
            continue
        per_share = r.price / m
        out.append(
            BandLine(
                r.trade_date, r.price, {k: v * per_share for k, v in ps.items() if v is not None}
            )
        )
    return out
