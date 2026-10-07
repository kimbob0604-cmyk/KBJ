#!/usr/bin/env python3
"""
합성 데이터 데모 — 네트워크 없이 수집 이후 전 구간을 돌린다.

목적은 두 가지다.
  1) 엔진→state JSON→렌더 배관이 실제로 이어지는지 확인
  2) 소스에 붙기 전에 화면을 눈으로 보기

여기 숫자는 전부 가짜다. 화면 상단에 그 사실을 띄운다.
"""
import os
import random
from datetime import date, timedelta

from ..engine import build as B
from ..engine import db as DB
from ..engine import newhigh as nh
from ..engine.config import load, themes as load_themes

SECTORS = ['전기가스업', '건설업', '기계', '전기전자', '화학', '운수장비', '운수창고',
           '음식료품', '유통업', '의약품', '금융업', '서비스업', '철강금속', '섬유의복']


def _names():
    """themes.yaml 시드 종목명을 그대로 유니버스로 쓴다. 테마 매핑이 실제로 붙는다."""
    out = []
    for t in load_themes().get('themes') or []:
        seeds = t.get('seeds') or {}
        if isinstance(seeds, list):
            seeds = {'기타': seeds}
        for names in seeds.values():
            out.extend(names or [])
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _series(rng, n, start_day, breakout):
    """랜덤워크. breakout 이면 마지막 날 직전 구간 최고가를 넘긴다."""
    px = 10000 * rng.uniform(0.3, 8)
    rows = []
    d = start_day
    for i in range(n):
        d += timedelta(days=1)
        if d.weekday() >= 5:
            d += timedelta(days=7 - d.weekday())
        px *= (1 + rng.gauss(0, 0.018))
        px = max(px, 500)
        hi = px * (1 + abs(rng.gauss(0, 0.012)))
        lo = px * (1 - abs(rng.gauss(0, 0.012)))
        rows.append(dict(asof=d.isoformat(), open=round(px, 0), high=round(hi, 0),
                         low=round(lo, 0), close=round(px, 0),
                         volume=round(rng.uniform(3e4, 4e6), 0)))
    if breakout:
        top = max(r['high'] for r in rows[:-1])
        last, prev = rows[-1], rows[-2]
        # 가격제한폭(+-30%)을 넘지 않게 막는다. 넘기면 수정주가 가드가 반응한다.
        target = min(top * rng.uniform(1.005, 1.10), prev['close'] * 1.29)
        last['close'] = round(max(target, prev['close'] * 0.9), 0)
        last['high'] = round(max(last['close'] * 1.02, top * 1.001), 0)
        last['open'] = round(min(last['close'], top * 0.99), 0)
        last['low'] = round(min(last['open'], top * 0.97), 0)
        last['volume'] = round(last['volume'] * rng.uniform(3, 40), 0)
    return rows


def _sample_draft(asof, cfg):
    """사실 팩 → 초안 형태의 견본. 서술이 아니라 수치 나열이다."""
    from ..engine import facts as F
    p = F.build(asof, cfg, log=lambda *a: None)
    L = [f'#{p["title_date"]}_신고가 및 등락률 Top 랭킹 코멘트', '',
         '> **이 페이지는 합성 데이터입니다.** 아래 초안은 LLM 이 쓴 것이 아니라',
         '> 사실 팩의 수치를 그대로 나열한 견본입니다. 실제 서술은',
         '> `python3 -m board.run --write` 가 Claude 를 호출해 만듭니다.', '']
    idx = (p['market'].get('indices') or {})
    for nm, v in idx.items():
        L.append(f'- {nm} {v.get("close")}({v.get("chg")})'
                 + (f' · {v["streak"]}' if v.get('streak') else ''))
    top = p.get('sectors') or []
    if top:
        L.append('- 업종별로 ' + ' > '.join(
            f'{s["name"]}({s.get("chg")})' for s in top[:3]) + ' 순')
    L.append('')
    for t in p.get('themes') or []:
        L.append(f'#{t["name"]}')
        stocks = t.get('stocks') or []
        L.append(f'- 테마 등락률 {t.get("chg")} · 거래대금 {t.get("turnover")} · '
                 f'신고가 {t.get("n_newhigh", 0)}종목')
        if stocks:
            L.append('- ' + ', '.join(
                f'{x["name"]}({x.get("chg")})' for x in stocks[:8]))
        if t.get('stages_new_today'):
            L.append('» 오늘 새로 반응한 단계: ' + '·'.join(t['stages_new_today']))
        for d in (t.get('detected') or [])[:3]:
            L.append(f'» {", ".join(d.get("stocks") or [])} — {d.get("fact", "")}')
        L.append('')
    if p.get('themes_table'):
        L.append('#기타 테마 (수치만)')
        L.append('')
        L.append('| 테마 | 등락률 | 신고가 | 거래대금 |')
        L.append('|---|---:|---:|---:|')
        for t in p['themes_table'][:12]:
            L.append(f'| {t["name"]} | {t.get("chg", "–")} | '
                     f'{t.get("n_newhigh", 0)} | {t.get("turnover", "–")} |')
        L.append('')
    L.append('---')
    L.append(f'기준일 {p["as_of"]} · {p["basis"]} 기준')
    return '\n'.join(L)


