"""종목별 무역 흐름(P10 단계 1 — docs/p10_trade_stocks_plan.md, 나쁜양파 '종목별 흐름' 같은 단위).

종목 매핑 줄(`StockMapRow` — flow_id·flow_meta)을 흐름마다 묶어 달마다 금액·중량·단가·일평균과
각각의 전년 같은 달 대비를 낸다. 원천 값은 `overlay.sources`·`line_series`(종목 수출 지수와 같은
색인) — 여기는 묶고 나누기만 한다(순수 함수, I/O 없음).

- 금액은 **가중치를 곱하지 않은** 흐름 합(화면에 보이는 통관 금액). 가중치는 종목 수출 지수 몫.
- 시군구 흐름은 중량이 없다(시군구 표는 금액·건수만) — 중량·단가 칸은 None.
- 한 달에 일부 줄만 값이 있으면 있는 줄의 합 + 품질 estimated. 하나도 없으면 None.
- 일평균 = 금액 ÷ 그 달 영업일 수(`days_of` — 리더가 거래일 달력으로 센다).
- 판 1 적재분(flow_id '')은 줄의 방향·상대국·시군구로 흐름을 다시 묶는다(해석기 `MapStock.flow_list`
  와 같은 규칙 — 매핑표를 다시 적재하면 사라진다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from kbj.core.quality import Quality
from kbj.core.trade_rows import StockMapRow, prev_ym
from kbj.engines.trade.explore import Amount, worst, yoy
from kbj.engines.trade.overlay import Sources, line_series

__all__ = ["NATURE_LABEL", "FlowPoint", "FlowView", "flow_views", "group_flows"]

NATURE_LABEL: Final[Mapping[str, str]] = {
    "company": "회사 출하",
    "mixed": "업황 혼합",
    "affiliate": "관계사·타사 물량 혼입",
}


@dataclass(frozen=True)
class FlowPoint:
    ym: str
    usd: Decimal | None
    kg: Decimal | None
    unit_price: float | None  # 달러/kg
    usd_yoy: float | None
    kg_yoy: float | None
    price_yoy: float | None
    days: int | None
    daily_usd: Decimal | None
    daily_yoy: float | None
    products: Mapping[str, Decimal | None]  # hs → 금액(같은 품목의 여러 시군구는 합)
    quality: Quality


@dataclass(frozen=True)
class FlowView:
    flow_id: str
    meta: Mapping[str, object]
    lines: tuple[StockMapRow, ...]
    points: tuple[FlowPoint, ...]  # 달 오름차순
    last_ym: str | None  # 금액이 있는 마지막 달
    has_kg: bool
    notes: tuple[str, ...]


def _v1_key(r: StockMapRow) -> tuple[str, dict[str, object]]:
    if r.region_id:
        rid = r.region_id
        slug = rid.lower().replace("-", "_")
        return f"v1_{slug}", {
            "name": f"시군구 수출 — {rid}", "kind": "sigungu", "direction": "exp",
            "nature": "mixed", "partners": [], "regions": [rid], "basis": "",
        }  # fmt: skip
    cc = list(r.countries)
    word = "수출" if r.flow == "exp" else "수입"
    return f"v1_{r.flow}_{'_'.join(c.lower() for c in cc) or 'all'}", {
        "name": f"전국 품목 {word}" + (f" — {', '.join(cc)}" if cc else ""), "kind": "national",
        "direction": r.flow, "nature": "mixed", "partners": cc, "regions": [], "basis": "",
    }  # fmt: skip


def group_flows(
    lines: Iterable[StockMapRow],
) -> list[tuple[str, dict[str, object], tuple[StockMapRow, ...]]]:
    """줄 → [(흐름 id, meta, 줄들)] — 매핑표에 적힌 순서가 아니라 id 순(판 1 묶음은 뒤)."""
    by: dict[str, tuple[dict[str, object], list[StockMapRow]]] = {}
    for r in lines:
        if r.flow_id:
            fid, meta = r.flow_id, dict(r.flow_meta)
        else:
            fid, meta = _v1_key(r)
        got = by.setdefault(fid, (meta, []))
        got[1].append(r)
    out: list[tuple[str, dict[str, object], tuple[StockMapRow, ...]]] = []
    for fid in sorted(by, key=lambda k: (k.startswith("v1_"), k)):
        meta, rows = by[fid]
        if "products" not in meta:  # 판 1 — 줄의 품목(이름은 리더가 hs_dim 으로 채운다)
            meta["products"] = [{"hs": h, "name": None} for h in sorted({r.hs for r in rows})]
        out.append((fid, meta, tuple(sorted(rows, key=lambda r: (r.hs, r.region_id)))))
    return out


def _add(parts: Sequence[Amount]) -> tuple[Decimal | None, Decimal | None]:
    usd = [p.usd for p in parts if p.usd is not None]
    kgs = [p.kg for p in parts if p.kg is not None]
    total_kg = sum(kgs, Decimal(0)) if parts and len(kgs) == len(parts) else None
    return (sum(usd, Decimal(0)) if usd else None), total_kg


def _ratio(a: Decimal | None, b: Decimal | None) -> float | None:
    if a is None or b is None or b <= 0:
        return None
    return float(a / b)


def _fyoy(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return cur / prev - 1.0


def _view(
    fid: str,
    meta: Mapping[str, object],
    rows: tuple[StockMapRow, ...],
    src: Sources,
    days_of: Mapping[str, int],
) -> FlowView:
    series: list[tuple[StockMapRow, Mapping[str, Amount | None]]] = []
    notes: list[str] = []
    for r in rows:
        s, note = line_series(r, src)
        if note:
            notes.append(note)
        if s is not None:
            series.append((r, s))
    has_kg = bool(series) and all(not r.region_id for r, _s in series)
    base: dict[str, tuple[Decimal | None, Decimal | None, dict[str, Decimal | None], Quality]] = {}
    for ym in src.months:
        parts: list[Amount] = []
        by_hs: dict[str, Decimal | None] = {}
        gap = len(series) < len(rows)
        for r, s in series:
            a = s.get(ym)
            if a is None or a.usd is None:
                gap = True
                by_hs.setdefault(r.hs, None)
                continue
            parts.append(a)
            by_hs[r.hs] = (by_hs.get(r.hs) or Decimal(0)) + a.usd
        usd, kg = _add(parts)
        q = worst(p.quality for p in parts) or Quality.ESTIMATED
        if gap and q is Quality.OK:
            q = Quality.ESTIMATED
        base[ym] = (usd, kg if has_kg and not gap else None, by_hs, q)
    points: list[FlowPoint] = []
    for ym in src.months:
        usd, kg, by_hs, q = base[ym]
        pusd, pkg, _p, _q = base.get(prev_ym(ym, 12), (None, None, {}, Quality.ESTIMATED))
        price, pprice = _ratio(usd, kg), _ratio(pusd, pkg)
        days, pdays = days_of.get(ym), days_of.get(prev_ym(ym, 12))
        daily = None if usd is None or not days else usd / days
        pdaily = None if pusd is None or not pdays else pusd / pdays
        points.append(
            FlowPoint(
                ym=ym, usd=usd, kg=kg, unit_price=price, usd_yoy=yoy(usd, pusd),
                kg_yoy=yoy(kg, pkg), price_yoy=_fyoy(price, pprice), days=days,
                daily_usd=daily, daily_yoy=yoy(daily, pdaily), products=by_hs, quality=q,
            )
        )  # fmt: skip
    last = max((p.ym for p in points if p.usd is not None), default=None)
    return FlowView(fid, meta, rows, tuple(points), last, has_kg, tuple(dict.fromkeys(notes)))


def flow_views(
    lines: Iterable[StockMapRow], src: Sources, *, days_of: Mapping[str, int]
) -> list[FlowView]:
    """한 종목의 매핑 줄 → 흐름별 월 시계열."""
    return [_view(fid, meta, rows, src, days_of) for fid, meta, rows in group_flows(lines)]
