"""시장 readers — 요약(지수·시장 거래대금·시장폭·시장 투자자)·업종 히트맵·상단 띠
(docs/p3_design.md §4.4·§5.3, metrics §6).

- 확정 값은 원장(krx > kis)에서, 장중 값은 KIS 지수 슬롯(estimated — '장중(지수 기준)', R23).
- 업종 지수 코드(`market.sector_indices`)·경기민감/방어 코드가 비어 있으면 [실측 필요] 사유와 함께
  '준비 중'(404 no_data / 칩 note) — 지어내지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final

from kbj.core.quality import Quality, Sourced
from kbj.core.rows import IndexBar, IndexQuote
from kbj.core.time import KST
from kbj.engines.flows.ledger import Ledger, close_as_of, source_label
from kbj.engines.flows.totals import market_investor_totals
from kbj.engines.market.breadth import market_breadth
from kbj.engines.market.ribbon import (
    RibbonInputs,
    SkewResult,
    cyclical_vs_defensive,
    ribbon,
    semi_skew_from_ledger,
)
from kbj.engines.market.sectors import PERIODS, sector_heat
from kbj.engines.market.turnover import (
    INTRADAY_INDEX_CODES,
    intraday_market_turnover,
    turnover_ratio,
    turnover_series,
)
from kbj.services.api.models.common import ChipValue, Envelope, SourcedInt
from kbj.services.api.models.flows import InvestorTotals
from kbj.services.api.models.market import (
    Breadth,
    Chip,
    DayValue,
    IndexTile,
    MarketSummary,
    Ribbon,
    SectorCell,
    SectorHeat,
    TurnoverPanel,
)
from kbj.services.api.readers._common import (
    NoData,
    ReadContext,
    day_start,
    envelope,
    label,
    slot_end,
    won,
    worst,
)
from kbj.services.api.readers.flows import market_totals

__all__ = ["market_ribbon", "market_sectors", "market_summary"]

SUMMARY_WINDOW: Final = 21  # 20일선·20일 평균 + 오늘
SPARK_N: Final = 20
KOSPI: Final = "0001"


def _sourced_int(v: Sourced[int] | None) -> SourcedInt | None:
    if v is None:
        return None
    return SourcedInt(value=v.value, source=v.source, as_of=v.as_of, quality=v.quality)


def _index_tile(
    code: str, name: str | None, bars: Sequence[IndexBar], quotes: Sequence[IndexQuote]
) -> IndexTile | None:
    good = [b for b in sorted(bars, key=lambda b: b.date) if b.quality.usable and b.close]
    live = [q for q in quotes if q.code == code and q.quality.usable and q.value is not None]
    q = max(live, key=lambda x: x.ts) if live else None
    spark = [b.close for b in good[-SPARK_N:] if b.close is not None]
    if (
        q is not None
        and q.value is not None
        and (not good or q.ts.astimezone(KST).date() > good[-1].date)
    ):
        return IndexTile(
            code=code,
            name=name or q.name,
            value=q.value,
            chg_pct=q.chg_pct,
            spark=[*spark[-(SPARK_N - 1) :], q.value],
            live=True,
            source=label([q.source]),
            as_of=slot_end(q.ts),
            quality=Quality.ESTIMATED,
        )
    if not good:
        return None
    last = good[-1]
    prev = good[-2] if len(good) > 1 else None
    chg = (
        (last.close / prev.close - 1) * 100
        if prev is not None and prev.close and last.close is not None
        else None
    )
    return IndexTile(
        code=code,
        name=name or last.name,
        value=last.close,
        chg_pct=chg,
        spark=spark,
        live=False,
        source=label([last.source]),
        as_of=close_as_of(last.date),
        quality=last.quality,
    )


def _breadth(ctx: ReadContext, ledger: Ledger) -> Breadth | None:
    day = ledger.last_day
    if day is None:
        return None
    labels = list(ctx.repos.board.labels(day, "close").values())
    n_board = len(ctx.repos.board.stock_days(day)) or None
    b = market_breadth(
        ledger,
        day,
        labels,
        limit_move_pct=ctx.markets.market.limit_move_pct,
        board_universe=n_board,
    )
    return Breadth(
        date=b.date,
        up=b.up,
        flat=b.flat,
        down=b.down,
        unknown=b.unknown,
        total=b.total,
        above_ma20_n=b.above_ma20_n,
        ma20_base=b.ma20_base,
        above_ma20_pct=b.above_ma20_pct,
        newhigh_n=b.newhigh_n,
        newhigh_pct=b.newhigh_pct,
        limit_up=b.limit_up,
        limit_down=b.limit_down,
        quality=b.quality,
        source=b.source or "KBJ",
        notes=list(b.notes),
    )


def _turnover(
    ctx: ReadContext, ledger: Ledger, quotes: Sequence[IndexQuote]
) -> tuple[TurnoverPanel | None, list[str]]:
    day = ledger.last_day
    if day is None:
        return None, []
    tags = ["NXT 미포함"] if ctx.venue_notes() else []
    live = intraday_market_turnover(
        (q for q in quotes if q.ts.astimezone(KST).date() > day),
        codes=INTRADAY_INDEX_CODES,
    )
    if live is not None:
        ratio = turnover_ratio(ledger, live.as_of.date(), today=live)
        basis = "intraday_index"
        tags.insert(0, "장중(지수 기준)")
        notes = ["시장 거래대금(장중)은 지수 누적 거래대금 — 확정(종목 합)과 정의가 다를 수 있다"]
    else:
        ratio = turnover_ratio(ledger, day)
        basis = "close"
        tags.insert(0, "마감")
        notes = []
    series = [
        DayValue(date=v.date, value=v.value, quality=v.quality, source=v.source)
        for v in turnover_series(ledger, day, 20)
    ]
    panel = TurnoverPanel(
        today=_sourced_int(ratio.today),
        basis=basis if ratio.today is not None else None,
        avg20=won(ratio.avg_prior),
        ratio=ratio.ratio,
        n_prior=ratio.n_prior,
        series=series,
        tags=tags,
    )
    if ratio.n_prior < 20:
        notes.append(f"20일 평균은 직전 {ratio.n_prior}영업일로 냈다")
    return panel, notes


def _end_day(ctx: ReadContext, day: date | None) -> tuple[date, bool]:
    """(기준일, 장중 값을 쓸지). `date` 를 주면 그날 이하 확정값만 — 오늘 이후면 오늘로 본다."""
    today = ctx.today()
    if day is None or day >= today:
        return today, True
    return day, False


def market_summary(ctx: ReadContext, *, day: date | None = None) -> Envelope[MarketSummary]:
    cfg = ctx.markets.market
    now = ctx.now_kst()
    today, with_live = _end_day(ctx, day)
    codes = list(cfg.intraday_indices)
    bars = ctx.repos.market.index_series(codes, today, SPARK_N + 1)
    quotes = ctx.repos.market.index_quotes(day_start(today), now) if with_live else []
    notes: list[str] = []
    tiles: list[IndexTile] = []
    for code in codes:
        t = _index_tile(code, cfg.intraday_indices.get(code), bars.get(code, []), quotes)
        if t is None:
            notes.append(f"지수 {code} 자료 없음")
        else:
            tiles.append(t)
    ledger: Ledger | None
    try:
        ledger = ctx.ledger(today)
    except NoData as e:
        ledger = None
        notes.append(e.message)
    turnover: TurnoverPanel | None = None
    breadth: Breadth | None = None
    investors: InvestorTotals | None = None
    if ledger is not None:
        turnover, t_notes = _turnover(ctx, ledger, quotes)
        notes += t_notes
        breadth = _breadth(ctx, ledger)
        if breadth is not None:
            notes += breadth.notes
    investors = market_totals(ctx, today)
    if not tiles and ledger is None and investors is None:
        raise NoData("아직 없음 — krx.daily 08:05 · market.intraday(장중 10분)")
    as_ofs: list[datetime] = [t.as_of for t in tiles]
    quals: list[Quality | None] = [t.quality for t in tiles]
    srcs: list[str] = [s for t in tiles for s in t.source.split("+")]
    if turnover is not None and turnover.today is not None:
        as_ofs.append(turnover.today.as_of)
        quals.append(turnover.today.quality)
        srcs += turnover.today.source.split("+")
    if breadth is not None:
        as_ofs.append(close_as_of(breadth.date))
        quals.append(breadth.quality)
        srcs += breadth.source.split("+")
    if investors is not None:
        as_ofs.append(close_as_of(investors.date))
        quals.append(investors.quality)
        srcs += investors.source.split("+")
        notes += investors.notes
    data = MarketSummary(
        date=None if ledger is None else ledger.last_day,
        indices=tiles,
        turnover=turnover,
        breadth=breadth,
        investors=investors,
    )
    return envelope(
        data,
        source=label(s for s in srcs if s != "KBJ"),
        as_of=max(as_ofs) if as_ofs else now,
        quality=worst(quals),
        notes=[*ctx.venue_notes(), *notes],
        generated_at=ctx.now(),
    )


def market_sectors(
    ctx: ReadContext, *, period: int, day: date | None = None
) -> Envelope[SectorHeat]:
    if period not in PERIODS:
        raise ValueError(f"period 는 {PERIODS} 중 하나")
    codes = list(ctx.markets.market.sector_indices)
    if not codes:
        raise NoData("준비 중 — 업종지수 코드(market.sector_indices) [실측 필요]")
    now = ctx.now_kst()
    end, with_live = _end_day(ctx, day)
    bars = ctx.repos.market.index_series(codes, end, period + 1)
    live = ctx.repos.market.sector_quotes(day_start(end), now) if with_live else []
    cells = sector_heat(bars, live, codes, period)
    rows = [
        SectorCell(
            code=c.code,
            name=c.name,
            market=c.market,
            chg_pct=c.chg_pct,
            turnover=c.turnover,
            quality=c.quality,
            source=c.source or "KBJ",
            as_of=c.as_of,
            note=c.note,
        )
        for c in cells
    ]
    got = [c for c in cells if c.chg_pct is not None]
    if not got:
        raise NoData("아직 없음 — 업종지수 일봉(krx.daily)·장중(market.intraday)")
    missing = len(cells) - len(got)
    notes = [f"업종 {missing}개는 값 없음(사유는 셀 note)"] if missing else []
    return envelope(
        SectorHeat(period=period, cells=rows),
        source=source_label(c.source for c in got) or "KBJ",
        as_of=max(c.as_of for c in got if c.as_of is not None),
        quality=worst(c.quality for c in got),
        notes=notes,
        generated_at=ctx.now(),
    )


def _chip_value(v: Sourced[Any] | None) -> ChipValue | None:
    if v is None:
        return None
    return ChipValue(value=v.value, source=v.source, as_of=v.as_of, quality=v.quality)


def market_ribbon(ctx: ReadContext) -> Envelope[Ribbon]:
    now = ctx.now_kst()
    today = now.date()
    rcfg = ctx.markets.ribbon
    notes: list[str] = []
    ledger: Ledger | None
    try:
        ledger = ctx.ledger(today)
    except NoData as e:
        ledger = None
        notes.append(e.message)
    turnover: Sourced[int] | None = None
    t_ratio: float | None = None
    n_prior: int | None = None
    semi: SkewResult | None = None
    cyc: SkewResult | None = None
    if ledger is not None and ledger.last_day is not None:
        day = ledger.last_day
        quotes = ctx.repos.market.index_quotes(day_start(today), now)
        live = intraday_market_turnover(
            (q for q in quotes if q.ts.astimezone(KST).date() > day),
            codes=INTRADAY_INDEX_CODES,
        )
        r = (
            turnover_ratio(ledger, live.as_of.date(), today=live)
            if live is not None
            else turnover_ratio(ledger, day)
        )
        turnover, t_ratio, n_prior = r.today, r.ratio, r.n_prior
        idx_codes = [KOSPI, *rcfg.cyclical, *rcfg.defensive]
        idx = ctx.repos.market.index_series(idx_codes, day, SUMMARY_WINDOW)
        semi = semi_skew_from_ledger(ledger, idx.get(KOSPI, []), day, semis=list(rcfg.semis))
        cyc = cyclical_vs_defensive(
            idx, day, cyclical=list(rcfg.cyclical), defensive=list(rcfg.defensive)
        )
    totals = None
    mrows = [r for rs in ctx.repos.flows.market_days(today, 1).values() for r in rs]
    if mrows:
        totals = market_investor_totals(mrows)
    bday = ctx.repos.board.last_day(today)
    newhigh: Sourced[int] | None = None
    n_board: int | None = None
    if bday is not None:
        labels = ctx.repos.board.labels(bday, "close")
        hits = [lb for lb in labels.values() if lb.kind in ("hist", "w52")]
        art = ctx.repos.board.artifact(bday, "newhigh")
        q = worst([art.quality if art is not None else None, *(lb.quality for lb in hits)])
        newhigh = Sourced[int](
            value=len(hits),
            source=label(lb.source for lb in hits) if hits else label([art.source] if art else []),
            as_of=close_as_of(bday),
            quality=q or Quality.ESTIMATED,
        )
        n_board = len(ctx.repos.board.stock_days(bday)) or None
    nxt = ctx.events.upcoming(today)
    chips = ribbon(
        RibbonInputs(
            turnover=turnover,
            turnover_ratio=t_ratio,
            turnover_n_prior=n_prior,
            investors=totals,
            newhigh=newhigh,
            newhigh_universe=n_board,
            semi_skew=semi,
            cyc_def=cyc,
            next_mpc=None if nxt is None else nxt.date,
            nxt_excluded=bool(ctx.venue_notes()),
        ),
        now,
        ctx.cal,
    )
    out = [
        Chip(
            key=c.key,
            label=c.label,
            value=_chip_value(c.value),
            tier=c.tier,
            phase_pending=c.phase_pending,
            tags=list(c.tags),
            note=c.note,
            detail=dict(c.detail),
        )
        for c in chips
    ]
    vals = [c.value for c in chips if c.value is not None]
    return envelope(
        Ribbon(chips=out),
        source="+".join(dict.fromkeys(v.source for v in vals)) or "KBJ",
        as_of=max((v.as_of for v in vals), default=now),
        quality=worst(v.quality for v in vals),
        notes=notes,
        generated_at=ctx.now(),
    )
