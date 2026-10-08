"""수급 readers — 스크리닝·시장 투자자 합계·종목 상세·장중 잠정(docs/p3_design.md §4.2·§5.3).

- 스크리너·종목 상세는 원장 하나(`kbj.engines.flows.ledger` — 원천 우선순위 krx > kis >
  kis.prelim)로
  계산한다. 장중에는 전 종목 원장이 없어 스크리너는 **마지막 확정 원장**으로 돌고, 15:35 마감 수집이
  끝나면 오늘로 넘어간다(`as_of` 로 표시 — §4.2 장중 화면).
- 검산 불가(4구분·7구분 결측)는 None 과 사유(notes) — 0 으로 바꾸지 않는다(R2).
"""

from __future__ import annotations

from datetime import date
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.core.rows import Investor
from kbj.engines.flows.ledger import close_as_of, source_label
from kbj.engines.flows.screen import STREAK_LOOKBACK, ScreenTuning, screen
from kbj.engines.flows.totals import InvestorTotals as EngineTotals
from kbj.engines.flows.totals import market_investor_totals, stock_detail
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.flows import (
    DayFlow,
    DayTotals,
    Excluded,
    IntradayFlows,
    IntradayRow,
    InvestorTotals,
    RankItem,
    ScreenResponse,
    ScreenRow,
    StockFlowDetail,
)
from kbj.services.api.readers._common import (
    LEDGER_WINDOW,
    NoData,
    ReadContext,
    day_start,
    envelope,
    label,
    ledger_quality,
    slot_end,
    won,
    worst,
)

__all__ = [
    "INTRADAY_TOP_N",
    "flows_intraday",
    "flows_investors",
    "flows_screen",
    "flows_stock",
    "investor_totals_model",
    "market_totals",
]

Mode = Literal["value", "foreign", "inst", "both", "streak", "spike"]
MarketQ = Literal["all", "KOSPI", "KOSDAQ"]
ShareClass = Literal["common", "pref"]
RECENT_DAYS: Final = 5
if (
    LEDGER_WINDOW < STREAK_LOOKBACK
):  # 연속일이 원장 창에서 잘리지 않게(엔진 한도가 바뀌면 바로 실패)
    raise RuntimeError("원장 창이 연속일 한도보다 짧다")
INTRADAY_TOP_N: Final = 30


def _newhigh_labels(ctx: ReadContext, day: date) -> dict[str, str]:
    return {code: lb.kind for code, lb in ctx.repos.board.labels(day, "close").items()}


def _sectors(ctx: ReadContext, day: date) -> dict[str, str]:
    bday = ctx.repos.board.last_day(day)
    if bday is None:
        return {}
    return {c: s.sector for c, s in ctx.repos.board.stock_days(bday).items() if s.sector}


def flows_screen(
    ctx: ReadContext,
    *,
    mode: Mode,
    market: MarketQ,
    period: int,
    min_avg_turnover: int | None,
    include_flagged: bool,
    limit: int,
    share_class: ShareClass,
    day: date | None = None,
) -> Envelope[ScreenResponse]:
    end = day or ctx.today()
    ledger = ctx.ledger(end)
    asof = ledger.last_day
    if asof is None:  # load_ledger 가 빈 원장이면 NoData 를 올린다 — 형 좁히기
        raise NoData("아직 없음 — market.close_collect 15:35")
    cfg = ctx.markets.screen
    floor = cfg.min_avg_turnover_krw if min_avg_turnover is None else min_avg_turnover
    res = screen(
        ledger,
        mode=mode,
        market=market,
        period=period,
        min_avg_turnover=floor,
        include_flagged=include_flagged,
        newhigh=_newhigh_labels(ctx, asof),
        limit=limit,
        share_class=share_class,
        tuning=ScreenTuning.from_config(cfg),
        sectors=_sectors(ctx, asof),
    )
    rows = [
        ScreenRow(
            rank=r.rank,
            code=r.code,
            name=r.name,
            market=r.market,
            sector=r.sector,
            kind=r.kind,
            chg_pct=r.chg_pct,
            turnover_sum=r.turnover_sum,
            turnover_avg=won(r.turnover_avg),
            turnover_rate_pct=r.turnover_rate_pct,
            foreign=r.foreign,
            inst=r.inst,
            other_corp=r.other_corp,
            indiv=r.indiv,
            streak_foreign=r.streak_foreign,
            streak_inst=r.streak_inst,
            spike_mult=r.spike_mult,
            newhigh_label=r.newhigh_label,
            flags=None if r.flags is None else list(r.flags),
            quality=r.quality,
            source=r.source or "KBJ",
        )
        for r in res.rows
    ]
    ex = res.n_excluded
    data = ScreenResponse(
        mode=mode,
        market=market,
        period=period,
        share_class=share_class,
        min_avg_turnover=floor,
        date=res.as_of,
        n_total=res.n_total,
        n_excluded=Excluded(
            flagged=ex["flagged"],
            invalid=ex["invalid"],
            below_min=ex["below_min"],
            status_unknown=ex["status_unknown"],
        ),
        rows=rows,
    )
    check_notes = ledger.checks.notes() if ledger.checks is not None else ()
    day_q = ledger_quality(ledger, asof)
    return envelope(
        data,
        source=res.source or label(s for r in ledger.day(asof) for s in r.sources.values()),
        as_of=close_as_of(asof),
        quality=worst([res.quality, day_q]),
        notes=[*ctx.venue_notes(), *res.notes, *check_notes],
        generated_at=ctx.now(),
    )


