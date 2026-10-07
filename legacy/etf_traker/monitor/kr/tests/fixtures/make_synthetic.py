#!/usr/bin/env python3
"""
monitor/kr 합성 데이터 생성기 (KBJ P1, U3).

원본은 제3자 `rsm0kk/kr-sector` 배포본에서 뽑은 사전·골든을 썼다. 공개 레포에는
그것을 넣지 않으므로 같은 **형태**(키·필드·자료형·행 수 규모)의 합성 데이터를 만든다.
값은 전부 가상이다 — 종목코드·종목명·테마명·업종명 어느 것도 실제와 관계없다.

만드는 것 (시드 고정, 몇 번을 돌려도 같은 파일):

  knowledge/themes.json       가상 테마 198칸(상위 33 + 하위 165) + 가상 밸류체인 23칸
  knowledge/industries.json   가상 업종 58개, 종목 배정 중복 없음
  tests/fixtures/golden.json  합성 일봉·합성 지수·합성 수급을 **우리 엔진**(build.build_state)에
                              넣어 나온 값을 그대로 적은 회귀 골든 — 섹터 12개와 그 구성종목

골든의 의미가 바뀐다. 원본 골든은 '남의 배포본과 숫자가 같은가'(독립 구현 대조)였고,
이 골든은 '우리 엔진의 출력이 바뀌지 않았는가'(회귀)다. 자세한 것은
legacy/etf_traker/MIGRATION.md.

실행 (legacy/etf_traker 에서):

    python monitor/kr/tests/fixtures/make_synthetic.py

themes.json 을 먼저 쓰고 그 사전으로 골든을 만든다(build_state 가 사전을 읽는다).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KR = os.path.normpath(os.path.join(HERE, '..', '..'))
REPO = os.path.normpath(os.path.join(KR, '..', '..'))
sys.path.insert(0, REPO)

SEED = 20261007
N_CODES = 700             # 가상 종목 수
N_PARENTS = 33            # 상위 테마
N_CHILDREN = 5            # 상위 하나당 하위 테마
N_INDUSTRIES = 58
VC_SHAPE = (4, 4, 3, 3, 4)  # 밸류체인 상위 5개의 하위 개수 → 5 + 18 = 23칸
N_DAYS = 400
LAST_DAY = dt.date(2026, 9, 14)
GOLDEN_SECTORS = 12

THEMES_PATH = os.path.join(KR, 'knowledge', 'themes.json')
INDUSTRIES_PATH = os.path.join(KR, 'knowledge', 'industries.json')
GOLDEN_PATH = os.path.join(HERE, 'golden.json')

SOURCE = (f'합성 — monitor/kr/tests/fixtures/make_synthetic.py (seed={SEED}). '
          '가상 테마·업종·종목이며 실제 시장과 관계없다')


def codes():
    # 끝자리가 0 이어야 engine.classify_kind 가 우선주로 보지 않는다.
    return [f'{800000 + i * 10:06d}' for i in range(N_CODES)]


def name_of(i):
    return f'가상종목{i:04d}'


# ------------------------------------------------------------------ 사전

def make_themes(rnd, pool):
    sectors = []
    for p in range(1, N_PARENTS + 1):
        parent = f'가상테마{p:02d}'
        kids = [f'{parent} › 하위{c}' for c in range(1, N_CHILDREN + 1)]
        child_rows, union = [], set()
        for k, nm in enumerate(kids):
            # 크기를 고르게 두지 않는다 — 큰 섹터(골든용)와 2종목짜리 섹터가 섞인다.
            size = rnd.choice((2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20))
            mem = sorted(rnd.sample(pool, size))
            union |= set(mem)
            child_rows.append(dict(name=nm, label=f'하위{k + 1}', level=1, parent=parent,
                                   children=[], declared=len(mem), members=mem))
        sectors.append(dict(name=parent, label=f'{parent} · Synthetic Theme {p:02d}',
                            level=0, parent=None, children=kids,
                            declared=len(union), members=[]))
        sectors.extend(child_rows)

    vc = []
    for p, n in enumerate(VC_SHAPE, start=1):
        parent = f'가상체인{p}'
        kids = [f'{parent} › 단계{c}' for c in range(1, n + 1)]
        decl = [rnd.randint(2, 6) for _ in kids]
        vc.append(dict(name=parent, label=f'{parent} · Synthetic Chain {p}', level=0,
                       parent=None, children=kids, declared=sum(decl), members=[],
                       note='합성 밸류체인. 구성종목은 비워 둔다(원본 사전과 같은 모양).'))
        for c, (nm, d) in enumerate(zip(kids, decl), start=1):
            vc.append(dict(name=nm, label=f'단계{c}', level=1, parent=parent,
                           children=[], declared=d, members=[]))
    return dict(source=SOURCE, groups=[
        dict(key='테마', title='테마 · Themes', sectors=sectors),
        dict(key='밸류체인', title='밸류체인 · Global Value Chain', sectors=vc)])


def make_industries(rnd, pool):
    left = list(pool)
    rnd.shuffle(left)
    secs = []
    for i in range(1, N_INDUSTRIES + 1):
        size = rnd.randint(6, 14)
        mem, left = sorted(left[:size]), left[size:]
        nm = f'가상업종{i:02d}'
        secs.append(dict(name=nm, label=nm, members=mem))
    return dict(source=SOURCE,
                note='합성 업종 사전. 이름·배정 모두 가상. 한 종목은 한 업종에만 든다.',
                title='업종 · Industry (DART 표준산업분류)', sectors=secs)


# ------------------------------------------------------------------ 시세

def trading_days(n, last):
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime('%Y%m%d'))
        d -= dt.timedelta(days=1)
    return out[::-1]


def make_daily(rnd, pool, dates):
    """등락률 중심의 합성 일봉. 종목마다 추세·변동성·규모가 다르다."""
    prof = {}
    for j, c in enumerate(pool):
        mkt = 'KOSDAQ' if j % 3 == 0 else 'KOSPI'
        prof[c] = dict(
            n=name_of(j), m=mkt,
            drift=rnd.uniform(-0.15, 0.2), vol=rnd.uniform(1.0, 4.5),
            cap=rnd.choice((3, 5, 8, 12, 20, 40, 80, 150, 400, 1200, 5000)) * 10 ** 10,
            turn=rnd.choice((0.3, 0.8, 1.5, 3, 6, 12, 30, 80)) * 10 ** 9,
            price=rnd.choice((2000, 5000, 12000, 30000, 80000, 250000)),
            # 25종목마다 하나는 30일을 통째로 쉰다(결측 > GAP_LIMIT).
            halt=(j % 25 == 7))
    daily = {}
    n = len(dates)
    for i, d in enumerate(dates):
        rows = []
        for c in pool:
            p = prof[c]
            if p['halt'] and n - 90 <= i < n - 60:
                continue
            f = round(max(-29.9, min(29.9, rnd.gauss(p['drift'], p['vol']))), 2)
            p['price'] = max(100, int(p['price'] * (1 + f / 100.0)))
            spike = rnd.random()
            mult = 4.0 if spike > 0.985 else (2.5 if spike > 0.96 else rnd.uniform(0.6, 1.4))
            rows.append(dict(c=c, n=p['n'], m=p['m'], p=p['price'], f=f,
                             v=rnd.randint(10 ** 3, 10 ** 6),
                             t=float(round(p['turn'] * mult)),
                             k=float(p['cap'] * (1 + i / n * p['drift'])),
                             s=10 ** 7))
        daily[d] = rows
    return daily


def make_indices(rnd, dates):
    out = {}
    for nm, lvl, vol in (('코스피', 2600.0, 0.9), ('코스닥', 820.0, 1.3)):
        rows, x = [], lvl
        for d in dates:
            x *= 1 + rnd.gauss(0.03, vol) / 100.0
            rows.append(dict(d=d, c=round(x, 2)))
        out[nm] = rows
    return out


def make_flows(rnd, pool, dates):
    """{code: {날짜: {f,o,p}}} — 억원. 최근 25거래일."""
    out = {}
    for c in pool:
        if rnd.random() < 0.1:
            continue          # 수급을 못 받은 종목도 섞는다
        out[c] = {d: dict(f=round(rnd.uniform(-60, 60), 2), o=round(rnd.uniform(-40, 40), 2),
                          p=round(rnd.uniform(-80, 80), 2)) for d in dates[-25:]}
    return out


# ------------------------------------------------------------------ 골든

STOCK_KEYS = ('c', 'n', 'm', 'p', 'f', 'v', 'k', 'r', 'sec', 'rs', 'rsp', 't', 'tl', 'sg',
              'dd', 'fl')


def make_golden(state):
    theme = next(g for g in state['groups'] if g['key'] == '테마')
    cand = [s for s in theme['sectors'] if s['level'] == 1 and s['count']]
    cand.sort(key=lambda s: (-s['count'], s['name']))
    picked = sorted(cand[:GOLDEN_SECTORS], key=lambda s: s['name'])
    stocks = {}
    for s in picked:
        for c in s['members']:
            st = state['stocks'][c]
            stocks[c] = {k: st.get(k) for k in STOCK_KEYS}
    return dict(
        note=('합성 회귀 골든 — make_synthetic.py 가 합성 입력을 우리 엔진(build_state)에 '
              f'넣어 낸 값(seed={SEED}). 배포본 재현이 아니라 회귀 대조용.'),
        periods=state['periods'],
        stocks=dict(sorted(stocks.items())),
        sectors=[dict(name=s['name'], count=s['count'], stats=s['stats']) for s in picked],
        sectorExtra={s['name']: dict(momentum=s['momentum'], rot=s['rot'],
                                     ddStat=s['ddStat'], rs=s['rs'], rsTop=s['rsTop'],
                                     surge=s['surge'], members=s['members'],
                                     flow=s['flow'])
                     for s in picked})


def dump(obj, path, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.write('\n')


def main():
    pool = codes()
    rnd = random.Random(SEED)
    dump(make_themes(rnd, pool), THEMES_PATH, indent=1)
    dump(make_industries(rnd, pool), INDUSTRIES_PATH, indent=1)

    from monitor.kr import build as B   # 사전을 쓴 뒤에 불러야 새 사전을 읽는다

    dates = trading_days(N_DAYS, LAST_DAY)
    daily = make_daily(rnd, pool, dates)
    state = B.build_state(daily, failed=[], index_rows=make_indices(rnd, dates),
                          flow_by_code=make_flows(rnd, pool, dates))
    g = make_golden(state)
    dump(g, GOLDEN_PATH)
    n_rs = sum(1 for v in g['stocks'].values() if v.get('rs') is not None)
    print(f'themes → {THEMES_PATH}\nindustries → {INDUSTRIES_PATH}\n'
          f'golden → {GOLDEN_PATH} · 섹터 {len(g["sectors"])} · 종목 {len(g["stocks"])} '
          f'(rs 있음 {n_rs})')


if __name__ == '__main__':
    main()
