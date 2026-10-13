#!/usr/bin/env python3
"""
미국장 수집기 — KBJ P5 묶음 US 이후 **shim** 이다(docs/p5_design.md §1.6·§1.9).

정본은 kbj 어댑터다(같은 수집을 두 벌 두지 않는다 — CLAUDE.md 3장):
  파서·클라이언트  `kbj.data.private.nasdaq`(스크리너·히스토리)·`kbj.data.private.stooq`(CSV)·
                  `kbj.data.private.yahoo`(차트 — FND)
  출처 폴백·점검    `kbj.data.private.nasdaq.history`(`history`·`adjust_probe`)

여기 남은 것은 예전 호출 모양(`sess` 인자·`start` 문자열)을 kbj 클라이언트로 잇는 얇은 함수와
`probe`(--us-check 출력)뿐이다. 리미터는 이 프로세스 안에서만 도는 `LocalRateLimiter`(legacy 에는
Redis 가 없다). `sess` 는 받기만 하고 쓰지 않는다.
"""
import functools
from datetime import date, timedelta

from kbj.data.private.nasdaq.history import SPLIT_PROBE  # noqa: F401
from kbj.data.private.nasdaq.history import adjust_probe as _kbj_adjust_probe
from kbj.data.private.nasdaq.history import history as _kbj_history
from kbj.data.private.nasdaq.client import NASDAQ_REFERER, NasdaqClient  # noqa: F401
from kbj.data.private.nasdaq.client import is_fund as _is_fund  # noqa: F401
from kbj.data.private.nasdaq.client import (  # noqa: F401
    nasdaq_symbol,
    nasdaq_symbol_forms,
    parse_nasdaq_history,
    parse_screener,
)
from kbj.data.private.nasdaq.client import num as _num  # noqa: F401
from kbj.data.private.stooq.client import StooqClient, parse_stooq, stooq_symbol  # noqa: F401
from kbj.data.private.yahoo.client import YahooClient, parse_yahoo  # noqa: F401
from kbj.data.ratelimit import LocalRateLimiter, RateLimitConfig

from ..ingest import http as H


@functools.lru_cache(maxsize=1)
def _nasdaq():
    return NasdaqClient(LocalRateLimiter(RateLimitConfig(rate=2.0)))


@functools.lru_cache(maxsize=1)
def _stooq():
    return StooqClient(LocalRateLimiter(RateLimitConfig(rate=1.0)))


@functools.lru_cache(maxsize=1)
def _yahoo():
    return YahooClient(LocalRateLimiter(RateLimitConfig(rate=1.0)))


def _day(v, default):
    return date.fromisoformat(v) if v else default


def screener(cfg, sess=None, exchanges=None):
    """전 종목 스냅샷(kbj `NasdaqClient.screener`)."""
    return _nasdaq().screener(cfg, exchanges)


def nasdaq_daily(ticker, sess=None, start=None, end=None, assetclass='stocks'):
    today = date.today()
    return _nasdaq().daily(ticker, _day(start, today - timedelta(days=760)), _day(end, today),
                           assetclass)


def stooq_daily(ticker, sess=None, start=None):
    return _stooq().daily(ticker, start)


def yahoo_daily(ticker, sess=None, rng='2y'):
    return parse_yahoo(_yahoo().chart_payload(ticker, rng))


def history(ticker, cfg, sess=None, start=None):
    """설정이 고른 어댑터로 일봉을 받는다 — 실패하면 kbj 폴백 순서(nasdaq → stooq).

    `yahoo` 는 kbj 폴백에 없다(지수 전용) — 예전처럼 설정이 고르면 그것만 부른다."""
    if cfg['sources'].get('history', 'nasdaq') == 'yahoo':
        return yahoo_daily(ticker, sess)
    today = date.today()
    return _kbj_history(ticker, cfg, nasdaq=_nasdaq(), stooq=_stooq(),
                        start=_day(start, today - timedelta(days=760)), end=today)


def adjust_probe(cfg, sess=None):
    """일봉이 수정주가인지 본다(kbj `adjust_probe` — 사실만)."""
    return _kbj_adjust_probe(lambda t: history(t, cfg, sess))


def probe(cfg):
    """--us-check 가 부른다. 무엇이 되고 무엇이 안 되는지 한 줄씩."""
    out = []
    try:
        rows, drop = screener(cfg, exchanges=['NASDAQ'])
        mc = sum(1 for r in rows if r.get('mktcap'))
        out.append(('나스닥 스크리너', bool(rows),
                    f'{len(rows)}종목 · 시총 있는 것 {mc} · 제외 {drop}'))
    except Exception as e:                       # noqa: BLE001
        out.append(('나스닥 스크리너', False, H.scrub(e)[:200]))
    for name, fn in (('nasdaq 일봉', lambda: nasdaq_daily('AAPL')),
                     ('stooq 일봉', lambda: stooq_daily('AAPL')),
                     ('yahoo 일봉', lambda: yahoo_daily('AAPL', rng='1mo'))):
        try:
            bars = fn()
            last = bars[-1] if bars else None
            out.append((name, bool(bars),
                        f'{len(bars)}행 · 마지막 {last["asof"] if last else "-"} '
                        f'{last["close"] if last else "-"}'))
        except Exception as e:                   # noqa: BLE001
            out.append((name, False, H.scrub(e)[:200]))
    out.extend(adjust_probe(cfg))
    return out
