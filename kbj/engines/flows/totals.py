"""시장 투자자 합계·종목 수급 상세(docs/p3_design.md §5.2 `InvestorTotals`·`StockFlowDetail`).

- 시장 합계: `prv_flows.market_investor_daily` 행(code = 시장 코드)에서 시장마다 원장과 같은 규칙
  (`ledger.investor_values`)으로 4구분·7구분을 만들고 더한다. 한 시장이라도 값이 없는 구분은 합을
  None 으로 둔다(일부만 더하면 합이 작게 나온다 — 지어내지 않는다). 검산 ①② 잔차를 함께 낸다.
- 종목 상세: 원장의 최근 n 영업일 일별 값·누적·검산 수.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from kbj.core.quality import Quality
from kbj.core.rows import INST7, Investor, InvestorDay, pick_best, row_rank
from kbj.engines.flows.checks import check1, check2
from kbj.engines.flows.ledger import (
    WHO,
    InvestorValues,
    Ledger,
    investor_values,
    source_label,
    sum_known,
    worst_quality,
)

__all__ = [
    "TOTAL_KEYS",
    "DayFlow",
    "InvestorTotals",
    "StockFlowDetail",
    "market_investor_totals",
    "stock_detail",
]

# 응답 키(§5.2 InvestorTotals.by_investor) ← 원장 열 이름
TOTAL_KEYS: Mapping[str, str] = {
    "foreign": "foreign",
    "inst": "institution",
    "other_corp": "other_corp",
    "indiv": "individual",
}


@dataclass(frozen=True)
class InvestorTotals:
    date: date
    by_investor: Mapping[str, int | None]  # foreign·institution·other_corp·individual(원)
    inst7: Mapping[str, int] | None
    check1_residual: int | None
    check2_residual: int | None
    by_market: Mapping[str, Mapping[str, int | None]]
    quality: Quality
    source: str
    notes: tuple[str, ...] = field(default_factory=tuple[str, ...])


def _values_dict(v: InvestorValues) -> dict[str, int | None]:
    return {TOTAL_KEYS[w]: getattr(v, w) for w in WHO}


def market_investor_totals(
    rows: Iterable[InvestorDay],
    day: date | None = None,
    *,
    markets: Collection[str] | None = None,
) -> InvestorTotals | None:
    """시장별 투자자 행 → 그날 합계. day 가 없으면 행이 있는 가장 최근 날. 행이 없으면 None.

    `markets` 를 주면 그 시장이 모두 있어야 합을 낸다(빠진 시장은 notes 에, 합은 None).
    """
    best = pick_best(
        rows,
        lambda r: (r.code, r.date, r.investor),
        lambda r: row_rank(r.source, r.quality, r.venue),
    )
    if not best:
        return None
    use = day if day is not None else max(k[1] for k in best)
    per: dict[str, dict[Investor, InvestorDay]] = {}
    for (code, d, who), r in best.items():
        if d == use and (markets is None or code in markets):
            per.setdefault(code, {})[who] = r
    if not per:
        return None
    notes: list[str] = []
    missing = sorted(set(markets or ()) - set(per))
    if missing:
        notes.append(f"시장 행 없음: {', '.join(missing)} — 합계를 내지 않는다")
    vals = {code: investor_values(inv) for code, inv in sorted(per.items())}
    invalid = any(v.invalid for v in vals.values())
    for code, v in vals.items():
        notes.extend(f"{code}: {n}" for n in v.notes)
        if v.invalid:
            notes.append(f"{code}: invalid 행 — 그 구분은 합계에서 뺀다")

    def total(get: str) -> int | None:
        if missing:
            return None
        parts = [getattr(v, get) for v in vals.values()]
        return None if any(p is None for p in parts) else sum_known(parts)

    by = {TOTAL_KEYS[w]: total(w) for w in WHO}
    inst7: dict[str, int] | None = None
    if not missing and all(v.inst7 is not None for v in vals.values()):
        inst7 = {
            i.value: sum(v.inst7[i] for v in vals.values() if v.inst7 is not None) for i in INST7
        }
    c1 = sum_known(by.values()) if all(x is not None for x in by.values()) else None
    c2 = (
        None
        if inst7 is None or by["institution"] is None
        else sum(inst7.values()) - by["institution"]
    )
    if c1 is None:
        notes.append("검산 ① 불가: 4구분 중 빠진 구분이 있다")
    if c2 is None:
        notes.append("검산 ② 불가: 기관 7구분 미제공")
    qs = [q for v in vals.values() for q in v.qualities]
    srcs = [s for v in vals.values() for s in v.sources]
    quality = Quality.INVALID if invalid and not qs else (worst_quality(qs) or Quality.INVALID)
    return InvestorTotals(
        date=use,
        by_investor=by,
        inst7=inst7,
        check1_residual=c1,
        check2_residual=c2,
        by_market={code: _values_dict(v) for code, v in vals.items()},
        quality=quality,
        source=source_label(srcs),
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class DayFlow:
    date: date
    turnover: int | None
    foreign: int | None
    inst: int | None
    other_corp: int | None
    indiv: int | None
    check1: int | None
    check2: int | None
    traded: bool
    quality: Quality | None  # 그날 행이 없으면 None


@dataclass(frozen=True)
class StockFlowDetail:
    code: str
    name: str | None
    days: tuple[DayFlow, ...]  # 오름차순
    cumulative: Mapping[str, int | None]  # 기간 누적(invalid 제외)
    checks: Mapping[str, int]  # c1_failed·c1_unavailable·c2_failed·c2_unavailable
    quality: Quality | None
    source: str


def stock_detail(ledger: Ledger, code: str, end: date, n: int) -> StockFlowDetail:
    """종목 한 개의 최근 n 영업일. invalid 행은 값을 싣지 않는다(품질만)."""
    days: list[DayFlow] = []
    counts = {"c1_failed": 0, "c1_unavailable": 0, "c2_failed": 0, "c2_unavailable": 0}
    name: str | None = None
    for d in ledger.days_upto(end, n):
        r = ledger.get(code, d)
        if r is None:
            days.append(DayFlow(d, None, None, None, None, None, None, None, False, None))
            continue
        name = r.name or name
        if not r.usable:
            for cid in r.failed_checks:
                counts[f"{cid}_failed"] += 1
            days.append(DayFlow(d, None, None, None, None, None, None, None, r.traded, r.quality))
            continue
        c1, c2 = check1(r), check2(r)
        counts["c1_unavailable"] += c1 is None
        counts["c2_unavailable"] += c2 is None
        days.append(
            DayFlow(
                d, r.turnover, r.foreign, r.inst, r.other_corp, r.indiv, c1, c2, r.traded,
                r.quality,
            )
        )  # fmt: skip
    agg = ledger.window(code, end, n)
    cum = {w: agg.sums_by_investor[w] for w in WHO}
    return StockFlowDetail(
        code=code,
        name=name,
        days=tuple(days),
        cumulative=cum,
        checks=counts,
        quality=agg.quality,
        source=source_label(agg.sources),
    )
