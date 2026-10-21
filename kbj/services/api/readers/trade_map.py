"""종목↔HS 매핑·겹쳐 보기 reader — `/api/trade/stocks`·`/api/trade/stock/{code}`·
`/api/trade/stock/{code}/flows`(docs/p6_design.md §4.6·§5, metrics §28 — 묶음 MAP, 흐름은 P10).

`pub_trade.stock_map`(`trade.map_load`)·`stock_export`(`trade.stock_index`)·`hs_dim`(품목명)만
읽는다 (DB 만 — 계약 ⑩). 로그인 API 라 `origin: local`(관심종목 매핑) 줄도 읽고, 같은 종목이
공개·local 둘 다에 있으면 local 우선(`mapping.resolve` — 작업과 같은 규칙). 공개 내보내기는 public
만(D-P6-14).

- 랭킹: 지수가 있는 마지막 달까지 최근 `overlay.rank_months`(3)개월 E 합 / 전년 같은 달 합 − 1
  내림차순(None 은 뒤, 동률 코드 순 — `overlay.rank`). 품질은 그 종목 마지막 달 행.
- 종목: 매핑 줄(품목명 = `hs_dim`, 없으면 None)·근거·월/분기 지수·매출 YoY·시차 상관·상태.
  매핑표에 없는 종목은 404 `no_data` '매핑 없음'(화면은 오류가 아니라 안내 한 줄 — §6).
  매핑은 있는데 지수가 아직 없으면 404 `no_data`(trade.stock_index 예정 시각).
- 금액은 미화 달러(환율 미조정 — `fx_note`), 시세 칸 없음(로그인 종가 줄은
  `/api/stock/{code}/chart`).
- 흐름(`trade_stock_flows`): 그 종목 매핑 줄만으로 원천 색인을 만들어(`trade.stock_index` 와 같은
  표 — 품목 월간·품목×국가·시군구) 흐름마다 최근 `FLOW_MONTHS` 개월 금액·중량·단가·일평균과 YoY
  (`kbj.engines.trade.flows`). 영업일은 거래일 달력, 시군구 이름은 `trade_regions.yaml`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, cast

from kbj.config.trade import load_trade, load_trade_regions
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.trade_rows import (
    ItemCountryMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    StockExportRow,
    StockMapRow,
    prev_ym,
)
from kbj.engines.trade.explore import months_between
from kbj.engines.trade.flows import NATURE_LABEL, FlowView, flow_views
from kbj.engines.trade.mapping import resolve
from kbj.engines.trade.overlay import rank, rank_yoy, sources
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.trade import (
    TradeFlow,
    TradeFlowPoint,
    TradeFlowProduct,
    TradeMapEvidence,
    TradeMapLine,
    TradeStock,
    TradeStockFlows,
    TradeStockPoint,
    TradeStockRank,
    TradeStocks,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = [
    "FLOW_MONTHS",
    "NOT_MAPPED",
    "NO_MAP",
    "NO_STOCK_INDEX",
    "NO_TRADE",
    "SOURCE",
    "month_days",
    "trade_stock",
    "trade_stock_flows",
    "trade_stocks",
]

SOURCE: Final = "관세청(공공데이터포털)+DART"
NO_MAP: Final = "아직 없음 — trade.map_load 07:35(매핑표)"
NO_STOCK_INDEX: Final = "아직 없음 — trade.stock_index 21:00(매핑 종목 수출 지수)"
NOT_MAPPED: Final = "매핑 없음 — 매핑표에 없는 종목"
NO_TRADE: Final = "아직 없음 — trade.customs_monthly 14~20일(품목 월간)"
FLOW_MONTHS: Final = 36  # 흐름 차트·표 — YoY 를 위해 12개월 더 읽는다
NOTE_DAYS: Final = "일평균 = 금액 ÷ 그 달 영업일(거래일 달력 — 관세청 조업일수와 다를 수 있다)"
NOTE_RAW: Final = "흐름 금액은 통관 합(가중치 미적용) — 회사 매출이 아니라 그 품목·지역의 통관 금액"
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"
NOTE_DRAFT: Final = (
    "매핑 가중치는 자체 큐레이션 초안 — '검토 전' 종목은 사람이 아직 확인하지 않았다"
)


def _usd(v: Decimal | None) -> int | None:
    return None if v is None else int(v)


def _by_code(rows: Sequence[StockExportRow]) -> dict[str, list[StockExportRow]]:
    out: dict[str, list[StockExportRow]] = {}
    for r in rows:
        out.setdefault(r.code, []).append(r)
    return out


def _monthly(rows: Sequence[StockExportRow]) -> list[StockExportRow]:
    return sorted((r for r in rows if not r.is_quarter), key=lambda r: r.period)


def _quarterly(rows: Sequence[StockExportRow]) -> list[StockExportRow]:
    return sorted((r for r in rows if r.is_quarter), key=lambda r: r.period)


def trade_stocks(ctx: ReadContext) -> Envelope[TradeStocks]:
    smap = ctx.repos.trade.stock_map()
    if not smap:
        raise NoData(NO_STOCK_INDEX)  # 랭킹은 지수 행이 있어야 — 매핑 전이어도 지수 작업 안내
    res = resolve(smap)
    exports = _by_code(ctx.repos.trade.stock_export())
    have = [c for c in res.codes() if exports.get(c)]
    if not have:
        raise NoData(NO_STOCK_INDEX)
    months = [r.period for c in have for r in _monthly(exports[c]) if r.export_usd is not None]
    last = max(months) if months else None
    n = load_trade().overlay.rank_months
    vals: dict[str, float | None] = {}
    for c in have:
        m = {r.period: r.export_usd for r in _monthly(exports[c])}
        vals[c] = None if last is None else rank_yoy(m, ym=last, months=n)
    rows: list[TradeStockRank] = []
    qs: list[Quality] = []
    ats: list[datetime] = []
    versions: set[str] = set()
    for code, v in rank(vals.items()):
        lines = res.lines[code]
        ex = exports[code]
        mon = _monthly(ex)
        tail = mon[-1] if mon else ex[-1]
        m = {r.period: r.export_usd for r in mon}
        own = max((r.period for r in mon if r.export_usd is not None), default=None)
        sector = next((str(x.flow_meta["sector"]) for x in lines if x.flow_meta.get("sector")),
                      None)  # fmt: skip
        rows.append(
            TradeStockRank(
                code=code, name=lines[0].name, yoy_3m=v, status=tail.status,
                reviewed=all(x.reviewed for x in lines), origin=lines[0].origin,
                quality=tail.quality, sector=sector, last_ym=own,
                yoy_1m=None if last is None else rank_yoy(m, ym=last, months=1),
                spark=[] if last is None else [_usd(m.get(prev_ym(last, k)))
                                               for k in range(11, -1, -1)],
            )
        )  # fmt: skip
        qs.append(tail.quality)
        ats.append(tail.as_of)
        versions.add(lines[0].map_version)
    data = TradeStocks(map_version="+".join(sorted(versions)), rows=rows)
    notes = [NOTE_USD, NOTE_DRAFT]
    if last is not None:
        notes.append(f"랭킹 = {last} 까지 최근 {n}개월 수출 지수 YoY")
    return envelope(
        data, source=SOURCE, as_of=max(ats), quality=worst(qs), notes=notes,
        generated_at=ctx.now(),
    )  # fmt: skip


def _line(r: StockMapRow, names: dict[str, str]) -> TradeMapLine:
    return TradeMapLine(
        hs=r.hs, flow=r.flow, region_id=r.region_id or None, weight=r.weight,
        countries=list(r.countries), name=names.get(r.hs),
    )  # fmt: skip


def _point(r: StockExportRow, *, quarter: bool) -> TradeStockPoint:
    return TradeStockPoint(
        period=r.period, export_usd=_usd(r.export_usd), yoy=r.yoy,
        revenue_yoy=r.revenue_yoy if quarter else None, quality=r.quality,
    )  # fmt: skip


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


def _evidence(lines: Sequence[StockMapRow]) -> list[TradeMapEvidence]:
    out: list[TradeMapEvidence] = []
    seen: set[tuple[str, str, str | None]] = set()
    for x in lines:
        for e in x.evidence:
            key = (str(e.get("kind", "")), str(e.get("note", "")), _opt(e.get("rcept_no")))
            if key not in seen:
                seen.add(key)
                out.append(TradeMapEvidence(kind=key[0], note=key[1], rcept_no=key[2]))
    return out


def trade_stock(ctx: ReadContext, *, code: str) -> Envelope[TradeStock]:
    smap = ctx.repos.trade.stock_map()
    if not smap:
        raise NoData(NO_MAP)
    lines = resolve(r for r in smap if r.code == code).lines.get(code)
    if not lines:
        raise NoData(NOT_MAPPED)
    ex = ctx.repos.trade.stock_export(code=code)
    if not ex:
        raise NoData(NO_STOCK_INDEX)
    mon, qtr = _monthly(ex), _quarterly(ex)
    names: dict[str, str] = {}
    for h in sorted({x.hs for x in lines}):
        names.update({d.hs: d.name_ko for d in ctx.repos.trade.hs_dim(prefix=h) if d.hs == h})
    tail = (qtr or mon)[-1]
    data = TradeStock(
        code=code,
        name=lines[0].name,
        reviewed=all(x.reviewed for x in lines),
        origin=lines[0].origin,
        map_version=lines[0].map_version,
        status=tail.status,
        lines=[_line(x, names) for x in lines],
        evidence=_evidence(lines),
        monthly=[_point(r, quarter=False) for r in mon],
        quarterly=[_point(r, quarter=True) for r in qtr],
        lag_corr={k: tail.lag_corr.get(k) for k in ("0", "1", "2")} if qtr else {},
    )
    notes = [NOTE_USD]
    if not data.reviewed:
        notes.append(NOTE_DRAFT)
    known = [r.quality for r in mon if r.export_usd is not None]
    return envelope(
        data, source=SOURCE, as_of=max(r.as_of for r in ex),
        quality=worst(known) if known else Quality.ESTIMATED, notes=notes,
        generated_at=ctx.now(),
    )  # fmt: skip


# ── 종목별 흐름(P10 단계 1) ─────────────────────────────────────────────────────────────────


def month_days(cal: TradingCalendar, months: Sequence[str]) -> dict[str, int]:
    """달 → 영업일 수(거래일 달력)."""
    out: dict[str, int] = {}
    for ym in months:
        first = date(int(ym[:4]), int(ym[5:7]), 1)
        nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        out[ym] = cal.trading_days_between(first - timedelta(days=1), nxt - timedelta(days=1))
    return out


def _int(v: Decimal | None) -> int | None:
    return None if v is None else int(v)


def _flow(v: FlowView, names: dict[str, str], region_names: dict[str, str], keep: int) -> TradeFlow:
    m = v.meta
    weights: dict[str, float] = {}
    for r in v.lines:
        weights.setdefault(r.hs, r.weight)
    products = [
        TradeFlowProduct(hs=hs, name=cast("str | None", p.get("name")) or names.get(hs),
                         weight=weights.get(hs, 0.0))
        for p in cast("list[dict[str, object]]", m.get("products") or [])
        if (hs := str(p["hs"]))
    ]  # fmt: skip
    nature = str(m.get("nature") or "mixed")
    pts = [
        TradeFlowPoint(
            ym=p.ym, usd=_int(p.usd), kg=_int(p.kg), unit_price=p.unit_price, usd_yoy=p.usd_yoy,
            kg_yoy=p.kg_yoy, price_yoy=p.price_yoy, days=p.days, daily_usd=_int(p.daily_usd),
            daily_yoy=p.daily_yoy, products={k: _int(x) for k, x in p.products.items()},
            quality=p.quality,
        )
        for p in v.points[-keep:]
    ]  # fmt: skip
    return TradeFlow(
        id=v.flow_id, name=str(m.get("name") or v.flow_id),
        kind="sigungu" if m.get("kind") == "sigungu" else "national",
        direction="imp" if m.get("direction") == "imp" else "exp",
        nature=nature if nature in NATURE_LABEL else "mixed",  # type: ignore[arg-type]
        nature_label=NATURE_LABEL.get(nature, NATURE_LABEL["mixed"]),
        partners=[str(c) for c in cast("list[object]", m.get("partners") or [])],
        regions=[region_names.get(str(r), str(r))
                 for r in cast("list[object]", m.get("regions") or [])],
        products=products, basis=str(m.get("basis") or ""), last_ym=v.last_ym, has_kg=v.has_kg,
        points=pts, notes=list(v.notes),
    )  # fmt: skip


def trade_stock_flows(ctx: ReadContext, *, code: str) -> Envelope[TradeStockFlows]:
    smap = ctx.repos.trade.stock_map()
    if not smap:
        raise NoData(NO_MAP)
    lines = resolve(r for r in smap if r.code == code).lines.get(code)
    if not lines:
        raise NoData(NOT_MAPPED)
    repo = ctx.repos.trade
    last = repo.last_ym("pub_trade.item_monthly")
    if last is None:
        raise NoData(NO_TRADE)
    ym_from = prev_ym(last, FLOW_MONTHS + 12 - 1)
    items: list[ItemMonthRow] = []
    ics: list[ItemCountryMonthRow] = []
    sgg: list[SigunguMonthRow] = []
    for hs in sorted({ln.hs for ln in lines if not ln.region_id}):
        items += repo.item_monthly(prefix=hs, ym_from=ym_from, ym_to=last)
    for hs, cc in sorted({(ln.hs, c) for ln in lines if not ln.region_id for c in ln.countries}):
        ics += repo.item_country_monthly(prefix=hs, country=cc, ym_from=ym_from, ym_to=last)
    for rid, hs6 in sorted({(ln.region_id, ln.hs) for ln in lines if ln.region_id}):
        sgg += repo.sigungu_monthly(hs6=hs6, region_id=rid, ym_from=ym_from, ym_to=last)
    months = months_between(ym_from, last)
    src = sources(
        {(r.ym, r.hs): r for r in items}.values(),
        {(r.ym, r.country, r.hs): r for r in ics}.values(),
        {(r.ym, r.sido_cd, r.sgg_nm, r.hs6): r for r in sgg}.values(),
        months=months,
    )
    views = flow_views(lines, src, days_of=month_days(ctx.cal, months))
    names: dict[str, str] = {}
    for h in sorted({x.hs for x in lines}):
        names.update({d.hs: d.name_ko for d in repo.hs_dim(prefix=h) if d.hs == h})
    region_names = {r.id: r.sgg_nm for r in load_trade_regions().regions}
    flows = [_flow(v, names, region_names, FLOW_MONTHS) for v in views]
    data = TradeStockFlows(
        code=code, name=lines[0].name, reviewed=all(x.reviewed for x in lines),
        origin=lines[0].origin, map_version=lines[0].map_version, evidence=_evidence(lines),
        flows=flows,
    )  # fmt: skip
    notes = [NOTE_USD, NOTE_RAW, NOTE_DAYS]
    if not data.reviewed:
        notes.append(NOTE_DRAFT)
    qs = [p.quality for f in flows for p in f.points if p.usd is not None]
    ats = [r.as_of for r in (*items, *ics, *sgg)]
    return envelope(
        data, source=SOURCE, as_of=max(ats) if ats else ctx.now(),
        quality=worst(qs) if qs else Quality.ESTIMATED, notes=notes, generated_at=ctx.now(),
    )  # fmt: skip