def _sample_news(asof, cfg):
    """뉴스 탭 확인용 합성 기사. 실호출은 run.py --news 가 한다.

    한 테마는 일부러 비워 둔다. '검색은 했는데 그날 기사가 없었다' 와 '못 받았다'
    를 화면이 구분해 적는지 봐야 한다 — 둘을 같게 그리면 리포트가 "재료가
    없었다"는 사실 주장을 하게 된다.
    """
    from ..ingest import news as N
    sec = B.read(asof, 'sectors.json') or {}
    uni = B.read(asof, 'universe.json') or {}
    picked = N.pick_themes(sec.get('themes'), cfg)
    out = []
    for i, t in enumerate(picked):
        names = N.theme_stock_names(t, uni.get('stocks') or [], 3)
        arts = [] if i == 2 else [
            dict(title=f'{q} 관련 합성 기사 제목', source='demo',
                 summary=f'{q} — 이 자리에 네이버 검색 API 의 요약문이 들어간다.',
                 url=f'https://news.example.com/demo/{i}/{j}',
                 outlet='news.example.com', date=asof, query=q)
            for j, q in enumerate(([t['name']] + names)[:4])]
        out.append(dict(
            name=t['name'], theme=t.get('theme'), axis=t.get('axis'),
            chg_pct=t.get('chg_pct'), n_newhigh=t.get('n_newhigh'),
            leader=t.get('leader'), queries=[t['name']] + names,
            articles=arts, n_found=len(arts)))
    return dict(source='demo', as_of=asof, generated_at='demo',
                themes=out, missing=[], n_queries=0)


