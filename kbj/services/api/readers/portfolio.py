"""포트폴리오 리더 — `/api/portfolio`·`/api/portfolio/correlation`(docs/p8_design.md §4.7·§5 —
로그인). 본문은 JNL(웨이브 2): 매매 기록 → 이동평균 단가 포지션·실현/평가 손익(읽을 때 계산 —
`kbj.engines.journal.portfolio`), 상관은 원장 일간 수익률 피어슨(`kbj.engines.journal.correlation`).

- 매매가 없으면 200 + 빈 포지션(안내는 notes — 화면은 입력 칸을 그대로 보인다).
- 평가 손익은 최신 확정 일봉 종가(최근 `CLOSE_LOOKBACK` 거래일 안의 마지막 값 — 기준일·품질 표시).
  보유 종목 중 하나라도 종가가 없으면 평가 합은 None(지어내지 않는다).
- 상관: 관심종목(main) 또는 보유 종목, 앞에서 `MAX_CODES`(50)개. 2종목 미만이면 404.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.journal.correlation import MAX_CODES, corr_matrix
from kbj.engines.journal.portfolio import ZERO, monthly, replay, to_won, unrealized
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.journal import Correlation, MonthlyOut, Portfolio, PositionOut
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst

__all__ = ["CLOSE_LOOKBACK", "NO_CORR", "NO_PORTFOLIO", "correlation", "portfolio"]

NO_PORTFOLIO: Final = "아직 없음 — 매매 기록 없음(페이지 12 에서 입력)"
NO_CORR: Final = "아직 없음 — 상관을 볼 종목이 2개 미만(관심종목·보유)"
CLOSE_LOOKBACK: Final = 5
SOURCE: Final = "KBJ"


def portfolio(ctx: ReadContext) -> Envelope[Portfolio]:
    rows = ctx.repos.journal.trades()
    now = ctx.now()
    if not rows:
        empty = Portfolio(
            positions=[], realized_total=0, unrealized_total=0, monthly=[], trades_n=0
        )
        return envelope(empty, source=SOURCE, as_of=now, quality=Quality.OK,
                        notes=[NO_PORTFOLIO], generated_at=now)  # fmt: skip
    today = ctx.today()
    book = replay(rows)
    codes = sorted(book.lots)
    first = min(t.trade_date for t in rows)
    n_hist = min(3000, (today - first).days + 40)
    hist = ctx.repos.market.series(codes, today, n_hist)
    uni = {u.code: u.name for u in ctx.repos.market.universe(today)}
    names = {t.code: t.name_at_trade for t in rows if t.name_at_trade}
    positions: list[PositionOut] = []
    quals: list[Quality | None] = []
    srcs: set[str] = set()
    last_days: list[date] = []
    u_total: Decimal | None = ZERO
    missing = 0
    for code in codes:
        lot = book.lots[code]
        bars = [b for b in hist.get(code, []) if b.close is not None][-CLOSE_LOOKBACK:]
        last = bars[-1] if bars else None
        close = None if last is None else last.close
        u = unrealized(lot, close) if lot.qty > 0 else None
        if lot.qty > 0:
            if u is None:
                missing += 1
                u_total = None
            elif u_total is not None:
                u_total += u
            if last is not None:
                quals.append(last.quality)
                srcs.add(last.source)
                last_days.append(last.date)
        avg = lot.avg_cost
        positions.append(
            PositionOut(
                code=code,
                name=uni.get(code) or names.get(code),
                qty=lot.qty,
                avg_cost=None if avg is None else float(avg),
                last_close=close if lot.qty > 0 else None,
                close_date=last.date if last is not None and lot.qty > 0 else None,
                market_value=None if close is None or lot.qty == 0 else round(close * lot.qty),
                unrealized=None if u is None else to_won(u),
                unrealized_pct=(None if u is None or lot.cost == 0 else float(u / lot.cost * 100)),
                realized=to_won(lot.realized),
                quality=last.quality if last is not None and lot.qty > 0 else None,
            )
        )
    positions.sort(key=lambda p: (p.qty == 0, p.code))
    closes = {c: [(b.date, b.close) for b in hist.get(c, []) if b.close is not None] for c in codes}
    months = [
        MonthlyOut(
            month=m.month,
            realized=to_won(m.realized),
            unrealized_change=None if m.unrealized_change is None else to_won(m.unrealized_change),
            ret_pct=m.ret_pct,
        )
        for m in monthly(rows, closes, asof=today)
    ]
    notes = ["월별 수익률 분모 = 월초 보유 원가 + 그 달 매수 원가 [확인 필요 — 현금 원장 없음]"]
    if missing:
        notes.append(f"종가 없는 보유 종목 {missing}개 — 평가 합 없음")
    data = Portfolio(
        positions=positions,
        realized_total=to_won(book.realized_total),
        unrealized_total=None if u_total is None else to_won(u_total),
        monthly=months,
        trades_n=len(rows),
    )
    return envelope(
        data,
        source=label(srcs | {SOURCE}),
        as_of=close_as_of(max(last_days)) if last_days else now,
        quality=worst(quals) or Quality.OK,
        notes=notes,
        generated_at=now,
    )


def correlation(
    ctx: ReadContext, *, set_name: Literal["watchlist", "holdings"], days: Literal[60, 120]
) -> Envelope[Correlation]:
    today = ctx.today()
    if set_name == "watchlist":
        codes = [w.code for w in ctx.repos.journal.watchlist("main")]
    else:
        rows = ctx.repos.journal.trades()
        codes = sorted(c for c, lot in replay(rows).lots.items() if lot.qty > 0) if rows else []
    notes: list[str] = []
    if len(codes) > MAX_CODES:
        notes.append(f"앞 {MAX_CODES}종목만(전체 {len(codes)})")
        codes = codes[:MAX_CODES]
    if len(codes) < 2:
        raise NoData(NO_CORR)
    cal = sorted(ctx.repos.market.trading_days(today, days + 1))
    series = ctx.repos.market.series(codes, today, days + 1)
    closes = {c: {b.date: b.close for b in series.get(c, [])} for c in codes}
    matrix = corr_matrix(closes, cal, codes, window=days)
    uni = {u.code: u.name for u in ctx.repos.market.universe(today)}
    short = sum(1 for i in range(len(codes)) if matrix[i][i] is None)
    if short:
        notes.append(f"수익률 40일 미만 종목 {short}개 — 값 없음")
    now = ctx.now()
    bars = [b for bs in series.values() for b in bs]
    return envelope(
        Correlation(
            set=set_name,
            window_days=days,
            codes=codes,
            names=[uni.get(c) for c in codes],
            matrix=matrix,
        ),
        source=label({b.source for b in bars}),
        as_of=close_as_of(cal[-1]) if cal else now,
        quality=worst(b.quality for b in bars),
        notes=notes,
        generated_at=now,
    )