def investor_totals_model(t: EngineTotals, recent: list[DayTotals]) -> InvestorTotals:
    return InvestorTotals(
        date=t.date,
        by_investor=dict(t.by_investor),
        inst7=None if t.inst7 is None else dict(t.inst7),
        check1_residual=t.check1_residual,
        check2_residual=t.check2_residual,
        by_market={k: dict(v) for k, v in t.by_market.items()},
        quality=t.quality,
        source=t.source or "KBJ",
        notes=list(t.notes),
        recent=recent,
    )


def market_totals(ctx: ReadContext, end: date) -> InvestorTotals | None:
    """시장 투자자 합계(마지막 날) + 최근 5영업일. 행이 없으면 None."""
    by_code = ctx.repos.flows.market_days(end, RECENT_DAYS)
    rows = [r for rs in by_code.values() for r in rs]
    days = sorted({r.date for r in rows})
    if not days:
        return None
    recent: list[DayTotals] = []
    last: EngineTotals | None = None
    for d in days:
        t = market_investor_totals(rows, d)
        if t is None:
            continue
        recent.append(DayTotals(date=d, by_investor=dict(t.by_investor), quality=t.quality))
        last = t
    if last is None:
        return None
    return investor_totals_model(last, recent)


def flows_investors(ctx: ReadContext, *, day: date | None = None) -> Envelope[InvestorTotals]:
    t = market_totals(ctx, day or ctx.today())
    if t is None:
        raise NoData("아직 없음 — market.close_collect 15:35(시장 투자자)")
    return envelope(
        t,
        source=t.source,
        as_of=close_as_of(t.date),
        quality=t.quality,
        notes=[*ctx.venue_notes(), *t.notes],
        generated_at=ctx.now(),
    )


def flows_stock(ctx: ReadContext, code: str, *, days: int) -> Envelope[StockFlowDetail]:
    ledger = ctx.ledger(ctx.today())
    end = ledger.last_day
    if end is None or not ledger.series(code):
        raise NoData(f"아직 없음 — 종목 {code} 원장 행 없음")
    d = stock_detail(ledger, code, end, days)
    data = StockFlowDetail(
        code=d.code,
        name=d.name,
        days=[
            DayFlow(
                date=x.date,
                turnover=x.turnover,
                foreign=x.foreign,
                inst=x.inst,
                other_corp=x.other_corp,
                indiv=x.indiv,
                check1=x.check1,
                check2=x.check2,
                traded=x.traded,
                quality=x.quality,
            )
            for x in d.days
        ],
        cumulative=dict(d.cumulative),
        checks=dict(d.checks),
    )
    notes = list(ctx.venue_notes())
    if d.checks.get("c1_unavailable"):
        notes.append(f"검산 ① 불가 {d.checks['c1_unavailable']}일 — 4구분 중 미제공 구분이 있다")
    if d.checks.get("c2_unavailable"):
        notes.append(f"검산 ② 불가 {d.checks['c2_unavailable']}일 — 기관 7구분 미제공")
    failed = d.checks.get("c1_failed", 0) + d.checks.get("c2_failed", 0)
    if failed:
        notes.append(f"검산 실패 {failed}일 — invalid 로 값을 싣지 않았다")
    return envelope(
        data,
        source=d.source or "KBJ",
        as_of=close_as_of(end),
        quality=d.quality,
        notes=notes,
        generated_at=ctx.now(),
    )


def flows_intraday(ctx: ReadContext) -> Envelope[IntradayFlows]:
    """오늘 장중 가집계 상위·거래대금 순위의 마지막 슬롯(모두 잠정)."""
    now = ctx.now_kst()
    start = day_start(now.date())
    inv = ctx.repos.flows.intraday(start, now)
    ranks = ctx.repos.market.rank_rows(start, now)
    stamps = [r.ts for r in inv] + [r.ts for r in ranks]
    if not stamps:
        raise NoData("아직 없음 — flows.intraday(장중 10분 슬롯)")
    slot = max(stamps)

    def top(who: Investor) -> list[IntradayRow]:
        rows = [r for r in inv if r.ts == slot and r.investor is who and r.quality.usable]
        rows.sort(key=lambda r: (r.rank if r.rank is not None else 10**9, -(r.net_value or 0)))
        return [
            IntradayRow(rank=r.rank, code=r.code, value=r.net_value, qty=r.net_qty)
            for r in rows[:INTRADAY_TOP_N]
        ]

    rk = sorted(
        (r for r in ranks if r.ts == slot and r.quality.usable), key=lambda r: (r.market, r.rank)
    )
    data = IntradayFlows(
        slot=slot,
        top_foreign=top(Investor.FOREIGN),
        top_inst=top(Investor.INSTITUTION),
        turnover_rank=[
            RankItem(
                market=r.market,
                rank=r.rank,
                code=r.code,
                name=r.name,
                turnover=r.turnover,
                chg_pct=r.chg_pct,
            )
            for r in rk
        ],
    )
    used = [r.source for r in inv if r.ts == slot] + [r.source for r in rk]
    notes = [*ctx.venue_notes(), "장중 잠정 — 15:35 마감 뒤 확정"]
    if not any(r.ts == slot for r in inv):
        notes.append("이 슬롯의 가집계 없음")
    if not rk:
        notes.append("이 슬롯의 거래대금 순위 없음")
    return envelope(
        data,
        source=source_label(used) or "KIS(잠정)",
        as_of=slot_end(slot),
        quality=Quality.ESTIMATED,
        notes=notes,
        generated_at=ctx.now(),
    )