def main(db_path, out_path, log=print, seed=20260826):
    cfg = load()
    rng = random.Random(seed)
    for suffix in ('', '-wal', '-shm'):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)
    conn = DB.connect(db_path)

    names = _names()
    # 실행 경로에 펀드 필터가 실제로 걸리는지 보려고 ETF·ETN 을 섞는다.
    # 2026-08-27 실데이터에서 이것들이 신고가 표를 점령했다.
    funds = ['KODEX CD금리액티브(합성)', 'TIGER KOFR금리액티브(합성)',
             'SOL 머니마켓액티브', 'N2 KIS CD금리투자 ETN', 'RISE 단기통안채']
    names = names + funds
    log(f'합성 유니버스 {len(names)}종목 (ETF·ETN {len(funds)} 포함) · 300영업일')
    start = date(2024, 1, 1)
    px, snaps, at, smap = [], [], [], []
    asof = None
    for i, nm in enumerate(names):
        code = f'{900000 + i:06d}'
        rows = _series(rng, 300, start, breakout=(rng.random() < 0.16))
        asof = rows[-1]['asof']
        px.extend((code, r['asof'], r['open'], r['high'], r['low'], r['close'],
                   r['volume'], 'demo') for r in rows)
        a = nh.roll_alltime(None, rows, cfg)
        # 절반은 사상 최고가를 과거에 둬서 역사적 신고가가 아무 데서나 뜨지 않게 한다
        if rng.random() < 0.5:
            k = rng.uniform(1.05, 2.0)
            a['hi'], a['cl'] = a['hi'] * k, a['cl'] * k
            a['prev_hi'], a['prev_cl'] = a['hi'], a['cl']
        a['n_days'] = 1200
        at.append((code, a['hi'], a['hi_date'], a['cl'], a['cl_date'],
                   a['prev_hi'], a['prev_cl'], a['first_date'],
                   a['last_date'], a['n_days'], 0, None, None, 'demo'))
        last = rows[-1]
        prev = rows[-2]
        vol = last['volume']
        smap.append((code, SECTORS[i % len(SECTORS)], 'demo', 'demo'))
        snaps.append((code, asof, nm, 'KOSPI' if i % 3 else 'KOSDAQ', last['close'],
                      round((last['close'] / prev['close'] - 1) * 100, 2), vol,
                      round(last['close'] * vol / 1e8, 1),
                      round(last['close'] * vol / 1e8 * rng.uniform(20, 400), 1),
                      1, 'demo'))

    from ..ingest import funds as FD
    keep = {c for c, n in zip([f'{900000+i:06d}' for i in range(len(names))], names)
            if not FD.looks_like_fund(n)}
    n_drop = len(names) - len(keep)
    px = [r for r in px if r[0] in keep]
    snaps = [r for r in snaps if r[0] in keep]
    at = [r for r in at if r[0] in keep]
    smap = [r for r in smap if r[0] in keep]
    log(f'  ETF·ETN {n_drop}종목 제외 → 주식 {len(keep)}종목')
    DB.log_step(conn, asof, 'funds_excluded', True,
                f'{n_drop}종목 제외 / 이름 판정 {n_drop}종목')
    conn.executemany('INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)', px)
    conn.executemany(
        'INSERT OR REPLACE INTO alltime(code,hi,hi_date,cl,cl_date,prev_hi,prev_cl,'
        'first_date,last_date,n_days,suspect,suspect_date,suspect_note,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', at)
    conn.executemany(
        'INSERT OR REPLACE INTO snap(code,asof,name,market,close,chg_pct,volume,turnover,'
        'mktcap,turnover_is_estimate,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)', snaps)
    conn.executemany('INSERT OR REPLACE INTO sector_map VALUES(?,?,?,?)', smap)
    conn.commit()
    conn.close()

    B.write(asof, 'market.json', dict(
        as_of=asof, generated_at='demo', indices={
            'KOSPI': dict(label='코스피', close=6808.21, chg_pct=0.97, asof=asof, source='demo'),
            'KOSDAQ': dict(label='코스닥', close=826.87, chg_pct=-0.03, asof=asof, source='demo')},
        fx=dict(value=1384.8, chg_pct=-0.42, asof=asof, source='demo'), flows=None,
        missing=['이 페이지는 합성 데이터입니다. 실제 시세가 아닙니다.',
                 '투자자별 수급(기관·외국인·개인·기타법인): 소스 미확보.']))

    B.run(db_path, asof=asof, cfg=cfg, log=log)

    # 코멘트 탭이 어떻게 보이는지 확인할 수 있게 사실 팩에서 결정론적으로 초안을
    # 하나 만든다. LLM 호출이 아니다 — 수치를 문장 형태로 옮기기만 한다.
    # 실제 서술은 run.py --write 가 Claude 를 불러서 만든다.
    B.write_text(asof, 'draft.md', _sample_draft(asof, cfg))

    # 뉴스 탭도 같은 이유로 채운다. 네트워크를 타지 않으므로 기사는 합성이고,
    # 링크는 example.com 이다. 화면 상단 '빠진 데이터'가 이 페이지 전체를
    # 합성이라고 이미 밝히고 있다.
    B.write(asof, 'news.json', _sample_news(asof, cfg))

    from ..web import app, payload, render, site
    html = render.from_state(asof)
    site.write_data(out_path, asof, payload.from_state(asof), log=log)
    p, n = site.publish(out_path, asof, html, log=log, index_html=app.build())
    log(f'데모 렌더 → {p}')
    # 데모는 docs/ 가 아니라 docs-demo/ 로 나간다. `--serve` 만 적으면
    # 실제 사이트가 열려서 방금 만든 화면이 아닌 것을 보게 된다.
    log(f'  보려면 → python3 -m board.run --serve --site {out_path}')
    return 0
