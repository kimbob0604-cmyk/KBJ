#!/usr/bin/env python3
"""
미국장 시스템 조합 탐색 — 국장 engine/search.py 와 **같은 방법, 같은 기준**을 미국
일봉에 건다.

  1. 최근 holdout_days 영업일을 봉인한다.
  2. 그 앞(탐색 구간)에서 조합마다 성적을 매겨, 전체·앞 절반·뒤 절반 모두 기준을
     넘는 것만 후보로 남긴다.
  3. 후보만 봉인 구간에서 한 번 잰다. 봉인 성적으로 후보를 다시 고르지 않는다.

조합 표(variants)는 국장 36개 격자에 11장 점검 시스템 넷(쿠퍼·라쉬키·볼린저 수축·
이격도)을 원문 값 그대로 더한 44개다. 대부분이 **원래 미국 규칙**이라 국장에서
빼 두었던 넷도 여기서는 다시 본다. 결과를 보기 전에 고정했다.

국장과 다른 점 (config/us.yaml `backtest`, 전부 새정의)
  대상   오늘 스크리너 시총 $300M↑ 종목(미래 정보·생존편향 — 결과를 좋게 만든다)
         + 그날 종가 $5↑ · 20일 평균 거래대금(추정) $20M↑
  비용   매도 0.15%(국내 증권사 수수료 두 번 몰아) + 편도 슬리피지 0.1%. 환전 제외
  게이트 SPY 종가 > 200일선. 국장 #42 의 지수 평균선 규칙을 미국 지수로 옮긴 것
  가격제한 없음
"""
import os
from datetime import date, timedelta

from ..engine import backtest as BT
from ..engine import search as SE
from . import db as DB
from . import pipeline as P
from . import sources as S

# 11장 점검에서 격자에 넣지 않았던 원문 시스템 — 원문 값 그대로 한 칸씩
EXTRA = ('cooper', 'raschke', 'bb_squeeze', 'moon_disparity')


def variants():
    base = SE.variants()
    out = []
    for gate in (False, True):
        out += [v for v in base if v['gate'] == gate]
        out += [SE._v(k, k, gate) for k in EXTRA]
    return out


# ─────────────────────────── 수집 ───────────────────────────
def fetch(conn, cfg, log=print):
    """유니버스 전 종목 + 게이트 지수의 긴 일봉을 받는다. 실패는 사유와 함께 돌려준다.

    이미 받아 둔 종목은 최근 40일만 겹쳐 받는다(같은 날 두 번 돌릴 때 빠르게).
    """
    bt = cfg['backtest']
    rows, drop = S.screener(cfg)
    keep = [r for r in rows if (r.get('mktcap') or 0) >= bt['min_mktcap_usd_now']]
    log(f'  스크리너 {len(rows)}종목 → 오늘 시총 ${bt["min_mktcap_usd_now"] / 1e6:,.0f}M↑ '
        f'{len(keep)}종목 (제외 {drop})')
    today = date.today()
    deep = (today - timedelta(days=int(365.25 * bt['fetch_years']))).isoformat()
    recent = (today - timedelta(days=40)).isoformat()
    have = DB.bar_counts(conn)
    fresh = [r['ticker'] for r in keep
             if not have.get(r['ticker']) or have[r['ticker']]['first'] > deep]
    known = [r['ticker'] for r in keep if r['ticker'] not in set(fresh)]
    log(f'  일봉 — 전 구간({deep}~) {len(fresh)}종목 · 최근 구간 {len(known)}종목')
    fails = []
    for group, start in ((fresh, deep), (known, recent)):
        if not group:
            continue
        got, bad = P.fetch_history(group, cfg, start, log)
        for t, bars in got.items():
            DB.put_px(conn, t, bars)
        conn.commit()
        fails += bad
    g = bt['gate']['symbol']
    try:
        bars = S.nasdaq_daily(g, start=deep, assetclass='etf')
        DB.put_px(conn, g, bars)
        conn.commit()
    except Exception as e:                            # noqa: BLE001
        fails.append((g, str(e)[:160]))
        log(f'  게이트 지수 {g} 실패 — 게이트 조합은 거래 0 이 된다: {e}')
    snaps = [dict(r, source='nasdaq') for r in keep]
    DB.put_snap(conn, today.isoformat(), snaps)
    firsts = sorted(v['first'] for t, v in DB.bar_counts(conn).items() if t != g)
    if firsts:
        log(f'  보유 일봉 첫 날짜 — 중앙값 {firsts[len(firsts) // 2]} · 가장 이른 {firsts[0]}')
    return fails, len(keep)


