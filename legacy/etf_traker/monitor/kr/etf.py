#!/usr/bin/env python3
"""
국장 섹터 모니터 — ETF 탭 (`state.etf`).

주식 쪽과 똑같은 엔진(누적지수·기간수익률·낙폭·상대강도)을 ETF 에 그대로 돌린다.
다른 것은 유니버스 기준과 묶는 방식뿐이다.

--------------------------------------------------------------------------
소스
--------------------------------------------------------------------------
`etf_tracker_v9/market.py` 를 재사용한다. 거기 이미 들어 있는 것 —
  - `fetch_list()`  네이버 etfItemList. 전 종목 1회 호출로 NAV·시총·거래대금·탭코드
  - `fetch_hist()`  siseJson 일봉. 확정 종가·거래량

목록 API 의 등락률은 **호출 시점의 장중 값**이다. 그래서 기준일은 항상 직전
완료 영업일이고, 장중 값을 그날 수치로 쓰지 않는다 — market.py 머리말과 같은 규칙.

--------------------------------------------------------------------------
분류는 이름에서 뽑는다
--------------------------------------------------------------------------
운용사(brand)·레버리지·환헤지·합성·액티브는 ETF 이름 규칙이 업계 표준이라
이름에서 읽을 수 있다. 자산군(asset)은 네이버 탭코드를 쓴다 — 우리가 지어낸
분류가 아니라 소스가 준 값이다.

국내테마(theme)만은 이름 키워드로 가른다. 확실하지 않으면 '기타' 로 두고
억지로 배정하지 않는다.

--------------------------------------------------------------------------
구성종목(PDF)은 아직 없다
--------------------------------------------------------------------------
배포본은 KIS 에서 구성종목을 받는다(화면 문구: '해외자산·채권·합성(스왑)
ETF는 KIS가 구성종목을 주지 않습니다'). 여기서는 아직 안 붙였고, 화면은
`items[*].pdf` · `etf.flow` · `etf.overlap` 을 각각 가드하므로 그 칸만 빠진다.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from . import engine as E  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, 'cache', 'etf')

# 유니버스 기준. 화면 문구가 '순자산 50억↑ · 20일 거래대금 1억↑' 이라고 못 박는다.
MIN_NET_ASSET = 5_000_000_000      # 50억
MIN_TURNOVER = 100_000_000         # 1억

# 네이버 etfTabCode → 자산군. 소스가 준 분류라 우리가 지어낸 것이 아니다.
TAB_ASSET = {1: '국내주식', 2: '국내주식', 3: '국내파생', 4: '해외주식',
             5: '원자재', 6: '채권', 7: '기타', 8: '해외파생', 9: '해외혼합'}

# 운용사 브랜드. 이름 맨 앞에 붙는 것이 업계 관행이다.
BRANDS = [
    ('KODEX', '삼성자산운용'), ('TIGER', '미래에셋자산운용'),
    ('KBSTAR', 'KB자산운용'), ('RISE', 'KB자산운용'),
    ('KIWOOM', '키움투자자산운용'), ('KOSEF', '키움투자자산운용'),
    ('ARIRANG', '한화자산운용'), ('PLUS', '한화자산운용'),
    ('HANARO', 'NH아문디자산운용'), ('SOL', '신한자산운용'),
    ('ACE', '한국투자신탁운용'), ('TIMEFOLIO', '타임폴리오자산운용'),
    ('TIME', '타임폴리오자산운용'), ('KOACT', '삼성액티브자산운용'),
    ('WON', '우리자산운용'), ('BNK', 'BNK자산운용'), ('히어로즈', '키움투자자산운용'),
    ('마이다스', '마이다스에셋자산운용'), ('파워', '교보악사자산운용'),
    ('마이티', 'DB자산운용'), ('FOCUS', '브이아이자산운용'),
    ('에셋플러스', '에셋플러스자산운용'), ('UNICORN', '현대자산운용'),
]

# 국내테마. 위에서부터 먼저 맞는 것을 쓴다 — 좁은 것을 앞에 둔다.
THEMES = [
    ('반도체', ('반도체', 'AI반도체', '시스템반도체', 'Fn반도체')),
    ('2차전지', ('2차전지', '배터리', 'K-배터리')),
    ('바이오·헬스케어', ('바이오', '헬스케어', '제약')),
    ('자동차', ('자동차', '모빌리티')),
    ('조선·방산', ('조선', '방산', '우주항공', 'K-방산')),
    ('은행·금융', ('은행', '금융', '증권', '보험')),
    ('인터넷·게임', ('인터넷', '게임', '미디어', '엔터')),
    ('에너지·화학', ('에너지', '화학', '신재생', '태양광', '수소', '원자력', '원전')),
    ('건설·기계', ('건설', '기계', '중공업', '인프라')),
    ('소비재', ('소비재', '음식료', '화장품', '유통', '리테일')),
    ('고배당·가치', ('배당', '가치', '밸류', '로우볼', '퀄리티')),
    ('대표지수', ('200', 'KRX100', '코스닥150', 'MSCI Korea', '코스피')),
    ('리츠·부동산', ('리츠', '부동산')),
    ('ESG', ('ESG', '탄소', '친환경')),
    ('중소형', ('중소형', '중형', '소형')),
]


def _brand(name):
    up = (name or '').upper()
    for b, co in BRANDS:
        if up.startswith(b.upper()):
            return b, co
    return (name or '').split()[0] if name else '기타', None


def _leverage(name):
    """1 = 일반, 2 = 2배, -1 = 인버스, -2 = 2배 인버스."""
    n = (name or '').upper()
    inv = '인버스' in n or 'INVERSE' in n
    two = bool(re.search(r'2X|레버리지|\(2\)', n))
    if inv:
        return -2 if two else -1
    return 2 if two else 1


def classify(name, tab):
    """이름과 탭코드로 분류. 확실하지 않으면 '기타' 로 둔다."""
    brand, company = _brand(name)
    asset = TAB_ASSET.get(tab or 0, '기타')
    theme = '기타'
    if asset == '국내주식':
        for key, words in THEMES:
            if any(w.upper() in (name or '').upper() for w in words):
                theme = key
                break
    n = (name or '').upper()
    return dict(
        asset=asset, theme=theme, brand=brand, company=company,
        lev=_leverage(name),
        hedged=bool(re.search(r'\(H\)|헤지', name or '')),
        synthetic=bool(re.search(r'합성|SYNTH', n)),
        active=bool(re.search(r'액티브|ACTIVE', n)))


def build_items(listing, hist, dates, period_meta):
    """목록 + 일봉 → items. 주식과 같은 엔진을 쓴다.

    listing: {code: {name, tab, price, chg, nav, mktcap, units, volume, turnover}}
             — mktcap·turnover 는 억원(etf_tracker_v9/market.live_rows 규약).
    hist:    {code: {날짜: 종가}}
    """
    out = {}
    for code, meta in listing.items():
        bars = hist.get(code) or {}
        have = [d for d in dates if d in bars]
        if len(have) < 2:
            continue
        # ETF 일봉은 수정주가로 온다. 종목 쪽과 달리 등락률 계열이 없으므로
        # 종가 비로 낸다 — 분배금 재투자는 반영되지 않는다(가격수익률).
        idx = {d: bars[d] for d in have}
        rets = {}
        for key, tdays in E.PERIODS:
            rets[key], _ = E.period_return(idx, dates, tdays)
        year = dates[-1][:4]
        prev = [d for d in have if d[:4] < year]
        rets['YTD'] = ((idx[have[-1]] / idx[prev[-1]] - 1) * 100.0
                       if prev and idx.get(prev[-1]) else None)

        nav = meta.get('nav')
        price = meta.get('price')
        net = (meta.get('mktcap') or 0) * 1e8      # 억원 → 원
        out[code] = dict(
            c=code, n=meta.get('name'), bi=None,
            p=price, f=meta.get('chg'), nav=nav,
            prem=((price / nav - 1) * 100.0) if (nav and price) else None,
            a=meta.get('units'), net=net,
            t=(meta.get('turnover') or 0) * 1e8,
            r=rets,
            dd=E.drawdown(idx, dates[-252:]),
            rs=None,
            **classify(meta.get('name'), meta.get('tab')))
    return out


def filter_universe(items):
    """순자산 50억↑ · 20일 거래대금 1억↑. 무엇이 왜 빠졌는지 센다."""
    ex = {'noData': 0, 'illiquid': 0, 'small': 0}
    keep = {}
    for c, x in items.items():
        if x['p'] is None or not x['r']:
            ex['noData'] += 1; continue
        if (x['net'] or 0) < MIN_NET_ASSET:
            ex['small'] += 1; continue
        if (x['t'] or 0) < MIN_TURNOVER:
            ex['illiquid'] += 1; continue
        keep[c] = x
    return keep, ex


def build_groups(items, period_meta):
    """자산군·국내테마·운용사 세 묶음. 구성이 없는 칸은 만들지 않는다."""
    period_keys = [k for k, _ in E.PERIODS] + ['YTD']

    def pack(group, name, members):
        stats = {}
        for p in period_keys:
            td, bd = period_meta.get(p, (None, None))
            stats[p] = E.sector_stats(members, p, tdays=td, base_date=bd)
        rs = [m['rs'] for m in members if m.get('rs') is not None]
        prem = [m['prem'] for m in members if m.get('prem') is not None]
        import statistics
        return dict(
            group=group, name=name, label=name, level=0, parent=None, children=[],
            members=[m['c'] for m in members], count=len(members),
            stats=stats,
            rs=statistics.median(rs) if rs else None,
            prem=statistics.median(prem) if prem else None)

    groups = []
    for key, title, field in (('자산군', '자산군 · Asset Class', 'asset'),
                              ('국내테마', '국내주식 테마 · Domestic Themes', 'theme'),
                              ('운용사', '운용사 · Issuer', 'brand')):
        buckets = {}
        for x in items.values():
            if field == 'theme' and x['asset'] != '국내주식':
                continue
            buckets.setdefault(x[field], []).append(x)
        secs = [pack(key, nm, mem) for nm, mem in
                sorted(buckets.items(), key=lambda kv: -len(kv[1])) if mem]
        if secs:
            groups.append(dict(key=key, title=title, sectors=secs))
    return groups


def build(listing, hist, dates, total=None):
    """화면의 `etf`. 받은 게 없으면 None — 빈 dict 는 화면을 죽인다."""
    if not listing or not dates:
        return None

    periods, period_meta = [], {}
    for key, tdays in E.PERIODS:
        i = len(dates) - 1 - tdays
        bd = dates[i] if i >= 0 else None
        periods.append(dict(key=key, label=key, tdays=tdays, baseDate=bd))
        period_meta[key] = (tdays, bd)
    year = dates[-1][:4]
    prev = [d for d in dates if d[:4] < year]
    bd = prev[-1] if prev else None
    periods.append(dict(key='YTD', label='YTD',
                        tdays=sum(1 for d in dates if d[:4] == year), baseDate=bd))
    period_meta['YTD'] = (period_meta['1Y'][0], bd)

    allitems = build_items(listing, hist, dates, period_meta)
    keep, ex = filter_universe(allitems)
    if not keep:
        return None

    rets = {p: {c: x['r'].get(p) for c, x in keep.items()} for p in E.RS_WEIGHTS}
    rs = E.relative_strength(rets)
    for c, x in keep.items():
        x['rs'] = rs.get(c, (None, {}))[0]

    return dict(
        meta=dict(latestTradingDay=dates[-1], tradingDays=len(dates),
                  total=total if total is not None else len(listing),
                  universeCount=len(keep), excluded=ex,
                  minTurnover=MIN_TURNOVER, minNetAsset=MIN_NET_ASSET,
                  # 구성종목은 아직 안 받는다. 화면은 overlap 이 없으면 이 칸을
                  # 아예 읽지 않지만, 읽더라도 숫자가 맞게 0 으로 둔다.
                  pdf=dict(available=False, withHoldings=0,
                           noHoldings=len(keep), snapshotDates=[], changes=None)),
        periods=periods,
        groups=build_groups(keep, period_meta),
        items=keep,
        flow=None, overlap=None, charts=None)


def collect(dates, log=print):
    """네이버에서 ETF 목록과 일봉을 받는다. 실패하면 None — 탭만 빠진다."""
    try:
        sys.path.insert(0, os.path.join(ROOT, '..', '..', 'etf_tracker_v9'))
        from etf_tracker_v9 import market
    except ImportError as e:
        log(f'  ETF: etf_tracker_v9 를 못 불러왔다 ({e})')
        return None
    try:
        rows = market.fetch_list()
    except Exception as e:  # noqa: BLE001
        log(f'  ETF 목록 실패: {e}')
        return None

    listing = market.live_rows(rows)
    start = f'{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:8]}'
    end = f'{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:8]}'
    hist, fail = {}, 0
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(market.fetch_hist, c, start, end): c for c in listing}
        for fu in cf.as_completed(futs):
            c = futs[fu]
            try:
                bars = fu.result()
            except Exception:  # noqa: BLE001
                fail += 1
                continue
            if bars:
                hist[c] = {d.replace('-', ''): close for d, close, _ in bars}
            else:
                fail += 1
    log(f'  ETF: 목록 {len(listing)} · 일봉 {len(hist)} · 실패 {fail}')
    return build(listing, hist, dates, total=len(listing))
