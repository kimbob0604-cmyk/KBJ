#!/usr/bin/env python3
"""
합성 데이터로 미국장 보드를 끝까지 돌린다. 네트워크가 필요 없다.

**숫자는 전부 가짜다.** 배관과 표 모양을 확인하는 용도이고, 브리프 맨 위에
그렇게 적는다. 시드를 고정해 같은 입력이면 같은 표가 나온다 — 화면을 고칠 때
무엇이 바뀌었는지 보려면 이 성질이 있어야 한다.
"""
import random
from datetime import date, timedelta

SECTORS = [
    ('소프트웨어·인터넷', 'Computer Software: Prepackaged Software', 'Technology'),
    ('반도체', 'Semiconductors', 'Technology'),
    ('헬스케어', 'Major Pharmaceuticals', 'Health Care'),
    ('운송', 'Marine Transportation', 'Transportation'),
    ('보험', 'Property-Casualty Insurers', 'Finance'),
    ('통신', 'Telecommunications Equipment', 'Telecommunications'),
    ('금융', 'Major Banks', 'Finance'),
    ('에너지', 'Oil & Gas Production', 'Energy'),
    ('소비재', 'Catalog/Specialty Distribution', 'Consumer Discretionary'),
    ('산업재', 'Industrial Machinery/Components', 'Industrials'),
    ('유틸리티', 'Electric Utilities: Central', 'Public Utilities'),
    ('부동산', 'Real Estate Investment Trusts', 'Real Estate'),
]

DAYS = 300


def _bars(rng, n, drift, shock=0.0, breakout=False):
    """랜덤워크 일봉. breakout 이면 마지막 날 창 전체를 넘긴다."""
    px = 50 * (1 + rng.random())
    out = []
    d0 = date(2026, 9, 14) - timedelta(days=int(n * 1.45))
    day = d0
    for i in range(n):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        px = max(1.0, px * (1 + rng.gauss(drift, 0.018)))
        hi = px * (1 + abs(rng.gauss(0, 0.006)))
        lo = px * (1 - abs(rng.gauss(0, 0.006)))
        out.append(dict(asof=day.isoformat(), open=px, high=hi, low=lo, close=px,
                        volume=rng.randint(300_000, 9_000_000), source='demo'))
        day += timedelta(days=1)
    if breakout:
        top = max(r['high'] for r in out[:-1])
        last = out[-1]
        last['close'] = top * (1 + 0.005 + abs(rng.gauss(0, 0.04)))
        last['high'] = last['close'] * 1.01
        last['volume'] = int(last['volume'] * (1.5 + rng.random() * 3))
    elif shock:
        last = out[-1]
        last['close'] = last['close'] * (1 + shock)
        last['high'] = max(last['high'], last['close'])
        last['low'] = min(last['low'], last['close'])
    return out


def make(n_tickers=240, seed=20260914):
    """(series, snaps, asof) — DB 없이 build 에 그대로 넣을 수 있는 모양."""
    rng = random.Random(seed)
    series, snaps = {}, {}
    for i in range(n_tickers):
        sec, ind, sec_raw = SECTORS[i % len(SECTORS)]
        t = f'D{i:03d}'
        roll = rng.random()
        breakout = roll < 0.18
        shock = 0.0
        if not breakout:
            shock = rng.gauss(0, 0.02) + (-0.09 if roll > 0.93 else 0.0)
        bars = _bars(rng, DAYS, drift=rng.gauss(0.0007, 0.0006),
                     shock=shock, breakout=breakout)
        series[t] = bars
        last = bars[-1]
        prev = bars[-2]['close']
        cap_roll = rng.random()
        mktcap = (2e11 if cap_roll > 0.93 else
                  2e10 if cap_roll > 0.7 else
                  4e9 if cap_roll > 0.35 else 9e8)
        snaps[t] = dict(ticker=t, name=f'Demo {t} Inc.', close=last['close'],
                        chg_pct=round((last['close'] / prev - 1) * 100, 2),
                        volume=last['volume'],
                        turnover=last['close'] * last['volume'],
                        mktcap=mktcap, sector=sec, industry=ind,
                        sector_raw=sec_raw, industry_raw=ind,
                        exchange='NASDAQ', source='demo')
    # 실제 경로와 같은 분류기를 통과시킨다. 산업 라벨(Cybersecurity·리츠)이
    # 붙는 자리가 데모에서만 다르면 화면을 고칠 때 헛짚는다.
    from . import sectors as SEC
    SEC.apply(list(snaps.values()))
    asof = series[next(iter(series))][-1]['asof']
    return series, snaps, asof
