"""스크리닝 6기준(docs/metrics.md §3, docs/p3_design.md §4.2 스크리닝 표).

| mode | 정렬 키(내림차순) | 거르는 조건 |
|---|---|---|
| value | 기간 거래대금 | — |
| foreign | 기간 외국인 순매수 | 값이 있는 종목 |
| inst | 기간 기관 순매수 | 값이 있는 종목 |
| both | 기간 외국인 + 기관 | 둘 다 > 0 |
| streak | max(외국인 연속, 기관 연속), 같으면 기간 외국인 + 기관 | streak_min(3) 일 이상 |
| spike | 급증 배수(오늘 ÷ 직전 19영업일 평균 — 기간 무관) | spike_min(1.5) 배 이상 |

공통 필터(이 순서로 세어 `n_excluded` 에 남긴다)
- 시장(all = 코스피 + 코스닥 — 코넥스 제외), 종류(보통주 `common` 와 우선주 `pref` 는 따로 — 스팩·
  리츠·ETF·ETN 은 스크리너 밖). 기간 끝 날에 행이 있는 종목만 후보(`n_total`).
- 관리종목·거래정지·정리매매(`EXCLUDE_FLAGS`)는 기본 제외(`flagged`). 상태를 모르는 종목은 뺄 수
  없어 결과에 남기고 수를 `status_unknown` 으로 함께 낸다(R22).
- 일평균 거래대금(기간 합 ÷ 실제 거래된 영업일 수) < 하한이면 제외(`below_min`).
- invalid 행은 기간 집계에서 빠지고 그 행 수를 `invalid` 로 센다(종목은 남은 행으로 계산).

급증 배수: 오늘(기간 끝 날) 거래대금 ÷ 직전 19영업일 중 거래된 날의 평균. 직전 거래일이
`spike_min_prior_days`(10) 미만이면 계산하지 않는다(None — metrics §1, 신규 상장 등).
결과 행에 board 라벨(신고가 여부)·급증 배수를 함께 싣는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import (
    MAIN_MARKETS,
    Ledger,
    LedgerRow,
    WindowAgg,
    source_label,
    worst_quality,
)
from kbj.engines.flows.streak import streak

if TYPE_CHECKING:
    from kbj.config.markets import ScreenCfg

__all__ = [
    "MODES",
    "PERIODS",
    "Market",
    "Mode",
    "ScreenResult",
    "ScreenRow",
    "ScreenTuning",
    "ShareClass",
    "screen",
    "spike_multiple",
]

Mode = Literal["value", "foreign", "inst", "both", "streak", "spike"]
Market = Literal["all", "KOSPI", "KOSDAQ"]
ShareClass = Literal["common", "pref"]
MODES: Final[tuple[Mode, ...]] = ("value", "foreign", "inst", "both", "streak", "spike")
PERIODS: Final[tuple[int, ...]] = (1, 5, 20)
SPIKE_PRIOR_DAYS: Final = 19  # 급증 배수 분모 = 직전 19영업일(metrics §1)
STREAK_LOOKBACK: Final = 60  # 연속일을 셀 때 거슬러 보는 최대 영업일(화면 표시 한도)


@dataclass(frozen=True)
class ScreenTuning:
    """`config/markets.yaml` `screen.*`(하한 금액은 쿼리 값이라 따로 받는다)."""

    spike_min: float = 1.5
    spike_min_prior_days: int = 10
    streak_min: int = 3

    @classmethod
    def from_config(cls, cfg: ScreenCfg) -> ScreenTuning:
        return cls(
            spike_min=cfg.spike_min,
            spike_min_prior_days=cfg.spike_min_prior_days,
            streak_min=cfg.streak_min,
        )


@dataclass(frozen=True)
class ScreenRow:
    """결과 한 줄(docs/p3_design.md §5.2 `ScreenRow`). 금액은 원."""

    rank: int
    code: str
    name: str | None
    market: str | None
    sector: str | None
    kind: str | None
    chg_pct: float | None
    turnover_sum: int | None
    turnover_avg: float | None
    turnover_rate_pct: float | None
    foreign: int | None
    inst: int | None
    other_corp: int | None
    indiv: int | None
    streak_foreign: int
    streak_inst: int
    spike_mult: float | None
    newhigh_label: str | None
    flags: tuple[str, ...] | None
    quality: Quality
    source: str


@dataclass(frozen=True)
class ScreenResult:
    mode: Mode
    market: Market
    period: int
    share_class: ShareClass
    min_avg_turnover: int
    as_of: date | None  # 기간 끝 날(원장 마지막 영업일 또는 end)
    n_total: int
    n_excluded: Mapping[str, int]  # flagged·invalid·below_min·status_unknown
    rows: tuple[ScreenRow, ...]
    quality: Quality | None
    source: str
    notes: tuple[str, ...] = field(default_factory=tuple[str, ...])


def spike_multiple(ledger: Ledger, code: str, end: date, *, min_prior_days: int) -> float | None:
    """오늘 거래대금 ÷ 직전 19영업일(거래된 날) 평균. 직전 거래일 < min_prior_days 면 None."""
    today = ledger.get(code, end)
    if today is None or not today.usable or not today.traded or today.turnover is None:
        return None
    days = ledger.days_upto(end, SPIKE_PRIOR_DAYS + 1)
    if not days or days[-1] != end:
        return None
    prior = [ledger.get(code, d) for d in days[:-1]]
    vals = [
        r.turnover
        for r in prior
        if r is not None and r.usable and r.traded and r.turnover is not None
    ]
    if len(vals) < min_prior_days:
        return None
    avg = sum(vals) / len(vals)
    if avg <= 0:
        return None
    return today.turnover / avg


def _class_ok(row: LedgerRow, share_class: ShareClass) -> bool:
    return row.kind == share_class


def _market_ok(row: LedgerRow, market: Market) -> bool:
    if market == "all":
        return row.market in MAIN_MARKETS
    return row.market == market


def _pair(agg: WindowAgg) -> int | None:
    f, i = agg.sums_by_investor["foreign"], agg.sums_by_investor["inst"]
    if f is None or i is None:
        return None
    return f + i


def screen(
    ledger: Ledger,
    *,
    mode: Mode,
    market: Market = "all",
    period: int = 5,
    min_avg_turnover: int,
    include_flagged: bool = False,
    newhigh: Mapping[str, str] | None = None,
    limit: int = 100,
    end: date | None = None,
    share_class: ShareClass = "common",
    tuning: ScreenTuning | None = None,
    sectors: Mapping[str, str] | None = None,
) -> ScreenResult:
    """원장에서 스크리닝 결과. 원장은 `checks.apply_checks` 를 거친 것을 넘긴다(아니면 notes 에)."""
    if mode not in MODES:
        raise ValueError(f"mode 는 {MODES} 중 하나: {mode!r}")
    if period not in PERIODS:
        raise ValueError(f"period 는 {PERIODS} 중 하나: {period!r}")
    if market not in ("all", "KOSPI", "KOSDAQ"):
        raise ValueError(f"market 은 all·KOSPI·KOSDAQ 중 하나: {market!r}")
    if share_class not in ("common", "pref"):
        raise ValueError(f"share_class 는 common·pref: {share_class!r}")
    if limit < 1:
        raise ValueError("limit 은 1 이상")
    if min_avg_turnover < 0:
        raise ValueError("min_avg_turnover 는 0 이상")
    tune = tuning or ScreenTuning()
    asof = end if end is not None else ledger.last_day
    notes: list[str] = list(ledger.notes)
    if ledger.checks is None:
        notes.append("검산 ①② 를 적용하지 않은 원장")
    excluded = {"flagged": 0, "invalid": 0, "below_min": 0, "status_unknown": 0}
    if asof is None:
        return ScreenResult(
            mode, market, period, share_class, min_avg_turnover, None, 0, excluded, (), None,
            "", tuple(notes),
        )  # fmt: skip

    scored: list[tuple[tuple[float, ...], ScreenRow, tuple[str, ...]]] = []
    n_total = 0
    for code in ledger.codes():
        today = ledger.get(code, asof)
        if today is None or not _market_ok(today, market) or not _class_ok(today, share_class):
            continue
        n_total += 1
        agg = ledger.window(code, asof, period)
        excluded["invalid"] += agg.n_invalid
        flagged = today.flagged
        if flagged is None:
            excluded["status_unknown"] += 1
        elif flagged and not include_flagged:
            excluded["flagged"] += 1
            continue
        if agg.avg_turnover is None or agg.avg_turnover < min_avg_turnover:
            excluded["below_min"] += 1
            continue
        desc = ledger.rows_desc(code, asof, STREAK_LOOKBACK)
        s_f, s_i = streak(desc, "foreign"), streak(desc, "inst")
        spike = spike_multiple(ledger, code, asof, min_prior_days=tune.spike_min_prior_days)
        sums = agg.sums_by_investor
        pair = _pair(agg)
        key: tuple[float, ...] | None
        if mode == "value":
            key = None if agg.sum_turnover is None else (agg.sum_turnover,)
        elif mode == "foreign":
            key = None if sums["foreign"] is None else (sums["foreign"],)
        elif mode == "inst":
            key = None if sums["inst"] is None else (sums["inst"],)
        elif mode == "both":
            ok = (sums["foreign"] or 0) > 0 and (sums["inst"] or 0) > 0
            key = (pair,) if ok and pair is not None else None
        elif mode == "streak":
            best = max(s_f, s_i)
            key = (best, pair if pair is not None else float("-inf"))
            if best < tune.streak_min:
                key = None
        else:  # spike
            key = (spike,) if spike is not None and spike >= tune.spike_min else None
        if key is None:
            continue
        rate = (
            agg.avg_turnover / agg.last_mktcap * 100
            if agg.last_mktcap is not None and agg.last_mktcap > 0
            else None
        )
        row = ScreenRow(
            rank=0,
            code=code,
            name=today.name,
            market=today.market,
            sector=(sectors or {}).get(code),
            kind=today.kind,
            chg_pct=today.chg_pct if today.usable else None,
            turnover_sum=agg.sum_turnover,
            turnover_avg=agg.avg_turnover,
            turnover_rate_pct=rate,
            foreign=sums["foreign"],
            inst=sums["inst"],
            other_corp=sums["other_corp"],
            indiv=sums["indiv"],
            streak_foreign=s_f,
            streak_inst=s_i,
            spike_mult=spike,
            newhigh_label=(newhigh or {}).get(code),
            flags=today.flags,
            quality=agg.quality or Quality.INVALID,
            source=source_label(agg.sources),
        )
        scored.append((key, row, agg.sources))

    scored.sort(key=lambda kr: (tuple(-k for k in kr[0]), kr[1].code))
    top = scored[:limit]
    out = tuple(replace(r, rank=i) for i, (_, r, _) in enumerate(top, start=1))
    if excluded["status_unknown"]:
        notes.append(
            f"상태(관리·정지·정리매매)를 모르는 종목 {excluded['status_unknown']}개는 빼지 못했다"
        )
    if excluded["invalid"]:
        notes.append(f"invalid {excluded['invalid']}행을 집계에서 뺐다")
    return ScreenResult(
        mode=mode,
        market=market,
        period=period,
        share_class=share_class,
        min_avg_turnover=min_avg_turnover,
        as_of=asof,
        n_total=n_total,
        n_excluded=excluded,
        rows=out,
        quality=worst_quality(r.quality for r in out),
        source=source_label(s for _, _, srcs in top for s in srcs),
        notes=tuple(dict.fromkeys(notes)),
    )
