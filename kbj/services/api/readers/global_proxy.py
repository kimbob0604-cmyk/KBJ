"""글로벌 프록시 reader — `prv_market.global_quote`(작업 global.proxy, 5분) + 국내 종가로
화면 값을 만든다.

계산은 `kbj.engines.market.global_proxy.build`. 국내 종가는 마지막 스냅(마감 수집 15:35 · KRX 일별),
코스피200 은 지수 일봉('코스피200' — KRX 이름 코드, 없으면 KIS '2001'). DB 만 읽는다.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Final

from kbj.core.quality import Quality
from kbj.engines.market.global_proxy import LISTINGS, PERPS, ProxyRow, build
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.market import GlobalProxy, ProxyQuote
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = ["FRESH", "K200_CODES", "global_proxy"]

FRESH: Final = timedelta(hours=3)  # 이보다 오래된 시세는 쓰지 않는다(수집이 멈췄다)
K200_CODES: Final = ("코스피200", "2001")
NO_QUOTES: Final = "최근 3시간 시세 없음 — global.proxy 5분(Hyperliquid·야후)"


def _codes() -> set[str]:
    codes = {ref for _, _, (k, ref) in PERPS if k == "stock"}
    return codes | {code for _, _, code, _, _ in LISTINGS}


def _rows(rows: list[ProxyRow]) -> list[ProxyQuote]:
    return [ProxyQuote(**asdict(r)) for r in rows]


def global_proxy(ctx: ReadContext) -> Envelope[GlobalProxy]:
    now = ctx.now()
    mkt = ctx.repos.market
    quotes = mkt.global_quotes(now - FRESH)
    if not quotes:
        raise NoData(NO_QUOTES)
    snaps, snap_day = mkt.snapshot(ctx.today())
    domestic = {c: s.close for c in _codes() if (s := snaps.get(c)) is not None and s.close}
    index_close: dict[str, float] = {}
    idx = mkt.index_series(list(K200_CODES), ctx.today(), 5)
    bars = next((idx[c] for c in K200_CODES if idx.get(c)), [])
    if bars and bars[-1].close:
        index_close["KOSPI200"] = bars[-1].close
    view = build(quotes, domestic, index_close)
    notes: list[str] = []
    if not domestic:
        notes.append("국내 종가 없음 — 종가 대비·괴리율은 비운다")
    if "KOSPI200" not in index_close:
        notes.append("코스피200 종가 없음(krx.daily 08:05)")
    ts = max(q.ts for q in quotes.values())
    data = GlobalProxy(
        ts=ts,
        base_date=snap_day,
        usdkrw=view.usdkrw,
        perps=_rows(view.perps),
        listings=_rows(view.listings),
        futures=_rows(view.futures),
        macro=_rows(view.macro),
        us_etfs=_rows(view.us_etfs),
    )
    return envelope(
        data,
        source="Hyperliquid·Yahoo",
        as_of=ts,
        quality=Quality.ESTIMATED,
        notes=notes,
        generated_at=now,
    )
