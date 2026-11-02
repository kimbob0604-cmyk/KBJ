"""`global.daily` — 상대 시총·연도별 수익률 원자료(야후)를 하루 한 번 받는다
(stock-dashboard 화면을 옮김,
2026-11-02).

- 일봉 2년: 상대 시총 종목(국내 .KS·미국·일본)·환율(KRW=X·JPY=X) → `prv_market.global_daily`(1d).
- 월봉 전 기간: 주요 지수(^GSPC·^IXIC·^KS11·^KQ11) → 같은 표(1mo). period1=0 으로 부른다
  (range=max 묶음 회피).
- 해외 종목 현재 시총: 야후 quote(쿠키 + crumb) → `prv_market.global_cap`(as_of = 실행 날짜, KST).
  국내 시총은 스냅을 쓰므로 받지 않는다.
- 진행 중인 봉(정규장 중 마지막 봉 — estimated)은 쓰지 않는다.
  한 심볼 실패가 다른 심볼을 멈추지 않는다
  (429 는 남은 심볼을 멈춘다). 일봉·월봉을 하나도 못 받으면 failed, quote 만 실패면 그 키만 뺀다.
"""

from __future__ import annotations

from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import GlobalCap, GlobalClose
from kbj.core.time import KST
from kbj.data.private.yahoo.client import (
    ROW_SOURCE,
    YahooClient,
    YahooError,
    YahooThrottled,
    to_bars,
)
from kbj.engines.market.relcap import INDICES, PAIRS, daily_symbols
from kbj.services.collectors._p3 import repos
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = ["JOB", "foreign_symbols", "run"]

JOB: Final = "global.daily"


def foreign_symbols() -> tuple[str, ...]:
    """국내 코드가 없는(= 야후 quote 로 현재 시총을 받는) 심볼."""
    return tuple(sorted({s for _, a, b in PAIRS for s, code, _ in (*a, *b) if code is None}))


def _yahoo(ctx: JobContext) -> YahooClient:
    got = ctx.resources.get("yahoo")
    if got is not None:
        return got
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다")
    return YahooClient.from_settings(ctx.settings, ctx.resource("redis"))


def _closes(payload: Any, sym: str, interval: str) -> list[GlobalClose]:
    cur = ((payload or {}).get("chart") or {}).get("result") or [{}]
    currency = (cur[0].get("meta") or {}).get("currency") if isinstance(cur[0], dict) else None
    return [
        GlobalClose(symbol=sym, interval=interval, date=b.date, close=b.close,
                    currency=currency if isinstance(currency, str) else None, source=ROW_SOURCE,
                    quality=Quality.OK)
        for b in to_bars(payload, sym)
        if b.close is not None and b.close > 0 and b.quality is Quality.OK
    ]  # fmt: skip


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.global_daily:run`)."""
    y = _yahoo(ctx)
    rows: list[GlobalClose] = []
    failed: dict[str, str] = {}
    plan = [(s, "1d") for s in daily_symbols()] + [(s, "1mo") for s, _ in INDICES]
    for sym, interval in plan:
        try:
            if interval == "1d":
                payload = y.chart_payload(sym, "2y", "1d")
            else:
                payload = y.chart_payload(sym, "max", "1mo", span_all=True)
        except YahooThrottled as e:
            failed[sym] = e.reason
            break
        except YahooError as e:
            failed[sym] = e.reason
            continue
        got = _closes(payload, sym, interval)
        if not got:
            failed[sym] = "봉 없음"
        rows.extend(got)

    caps: list[GlobalCap] = []
    day = ctx.now.astimezone(KST).date()
    try:
        for sym, (cap, cur) in y.quote_caps(foreign_symbols()).items():
            caps.append(GlobalCap(symbol=sym, as_of=day, market_cap=cap, currency=cur,
                                  source=ROW_SOURCE, quality=Quality.OK))  # fmt: skip
    except YahooError as e:
        failed["quote"] = e.reason

    r = repos(ctx).market
    n = r.put_global_closes(rows, loaded_by=JOB, received_at=ctx.now)
    n += r.put_global_caps(caps, loaded_by=JOB, received_at=ctx.now)
    detail: dict[str, object] = {
        "closes": len(rows),
        "caps": len(caps),
        "failed": dict(list(failed.items())[:12]),
    }
    if not rows:
        return JobResult("failed", rows=n, detail={**detail, "reason": "야후 봉을 하나도 못 받음"})
    keys = [k for k in ctx.keys if k.dataset != "quote_caps" or caps]
    return JobResult("ok", collected=tuple(keys), rows=n, detail=detail)
