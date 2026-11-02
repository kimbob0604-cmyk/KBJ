"""상대 시총·연도별 수익률 reader — `prv_market.global_daily`·`global_cap`(작업 global.daily)
+ 국내 스냅
시총. 계산은 `kbj.engines.market.relcap`. DB 만 읽는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.market.relcap import (
    FX_SYMBOL,
    INDICES,
    PAIRS,
    PERIOD_DAYS,
    annual_returns,
    daily_symbols,
    pair_view,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.diag import SeriesPoint
from kbj.services.api.models.relcap import AnnualIndexRow, RelCap, RelCapPair, YearReturn
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = ["relcap"]

Period = Literal["6M", "1Y", "2Y"]
NO_DATA: Final = "아직 없음 — global.daily 07:25(야후 일봉·월봉·시총)"
HISTORY_DAYS: Final = 760
CAP_FRESH_DAYS: Final = 10  # 이보다 오래된 해외 시총은 쓰지 않는다


def relcap(ctx: ReadContext, *, period: Period) -> Envelope[RelCap]:
    mkt = ctx.repos.market
    today = ctx.today()
    hist = mkt.global_closes(daily_symbols(), "1d", today - timedelta(days=HISTORY_DAYS))
    monthly = mkt.global_closes([s for s, _ in INDICES], "1mo", date(1900, 1, 1))
    if not hist and not monthly:
        raise NoData(NO_DATA)
    caps: dict[str, float] = {
        s: c.market_cap for s, c in mkt.global_caps(today - timedelta(days=CAP_FRESH_DAYS)).items()
    }
    snaps, _ = mkt.snapshot(today)
    for _, a, b in PAIRS:
        for sym, code, _cur in (*a, *b):
            if code and (s := snaps.get(code)) is not None and s.mktcap:
                caps[sym] = float(s.mktcap)
    fx = {cur: hist.get(sym, []) for cur, sym in FX_SYMBOL.items()}
    last_day = max((v[-1][0] for v in hist.values() if v), default=today)
    since = last_day - timedelta(days=PERIOD_DAYS[period])
    pairs = []
    for name, a, b in PAIRS:
        v = pair_view(name, a, b, hist, caps, fx, since)
        pairs.append(
            RelCapPair(
                name=v.name, legs_a=list(v.legs[0]), legs_b=list(v.legs[1]), latest=v.latest,
                date=v.date, high=v.high, low=v.low, pct_of_range=v.pct_of_range, error=v.error,
                series=[SeriesPoint(date=d, value=x) for d, x in v.series],
            )
        )  # fmt: skip
    years, rows = annual_returns(monthly)
    annual = [
        AnnualIndexRow(
            symbol=r.symbol, name=r.name, asof=r.asof, error=r.error,
            returns=[YearReturn(year=y, pct=p) for y, p in sorted(r.returns.items(), reverse=True)],
        )
        for r in rows
    ]  # fmt: skip
    notes: list[str] = []  # 정의는 패널 설명(def)에 — 여기에는 빠진 값만
    bad = [p.name for p in pairs if p.error]
    if bad:
        notes.append(f"값 없는 쌍 {len(bad)}개(카드에 사유) — {', '.join(bad)}")
    data = RelCap(period=period, pairs=pairs, years=years[:40], annual=annual)
    return envelope(
        data,
        source="Yahoo·KIS",
        as_of=close_as_of(last_day),
        quality=Quality.ESTIMATED if any(p.error for p in pairs) else Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )
