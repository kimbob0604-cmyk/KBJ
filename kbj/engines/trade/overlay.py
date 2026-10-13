"""종목 수출 지수·겹쳐 보기(순수 — metrics §28, docs/p6_design.md §4.6).

`E_t = Σᵢ wᵢ·Xᵢ,t` — 매핑 줄 i 의 월 수출(달러):
- 품목 줄(countries 없음): 품목 월간의 그 접두 값(`explore.ItemIndex` — 가장 깊은 행만 더해
  이중 합산 0).
- 품목 줄(countries 있음): 품목×국가 표의 그 국가들 접두 합. 국가 표에 그 접두가 한 달도 없으면(국가
  표는 HS4 까지 — 더 깊은 접두) **전 국가 값으로 대신하고 `estimated`** + 메모(지어내지 않는다 —
  국가 몫을 나눠 짐작하지 않는다). 어떤 달에 일부 국가만 있으면 있는 국가 합 + `estimated`.
- 지역 줄: 시군구×HS6 표의 그 통합 지역 ID(개편 전후 같은 ID) 값.

규칙
- 한 달에 자료가 있는 줄 중 하나라도 값이 없으면 그 달 E = None(0 으로 채우지 않는다 — 절대 규칙 2).
  자료가 한 달도 없는 줄은 빼고 나머지로 계산(메모 + `estimated`), 모든 줄이 그러면 `no_trade`.
- 분기 = 달력 분기 세 달 합(하나라도 None → None). YoY = 분기 합 / 전년 같은 분기 합 − 1.
- 매출 YoY = DART 분기 매출(보고 기준 — 부르는 쪽이 달력 분기로 맞춰 준다) / 전년 같은 분기 − 1
  (전년 ≤ 0 이면 None).
- 시차 상관 = 피어슨(E 분기 YoY 의 k 분기 전, 매출 YoY), k ∈ `cfg.lags`. 짝이
  `cfg.min_quarters` 보다 적거나 분산 0 이면 None.
- 상태: `no_trade` > `no_revenue`(매출 없음) > `short_overlap`(시차 0 짝 < min_quarters —
  선은 그린다) > `ok`.
- 랭킹: 최근 `rank_months` 개월 E 합 / 전년 같은 달 합 − 1 내림차순(None 은 뒤, 동률 코드 순).

설정은 인자로(`OverlayCfg` — ⑨).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Final

from kbj.config.trade import OverlayCfg
from kbj.core.quality import Quality
from kbj.core.trade_rows import (
    Flow,
    ItemCountryMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    StockExportRow,
    StockExportStatus,
    StockMapRow,
    prev_ym,
)
from kbj.engines.trade.explore import Amount, ItemIndex, item_index, worst, yoy

__all__ = [
    "INDEX_SOURCE",
    "Overlay",
    "Point",
    "Sources",
    "build_overlay",
    "export_index",
    "lag_corr",
    "pearson",
    "quarter_of",
    "quarter_sums",
    "rank",
    "rank_yoy",
    "revenue_yoy",
    "sources",
    "to_rows",
]

_FLOWS: Final[tuple[Flow, ...]] = ("exp", "imp")
INDEX_SOURCE: Final = "KBJ:trade_map+DATAGO:15101609+DATAGO:15100475+DATAGO:15134343+DART"

Series = Mapping[str, Amount | None]


@dataclass(frozen=True)
class Sources:
    """겹쳐 보기 입력 색인(한 번 만들어 모든 종목에 쓴다)."""

    months: tuple[str, ...]  # 계산 달(오름차순)
    items: Mapping[Flow, ItemIndex]
    by_country: Mapping[tuple[str, Flow], ItemIndex]
    regions: Mapping[tuple[str, str], Mapping[str, Amount]]  # (region_id, hs6) → 달 → 수출


def sources(
    items: Iterable[ItemMonthRow],
    item_countries: Iterable[ItemCountryMonthRow],
    sigungu: Iterable[SigunguMonthRow],
    *,
    months: Sequence[str],
) -> Sources:
    items = list(items)
    ic_by: dict[str, list[ItemCountryMonthRow]] = {}
    for r in item_countries:
        ic_by.setdefault(r.country, []).append(r)
    by_country: dict[tuple[str, Flow], ItemIndex] = {
        (cc, f): item_index(rows, f) for cc, rows in ic_by.items() for f in _FLOWS
    }
    reg: dict[tuple[str, str], dict[str, list[SigunguMonthRow]]] = {}
    for r in sigungu:
        if r.region_id is None or r.quality is Quality.INVALID:
            continue
        reg.setdefault((r.region_id, r.hs6), {}).setdefault(r.ym, []).append(r)
    regions: dict[tuple[str, str], dict[str, Amount]] = {}
    for key, per in reg.items():
        out: dict[str, Amount] = {}
        for ym, rows in per.items():
            vals = [x.exp_usd for x in rows if x.exp_usd is not None]
            q = worst(x.quality for x in rows) or Quality.OK
            if len(vals) < len(rows) and q is Quality.OK:
                q = Quality.ESTIMATED
            out[ym] = Amount(usd=sum(vals, Decimal(0)) if vals else None, kg=None, quality=q)
        regions[key] = out
    return Sources(
        months=tuple(sorted(months)),
        items={"exp": item_index(items, "exp"), "imp": item_index(items, "imp")},
        by_country=by_country,
        regions=regions,
    )


def _country_sum(line: StockMapRow, src: Sources) -> dict[str, Amount | None] | None:
    out: dict[str, Amount | None] = {}
    seen = False
    for ym in src.months:
        parts: list[Amount] = []
        missing = False
        for cc in line.countries:
            ix = src.by_country.get((cc, line.flow))
            a = None if ix is None else ix.value(line.hs, ym)
            if a is None or a.usd is None:
                missing = True
            else:
                parts.append(a)
        if not parts:
            out[ym] = None
            continue
        seen = True
        q = worst(p.quality for p in parts) or Quality.OK
        if missing and q is Quality.OK:
            q = Quality.ESTIMATED
        out[ym] = Amount(
            usd=sum((p.usd or Decimal(0) for p in parts), Decimal(0)), kg=None, quality=q
        )
    return out if seen else None


def line_series(
    line: StockMapRow, src: Sources
) -> tuple[dict[str, Amount | None] | None, str | None]:
    """(달 → 값, 메모). 자료가 한 달도 없으면 (None, 메모)."""
    if line.region_id:
        got = src.regions.get((line.region_id, line.hs))
        if not got:
            return None, f"지역 {line.region_id}×{line.hs} 자료 없음"
        return {ym: got.get(ym) for ym in src.months}, None
    if line.countries:
        cs = _country_sum(line, src)
        if cs is not None:
            return cs, None
        note = f"국가 표에 {line.hs} 없음 — 전 국가 값(추정)"
        base, n2 = _all_country(line, src)
        if base is None:
            return None, n2
        return {
            ym: None if a is None else Amount(a.usd, a.kg, _est(a.quality))
            for ym, a in base.items()
        }, note
    return _all_country(line, src)


def _est(q: Quality) -> Quality:
    return Quality.ESTIMATED if q is Quality.OK else q


def _all_country(
    line: StockMapRow, src: Sources
) -> tuple[dict[str, Amount | None] | None, str | None]:
    ix = src.items[line.flow]
    vals = {ym: ix.value(line.hs, ym) for ym in src.months}
    if all(a is None or a.usd is None for a in vals.values()):
        return None, f"품목 {line.hs} 자료 없음"
    return vals, None


@dataclass(frozen=True)
class Point:
    period: str  # YYYY-MM · YYYYQn
    usd: Decimal | None
    yoy: float | None
    quality: Quality
    revenue_yoy: float | None = None


def export_index(
    lines: Sequence[StockMapRow], src: Sources
) -> tuple[dict[str, tuple[Decimal | None, Quality]], bool, tuple[str, ...]]:
    """(달 → (E, 품질), 자료 있음, 메모). 자료가 있는 줄이 없으면 (전부 None, False, 메모)."""
    used: list[tuple[StockMapRow, Mapping[str, Amount | None]]] = []
    notes: list[str] = []
    dropped = False
    for ln in lines:
        s, note = line_series(ln, src)
        if note:
            notes.append(note)
        if s is None:
            dropped = True
            continue
        used.append((ln, s))
    if not used:
        return {ym: (None, Quality.ESTIMATED) for ym in src.months}, False, tuple(notes)
    out: dict[str, tuple[Decimal | None, Quality]] = {}
    for ym in src.months:
        total = Decimal(0)
        qs: list[Quality] = []
        gap = False
        for ln, s in used:
            a = s.get(ym)
            if a is None or a.usd is None:
                gap = True
                if a is not None:
                    qs.append(a.quality)
                continue
            total += Decimal(str(ln.weight)) * a.usd
            qs.append(a.quality)
        q = worst(qs) or Quality.OK
        if (gap or dropped) and q is Quality.OK:
            q = Quality.ESTIMATED
        out[ym] = (None if gap else total, q)
    return out, True, tuple(notes)


def quarter_of(ym: str) -> str:
    return f"{ym[:4]}Q{(int(ym[5:7]) - 1) // 3 + 1}"


def _quarter_months(period: str) -> tuple[str, str, str]:
    y, q = period[:4], int(period[5])
    return tuple(f"{y}-{3 * (q - 1) + k:02d}" for k in (1, 2, 3))  # type: ignore[return-value]


def _prev_quarter(period: str, n: int) -> str:
    idx = int(period[:4]) * 4 + int(period[5]) - 1 - n
    return f"{idx // 4}Q{idx % 4 + 1}"


def quarter_sums(
    monthly: Mapping[str, tuple[Decimal | None, Quality]],
) -> dict[str, tuple[Decimal | None, Quality]]:
    """달 → 분기 합(세 달 중 하나라도 없거나 None → None)."""
    out: dict[str, tuple[Decimal | None, Quality]] = {}
    for p in sorted({quarter_of(m) for m in monthly}):
        cells = [monthly.get(m) for m in _quarter_months(p)]
        qs = [c[1] for c in cells if c is not None]
        q = worst(qs) or Quality.ESTIMATED
        if any(c is None or c[0] is None for c in cells):
            out[p] = (None, _est(q))
        else:
            out[p] = (
                sum((c[0] for c in cells if c is not None and c[0] is not None), Decimal(0)),
                q,
            )
    return out


def revenue_yoy(revenue: Mapping[str, int | None]) -> dict[str, float | None]:
    """분기 매출(YYYYQn → 원) → YoY(전년 같은 분기 ≤ 0·없음 → None)."""
    out: dict[str, float | None] = {}
    for p in sorted(revenue):
        cur, prev = revenue[p], revenue.get(_prev_quarter(p, 4))
        out[p] = None if cur is None or prev is None or prev <= 0 else cur / prev - 1.0
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    r = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / math.sqrt(sxx * syy)
    if not math.isfinite(r):
        return None
    return max(-1.0, min(1.0, r))


def lag_corr(
    e_yoy: Mapping[str, float | None],
    r_yoy: Mapping[str, float | None],
    *,
    lags: Sequence[int],
    min_quarters: int,
) -> tuple[dict[str, float | None], dict[int, int]]:
    """({'k': r}, {k: 짝 수}). 수출이 k 분기 앞선다 — 짝 = (E YoY[q−k], 매출 YoY[q])."""
    out: dict[str, float | None] = {}
    counts: dict[int, int] = {}
    for k in lags:
        xs: list[float] = []
        ys: list[float] = []
        for q, ry in sorted(r_yoy.items()):
            ex = e_yoy.get(_prev_quarter(q, k))
            if ry is None or ex is None:
                continue
            xs.append(ex)
            ys.append(ry)
        counts[k] = len(xs)
        out[str(k)] = pearson(xs, ys) if len(xs) >= min_quarters else None
    return out, counts


def rank_yoy(monthly: Mapping[str, Decimal | None], *, ym: str, months: int) -> float | None:
    """최근 `months` 개월(ym 포함) 합 / 전년 같은 달 합 − 1(하나라도 None → None)."""

    def window(end: str) -> Decimal | None:
        total = Decimal(0)
        for k in range(months):
            v = monthly.get(prev_ym(end, k))
            if v is None:
                return None
            total += v
        return total

    return yoy(window(ym), window(prev_ym(ym, 12)))


def rank(values: Iterable[tuple[str, float | None]]) -> list[tuple[str, float | None]]:
    """YoY 내림차순(None 은 뒤), 동률은 코드 순."""
    return sorted(values, key=lambda t: (t[1] is None, -(t[1] or 0.0), t[0]))


@dataclass(frozen=True)
class Overlay:
    code: str
    status: StockExportStatus
    monthly: tuple[Point, ...]
    quarterly: tuple[Point, ...]
    lag_corr: Mapping[str, float | None]
    notes: tuple[str, ...] = field(default=())

    def monthly_usd(self) -> dict[str, Decimal | None]:
        return {p.period: p.usd for p in self.monthly}


def build_overlay(
    code: str,
    lines: Sequence[StockMapRow],
    src: Sources,
    *,
    revenue: Mapping[str, int | None],
    cfg: OverlayCfg,
) -> Overlay:
    """한 종목의 월·분기 지수, 매출 YoY, 시차 상관, 상태."""
    monthly, has_trade, notes = export_index(lines, src)
    m_pts: list[Point] = []
    for ym in src.months:
        v, q = monthly[ym]
        prev = monthly.get(prev_ym(ym, 12))
        m_pts.append(Point(ym, v, yoy(v, None if prev is None else prev[0]), q))
    qs = quarter_sums(monthly)
    e_yoy: dict[str, float | None] = {}
    for p, (v, _q) in qs.items():
        prev = qs.get(_prev_quarter(p, 4))
        e_yoy[p] = yoy(v, None if prev is None else prev[0])
    r_yoy = revenue_yoy(revenue)
    corr, counts = lag_corr(e_yoy, r_yoy, lags=cfg.lags, min_quarters=cfg.min_quarters)
    q_pts = [Point(p, v, e_yoy[p], q, r_yoy.get(p)) for p, (v, q) in sorted(qs.items())]
    status: StockExportStatus
    if not has_trade:
        status = "no_trade"
    elif not any(v is not None for v in revenue.values()):
        status = "no_revenue"
    elif counts.get(cfg.lags[0], 0) < cfg.min_quarters:
        status = "short_overlap"
    else:
        status = "ok"
    return Overlay(
        code=code,
        status=status,
        monthly=tuple(m_pts),
        quarterly=tuple(q_pts),
        lag_corr=corr,
        notes=notes,
    )


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def to_rows(ov: Overlay, *, map_version: str, as_of: datetime) -> list[StockExportRow]:
    """`pub_trade.stock_export` 행 — 월 행(lag_corr 빈칸)·분기 행(lag_corr = 종목 값)."""
    out: list[StockExportRow] = []
    for p in ov.monthly:
        out.append(
            StockExportRow(
                code=ov.code, period=p.period, export_usd=_whole(p.usd), yoy=_finite(p.yoy),
                revenue_yoy=None, status=ov.status, map_version=map_version,
                source=INDEX_SOURCE, as_of=as_of, quality=p.quality, lag_corr={},
            )
        )  # fmt: skip
    for p in ov.quarterly:
        out.append(
            StockExportRow(
                code=ov.code, period=p.period, export_usd=_whole(p.usd), yoy=_finite(p.yoy),
                revenue_yoy=_finite(p.revenue_yoy), status=ov.status, map_version=map_version,
                source=INDEX_SOURCE, as_of=as_of, quality=p.quality, lag_corr=dict(ov.lag_corr),
            )
        )  # fmt: skip
    return out


def _whole(v: Decimal | None) -> Decimal | None:
    """numeric(20,0) — 달러 정수로 반올림(가중치 곱의 소수)."""
    return None if v is None else v.quantize(Decimal(1))
