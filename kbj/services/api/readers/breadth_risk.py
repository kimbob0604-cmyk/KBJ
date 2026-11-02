"""시장 체력 reader — ADR(등락비율)·지수 이격도(`kbj.engines.market.adr`). DB 만 읽는다.

종목 일봉(prv_market.daily_bar, KRX 확정 — krx.daily 08:05)으로 시장별 하루 상승·하락을 세고,
지수 일봉
('코스피'·'코스닥' — KRX 지수 이름 코드)으로 이격도를 낸다. 그날 값은 다음 날 아침에 들어온다.
"""

from __future__ import annotations

from datetime import date
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.market.adr import (
    ADR_COLD,
    ADR_HOT,
    ADR_WINDOW,
    DISPARITY_MAS,
    adr_series,
    adr_zone,
    daily_counts,
    disparity,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.diag import BreadthRisk, IndexDisparity, MarketAdr, SeriesPoint
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = ["INDEX_CODES", "PERIOD_DAYS", "breadth_risk"]

Period = Literal["3M", "6M", "1Y"]
PERIOD_DAYS: Final[dict[str, int]] = {"3M": 63, "6M": 126, "1Y": 250}
# KRX 지수 일별 코드(공백 뺀 이름 — krx.daily)를 먼저, 없으면 KIS 업종 코드(market.intraday 일봉)
INDEX_CODES: Final[dict[Literal["KOSPI", "KOSDAQ"], tuple[str, ...]]] = {
    "KOSPI": ("코스피", "0001"),
    "KOSDAQ": ("코스닥", "1001"),
}
NO_BARS: Final = "아직 없음 — krx.daily 08:05(종목·지수 일봉)"


def breadth_risk(ctx: ReadContext, *, period: Period, ma: int) -> Envelope[BreadthRisk]:
    n = PERIOD_DAYS[period]
    ma = ma if ma in DISPARITY_MAS else DISPARITY_MAS[-1]
    mkt = ctx.repos.market
    last = mkt.trading_days(ctx.today(), 1)
    if not last:
        raise NoData(NO_BARS)
    end = last[0]
    days_n = n + ADR_WINDOW
    tds = sorted(mkt.trading_days(end, days_n + 1))
    bars = mkt.series(None, end, days_n + 1)
    # 시장·종류 — 유니버스, 없으면 마지막 스냅(유니버스가 비었거나 늦게 찬 날)
    snaps, _ = mkt.snapshot(end)
    market_of: dict[str, str | None] = {c: s.market for c, s in snaps.items()}
    kind_of: dict[str, str | None] = {c: s.kind for c, s in snaps.items()}
    for u in mkt.universe(end):
        if u.market:
            market_of[u.code] = u.market
        if u.kind:
            kind_of[u.code] = u.kind
    counts = daily_counts(bars, market_of, tds, kind_of=kind_of)
    notes: list[str] = []
    adr_out: list[MarketAdr] = []
    for m in ("KOSPI", "KOSDAQ"):
        pts = adr_series(counts.get(m, {}))
        cut = pts[-n:]
        last_pt = next((p for p in reversed(pts) if p.adr is not None), None)
        today_pt = pts[-1] if pts else None
        adr_out.append(
            MarketAdr(
                market=m,
                date=last_pt.date if last_pt else None,
                latest=last_pt.adr if last_pt else None,
                zone=adr_zone(last_pt.adr) if last_pt else None,  # type: ignore[arg-type]
                up=today_pt.up if today_pt else None,
                down=today_pt.down if today_pt else None,
                flat=today_pt.flat if today_pt else None,
                series=[SeriesPoint(date=p.date, value=p.adr) for p in cut],
            )
        )
        if last_pt is None:
            notes.append(f"{m} ADR — 일봉이 {ADR_WINDOW}거래일을 못 채운다")
    idx = mkt.index_series(
        [c for codes in INDEX_CODES.values() for c in codes], end, n + max(DISPARITY_MAS)
    )
    disp_out: list[IndexDisparity] = []
    for m, codes in INDEX_CODES.items():
        rows = next((idx[c] for c in codes if idx.get(c)), [])
        series = disparity([(b.date, b.close) for b in rows], ma)
        valid = [(d, v) for d, v in series if v is not None]
        disp_out.append(
            IndexDisparity(
                market=m,
                date=valid[-1][0] if valid else None,
                close=rows[-1].close if rows else None,
                latest=valid[-1][1] if valid else None,
                series=[SeriesPoint(date=d, value=v) for d, v in series[-n:]],
            )
        )
        if not valid:
            notes.append(f"{m} 이격도 — 지수 일봉이 {ma}거래일을 못 채운다")
    if all(a.latest is None for a in adr_out) and all(d.latest is None for d in disp_out):
        raise NoData(NO_BARS)
    notes.append(
        f"ADR = {ADR_WINDOW}거래일 상승 종목 수 합 ÷ 하락 종목 수 합 × 100 "
        f"({ADR_HOT:.0f} 이상 과열·{ADR_COLD:.0f} 이하 바닥권) · 이격도 = 종가 ÷ {ma}일 평균 × 100"
    )
    asof: date = max(
        (d for d in [*(a.date for a in adr_out), *(x.date for x in disp_out)] if d), default=end
    )
    data = BreadthRisk(
        period=period,
        ma=ma,
        mas=list(DISPARITY_MAS),
        hot=ADR_HOT,
        cold=ADR_COLD,
        window=ADR_WINDOW,
        adr=adr_out,
        disparity=disp_out,
    )
    return envelope(
        data,
        source="KRX",
        as_of=close_as_of(asof),
        quality=Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )
