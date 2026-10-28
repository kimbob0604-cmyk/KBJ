"""`gex.option_positions` — 옵션 행사가별 투자자 추정 포지션 하루 배분(metrics §34.1, ADR 0044).

평일 16:05(파생 마감 수집 `flows.market_minute` 15:55 뒤). 그날 `investor_flow` 분 누적(옵션 6묶음 ×
투자자 frgn·prsn·orgn + 선물 F001)과 `chain_snapshots` 분 시세를 순수 엔진
`kbj.engines.gex.option_positions.allocate_day` 에 넣고, 결과(행사가 하루 매수·매도, 묶음 하루
요약)를 `prv_gex.opt_pos_strike`·`opt_pos_day` 에 **그날 행 교체**로 쓴다(멱등 — 쓰기 주인 하나).

- 누적·OI 검산·벽·합성 델타는 리더가 읽을 때 계산한다(여기서 하지 않는다).
- 지난날 다시: `python -m kbj.services.scheduler run-once gex.option_positions --as-of <날>`
  (원천 표가 남아 있는 한 같은 결과).
- 한 묶음의 실패는 다른 묶음을 멈추지 않는다 — 사유를 남기고 그 묶음만 빈다(절대 규칙 4).
- 원천이 하나도 없으면(휴장·수집 없음) `not_ready` — 0 을 사실로 쓰지 않는다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Final

from kbj.core.calendar import TradingCalendar, session_bounds
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.gex.option_positions import (
    FUT_MARKET,
    FUT_SECTOR,
    INVESTORS,
    OPTION_SECTORS,
    DayAlloc,
    FlowPoint,
    Quote,
    allocate_day,
)
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import Repos, pg_repos
from kbj.store.repos.opt_pos import ChainClose, FlowRow, OptPosDayRow, OptPosStrikeRow

__all__ = ["LOADED_BY", "compute_day", "daily"]

LOADED_BY: Final = "gex.option_positions"
SERVICE: Final = "scheduler"
log = logging.getLogger("kbj.services.engine")


def _point(r: FlowRow) -> FlowPoint:
    return FlowPoint(r.ts, r.buy_qty, r.buy_value, r.sell_qty, r.sell_value, Quality(r.quality))


def _day_row(day: date, m: str, s: str, a: DayAlloc) -> OptPosDayRow:
    return OptPosDayRow(
        trade_date=day, investor=a.investor, market_code=m, sector_code=s, minutes=a.minutes,
        gap_minutes=a.gap_minutes, first_ts=a.first_ts, last_ts=a.last_ts, total_buy=a.total_buy,
        total_sell=a.total_sell, unallocated_buy=a.unallocated_buy,
        unallocated_sell=a.unallocated_sell, imprecise_qty=a.imprecise_qty,
        single_qty=a.single_qty, reverted=a.reverted, skipped=a.skipped, reasons=a.reasons,
    )  # fmt: skip


def compute_day(
    repos: Repos, *, day: date, cal: TradingCalendar
) -> tuple[list[OptPosStrikeRow], list[OptPosDayRow], dict[str, Any]]:
    """그날 배분 → (행사가 행, 묶음 행, detail). 쓰지 않는다."""
    src = repos.opt_pos
    pairs = [*OPTION_SECTORS, (FUT_MARKET, FUT_SECTOR)]
    flows = src.flow_points(day, pairs, INVESTORS)
    by_pair: dict[tuple[str, str], dict[str, list[FlowPoint]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in flows:
        by_pair[(r.market_code, r.sector_code)][r.investor].append(_point(r))
    classes = sorted({cls for cls, _cp in OPTION_SECTORS.values()})
    quotes = [
        Quote(q.ts, q.mrkt_cls, q.expiry, q.strike, "C" if q.cp == "C" else "P", q.last)
        for q in src.chain_quotes(day, classes)
    ]
    closes: dict[tuple[str, str, float, str], ChainClose] = {
        (c.mrkt_cls, c.expiry, c.strike, c.cp): c for c in src.chain_close(day, classes)
    }
    open_at = cal.equity_bounds(day)[0]
    strikes: list[OptPosStrikeRow] = []
    days: list[OptPosDayRow] = []
    detail: dict[str, Any] = {
        "day": day.isoformat(),
        "quotes": len(quotes),
        "flow_rows": len(flows),
    }
    errors: dict[str, str] = {}
    for (m, s), (cls, cp) in OPTION_SECTORS.items():
        pts = by_pair.get((m, s))
        if not pts:
            continue
        try:
            res = allocate_day(pts, [q for q in quotes if q.mrkt_cls == cls], mrkt_cls=cls,
                               cp=cp, open_at=open_at, cal=cal)  # fmt: skip
        except Exception as e:  # 격리 — 이 묶음만 빈다(사유를 남긴다)
            errors[f"{m}:{s}"] = f"{type(e).__name__}: {e}"
            log_event(log, logging.WARNING, SERVICE, "opt_pos_pair_failed", pair=f"{m}:{s}",
                      error=errors[f"{m}:{s}"])  # fmt: skip
            continue
        for inv, a in res.items():
            days.append(_day_row(day, m, s, a))
            for st in a.strikes:
                c = closes.get((st.mrkt_cls, st.expiry, st.strike, st.cp))
                delta, d_src = None, None
                if c is not None and c.delta_self is not None:
                    delta, d_src = c.delta_self, "self"
                elif c is not None and c.delta_kis is not None:
                    delta, d_src = c.delta_kis, "kis"
                strikes.append(
                    OptPosStrikeRow(
                        trade_date=day, investor=inv, mrkt_cls=st.mrkt_cls, expiry=st.expiry,
                        strike=st.strike, cp=st.cp, buy_qty=st.buy, sell_qty=st.sell,
                        close_price=None if c is None else c.last,
                        board_oi=None if c is None else c.oi, delta=delta, delta_source=d_src,
                    )
                )  # fmt: skip
    fut_rows = [r for r in flows if (r.market_code, r.sector_code) == (FUT_MARKET, FUT_SECTOR)]
    price = src.futures_close(day) if fut_rows else None
    fut_end = session_bounds(day, "day")[1]  # 선물 정규장 끝 15:45
    by_inv: dict[str, list[FlowRow]] = defaultdict(list)
    for r in fut_rows:
        if r.quality != Quality.INVALID.value:
            by_inv[r.investor].append(r)
    for inv, rows in sorted(by_inv.items()):
        rows.sort(key=lambda r: r.ts)
        last = rows[-1]
        reasons: tuple[str, ...] = ()
        if last.ts < fut_end:
            reasons = (f"선물 마감(15:45) 전 마지막 슬롯 {last.ts.astimezone(KST):%H:%M}",)
        days.append(
            OptPosDayRow(
                trade_date=day, investor=inv, market_code=FUT_MARKET, sector_code=FUT_SECTOR,
                minutes=len(rows), gap_minutes=0, first_ts=rows[0].ts, last_ts=last.ts,
                futures_net=last.net_qty, fut_price=price, reasons=reasons,
            )
        )  # fmt: skip
    detail.update(strike_rows=len(strikes), day_rows=len(days))
    if errors:
        detail["pair_errors"] = dict(sorted(errors.items()))
    return strikes, days, detail


def _repos(ctx: JobContext) -> Repos:
    got = ctx.resources.get("repos")
    return got if got is not None else pg_repos(ctx.resource("connect"))


def daily(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.option_positions:daily`)."""
    day = date.fromisoformat(ctx.as_of[:10])
    cal: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    repos = _repos(ctx)
    strikes, days, detail = compute_day(repos, day=day, cal=cal)
    if not days:
        return JobResult(
            "not_ready", detail={**detail, "reason": "그날 옵션·선물 투자자 행이 없다"}
        )
    n = repos.opt_pos.replace_day(day, strikes, days, computed_at=ctx.now, loaded_by=LOADED_BY)
    status = "failed" if "pair_errors" in detail else "ok"
    return JobResult(status, rows=n, detail=detail)
