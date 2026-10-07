#!/usr/bin/env python3
"""
국장 섹터 모니터 — 조립.

  수집(cache/daily) → 계산(engine) → state → template.html 치환 → docs/kr/

  python -m monitor.kr.build                 수집 포함 전체
  python -m monitor.kr.build --no-fetch      캐시만 써서 다시 조립
  python -m monitor.kr.build --days 300      받아 둘 영업일 수

원본 템플릿은 rsm0kk/kr-sector 배포본에서 데이터만 자리표시자로 바꾼 것이었다.
[KBJ P1] 공개 레포에는 그것을 넣지 않고(U3) state 계약만 따라 직접 쓴 최소
template.html 을 둔다. 사전(knowledge/*.json)도 합성이다. state 의 모양이 곧 계약이다. 필드를 지우면 화면이
조용히 빈칸이 된다 — engine 을 고치면 tests/test_engine.py 부터 돌린다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

from . import engine as E

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, '..', '..')
OUT = os.path.join(REPO, 'docs', 'kr')
TOKEN = '"@@KR_STATE@@"'

# 유니버스 기준. rsm0kk/kr-sector 배포본(2026-09-14)의 settings 를 그대로 옮겼다.
SETTINGS = dict(
    includePreferred=False, includeSpac=False, includeReit=True, includeKonex=False,
    minMarketCap=0, minTurnover=1_000_000_000, liquidityWindow=20,
    kosdaqMinCap=100_000_000_000, kosdaqTopN=200)

LAG_NOTE = ('공공데이터포털은 기준일 다음 영업일 13시 이후에 갱신됩니다. '
            '따라서 오늘 아침 화면의 최신 거래일은 보통 전전 영업일입니다.')
ADJUST_NOTE = ('clpr(종가)은 원주가이므로 액면분할·병합·무상증자·권리락 전후 비율이 '
               '틀립니다. 이 대시보드는 기준가 기반인 fltRt(등락률)를 누적해 수익률을 '
               '계산하므로 그 영향을 받지 않습니다.')
GAP_NOTE = (f'기준일 사이에 결측 거래일이 {E.GAP_LIMIT}일을 넘으면 종목코드 재사용/'
            '합병 가능성을 의심해 N/A 로 둡니다.')

# 선그래프와 섹터 지수 낙폭이 보는 구간(영업일).
CHART_SPAN = 252


def load_themes():
    with open(os.path.join(ROOT, 'knowledge', 'themes.json'), encoding='utf-8') as f:
        return json.load(f)


def build_stocks(daily, dates):
    """일별 스냅샷 → 종목별 레코드. 수익률·낙폭·유동성까지 여기서 붙인다."""
    chg = {}      # code -> {date: fltRt}
    last_row = {}  # code -> 최신 관측 행
    turnovers = {}  # code -> [최근 거래대금]

    for d in dates:
        for r in daily.get(d, []):
            c = r['c']
            if r.get('f') is not None:
                chg.setdefault(c, {})[d] = r['f']
            last_row[c] = r

    recent = dates[-E.LIQUIDITY_WINDOW:] if len(dates) >= E.LIQUIDITY_WINDOW else dates
    for d in recent:
        for r in daily.get(d, []):
            if r.get('t') is not None:
                turnovers.setdefault(r['c'], []).append(r['t'])

    first_seen = {}
    for c, per_day in chg.items():
        first_seen[c] = min(per_day)

    out, index_by_code = [], {}
    for c, row in last_row.items():
        idx = E.cumulative_index(chg.get(c, {}), dates)
        if not idx:
            continue
        index_by_code[c] = idx
        rets = {}
        for key, tdays in E.PERIODS:
            rets[key], _ = E.period_return(idx, dates, tdays)
        # YTD 는 영업일 수가 아니라 작년 마지막 거래일 기준이다.
        rets['YTD'] = ytd_return(idx, dates)

        # tl 은 오늘 거래대금, t 는 20일 **중앙값**이다. sg = tl/t 가 급증 배수다.
        # 평균을 쓰면 하루 급등이 기준 자체를 들어올려 급증이 안 잡힌다.
        window = turnovers.get(c) or []
        tl = row.get('t')
        t = statistics.median(window) if window else None
        kind = E.classify_kind(c, row.get('n') or '')

        out.append(dict(
            c=c, n=row.get('n'), m=row.get('m'), p=row.get('p'), f=row.get('f'),
            v=row.get('v'), k=row.get('k'), t=t, tl=tl,
            sg=(tl / t) if (tl is not None and t) else None,
            r=rets, firstDay=first_seen.get(c),
            halted=row.get('v') in (0, None),
            stale=dates[-1] not in idx,
            thin=bool(t and t < SETTINGS['minTurnover']),
            dd=E.drawdown(idx, dates[-252:]) if len(dates) > 1 else None,
            ddAll=E.drawdown(idx, dates) if len(dates) > 1 else None,
            sec=[], **kind))
    return out, index_by_code


def ytd_return(index, dates):
    """올해 첫 거래일 직전(=작년 마지막 거래일) 대비."""
    year = dates[-1][:4]
    prev = [d for d in dates if d[:4] < year and d in index]
    if not prev or dates[-1] not in index:
        return None
    base = index[prev[-1]]
    return (index[dates[-1]] / base - 1.0) * 100.0 if base else None


def assign_sectors(stocks, themes):
    """themes.json 의 배정을 종목에 붙인다. 한 종목이 여러 테마에 들 수 있다."""
    by_code = {s['c']: s for s in stocks}
    for g in themes['groups']:
        for sec in g['sectors']:
            for code in sec.get('members') or []:
                if code in by_code:
                    by_code[code]['sec'].append(sec['name'])
    return sum(1 for s in stocks if s['sec'])


def _sector_flow(members):
    from . import flows as FL
    return FL.sector_flow(members)


def sector_index(members, index_by_code, days):
    """구성종목 누적지수의 평균 = 섹터 동일가중 지수.

    그날 관측이 있는 종목만 평균한다. 거래정지 종목을 직전값으로 끌고 가면
    지수가 그 종목만큼 굳는다.

    ddIndex(섹터 지수의 낙폭)와 charts(화면 선그래프)가 같은 계열을 써야 한다.
    따로 만들면 화면의 선과 낙폭 숫자가 조용히 어긋난다.
    """
    if not members:
        return None
    out = {}
    for d in days:
        vals = [index_by_code[m['c']][d] for m in members
                if m['c'] in index_by_code and d in index_by_code[m['c']]]
        if vals:
            out[d] = sum(vals) / len(vals)
    return out or None


def build_groups(stocks, themes, period_meta=None, bench=None,
                 index_by_code=None, chart_days=None, industry=None):
    """화면이 쓰는 6개 묶음. 테마·밸류체인은 지식이고 나머지는 규칙이다.

    period_meta: {기간: (tdays, baseDate)} — 화면 툴팁이 기준일을 찍는다.
    bench:       {기간: {'코스피': 수익률, '코스닥': 수익률}} — 지수 대비 칸.
    """
    period_keys = [k for k, _ in E.PERIODS] + ['YTD']
    period_meta = period_meta or {}
    bench = bench or {}
    index_by_code = index_by_code or {}
    chart_days = chart_days or []
    groups, indices = [], {}

    group_key = ''

    def pack(name, label, members, level=0, parent=None, children=None,
             note='', declared=None):
        stats = {}
        for p in period_keys:
            td, bd = period_meta.get(p, (None, None))
            stats[p] = E.sector_stats(members, p, tdays=td, base_date=bd,
                                      bench=bench.get(p))
        rs, rs_top = E.sector_rs(members)
        idx = sector_index(members, index_by_code, chart_days)
        if idx:
            indices[f'{group_key}/{name}'] = idx
        return dict(
            name=name, label=label, note=note, count=len(members),
            declared=declared, level=level, parent=parent,
            children=children or [],
            haltedCount=sum(1 for m in members if m.get('halted')),
            stats=stats,
            momentum=E.momentum(stats),
            rot=E.rotation(stats),
            members=[m['c'] for m in members],
            ddStat=E.dd_stats(members),
            # 섹터 자체 지수의 낙폭. 구성종목 낙폭 요약(ddStat)과 다른 것이다 —
            # 종목들이 각각 크게 빠져도 서로 엇갈리면 지수는 덜 빠진다.
            ddIndex=(E.drawdown(idx, chart_days) if idx else None),
            rs=rs, rsTop=rs_top,
            surge=E.surge_stats(members),
            # 구성종목 fl 의 합. 하나도 없으면 None 이라 화면이 그 칸을 감춘다.
            flow=_sector_flow(members))

    by_name = {}
    for s in stocks:
        for nm in s['sec']:
            by_name.setdefault(nm, []).append(s)

    for g in themes['groups']:
        group_key = g['key']
        secs = []
        for sec in g['sectors']:
            if sec['level'] == 0:
                # 상위는 하위 전체를 합쳐 만든다. 직접 배정은 없다.
                members, seen = [], set()
                for child in sec['children']:
                    for m in by_name.get(child, []):
                        if m['c'] not in seen:
                            seen.add(m['c']); members.append(m)
            else:
                members = by_name.get(sec['name'], [])
            secs.append(pack(sec['name'], sec['label'], members, sec['level'],
                             sec['parent'], sec['children'],
                             sec.get('note', ''), sec.get('declared')))
        groups.append(dict(key=g['key'], title=g['title'], sectors=secs))

    # 시장
    group_key = '시장'
    mk = [pack(f'{m} 전체', f'{m} 전체', [s for s in stocks if s['m'] == m])
          for m in ('KOSPI', 'KOSDAQ')]
    groups.append(dict(key='시장', title='시장 · Market', sectors=mk))

    # 규모
    group_key = '규모'
    ranked = sorted(stocks, key=lambda s: s.get('k') or 0, reverse=True)
    size = [pack('대형주 Top100', '대형주 Top100', ranked[:100]),
            pack('중형주 101-300', '중형주 101-300', ranked[100:300]),
            pack('소형주 301+', '소형주 301+', ranked[300:])]
    groups.append(dict(key='규모', title='시가총액 규모 · Size', sectors=size))

    # 업종 (DART 표준산업분류). 배정 못 한 종목은 여기서 빠진다 — 억지로
    # '기타' 에 몰면 그 칸이 실제 업종인 것처럼 보인다.
    industry = industry or {}
    if industry:
        group_key = '업종'
        buckets = {}
        for st in stocks:
            nm = industry.get(st['c'])
            if nm:
                buckets.setdefault(nm, []).append(st)
        secs = [pack(nm, nm, mem) for nm, mem in
                sorted(buckets.items(), key=lambda kv: -len(kv[1]))]
        if secs:
            groups.append(dict(key='업종',
                               title='업종 · Industry (DART 표준산업분류)',
                               sectors=secs))

    # 미분류
    group_key = '미분류'
    un = [s for s in stocks if not s['sec']]
    if un:
        kids = [f'미분류 › {m}' for m in ('KOSPI', 'KOSDAQ')]
        secs = [pack('미분류 전체', f'미분류 전체 · Unclassified', un, 0, None, kids)]
        for m in ('KOSPI', 'KOSDAQ'):
            secs.append(pack(f'미분류 › {m}', f'미분류 › {m}',
                             [s for s in un if s['m'] == m], 1, '미분류 전체'))
        groups.append(dict(key='미분류',
                           title=f'미분류 · Unclassified ({len(un)}종목)', sectors=secs))
    return groups, indices


def build_charts(indices, chart_days):
    """화면 선그래프. build_groups 가 만든 섹터 지수를 그대로 쓴다.

    여기서 다시 계산하지 않는다 — 따로 만들면 화면의 선과 ddIndex 숫자가
    조용히 어긋난다.
    """
    series = {}
    for name, idx in indices.items():
        series[name] = [round(idx[d], 4) if d in idx else None for d in chart_days]
    return dict(days=chart_days, series=series)


def build_indices(index_rows, dates):
    """지수 카드와 낙폭. 화면이 가드 없이 읽는 자리라 모양을 반드시 채운다.

    S.indices['코스피'].r['1M'] 은 상대수익률 칸의 기준이고 접근에 옵셔널 체이닝이
    없다. 값을 못 받았으면 r 을 None 으로 채운 껍데기라도 둬야 페이지가 산다.
    """
    out, dd = {}, {}
    for name in ('코스피', '코스닥'):
        rows = index_rows.get(name) or []
        closes = {r['d']: r['c'] for r in rows if r.get('c')}
        have = [d for d in dates if d in closes]
        rets = {k: None for k, _ in E.PERIODS}
        rets['YTD'] = None
        close = chg = None
        if have:
            last = have[-1]
            close = closes[last]
            idx = {d: closes[d] for d in have}
            for key, tdays in E.PERIODS:
                rets[key], _ = E.period_return(idx, dates, tdays)
            rets['YTD'] = ytd_return(idx, dates)
            chg = rets.get('1D')
            d = E.drawdown(idx, dates)
            if d:
                dd[name] = d
        out[name] = dict(name=name, close=close, chg=chg, r=rets)
    return out, dd


def build_state(daily, failed, index_rows=None, fin=None, flow_by_code=None,
                etf=None, industry=None, provisional=None):
    dates = sorted(daily)
    if len(dates) < 2:
        raise SystemExit('거래일이 2일도 없습니다. 수집부터 확인하세요.')

    themes = load_themes()
    allrows, index_by_code = build_stocks(daily, dates)
    total_listed = len(allrows)
    kept, excluded = E.filter_universe(allrows, SETTINGS)
    assigned = assign_sectors(kept, themes)

    from . import flows as FL
    flow_by_code = flow_by_code or {}
    for s in kept:
        s['fl'] = FL.windows(flow_by_code.get(s['c']) or {}, dates)
    flow_n = sum(1 for s in kept if s.get('fl'))

    rets = {p: {s['c']: s['r'].get(p) for s in kept} for p in E.RS_WEIGHTS}
    rs = E.relative_strength(rets)
    for s in kept:
        val, parts = rs.get(s['c'], (None, {}))
        s['rs'], s['rsp'] = val, parts

    period_keys = [k for k, _ in E.PERIODS] + ['YTD']

    periods = []
    for key, tdays in E.PERIODS:
        i = len(dates) - 1 - tdays
        periods.append(dict(key=key, label=key, tdays=tdays,
                            baseDate=dates[i] if i >= 0 else None))
    year = dates[-1][:4]
    prev_year = [d for d in dates if d[:4] < year]
    periods.append(dict(key='YTD', label='YTD',
                        tdays=sum(1 for d in dates if d[:4] == year),
                        baseDate=prev_year[-1] if prev_year else None))

    period_meta = {p['key']: (p['tdays'], p['baseDate']) for p in periods}
    indices, index_dd = build_indices(index_rows or {}, dates)
    bench = {p: {nm: indices[nm]['r'].get(p) for nm in ('코스피', '코스닥')}
             for p in period_keys}
    chart_days = dates[-CHART_SPAN:]
    groups, sec_indices = build_groups(kept, themes, period_meta, bench,
                                       index_by_code, chart_days, industry)
    industry_n = sum(1 for s in kept if (industry or {}).get(s['c']))

    return dict(
        meta=dict(
            latestTradingDay=dates[-1], prevTradingDay=dates[-2],
            firstTradingDay=dates[0], tradingDays=len(dates),
            generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(),
            universeCount=len(kept), totalListed=total_listed,
            excluded=excluded, themedCount=assigned,
            unclassifiedCount=len(kept) - assigned,
            themeCoverage=round(assigned / len(kept) * 100, 1) if kept else 0,
            settings=SETTINGS,
            # 확정치가 어디까지인지와 화면의 최신 거래일은 다를 수 있다.
            # 잠정치를 얹은 날은 provisional 로 그 사실을 밝힌다.
            confirmedThrough=(provisional['confirmedThrough'] if provisional
                              else dates[-1]),
            provisional=provisional,
            industryAvailable=industry_n > 0,
            industryCoverage=round(industry_n / len(kept) * 100, 1) if kept else 0,
            finAvailable=fin is not None,
            flowAvailable=flow_n > 0,
            flowCoverage=round(flow_n / len(kept) * 100, 1) if kept else 0,
            flowWindows=[1, 5, 20], holidays=[],
            fetchFailures=[d for d, _ in failed],
            dataLagNote=LAG_NOTE, adjustNote=ADJUST_NOTE, gapRule=GAP_NOTE),
        periods=periods,
        indices=indices, indexDD=index_dd,
        groups=groups,
        stocks={s['c']: s for s in kept},
        charts=build_charts(sec_indices, chart_days),
        leaders={p: E.leaders(kept, p) for p in period_keys},
        # 빈 dict 가 아니라 None 이다. 화면은 `S.fin ? ... : '미적용'` 으로 가르는데
        # 자바스크립트에서 {} 는 참이라 빈 dict 를 주면 가드를 통과한 뒤
        # S.fin.years.join(...) 에서 죽는다. 아직 안 붙인 묶음은 None 으로 둔다.
        fin=fin, etf=etf)


def render(state):
    with open(os.path.join(ROOT, 'template.html'), encoding='utf-8') as f:
        tpl = f.read()
    if tpl.count(TOKEN) != 1:
        raise SystemExit(f'template.html 의 자리표시자가 {tpl.count(TOKEN)}개입니다 (1개여야 함)')
    payload = json.dumps(state, ensure_ascii=False, separators=(',', ':'))

    # 산출물에 키가 섞였는지 본다. bok 쪽 build.js 와 같은 자세다.
    for name in ('DATAGO_KEY', 'KIS_APP_KEY', 'KIS_APP_SECRET', 'DART_API_KEY'):
        v = (os.environ.get(name) or '').strip()
        if len(v) >= 8 and v in payload:
            raise SystemExit(f'산출물에 {name} 가 포함됐습니다 — 중단')

    html = tpl.replace(TOKEN, payload)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    with open(os.path.join(OUT, 'robots.txt'), 'w', encoding='utf-8') as f:
        f.write('User-agent: *\nDisallow: /\n')
    return len(html)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-fetch', action='store_true', help='캐시만 쓴다')
    ap.add_argument('--days', type=int, default=None, help='받아 둘 영업일 수')
    a = ap.parse_args(argv)

    from . import ingest
    if a.no_fetch:
        dates = sorted(os.path.splitext(f)[0]
                       for f in os.listdir(ingest.CACHE)) if os.path.isdir(ingest.CACHE) else []
        daily, failed = {}, []
        for d in dates:
            rows = ingest.load_day(d)
            if rows:
                daily[d] = rows
        print(f'캐시만 사용: {len(daily)}일')
    else:
        daily, failed = ingest.collect(days=a.days or ingest.HISTORY_DAYS)

    index_rows = {}
    if not a.no_fetch and daily:
        ds = sorted(daily)
        index_rows = ingest.collect_indices(
            f'{ds[0][:4]}-{ds[0][4:6]}-{ds[0][6:8]}',
            f'{ds[-1][:4]}-{ds[-1][4:6]}-{ds[-1][6:8]}')
    elif os.path.exists(ingest.INDEX_FILE):
        with open(ingest.INDEX_FILE, encoding='utf-8') as f:
            index_rows = json.load(f)

    flow_by_code = {}
    fin = None
    if daily:
        from . import financials
        codes = sorted({r['c'] for rows in daily.values() for r in rows})
        if a.no_fetch:
            from board.ingest import financials as BF
            fin = financials.build((BF.load(financials.CACHE).get('by_code') or {}))
            print(f'재무: 캐시만 사용 ({len(fin["byCode"]) if fin else 0}종목)')
        else:
            fin = financials.collect(codes)

        # 수급은 종목당 한 번씩이라 가장 비싸다. 시총 큰 순으로 예산 안에서 채운다.
        from . import flows
        last = sorted(daily)[-1]
        by_cap = [r['c'] for r in sorted(daily[last], key=lambda r: r.get('k') or 0,
                                         reverse=True)]
        if a.no_fetch:
            flow_by_code = flows.load().get('by_code') or {}
            print(f'수급: 캐시만 사용 ({len(flow_by_code)}종목)')
        else:
            flow_by_code = flows.collect(by_cap)

    etf = None
    if daily and not a.no_fetch:
        from . import etf as ETF
        etf = ETF.collect(sorted(daily))

    industry = None
    if daily:
        from . import industries as IND
        codes = sorted({r['c'] for rows in daily.values() for r in rows})
        industry = IND.refresh(codes, fetch=not a.no_fetch)

    provisional = None
    if daily and not a.no_fetch:
        confirmed = sorted(daily)[-1]
        got = ingest.collect_provisional(confirmed)
        if got:
            day, rows, traded = got
            daily[day] = rows
            provisional = dict(date=day, asOf=dt.datetime.now(
                dt.timezone(dt.timedelta(hours=9))).strftime('%H:%M'),
                source=ingest.PROVISIONAL_SOURCE, traded=traded,
                count=len(rows), confirmedThrough=confirmed)

    state = build_state(daily, failed, index_rows, fin, flow_by_code, etf,
                        industry, provisional)
    n = render(state)
    m = state['meta']
    print(f"→ {os.path.join(OUT, 'index.html')} · {n/1024:.0f}KB · 최신 {m['latestTradingDay']} · "
          f"유니버스 {m['universeCount']}/{m['totalListed']} · 테마적용 {m['themeCoverage']}% · "
          f"재무 {len(state['fin']['byCode']) if state['fin'] else 0}종목 · "
          f"수급 {m['flowCoverage']}% · "
          f"ETF {state['etf']['meta']['universeCount'] if state['etf'] else 0}종목 · "
          f"업종 {m['industryCoverage']}%")
    if failed:
        print(f"::warning::수집 실패 {len(failed)}일 — {[d for d,_ in failed][:5]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
