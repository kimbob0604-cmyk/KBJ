"""DART 만으로 만든 재무 비율 — `pub_fin.ratio`(공개, docs/p4_design.md §4.5, docs/metrics.md §9).

입력은 분기 단독 `QuarterRow`(normalize)와 주식총수(`ShareCount` — BPS 분모)뿐이다. 시세를 쓰지
않는다 (배수는 로그인 — 묶음 V). 기능 참고: SD
`dart_collector.calculate_derived_metrics`:362(EBITDA·순부채).

- 분기마다 그 분기에서 끝나는 **같은 기준(fs_div) 연속 4분기**로 TTM 을 만든다. 하나라도 없거나 값이
  None 이면 TTM None(`missing_input`) — 0 으로 채우지 않는다.
- 기준은 분기마다 CFS 우선, 없으면 OFS(§4.5). 앞 분기와 기준이 바뀌면 notes + quality estimated
  (metrics §9 '연결/별도 — 혼재 → estimated'. TTM·YoY 는 언제나 한 기준 안에서만 만든다).
- 비율은 % 실수(반올림하지 않는다). 분모 ≤ 0 이면 None + 사유: 매출 → `no_revenue`, 자본 →
  `capital_impairment`. YoY 는 전년 같은 분기가 0·없음이면 None, 부호가 바뀌면 None +
  notes('흑자전환'·'적자전환').
- quality = 입력 분기 중 가장 나쁜 것(estimated 가 하나라도 있으면 estimated).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.core.fin_rows import NaReason, QuarterRow, RatioRow, ShareCount
from kbj.core.quality import Quality

__all__ = ["ratios", "series"]

_RANK: Final = {Quality.OK: 0, Quality.STALE: 1, Quality.ESTIMATED: 2, Quality.INVALID: 3}


def _seq(year: int, q: int) -> int:
    return year * 4 + (q - 1)


def series(quarters: Iterable[QuarterRow]) -> list[QuarterRow]:
    """(연도, 분기)마다 한 행 — CFS 가 있으면 CFS, 없으면 OFS. 오래된 것부터."""
    best: dict[tuple[int, int], QuarterRow] = {}
    for r in quarters:
        k = (r.fiscal_year, r.fiscal_q)
        cur = best.get(k)
        if cur is None or (cur.fs_div == "OFS" and r.fs_div == "CFS"):
            best[k] = r
    return [best[k] for k in sorted(best)]


@dataclass
class _Acc:
    na: dict[str, str]
    notes: list[str]


def _sum(rows: Sequence[QuarterRow], col: str) -> int | None:
    vals = [getattr(r, col) for r in rows]
    if any(v is None for v in vals):
        return None
    return int(sum(vals))


def _pct(
    num: float | None, den: float | None, field: str, reason: NaReason, acc: _Acc
) -> float | None:
    if num is None or den is None:
        acc.na[field] = NaReason.MISSING_INPUT
        return None
    if den <= 0:
        acc.na[field] = reason
        return None
    return num / den * 100


def _yoy(cur: int | None, base: int | None, field: str, label: str, acc: _Acc) -> float | None:
    if cur is None or base is None or base == 0:
        acc.na[field] = NaReason.MISSING_INPUT
        return None
    if base < 0 < cur:
        acc.na[field] = NaReason.MISSING_INPUT
        acc.notes.append(f"{label} 흑자전환")
        return None
    if base > 0 > cur:
        acc.na[field] = NaReason.MISSING_INPUT
        acc.notes.append(f"{label} 적자전환")
        return None
    return (cur - base) / abs(base) * 100


def _avg(a: int | None, b: int | None) -> float | None:
    if a is None or b is None:
        return None
    return (a + b) / 2


def _shares_for(counts: Sequence[ShareCount], end: date) -> ShareCount | None:
    same = [c for c in counts if c.period_end == end]
    if not same:
        return None
    return max(same, key=lambda c: c.rcept_no)


def ratios(
    quarters: Iterable[QuarterRow], share_counts: Sequence[ShareCount] = ()
) -> list[RatioRow]:
    """회사 하나의 분기 행들 → 분기마다 비율 행(`pub_fin.ratio`)."""
    rows = series(quarters)
    by_seq: Mapping[tuple[str, int], QuarterRow] = {
        (r.fs_div, _seq(r.fiscal_year, r.fiscal_q)): r
        for r in quarters  # 같은 기준의 TTM 은 기준 안에서 찾는다
    }
    out: list[RatioRow] = []
    prev_fs: str | None = None
    for r in rows:
        acc = _Acc({}, [])
        s = _seq(r.fiscal_year, r.fiscal_q)
        win = [by_seq.get((r.fs_div, x)) for x in range(s - 3, s + 1)]
        window = [w for w in win if w is not None]
        full = len(window) == 4
        switched = prev_fs is not None and prev_fs != r.fs_div
        if switched:
            acc.notes.append(f"기준이 {prev_fs} → {r.fs_div} 로 바뀌었다")
        prev_fs = r.fs_div
        ttm_rev = _sum(window, "revenue") if full else None
        ttm_op = _sum(window, "op") if full else None
        ttm_ni_owner = _sum(window, "ni_owner") if full else None
        ttm_ni = _sum(window, "ni") if full else None
        if not full:
            acc.notes.append("연속 4분기(같은 기준)가 없어 TTM 을 만들지 않았다")
        base = by_seq.get((r.fs_div, s - 4))  # 4분기 전 분기말(평균 자본·자산의 기초)
        opm = _pct(ttm_op, ttm_rev, "opm", NaReason.NO_REVENUE, acc)
        npm = _pct(ttm_ni_owner, ttm_rev, "npm", NaReason.NO_REVENUE, acc)
        roe = _pct(
            ttm_ni_owner,
            _avg(base.equity_owner if base else None, r.equity_owner),
            "roe",
            NaReason.CAPITAL_IMPAIRMENT,
            acc,
        )
        roa = _pct(
            ttm_ni,
            _avg(base.total_assets if base else None, r.total_assets),
            "roa",
            NaReason.MISSING_INPUT,
            acc,
        )
        debt_ratio = _pct(
            r.total_liabilities, r.total_equity, "debt_ratio", NaReason.CAPITAL_IMPAIRMENT, acc
        )
        net_debt_ratio = _pct(
            r.net_debt, r.total_equity, "net_debt_ratio", NaReason.CAPITAL_IMPAIRMENT, acc
        )
        prev_y = by_seq.get((r.fs_div, s - 4))
        rev_yoy = _yoy(r.revenue, prev_y.revenue if prev_y else None, "rev_yoy", "매출", acc)
        op_yoy = _yoy(r.op, prev_y.op if prev_y else None, "op_yoy", "영업이익", acc)
        eps_vals = [w.eps_basic for w in window] if full else []
        eps_ttm: float | None = None
        if full and all(v is not None for v in eps_vals):
            eps_ttm = float(sum(v for v in eps_vals if v is not None))
        else:
            acc.na["eps_ttm"] = NaReason.MISSING_INPUT
        sc = _shares_for(share_counts, r.period_end)
        basis = sc.common_outstanding if sc is not None else None
        bps: float | None = None
        if basis is None or r.equity_owner is None:
            acc.na["bps"] = NaReason.MISSING_INPUT
        elif basis <= 0:
            acc.na["bps"] = NaReason.MISSING_INPUT
            basis = None
        else:
            bps = r.equity_owner / basis
        used = [*window, *([base] if base is not None else []), r]
        worst = max((u.quality for u in used), key=lambda q: _RANK[q])
        if switched and _RANK[worst] < _RANK[Quality.ESTIMATED]:
            worst = Quality.ESTIMATED  # 연결/별도 혼재 → estimated(docs/metrics.md §9)
        out.append(
            RatioRow(
                corp_code=r.corp_code,
                fiscal_year=r.fiscal_year,
                fiscal_q=r.fiscal_q,
                fs_div=r.fs_div,
                ttm_revenue=ttm_rev,
                ttm_op=ttm_op,
                ttm_ni_owner=ttm_ni_owner,
                opm=opm,
                npm=npm,
                roe=roe,
                roa=roa,
                debt_ratio=debt_ratio,
                net_debt_ratio=net_debt_ratio,
                rev_yoy=rev_yoy,
                op_yoy=op_yoy,
                eps_ttm=eps_ttm,
                bps=bps,
                shares_basis=basis,
                na_reasons=dict(acc.na),
                notes=tuple(acc.notes),
                quality=worst,
            )
        )
    return out
