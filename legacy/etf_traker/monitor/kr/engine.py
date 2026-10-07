#!/usr/bin/env python3
"""
국장 섹터 모니터 — 계산 엔진.

입력은 '하루치 전 종목' 스냅샷의 나열이고 출력은 화면이 그대로 먹는 state dict 다.
외부 호출을 하지 않는다. 그래서 고정 표본만으로 전부 시험할 수 있다.

--------------------------------------------------------------------------
수익률을 종가가 아니라 등락률(fltRt)로 계산하는 이유
--------------------------------------------------------------------------
공공데이터포털의 `clpr`(종가)은 **원주가**다. 액면분할·병합·무상증자·권리락이
과거 시계열에 반영되지 않으므로 종가끼리 나누면 그 날짜를 걸친 구간의 수익률이
통째로 틀린다. 반면 `fltRt`(등락률)는 거래소가 기준가 대비로 산출하므로 권리락
당일에도 값이 맞다.

그래서 등락률을 곱해 누적지수를 만들고(100 에서 출발) 그 지수의 비로 수익률을
낸다. board/engine/newhigh.py 는 같은 문제를 '점프를 탐지해 제외'하는 방향으로
풀었지만(D-001·D-056), 여기서는 애초에 조정이 필요 없는 계열을 쓴다.

--------------------------------------------------------------------------
빠진 날은 0 이 아니다
--------------------------------------------------------------------------
어떤 종목이 특정 영업일에 응답에 없으면(거래정지·미상장·수집 누락) 등락률을 0 으로
두지 않는다. 0 은 '보합'이라는 뜻이라 거래정지 구간을 실제 성과인 것처럼 만든다.
빠진 날은 건너뛰되 몇 번 빠졌는지를 세고, 기준일까지의 결측이 GAP_LIMIT 을 넘으면
그 구간 수익률을 None 으로 둔다 — 종목코드 재사용·합병 가능성을 의심하는 것이다.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

# 기준일 사이 결측이 이 일수를 넘으면 수익률을 N/A 로 둔다.
GAP_LIMIT = 20

# 화면이 쓰는 기간. tdays 는 '영업일 몇 개 전'이다.
PERIODS = [
    ('1D', 1), ('1W', 5), ('2W', 10), ('1M', 21),
    ('3M', 63), ('6M', 126), ('1Y', 252),
]

# 상대강도 가중치. rsp(기간별 백분위)를 이 비율로 섞는다.
# rsm0kk/kr-sector 배포본 1,071종목에서 최소제곱으로 역산했다(잔차 1e-14).
RS_WEIGHTS = {'1M': 0.2, '3M': 0.2, '6M': 0.2, '1Y': 0.4}

# 유동성을 재는 창. 이 일수의 거래대금 **중앙값**을 쓴다(평균이 아니다 —
# 하루 급등이 창 전체를 들어올리는 것을 막는다).
#
# 용어를 배포본에 맞춘다. 한 번 반대로 읽어서 급증 배수가 역수로 나왔다.
#   tl = 오늘 거래대금            (화면 '오늘 대금')
#   t  = 20일 거래대금 중앙값      (비교 기준)
#   sg = tl / t = 급증 배수        (2 이상이면 급증)
LIQUIDITY_WINDOW = 20

# 급증으로 세는 배수.
SURGE_2X, SURGE_3X = 2.0, 3.0

# 상대강도 상위로 세는 기준. rsTop 은 이 값을 넘는 구성종목의 **비율(%)** 이다.
RS_TOP_CUT = 80.0

# 제외 사유. 화면 배너가 이 키를 하나씩 읽으므로 걸린 게 없어도 0 으로 채운다.
# 없는 키를 주면 배너에 undefined 가 찍힌다 — 0 도 정보다.
EXCLUDE_REASONS = ('pref', 'spac', 'konex', 'reit', 'cap', 'noData',
                   'illiquid', 'smallKosdaq', 'themedExempt')


# ---------------------------------------------------------------- 누적지수

def cumulative_index(chg_by_date, dates):
    """등락률(%)을 누적해 지수로. 첫 관측일을 100 으로 둔다.

    chg_by_date: {'20260914': -4.24, ...}
    dates:       오름차순 영업일 전체. 이 순서로 훑는다.

    돌려주는 것은 {날짜: 지수}. 값이 없는 날은 **키가 없다** — 0 으로 채우지
    않는다. 호출하는 쪽이 결측을 결측으로 다뤄야 한다.
    """
    out, level = {}, 100.0
    started = False
    for d in dates:
        r = chg_by_date.get(d)
        if r is None:
            continue
        if not started:
            # 첫 관측일은 그날의 등락률을 적용하지 않는다. 그 등락률은 우리가
            # 보지 못한 전일 대비라 기준점으로 쓸 수 없다.
            started = True
            out[d] = level
            continue
        level *= (1.0 + r / 100.0)
        out[d] = level
    return out


def period_return(index, dates, tdays):
    """지수에서 'tdays 영업일 전 대비' 수익률(%). 못 내면 (None, None).

    기준일은 dates[-1 - tdays] 다. 그 날 관측이 없으면 그 이전으로 물러나 가장
    가까운 관측일을 잡는다.

    그리고 기준일과 오늘 **사이**의 결측 거래일을 센다. GAP_LIMIT 을 넘으면
    수익률을 내지 않는다. 오래 멈춰 있던 종목의 값은 그 구간의 성과가 아니고,
    종목코드가 재사용됐거나 합병으로 다른 회사가 된 경우도 여기서 걸린다.

    물러나는 칸수와 구간 결측은 다른 것이다. 기준일 하루만 비어 있고 나머지가
    멀쩡하면 정상이고, 기준일은 있는데 그 뒤가 통째로 비었으면 비정상이다.
    """
    if len(dates) <= tdays:
        return None, None
    last = dates[-1]
    if last not in index:
        return None, None

    i = len(dates) - 1 - tdays
    base_j = None
    for j in range(i, -1, -1):
        if dates[j] in index:
            base_j = j
            break
    if base_j is None:
        return None, None

    span = dates[base_j:]
    missing = sum(1 for d in span if d not in index)
    if missing > GAP_LIMIT:
        return None, None

    base = index[dates[base_j]]
    if not base:
        return None, None
    return (index[last] / base - 1.0) * 100.0, dates[base_j]


def drawdown(index, dates):
    """고점 대비 낙폭. 구간 전체를 한 번 훑는다.

    curDD      현재 지수가 그간 최고점 대비 몇 % 아래인가
    peakDate   그 최고점 날짜, peakAgo 는 그로부터 몇 영업일 지났나
    mdd        구간 최대 낙폭과 그 고점·저점 날짜
    recovery   저점에서 얼마나 되돌렸나
    """
    seq = [(d, index[d]) for d in dates if d in index]
    if len(seq) < 2:
        return None

    peak_v, peak_d = seq[0][1], seq[0][0]
    mdd, mdd_peak, mdd_trough = 0.0, seq[0][0], seq[0][0]
    run_peak_v, run_peak_d = seq[0][1], seq[0][0]
    trough_v = seq[0][1]

    for d, v in seq:
        if v > run_peak_v:
            run_peak_v, run_peak_d = v, d
        dd = (v / run_peak_v - 1.0) * 100.0
        if dd < mdd:
            mdd, mdd_peak, mdd_trough, trough_v = dd, run_peak_d, d, v
        if v > peak_v:
            peak_v, peak_d = v, d

    last_d, last_v = seq[-1]
    cur = (last_v / peak_v - 1.0) * 100.0
    peak_ago = sum(1 for d, _ in seq if d > peak_d)
    rec = ((last_v / trough_v - 1.0) * 100.0) if trough_v else None
    return dict(curDD=cur, peakDate=peak_d, peakAgo=peak_ago, mdd=mdd,
                mddPeakDate=mdd_peak, mddTroughDate=mdd_trough, recovery=rec)


# ---------------------------------------------------------------- 백분위·RS

def percentile_ranks(values):
    """{키: 값} → {키: 백분위 0~100}. 값이 None 인 키는 결과에서 빠진다.

    같은 값이 여럿이면 같은 순위를 준다(평균 순위). 그래야 거래정지로 0% 가
    몰린 날 순위가 임의로 갈리지 않는다.
    """
    items = [(k, v) for k, v in values.items() if v is not None]
    if not items:
        return {}
    if len(items) == 1:
        return {items[0][0]: 100.0}
    items.sort(key=lambda kv: kv[1])
    n = len(items)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        # i..j 가 동점. 평균 순위를 0~100 으로 편다.
        rank = (i + j) / 2.0
        pct = rank / (n - 1) * 100.0
        for k in range(i, j + 1):
            out[items[k][0]] = pct
        i = j + 1
    return out


def relative_strength(returns_by_period):
    """기간별 수익률 → 종목별 rs(0~100)와 rsp(기간별 백분위).

    returns_by_period: {'1M': {code: ret}, '3M': {...}, '6M': ..., '1Y': ...}
    한 기간이라도 백분위를 못 내면 그 종목 rs 는 None 이다. 있는 기간만으로
    가중평균하면 1Y 가 없는 신규 상장이 과대평가된다.
    """
    ranks = {p: percentile_ranks(returns_by_period.get(p, {})) for p in RS_WEIGHTS}
    codes = set()
    for r in ranks.values():
        codes |= set(r)
    out = {}
    for c in codes:
        parts = {p: ranks[p].get(c) for p in RS_WEIGHTS}
        if any(v is None for v in parts.values()):
            out[c] = (None, parts)
            continue
        rs = sum(parts[p] * w for p, w in RS_WEIGHTS.items())
        out[c] = (rs, parts)
    return out


# ---------------------------------------------------------------- 섹터 집계

def sector_stats(members, period, tdays=None, base_date=None, bench=None):
    """구성종목 → 그 기간의 집계. members 는 종목 dict 의 리스트.

    계산할 값이 하나도 없으면 **None** 을 돌려준다. 빈 dict 를 주면 화면이
    `st ? ... : '계산 가능한 종목이 없습니다'` 가드를 통과한 뒤 breadth.toFixed()
    에서 죽는다. 구성종목이 없으면 통계도 없는 것이지 0% 인 것이 아니다.

    capW(시총가중)는 **기준일 시총**이 아니라 현재 시총으로 가중한다. 원본
    배포본과 0.2%p 안팎 어긋나는 값이 이 차이에서 나온다 — 기준일 시총을 쓰려면
    그날 스냅샷의 mktcap 이 필요하고, 캐시에 있으므로 넘기면 된다.

    bench 는 {'코스피': 그 기간 지수 수익률, '코스닥': ...}. 지수를 못 받았으면
    vsKospi·vsKosdaq 은 None 이다 — 0 으로 채우면 '지수와 같았다'가 된다.
    """
    vals, caps = [], []
    for m in members:
        v = m['r'].get(period)
        if v is None:
            continue
        vals.append(v)
        caps.append(m.get('k') or 0)
    if not vals:
        return None

    cap_sum = sum(caps)
    up = sum(1 for x in vals if x > 0)
    down = sum(1 for x in vals if x < 0)
    flat = len(vals) - up - down
    mean = sum(vals) / len(vals)
    bench = bench or {}
    kospi, kosdaq = bench.get('코스피'), bench.get('코스닥')
    return dict(
        mean=mean,
        capW=(sum(x * c for x, c in zip(vals, caps)) / cap_sum) if cap_sum else None,
        median=statistics.median(vals),
        max=max(vals), min=min(vals),
        up=up, down=down, flat=flat,
        n=len(vals), nTotal=len(members),
        breadth=up / len(vals) * 100.0,
        baseDate=base_date, tdays=tdays,
        vsKospi=(mean - kospi) if kospi is not None else None,
        vsKosdaq=(mean - kosdaq) if kosdaq is not None else None,
        # 하루당 수익률. 기간 길이가 다른 값을 나란히 볼 때 쓴다.
        runrate=(mean / tdays) if tdays else None)


def leaders(stocks, period, top_n=25):
    """기간 수익률 상·하위. 값이 없는 종목은 양쪽 어디에도 넣지 않는다."""
    rows = [dict(c=v['c'], n=v['n'], m=v['m'], r=v['r'][period], k=v.get('k'))
            for v in stocks if v['r'].get(period) is not None]
    rows.sort(key=lambda x: x['r'], reverse=True)
    return dict(top=rows[:top_n], bottom=rows[::-1][:top_n])


# ---------------------------------------------------------------- 유니버스

def classify_kind(code, name):
    """우선주·스팩·리츠 판별. 코드와 이름이 **둘 다** 맞을 때만 단정한다.

    board/engine/kinds.py 의 D-069 와 같은 자세다. 이름만 보고 자르면 '스팩터'
    같은 보통주가 스팩으로 걸린다.
    """
    pref = len(code) == 6 and code[5] != '0' and not name.endswith('스팩')
    spac = '스팩' in name or name.endswith('기업인수목적')
    reit = name.endswith('리츠') or '리츠' in name
    return dict(pref=bool(pref), spac=bool(spac), reit=bool(reit))


def filter_universe(stocks, settings):
    """설정대로 걸러낸다. 무엇이 왜 빠졌는지 세서 같이 돌려준다.

    화면 상단 배너가 이 숫자를 그대로 쓴다. 몇 종목을 보고 있는지 모르는 순위는
    순위가 아니다 — board 의 D-072 와 같은 이유로 **생성 단계에서** 거른다.
    """
    ex = defaultdict(int)
    for r in EXCLUDE_REASONS:
        ex[r] = 0
    keep = []
    for s in stocks:
        if not settings.get('includePreferred') and s.get('pref'):
            ex['pref'] += 1; continue
        if not settings.get('includeSpac') and s.get('spac'):
            ex['spac'] += 1; continue
        if not settings.get('includeReit', True) and s.get('reit'):
            ex['reit'] += 1; continue
        if not settings.get('includeKonex') and s.get('m') == 'KONEX':
            ex['konex'] += 1; continue
        if s.get('p') is None:
            ex['noData'] += 1; continue
        cap = s.get('k') or 0
        if settings.get('minMarketCap') and cap < settings['minMarketCap']:
            ex['cap'] += 1; continue
        # 하한은 오늘이 아니라 20일 중앙값(t)에 건다. 오늘 하루 조용했다고
        # 유니버스에서 빼면 매일 구성이 출렁인다.
        if settings.get('minTurnover') and (s.get('t') or 0) < settings['minTurnover']:
            ex['illiquid'] += 1; continue
        keep.append(s)

    # 코스닥은 종목 수가 많아 하한만으로는 안 줄어든다. 시총 하한과 상위 N 을
    # 함께 건다. 둘 중 하나만 쓰면 소형주가 통째로 남거나 통째로 사라진다.
    kmin = settings.get('kosdaqMinCap')
    ktop = settings.get('kosdaqTopN')
    if kmin or ktop:
        kq = [s for s in keep if s.get('m') == 'KOSDAQ']
        kq.sort(key=lambda s: s.get('k') or 0, reverse=True)
        allow = set()
        for i, s in enumerate(kq):
            if kmin and (s.get('k') or 0) >= kmin:
                allow.add(s['c'])
            elif ktop and i < ktop:
                allow.add(s['c'])
        dropped = [s for s in kq if s['c'] not in allow]
        ex['smallKosdaq'] += len(dropped)
        drop_codes = {s['c'] for s in dropped}
        keep = [s for s in keep if s['c'] not in drop_codes]

    return keep, dict(ex)


# ---------------------------------------------------------- 모멘텀·로테이션

# 하루당 수익률(rr)을 낼 기간과 그 영업일 수.
RR_PERIODS = [('1W', 5), ('2W', 10), ('1M', 21), ('3M', 63)]

# 로테이션 산점도에서 어느 기간을 무엇과 견주는가.
ROT_VS = {'1D': '1W', '1W': '2W', '2W': '1M', '1M': '3M', '3M': '6M', '6M': '1Y'}


def daily_rates(stats):
    """기간 평균수익률 ÷ 그 기간 영업일 수. '하루당 몇 %' 로 폈다.

    기간 길이가 다른 수익률을 그대로 견주면 3M 이 늘 커 보인다. 하루당으로
    펴야 가속·감속을 말할 수 있다.
    """
    out = {}
    for key, tdays in RR_PERIODS:
        s = stats.get(key) or {}
        m = s.get('mean')
        out[f'rr{key}'] = (m / tdays) if m is not None else None
    return out


def momentum(stats):
    """가속·감속 판정. 방향 지표가 아니다 — 마이너스여도 덜 빠지면 '가속'이다."""
    rr = daily_rates(stats)
    c1 = None if rr['rr1W'] is None or rr['rr2W'] is None else rr['rr1W'] > rr['rr2W']
    c2 = None if rr['rr1M'] is None or rr['rr3M'] is None else rr['rr1M'] > rr['rr3M']
    if c1 is None or c2 is None:
        verdict = 'na'
    elif c1 and c2:
        verdict = 'accel'
    elif not c1 and not c2:
        verdict = 'decel'
    else:
        verdict = 'neutral'
    return dict(c1=c1, c2=c2, verdict=verdict, **rr)


def rotation(stats):
    """기간별 산점도. x 는 그 기간 성과, y 는 직전 더 긴 기간 대비 가속도.

    사분면 이름은 배포본과 같다 — x>0,y>0 주도 / x>0,y<0 과열둔화 /
    x<0,y>0 반등초입 / x<0,y<0 소외.
    """
    def rr(key):
        tdays = dict(PERIODS).get(key)
        s = stats.get(key) or {}
        m = s.get('mean')
        return (m / tdays) if (m is not None and tdays) else None

    out = {}
    for key, vs in ROT_VS.items():
        s = stats.get(key) or {}
        x = s.get('mean')
        a, b = rr(key), rr(vs)
        y = (a - b) if (a is not None and b is not None) else None
        if x is None or y is None:
            quad = None
        elif x > 0:
            quad = '주도' if y > 0 else '과열둔화'
        else:
            quad = '반등초입' if y > 0 else '소외'
        out[key] = dict(x=x, y=y, quad=quad, vs=vs)
    return out


# 낙폭 요약의 두 경계. 둘 다 **비율(%)** 로 낸다 — 개수가 아니다.
# 배포본 198개 섹터 전수 대조로 확정했다. 개수로 두면 대부분의 섹터에서 0 이라
# 우연히 맞는 것처럼 보이므로, 하락장 표본만으로 검증하면 속는다.
NEAR_HIGH_CUT = -20.0   # 고점 대비 이 이상이면 '고점 근처'
DEEP_CUT = -50.0        # 이 이하면 '깊은 낙폭'


def dd_stats(members, near_high=NEAR_HIGH_CUT, deep=DEEP_CUT):
    """구성종목 낙폭 요약. nearHigh·deep 은 해당 구성종목의 비율(%)이다."""
    cur = [m['dd']['curDD'] for m in members if m.get('dd')]
    mdd = [m['dd']['mdd'] for m in members if m.get('dd')]
    if not cur:
        return dict(n=0, curMean=None, curMedian=None, curWorst=None, curBest=None,
                    mddMean=None, mddWorst=None, nearHigh=0, deep=0)
    return dict(
        n=len(cur),
        curMean=sum(cur) / len(cur), curMedian=statistics.median(cur),
        curWorst=min(cur), curBest=max(cur),
        mddMean=(sum(mdd) / len(mdd)) if mdd else None,
        mddWorst=min(mdd) if mdd else None,
        nearHigh=sum(1 for x in cur if x >= near_high) / len(cur) * 100.0,
        deep=sum(1 for x in cur if x <= deep) / len(cur) * 100.0)


def surge_stats(members):
    """거래대금 급증. sg 는 오늘 ÷ 20일 중앙값이라 2 이상이 급증이다."""
    sgs = [m['sg'] for m in members if m.get('sg') is not None]
    tl = sum(m['tl'] for m in members if m.get('tl') is not None)
    t = sum(m['t'] for m in members if m.get('t') is not None)
    return dict(
        median=statistics.median(sgs) if sgs else None,
        sectorRatio=(tl / t) if t else None,
        n2x=sum(1 for x in sgs if x >= SURGE_2X),
        n3x=sum(1 for x in sgs if x >= SURGE_3X),
        turnLast=tl)


def sector_rs(members):
    """섹터 상대강도는 구성종목 rs 의 **중앙값**이다(평균이 아니다).

    rsTop 은 RS_TOP_CUT 을 넘는 구성종목의 비율(%)이고, 분모는 rs 를 낼 수
    있었던 종목 수다 — 신규 상장으로 rs 가 없는 종목이 비율을 낮추지 않는다.
    """
    vals = [m['rs'] for m in members if m.get('rs') is not None]
    if not vals:
        return None, 0
    top = sum(1 for x in vals if x >= RS_TOP_CUT) / len(vals) * 100.0
    return statistics.median(vals), top