# ─────────────────────────── 탐색 ───────────────────────────
def load(conn, start, end, skip=()):
    """[start, end] 일봉 {ticker: BT.Series}. 거래량이 빈 봉은 0 으로 둔다.

    나스닥 히스토리는 거래량이 빠진 행을 섞어 준다. Series 는 빈 값이 있으면 봉을
    버리는데, 그러면 날짜가 비어 지표 창이 어긋난다. 탐색에서는 가격이 더 중요하므로
    거래량만 0 으로 채운다 — 그날 거래대금 평균이 낮게 잡히는 쪽(보수적)이다.
    """
    out = {}
    q = ('SELECT ticker, asof, open, high, low, close, volume FROM px '
         'WHERE asof>=? AND asof<=? ORDER BY ticker, asof')
    for r in conn.execute(q, (start, end)):
        if r['ticker'] in skip:
            continue
        s = out.get(r['ticker'])
        if s is None:
            s = out[r['ticker']] = BT.Series()
        d = dict(r)
        if d['volume'] is None:
            d['volume'] = 0.0
        s.add(d)
    return out


def run(conn, cfg, end=None, log=print):
    bt, se = cfg['backtest'], cfg['search']
    g = bt['gate']
    cal = [r[0] for r in conn.execute(
        'SELECT asof FROM px WHERE ticker=? ORDER BY asof', (g['symbol'],))]
    if not cal:
        # 게이트 지수가 없으면 달력도 없다. 종목 날짜의 합집합으로 대신한다.
        cal = [r[0] for r in conn.execute('SELECT DISTINCT asof FROM px ORDER BY asof')]
    end = end or cal[-1]
    days_all = [d for d in cal if d <= end]
    idx_rows = [(r[0], r[1]) for r in conn.execute(
        'SELECT asof, close FROM px WHERE ticker=? AND close IS NOT NULL ORDER BY asof',
        (g['symbol'],))]
    idx = ([d for d, _ in idx_rows], [c for _, c in idx_rows]) if idx_rows else None
    min_tv = bt['min_turnover_avg20_usd'] / 1e8          # Ind.tv20 은 종가×거래량/1e8

    def eligible(s, ind, i):
        return s.c[i] >= bt['min_price_usd'] and bool(ind.tv20[i]) and ind.tv20[i] >= min_tv

    def gate(code, day):
        return BT.gate_at(idx, day, g['ma'])

    return SE.evaluate(lambda start: load(conn, start, end, skip={g['symbol']}), days_all, end,
                       variants(), bt, se, se['screen'], lambda c, s: True, eligible, gate,
                       log)


def summary_lines(p, cfg):
    out = SE.lines(p, cfg['search']['screen'], cfg['search']['holdout'])
    bt = cfg['backtest']
    out.insert(1, f'미국 — 오늘 시총 ${bt["min_mktcap_usd_now"] / 1e6:,.0f}M↑ 현재 상장 종목 · '
                  f'그날 ${bt["min_price_usd"]:g}↑ · 20일 거래대금 ${bt["min_turnover_avg20_usd"] / 1e6:,.0f}M↑ · '
                  f'매도 {bt["cost_sell_pct"]}% + 편도 슬리피지 {bt["slippage_pct"]}% · '
                  f'게이트 {bt["gate"]["symbol"]} > {bt["gate"]["ma"]}일선')
    return out


DB_PATH = os.environ.get('US_BACKTEST_DB',
                         os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      'us_backtest.db'))
