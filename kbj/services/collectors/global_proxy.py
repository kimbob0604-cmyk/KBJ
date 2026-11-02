"""`global.proxy` — 글로벌 프록시 시세(Hyperliquid 무기한 선물·야후 해외 시세)를 5분마다
`prv_market.global_quote` 에 쓴다(stock-dashboard 화면을 옮김, 2026-11-02).

- Hyperliquid: `metaAndAssetCtxs`(dex xyz) 한 건 — `engines.market.global_proxy.PERPS` 이름만.
- 야후: v8 chart range=1d 의 meta(regularMarketPrice·chartPreviousClose·currency) — 심볼마다 한 건,
  한 심볼 실패가 다른 심볼을 멈추지 않는다(429 는 남은 심볼을 멈춘다).
- ts = 발화 분(aware). 한 원천이 통째로 실패하면 그 데이터 키는 돌려주지 않는다
  (재시도·다음 발화가 본다).
  둘 다 실패면 failed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import GlobalQuote
from kbj.data.private.hyperliquid.client import ROW_SOURCE as HL_SOURCE
from kbj.data.private.hyperliquid.client import HyperliquidClient, HyperliquidError
from kbj.data.private.yahoo.client import ROW_SOURCE as YAHOO_SOURCE
from kbj.data.private.yahoo.client import YahooClient, YahooError, YahooThrottled
from kbj.engines.market.global_proxy import PERPS, yahoo_symbols
from kbj.services.collectors._p3 import repos
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = ["JOB", "parse_meta", "run"]

JOB: Final = "global.proxy"


def _settings(ctx: JobContext) -> Any:
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다")
    return ctx.settings


def _yahoo(ctx: JobContext) -> YahooClient:
    got = ctx.resources.get("yahoo")
    return got or YahooClient.from_settings(_settings(ctx), ctx.resource("redis"))


def _hl(ctx: JobContext) -> HyperliquidClient:
    got = ctx.resources.get("hyperliquid")
    return got or HyperliquidClient.from_settings(_settings(ctx), ctx.resource("redis"))


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


def parse_meta(payload: Any) -> tuple[float, float | None, str | None] | None:
    """v8 chart → (가격, 직전 세션 종가, 통화). 가격이 없으면 None."""
    try:
        meta = payload["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    price = _f(meta.get("regularMarketPrice"))
    if price is None:
        return None
    return price, _f(meta.get("chartPreviousClose")), meta.get("currency")


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.global_proxy:run`)."""
    ts = ctx.now.replace(second=0, microsecond=0)
    rows: list[GlobalQuote] = []
    failed: dict[str, str] = {}
    got_src: set[str] = set()

    try:
        perps = _hl(ctx).perp_ctx({p[0] for p in PERPS})
    except HyperliquidError as e:
        failed["hyperliquid"] = e.reason
        perps = {}
    for p in perps.values():
        rows.append(
            GlobalQuote(symbol=p.name, ts=ts, price=p.mark, prev=p.prev_day,
                        funding_1h=p.funding_1h, vol_24h=p.vol_24h, currency="USD",
                        source=HL_SOURCE, quality=Quality.ESTIMATED)
        )  # fmt: skip
    if perps:
        got_src.add("HYPERLIQUID")

    y = _yahoo(ctx)
    for sym in yahoo_symbols():
        try:
            meta = parse_meta(y.chart_payload(sym, "1d", "1d"))
        except YahooThrottled as e:
            failed[sym] = e.reason
            break
        except YahooError as e:
            failed[sym] = e.reason
            continue
        if meta is None:
            failed[sym] = "가격 없음"
            continue
        price, prev, cur = meta
        rows.append(
            GlobalQuote(symbol=sym, ts=ts, price=price, prev=prev, funding_1h=None,
                        vol_24h=None, currency=cur, source=YAHOO_SOURCE,
                        quality=Quality.ESTIMATED)
        )  # fmt: skip
        got_src.add("YAHOO")

    n = repos(ctx).market.put_global_quotes(rows, loaded_by=JOB, received_at=_now(ctx))
    detail: dict[str, object] = {"rows": n, "failed": dict(list(failed.items())[:10])}
    if not got_src:
        return JobResult("failed", rows=n, detail={**detail, "reason": "두 원천 모두 실패"})
    keys = [k for k in ctx.keys if k.source in got_src]
    return JobResult("ok", collected=tuple(keys), rows=n, detail=detail)


def _now(ctx: JobContext) -> datetime:
    return ctx.now
