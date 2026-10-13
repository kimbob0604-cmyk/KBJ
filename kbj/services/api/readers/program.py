"""프로그램매매 reader — `/api/program/daily`·`/api/program/top`(P5 F2 — docs/p5_design.md §5,
F2 §0-2·§0-3·§1.4·§3.5, metrics §22).

- 금액은 원(저장 그대로). 거래소는 `config/markets.yaml kis.venues` 의 첫 거래소(기본 KRX — NXT 를
  켜기 전엔 'NXT 미포함' 메모).
- 일별 막대: 최근 `days` 거래일(≤ 60). 검산 ④ 를 다시 보고 어긋난 날은 값 없이 남긴다(수를 메모로).
- 상위 표: **범위 꼬리표(`scope_label`)를 반드시 싣는다**(F2 §1.4 — KIS 에 프로그램 순위 TR 이
  없다).
  - `intraday` — 오늘(휴장이면 직전 거래일) 마지막 훑기 슬롯. 범위 `거래대금 상위 N + 관심종목`.
  - `close` — 마감 훑기(`program.stock_sweep` 18:10)가 있는 가장 최근 거래일(오늘부터 거슬러 5
    거래일). 범위 `전 종목(마감 확정)` — 덜 끝났으면 `훑은 종목 n/N` 을 붙이고 quality estimated.
- 수집 작업은 실측(#40·#41) 전 꺼져 있다 — 행이 없으면 404 `no_data`(작업·예정 시각·'확인 전').
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.flows.program import (
    MARKET_NAME,
    apply_program_checks,
    day_bars,
    rank_top,
    scope_label,
    sweep_universe,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.program import ProgramDaily, ProgramDayBar, ProgramTop, ProgramTopRow
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = ["CLOSE_LOOKBACK", "NO_DAILY", "NO_TOP", "SOURCE", "program_daily", "program_top"]

NO_DAILY: Final = "아직 없음 — program.daily 15:50(확인 전 #40)"
NO_TOP: Final = (
    "아직 없음 — program.stock_scan 09:05~15:25 · program.stock_sweep 18:10(확인 전 #41)"
)
SOURCE: Final = "KIS"
CLOSE_LOOKBACK: Final = 5  # 마감 표를 찾을 거래일 수
_CLOSE_AT: Final = time(15, 30)
Market = Literal["K", "Q"]
Venue = Literal["KRX", "NXT", "TOTAL"]


def _venue(ctx: ReadContext) -> Venue:
    v = ctx.markets.kis.venues[0] if ctx.markets.kis.venues else "KRX"
    return v if v in ("KRX", "NXT", "TOTAL") else "KRX"  # pyright: ignore[reportReturnType]


def _market(market: str) -> Market:
    if market not in ("K", "Q"):
        raise ValueError(f"시장은 K·Q: {market!r}")  # 라우트가 이미 막는다
    return market  # pyright: ignore[reportReturnType]


def program_daily(ctx: ReadContext, *, market: str, days: int) -> Envelope[ProgramDaily]:
    mkt, venue = _market(market), _venue(ctx)
    now = ctx.now_kst()
    rows = ctx.repos.program.market_days(mkt, venue, now.date(), days)
    if not rows:
        raise NoData(NO_DAILY)
    checked, fails = apply_program_checks(rows)
    bars = day_bars(checked)
    invalid = sum(1 for b in bars if b.quality == Quality.INVALID)
    notes = list(ctx.venue_notes())
    if invalid:
        notes.append(f"검산 ④ 실패 {invalid}일 제외(전체 = 차익 + 비차익, 합계 = 위탁 + 자기)")
    if len(bars) < days:
        notes.append(f"{days}거래일 중 {len(bars)}일만 있다(백필 전 — program.backfill)")
    del fails
    last = bars[-1].date
    return envelope(
        ProgramDaily(
            market=mkt,
            venue=venue,
            days=len(bars),
            rows=[
                ProgramDayBar(
                    date=b.date,
                    net=b.net,
                    arbt_net=b.arbt_net,
                    nabt_net=b.nabt_net,
                    entm_net=b.entm_net,
                    onsl_net=b.onsl_net,
                    index_close=b.index_close,
                    quality=b.quality,
                )
                for b in bars
            ],
        ),
        source=SOURCE,
        as_of=datetime.combine(last, _CLOSE_AT, tzinfo=KST),
        quality=worst(b.quality for b in bars if b.quality != Quality.INVALID),
        notes=notes,
        generated_at=now,
    )


def _names(ctx: ReadContext, day: date) -> dict[str, str | None]:
    return {u.code: u.name for u in ctx.repos.market.universe(day)}


def _close_day(ctx: ReadContext, mkt: Market, venue: Venue, today: date) -> date | None:
    d = today if ctx.cal.is_trading_day(today) else ctx.cal.prev_trading_day(today)
    for _ in range(CLOSE_LOOKBACK):
        if ctx.repos.program.stock_days(d, mkt, venue):
            return d
        d = ctx.cal.prev_trading_day(d)
    return None


def program_top(ctx: ReadContext, *, market: str, side: str, scope: str) -> Envelope[ProgramTop]:
    mkt, venue = _market(market), _venue(ctx)
    now = ctx.now_kst()
    today = now.date()
    cfg = ctx.markets.program
    notes = list(ctx.venue_notes())
    slot: datetime | None = None
    if scope == "intraday":
        day = today if ctx.cal.is_trading_day(today) else ctx.cal.prev_trading_day(today)
        rows = ctx.repos.program.stock_scan(day, mkt, venue)
        if not rows:
            raise NoData(NO_TOP)
        slot = max((r.ts for r in rows if r.ts is not None), default=None)
        label = scope_label("intraday", top_n=cfg.scan_top_n)
        quality = worst(r.quality for r in rows if r.quality != Quality.INVALID)
        notes.append("장중 순위는 훑은 범위 안의 순위 — 마감 뒤 전 종목 순위와 다를 수 있다")
    else:
        found = _close_day(ctx, mkt, venue, today)
        if found is None:
            raise NoData(NO_TOP)
        day = found
        rows = ctx.repos.program.stock_days(day, mkt, venue)
        total = sum(1 for _, m in sweep_universe(ctx.repos.market.universe(day), [mkt]))
        label = scope_label("close", top_n=cfg.scan_top_n, swept=len(rows), total=total or None)
        quality = worst(r.quality for r in rows if r.quality != Quality.INVALID)
        if total and len(rows) < total:
            quality = Quality.ESTIMATED
            notes.append(f"마감 훑기 진행 중 — {MARKET_NAME[mkt]} {len(rows)}/{total}종목")
    side_v: Literal["buy", "sell"] = "sell" if side == "sell" else "buy"
    scope_v: Literal["intraday", "close"] = "close" if scope == "close" else "intraday"
    top = rank_top(rows, side_v, cfg.top_n)
    names = _names(ctx, day)
    invalid = sum(1 for r in rows if r.quality == Quality.INVALID)
    if invalid:
        notes.append(f"품질 invalid {invalid}종목 제외")
    return envelope(
        ProgramTop(
            market=mkt,
            venue=venue,
            side=side_v,
            scope=scope_v,
            scope_label=label,
            slot=slot,
            rows=[
                ProgramTopRow(
                    rank=i + 1,
                    code=r.code,
                    name=names.get(r.code),
                    price=r.price,
                    chg_pct=r.chg_pct,
                    net_value=r.net_value,
                    buy_value=r.buy_value,
                    sell_value=r.sell_value,
                    quality=r.quality,
                )
                for i, r in enumerate(top)
            ],
        ),
        source=SOURCE,
        as_of=slot or datetime.combine(day, _CLOSE_AT, tzinfo=KST),
        quality=quality,
        notes=notes,
        generated_at=now,
    )
