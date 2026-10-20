"""수출입 탐색 reader — `/api/trade/items*`·`countries*`·`regions`·`surges`·`items104`
(docs/p6_design.md §5, metrics §26·§27·§29 — 묶음 EXP).

`pub_trade` 월간·파생 표만 읽어 엔진(`kbj.engines.trade.explore`·`price_volume`)에 넘긴다(DB 만 —
계약 ⑩). 데이터가 없으면 404 `no_data`(작업·예정 시각 — 관세청 월간 수집은 실측 #1~#5 전 꺼져
있다).

- 기간을 주지 않으면 그 표의 마지막 기준 월 한 달. 기간 YoY 는 같은 길이의 전년 기간과 비교(두
  기간 모두 모든 달 값이 있을 때만).
- 접두 값은 엔진 규칙(그 행이 있으면 그 행, 없으면 바로 아래 접두의 합 — 이중 합산 금지).
- 이름: `pub_trade.hs_dim`(관세청 품목명), 없으면 HS2 는 `knowledge/hs_chapters.yaml` 자체 이름.
- 금액은 미화 달러 정수(원화 환산 없음 — 환율 미조정), 중량 kg. 비율은 소수.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from kbj.config.trade import (
    HsChapters,
    TradeConfig,
    load_hs_chapters,
    load_trade,
    load_trade_items,
)
from kbj.config.trade import TradeItems as TradeItemsCfg
from kbj.core.quality import Quality
from kbj.core.trade_monthly import OTHER, Stack, monthly_stack
from kbj.core.trade_rows import SurgeLevel, prev_ym
from kbj.engines.trade.explore import (
    Node,
    country_lines,
    item_country_lines,
    item_index,
    region_lines,
    tree_nodes,
    yoy,
    yoy_3m,
)
from kbj.engines.trade.price_volume import decompose, unit_price, weight_unfit
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.trade import (
    TradeChapterName,
    TradeCountries,
    TradeCountryItems,
    TradeCountryRow,
    TradeGroupRow,
    TradeItemCountries,
    TradeItemCountryRow,
    TradeItemNode,
    TradeItemPoint,
    TradeItems,
    TradeItems104,
    TradeItemSeries,
    TradeMonthly,
    TradeMonthlyPoint,
    TradeMonthlyStack,
    TradeQuarterPoint,
    TradeRegionRow,
    TradeRegions,
    TradeSurgeRow,
    TradeSurges,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = [
    "NO_DERIVED",
    "NO_MONTHLY",
    "SOURCE",
    "trade_countries",
    "trade_country_items",
    "trade_item_countries",
    "trade_item_series",
    "trade_items",
    "trade_items104",
    "trade_regions",
    "trade_surges",
]

SOURCE: Final = "관세청(공공데이터포털)"
NO_MONTHLY: Final = "아직 없음 — trade.customs_monthly 14~20일 10:10·15:10(확인 전 #1~#5)"
NO_DERIVED: Final = "아직 없음 — trade.derive 14~20일 10:40·15:40(trade.customs_monthly 뒤)"
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"
NOTE_PREFIX: Final = "접두 값 = 그 HS 행, 없으면 바로 아래 HS 의 합(상위·하위를 함께 더하지 않는다)"
Flow = Literal["exp", "imp"]
SURGE_ORDER: Final[tuple[str, ...]] = ("hs2", "hs4", "hs6", "hs10", "country")


@functools.cache
def _cfg() -> TradeConfig:
    """`config/trade.yaml`(한 번만 읽는다 — 바뀌면 API 재시작)."""
    return load_trade()


@functools.cache
def _chapters() -> HsChapters:
    return load_hs_chapters()


@functools.cache
def _items_cfg() -> TradeItemsCfg:
    return load_trade_items()


def _int(v: Decimal | None) -> int | None:
    return None if v is None else int(v)


def _last(ctx: ReadContext, table: str, ym: str | None, missing: str) -> str:
    last = ctx.repos.trade.last_ym(table)
    if last is None:
        raise NoData(missing)
    return ym or last


def _names(ctx: ReadContext, codes: Iterable[str]) -> dict[str, str]:
    want = set(codes)
    out: dict[str, str] = {}
    for n in sorted({len(c) for c in want}):
        for d in ctx.repos.trade.hs_dim(hs_len=n):
            if d.hs in want:
                out[d.hs] = d.name_ko
    chapters = _chapters().chapters
    for c in want:
        if c not in out and c in chapters:
            out[c] = chapters[c]
    return out


def _node(n: Node, names: dict[str, str]) -> TradeItemNode:
    return TradeItemNode(
        hs=n.hs,
        name=names.get(n.hs),
        usd=_int(n.usd),
        kg=_int(n.kg),
        yoy=n.yoy,
        share=n.share,
        unit_price=n.unit_price,
        has_children=n.has_children,
        quality=n.quality,
    )


def _env[T](ctx: ReadContext, data: T, rows: Sequence[object], *notes: str) -> Envelope[T]:
    ats: list[datetime] = [getattr(r, "as_of") for r in rows]  # noqa: B009 — 행 형이 여럿
    qs: list[Quality] = [getattr(r, "quality") for r in rows]  # noqa: B009
    return envelope(
        data,
        source=SOURCE,
        as_of=max(ats),
        quality=worst(qs),
        notes=[NOTE_USD, *notes],
        generated_at=ctx.now(),
    )


# ── 품목 ───────────────────────────────────────────────────────────────────────────────────


def trade_items(
    ctx: ReadContext,
    *,
    level: Literal[2, 4, 6, 10],
    parent: str | None,
    flow: Flow,
    ym_from: str | None,
    ym_to: str | None,
) -> Envelope[TradeItems]:
    """접두 트리 한 층(트리맵 입력 — 크기 = 금액, 색 = YoY)."""
    to = _last(ctx, "pub_trade.item_monthly", ym_to, NO_MONTHLY)
    frm = ym_from or to
    if frm > to:
        raise NoData(f"기간이 비었다 — from({frm}) ≤ to({to})")
    if parent is not None and len(parent) >= level:
        raise NoData(f"부모 {parent} 아래에 {level}자리 층이 없다")
    rows = ctx.repos.trade.item_monthly(prefix=parent, ym_from=prev_ym(frm, 12), ym_to=to)
    nodes = tree_nodes(item_index(rows, flow), level=level, parent=parent, ym_from=frm, ym_to=to)
    if not nodes:
        raise NoData(f"{parent or '전체'} 아래 {level}자리 값 없음 — {NO_MONTHLY}")
    names = _names(ctx, (n.hs for n in nodes))
    data = TradeItems(
        level=level,
        parent=parent,
        flow=flow,
        ym_from=frm,
        ym_to=to,
        rows=[_node(n, names) for n in nodes],
    )
    return _env(ctx, data, [r for r in rows if r.ym >= frm], NOTE_PREFIX)


def trade_item_series(ctx: ReadContext, *, hs: str, flow: Flow) -> Envelope[TradeItemSeries]:
    """월 금액·중량·단가·YoY·3개월 YoY·단가 대 물량 분해(§27)."""
    _last(ctx, "pub_trade.item_monthly", None, NO_MONTHLY)
    rows = ctx.repos.trade.item_monthly(prefix=hs)
    idx = item_index(rows, flow)
    points: list[TradeItemPoint] = []
    for ym in idx.months:
        a = idx.value(hs, ym)
        if a is None:
            continue
        p = idx.value(hs, prev_ym(ym, 12))
        d = decompose(a.usd, a.kg, p.usd if p else None, p.kg if p else None)
        points.append(
            TradeItemPoint(
                ym=ym,
                usd=_int(a.usd),
                kg=_int(a.kg),
                unit_price=unit_price(a.usd, a.kg),
                yoy=yoy(a.usd, p.usd if p else None),
                yoy_3m=yoy_3m(lambda m: idx.usd(hs, m), ym),
                ln_price=d.ln_price if d else None,
                ln_volume=d.ln_volume if d else None,
                quality=a.quality,
            )
        )
    if not points:
        raise NoData(f"HS {hs} 값 없음 — {NO_MONTHLY}")
    unfit = weight_unfit(hs, _cfg().price_volume.weight_unfit)
    data = TradeItemSeries(
        hs=hs,
        name=_names(ctx, [hs]).get(hs),
        flow=flow,
        weight_unfit=unfit,
        points=points,
    )
    notes = ["단가 = 금액 ÷ 중량(달러/kg) · ln(1+금액 YoY) = ln(1+단가 YoY) + ln(1+중량 YoY)"]
    if unfit:
        notes.append("중량 기준 부적합 품목 — 단가·물량 분해는 참고만")
    return _env(ctx, data, rows, *notes)


def _country_names(ctx: ReadContext, ym: str) -> dict[str, str]:
    rows = ctx.repos.trade.country_monthly(ym_from=ym, ym_to=ym)
    return {r.country: r.country_name for r in rows}


def trade_item_countries(
    ctx: ReadContext, *, hs: str, ym: str | None, flow: Flow
) -> Envelope[TradeItemCountries]:
    """품목(접두)×국가 — 그 달 금액·YoY·비중."""
    at = _last(ctx, "pub_trade.item_country_monthly", ym, NO_MONTHLY)
    rows = ctx.repos.trade.item_country_monthly(prefix=hs, ym_from=prev_ym(at, 12), ym_to=at)
    lines = item_country_lines(rows, prefix=hs, ym=at, flow=flow)
    if not lines:
        raise NoData(f"HS {hs}·{at} 국가별 값 없음 — {NO_MONTHLY}")
    names = _country_names(ctx, at)
    data = TradeItemCountries(
        hs=hs,
        ym=at,
        flow=flow,
        rows=[
            TradeItemCountryRow(
                country=cc,
                country_name=names.get(cc),
                usd=_int(a.usd),
                yoy=y,
                share=s,
                quality=a.quality,
            )
            for cc, a, y, s in lines
        ],
    )
    return _env(ctx, data, [r for r in rows if r.ym == at], NOTE_PREFIX)


# ── 국가 ───────────────────────────────────────────────────────────────────────────────────


def trade_countries(ctx: ReadContext, *, ym: str | None) -> Envelope[TradeCountries]:
    at = _last(ctx, "pub_trade.country_monthly", ym, NO_MONTHLY)
    rows = ctx.repos.trade.country_monthly(ym_from=prev_ym(at, 12), ym_to=at)
    lines = country_lines(rows, at)
    if not lines:
        raise NoData(f"{at} 국가 값 없음 — {NO_MONTHLY}")
    data = TradeCountries(
        ym=at,
        rows=[
            TradeCountryRow(
                country=c.country,
                country_name=c.country_name,
                exp_usd=_int(c.exp_usd),
                imp_usd=_int(c.imp_usd),
                bal_usd=_int(c.bal_usd),
                exp_yoy=c.exp_yoy,
                imp_yoy=c.imp_yoy,
                exp_share=c.exp_share,
                quality=c.quality,
            )
            for c in lines
        ],
    )
    return _env(ctx, data, [r for r in rows if r.ym == at])


def trade_country_items(
    ctx: ReadContext, *, country: str, ym: str | None, flow: Flow
) -> Envelope[TradeCountryItems]:
    """국가별 품목(HS4 — 품목×국가 표, 응답에 4자리 이상이 없으면 HS2)."""
    at = _last(ctx, "pub_trade.item_country_monthly", ym, NO_MONTHLY)
    rows = ctx.repos.trade.item_country_monthly(country=country, ym_from=prev_ym(at, 12), ym_to=at)
    idx = item_index(rows, flow)
    level = 4 if idx.codes_at(4) else 2
    nodes = tree_nodes(idx, level=level, parent=None, ym_from=at, ym_to=at)
    if not nodes:
        raise NoData(f"{country}·{at} 품목 값 없음 — {NO_MONTHLY}")
    names = _names(ctx, (n.hs for n in nodes))
    data = TradeCountryItems(
        country=country,
        country_name=_country_names(ctx, at).get(country),
        ym=at,
        flow=flow,
        rows=[_node(n, names) for n in nodes],
    )
    return _env(ctx, data, [r for r in rows if r.ym == at], NOTE_PREFIX)


# ── 지역 ───────────────────────────────────────────────────────────────────────────────────


def trade_regions(
    ctx: ReadContext, *, hs6: str | None, sido: str | None, ym: str | None
) -> Envelope[TradeRegions]:
    """시군구 표(시도 필터는 그 달 행에만 — 전년 비교는 통합 지역 ID 라 2026-07 개편을 넘는다)."""
    at = _last(ctx, "pub_trade.sigungu_monthly", ym, NO_MONTHLY)
    rows = ctx.repos.trade.sigungu_monthly(hs6=hs6, ym_from=prev_ym(at, 12), ym_to=at)
    lines = [x for x in region_lines(rows, at) if sido is None or x.sido_cd == sido]
    if not lines:
        raise NoData(f"{at} 시군구 값 없음(매핑 HS6 만 수집) — {NO_MONTHLY}")
    data = TradeRegions(
        hs6=hs6,
        sido=sido,
        ym=at,
        rows=[
            TradeRegionRow(
                region_id=x.region_id,
                sido_cd=x.sido_cd,
                sgg_nm=x.sgg_nm,
                hs6=x.hs6,
                exp_usd=_int(x.exp_usd),
                exp_yoy=x.exp_yoy,
                share=x.share,
                quality=x.quality,
            )
            for x in lines
        ],
    )
    used = [r for r in rows if r.ym == at and (sido is None or r.sido_cd == sido)]
    return _env(ctx, data, used, "비중 = 같은 HS6 안 시군구 수출 비중")


# ── 급등·104품목(trade.derive) ─────────────────────────────────────────────────────────────


def trade_surges(
    ctx: ReadContext, *, ym: str | None, level: SurgeLevel | None
) -> Envelope[TradeSurges]:
    at = _last(ctx, "pub_trade.surge", ym, NO_DERIVED)
    rows = ctx.repos.trade.surge(ym=at, level=level)
    if not rows:
        raise NoData(f"{at} 급등·급락 없음 — {NO_DERIVED}")
    rows.sort(key=lambda r: (SURGE_ORDER.index(r.level), r.flow, r.rule, r.rank))
    names = _names(ctx, (r.key for r in rows if r.level != "country"))
    if any(r.level == "country" for r in rows):
        names |= _country_names(ctx, at)
    cfg = _cfg().surge
    data = TradeSurges(
        ym=at,
        level=level,
        min_usd=cfg.min_usd,
        rows=[
            TradeSurgeRow(
                level=r.level,
                key=r.key,
                name=names.get(r.key),
                flow=r.flow,
                rule=r.rule,
                yoy=r.yoy,
                yoy_3m=r.yoy_3m,
                usd=_int(r.usd),
                rank=r.rank,
                quality=r.quality,
            )
            for r in rows
        ],
    )
    rule = (
        f"급등 = 금액 ≥ {cfg.min_usd:,}달러 ∧ YoY ≥ {cfg.surge_yoy:+.0%} ∧ 3개월 YoY ≥ "
        f"{cfg.surge_yoy_3m:+.0%} · 급락 = 전년 금액 ≥ 문턱 ∧ YoY ≤ {cfg.plunge_yoy:+.0%} ∧ "
        f"3개월 YoY ≤ {cfg.plunge_yoy_3m:+.0%}"
    )
    return _env(ctx, data, rows, rule)


def _group_name(gid: str, fixed: dict[str, str], hs_names: dict[str, str]) -> str:
    if gid in fixed:
        return fixed[gid]
    if gid.startswith("hs"):
        return hs_names.get(gid[2:]) or f"HS {gid[2:]}"
    return gid


def trade_items104(ctx: ReadContext, *, ym: str | None) -> Envelope[TradeItems104]:
    at = _last(ctx, "pub_trade.item_group_monthly", ym, NO_DERIVED)
    rows = ctx.repos.trade.item_group_monthly(ym_from=at, ym_to=at)
    if not rows:
        raise NoData(f"{at} 104품목 없음 — {NO_DERIVED}")
    mode: Literal["fixed", "top_n"] = "top_n" if any(r.mode == "top_n" for r in rows) else "fixed"
    rows = [r for r in rows if r.mode == mode]
    fixed = {g.id: g.name for g in _items_cfg().groups}
    hs_names = _names(ctx, (r.group_id[2:] for r in rows if r.group_id.startswith("hs")))
    total = sum((r.exp_usd for r in rows if r.exp_usd is not None), Decimal(0))
    rows.sort(key=lambda r: (-(r.exp_usd if r.exp_usd is not None else Decimal(-1)), r.group_id))
    data = TradeItems104(
        ym=at,
        mode=mode,
        auto_selected=mode == "top_n",
        rows=[
            TradeGroupRow(
                group_id=r.group_id,
                name=_group_name(r.group_id, fixed, hs_names),
                exp_usd=_int(r.exp_usd),
                yoy=r.yoy,
                unit_price=r.unit_price,
                share=None if r.exp_usd is None or not total else float(r.exp_usd / total),
                quality=r.quality,
            )
            for r in rows
        ],
    )
    note = (
        "자동 선택 — 직전 12개월 수출 상위 HS4(매달 바뀔 수 있다)"
        if mode == "top_n"
        else "자체 고정 HS4 목록(config/knowledge/trade_items.yaml)"
    )
    return _env(ctx, data, rows, note, "비중 = 104품목 합 대비")


# ── 월별 추이(metrics §29a) ──────────────────────────────────────────────────────────────────

MONTHLY_TOP: Final = 6


def trade_monthly(ctx: ReadContext, *, months: int = 36) -> Envelope[TradeMonthly]:
    """월별 수출·수입 — 상위 류 쌓기·전월비·전년동월비·분기 QoQ·YoY(비교용 12개월을 더 읽는다)."""
    last = _last(ctx, "pub_trade.item_monthly", None, NO_MONTHLY)
    rows = ctx.repos.trade.chapter_monthly(prev_ym(last, months + 11), last)
    if not rows:
        raise NoData(NO_MONTHLY)
    stacks = {
        f: monthly_stack(rows, flow=f, top=MONTHLY_TOP, months=months) for f in ("exp", "imp")
    }
    names = _names(ctx, {c for st in stacks.values() for c in st.chapters if c != OTHER})

    def view(st: Stack) -> TradeMonthlyStack:
        return TradeMonthlyStack(
            flow=st.flow,
            chapters=[
                TradeChapterName(hs=c, name="기타" if c == OTHER else names.get(c, f"HS {c}"))
                for c in st.chapters
            ],
            months=[
                TradeMonthlyPoint(ym=p.ym, total=p.total, parts=dict(p.parts), mom=p.mom, yoy=p.yoy)
                for p in st.months
            ],
            quarters=[
                TradeQuarterPoint(q=q.q, total=q.total, qoq=q.qoq, yoy=q.yoy) for q in st.quarters
            ],
        )

    data = TradeMonthly(exp=view(stacks["exp"]), imp=view(stacks["imp"]))
    probe = ctx.repos.trade.item_monthly(prefix="85", hs_len=10, ym_from=last, ym_to=last)
    n_months = len({r.ym for r in rows})
    note = f"HS10 합 · 상위 {MONTHLY_TOP}개 류(최근 12개월 합) + 기타 · 받은 달 {n_months}개"
    return _env(ctx, data, probe or ctx.repos.trade.item_monthly(ym_from=last, ym_to=last), note)
