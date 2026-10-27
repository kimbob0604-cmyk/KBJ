"""코스피200 선물 미결제약정(OI) 리더 — `/api/gex/futures-oi`(metrics §32, ADR 0042 — 로그인).

`ctx.repos.gex`(DB 만 — 계약 ⑩)의 세 표를 엔진 입력으로 옮기고 계산은 순수 엔진
`kbj.engines.gex.futures_oi` 에 맡긴다(절대 규칙 3 — 숫자를 지어내지 않는다).

- 확정: `prv_gex.krx_fut_daily`(코스피200·정규 행 — `gex.krx_derivatives`, 평일 08:05 전 거래일분)
- 잠정: `prv_gex.fut_board`(market F·source board·session day — 그날 종목별 마지막 행)
- 투자자: `prv_gex.investor_flow` 날마다 정규장 마지막 행(K2I 선물·월물 옵션, WKM·WKI 위클리 옵션)
- 잠정 대상일 = 투자자(선물) 행이 있는 가장 늦은 날과 오늘(거래일이고 전광판 행이 있으면) 중 늦은
  날 — 단 KRX 확정일보다 늦을 때만. 아침 KRX 적재 전이면 어제가 잠정으로 남는다.
- 행 품질 invalid(합계 OI 빈 값·전광판 구성 불일치)는 싣되 봉투 품질 계산에서 빼고 수를 notes 에.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final

from kbj.core.quality import Quality
from kbj.engines.gex.futures_oi import (
    BOARD_SANITY_PCT,
    FUT_MARKET,
    FUT_SECTOR,
    LABEL_KO,
    OI_FLAT_PCT,
    PRICE_FLAT_PCT,
    BoardRow,
    FlowRow,
    InvestorNet,
    OiDay,
    OptionNet,
    SeriesDay,
    build_oi_board,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex_oi import (
    FutOiDay,
    FutOiInvestor,
    FutOiOption,
    FutOiThresholds,
    FuturesOi,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = ["DEFAULT_DAYS", "FLOW_MARKETS", "MAX_DAYS", "MIN_DAYS", "NO_OI", "SOURCE", "futures_oi"]

NO_OI: Final = "아직 없음 — gex.krx_derivatives(평일 08:05 — 전 거래일 확정)"
SOURCE: Final = "KRX:drv/fut_bydd_trd+KIS(선물 전광판·투자자별)"
DEFAULT_DAYS: Final = 60
MIN_DAYS: Final = 20
MAX_DAYS: Final = 120
FLOW_MARKETS: Final = ("K2I", "WKM", "WKI")
WON_NOTE: Final = "투자자 대금은 원(investor_flow 백만원 × 10⁶)"
CONFIRM_NOTE: Final = "확정 OI 는 KRX 일별 — 다음 거래일 08:05 뒤"
PROVISIONAL_NOTE: Final = "{d} 은 KRX 확정 전 — 선물 전광판 잠정(estimated)"


def _span(days: int) -> timedelta:
    """거래일 days + 1 을 덮는 달력일(주말·휴장 포함 넉넉히)."""
    return timedelta(days=days * 2 + 14)


def _board(ctx: ReadContext, d: date, upto: datetime) -> list[BoardRow]:
    return [
        BoardRow(r.ts, r.code, r.price, r.oi, r.remaining_days, Quality(r.quality))
        for r in ctx.repos.gex.futures_latest(d, "day", upto)
        if r.market == "F" and r.source == "board"
    ]


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def _inv(n: InvestorNet) -> FutOiInvestor:
    return FutOiInvestor.model_validate(
        {
            "investor": n.investor, "net_qty": n.net_qty, "net_value_won": n.net_value_won,
            "as_of": n.ts, "quality": n.quality, "reason": n.reason,
        }
    )  # fmt: skip


def _day(d: OiDay) -> FutOiDay:
    return FutOiDay(
        trade_date=d.trade_date, provisional=d.provisional, source=d.source, as_of=d.as_of,
        quality=d.quality, total_oi=d.total_oi, oi_change=d.oi_change,
        oi_change_raw=d.oi_change_raw, oi_change_pct=d.oi_change_pct, near_expiry=d.near_expiry,
        near_oi=d.near_oi, near_close=_f(d.near_close), near_change=_f(d.near_change),
        near_change_pct=d.near_change_pct, expiry_day=d.expiry_day, label=d.label,
        label_text=LABEL_KO[d.label], reasons=list(d.reasons),
        flows=[_inv(n) for n in d.futures_flow],
    )  # fmt: skip


def _opt(o: OptionNet) -> FutOiOption:
    return FutOiOption.model_validate(
        {
            "trade_date": o.trade_date, "series": o.series, "cp": o.cp, "investor": o.investor,
            "net_qty": o.net_qty, "net_value_won": o.net_value_won, "as_of": o.ts,
            "quality": o.quality, "reason": o.reason,
        }
    )  # fmt: skip


def futures_oi(ctx: ReadContext, *, days: int = DEFAULT_DAYS) -> Envelope[FuturesOi]:
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError(f"days 는 {MIN_DAYS}~{MAX_DAYS}: {days}")
    now = ctx.now()
    today = ctx.today()
    first = today - _span(days)
    gex = ctx.repos.gex
    series = [SeriesDay(*r) for r in gex.krx_futures_days(first, today)]
    flows = [
        FlowRow(r.trade_date, r.ts, r.market_code, r.sector_code, r.investor, r.net_qty,
                r.net_value, Quality(r.quality))
        for r in gex.investor_closes(first, today, FLOW_MARKETS, now)
    ]  # fmt: skip
    last_confirmed = max((s.trade_date for s in series), default=None)
    board_today = _board(ctx, today, now) if ctx.cal.is_trading_day(today) else []
    cands = [
        f.trade_date for f in flows if (f.market_code, f.sector_code) == (FUT_MARKET, FUT_SECTOR)
    ]
    if board_today:
        cands.append(today)
    target = max(cands) if cands else None
    if target is not None and last_confirmed is not None and target <= last_confirmed:
        target = None
    if target is None or target == today:
        board = board_today
    else:
        board = _board(ctx, target, now)
    b = build_oi_board(series, flows, board, target=target, cal=ctx.cal, days=days)
    if not b.days and b.provisional is None:
        raise NoData(NO_OI)
    data = FuturesOi(
        days=[_day(d) for d in b.days],
        provisional=None if b.provisional is None else _day(b.provisional),
        options=[_opt(o) for o in b.options],
        foreign_streak=b.foreign_streak,
        thresholds=FutOiThresholds(
            price_flat_pct=PRICE_FLAT_PCT,
            oi_flat_pct=OI_FLAT_PCT,
            board_sanity_pct=BOARD_SANITY_PCT,
        ),
    )
    rows = [*data.days, *([data.provisional] if data.provisional else [])]
    invalid = sum(1 for r in rows if r.quality is Quality.INVALID)
    qs = [r.quality for r in rows if r.quality is not Quality.INVALID]
    qs += [n.quality for r in rows for n in r.flows] + [o.quality for o in data.options]
    stamps = [r.as_of for r in rows] + [n.as_of for r in rows for n in r.flows]
    stamps += [o.as_of for o in data.options]
    notes = [
        f"품질 invalid {invalid}일(빈 OI·전광판 구성 불일치 — 라벨 판단 불가)" if invalid else "",
        CONFIRM_NOTE,
        PROVISIONAL_NOTE.format(d=data.provisional.trade_date.isoformat())
        if data.provisional
        else "",
        WON_NOTE,
    ]
    return envelope(
        data, source=SOURCE, as_of=max(stamps), quality=worst(qs), notes=notes, generated_at=now
    )
