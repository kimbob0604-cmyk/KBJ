#!/usr/bin/env python3
"""
ETF 대시보드 — 집계와 렌더링.

기준일 규칙: 항상 '직전 완료 영업일'을 쓴다. 아침 9시에 도는 잡이라 당일 봉은
장중 값이고, 그걸 그날 수치로 쓰면 등락률·거래량이 전부 반쪽이 된다.

유동성 필터: 시가총액과 거래대금 하한을 둔다. 없으면 하루 몇 백만원 거래되는
초소형 ETF가 등락률 상위를 독식해서 순위표가 쓸모없어진다.
"""
import statistics as st
from datetime import date, timedelta, timezone

from kbj.core.time import now_kst as _kbj_now_kst

KST = timezone(timedelta(hours=9))


def kst_now():
    """컨테이너는 UTC 로 돈다. 한국 시장 데이터에 UTC 시각을 붙이면 9시간 어긋난다."""
    return _kbj_now_kst().astimezone(KST)  # KBJ P2(설계 §7.2): 벽시계는 kbj.core.time 한 곳

MIN_MKTCAP = 50.0      # 억원. 이 미만은 순위에서 제외
MIN_TURN = 1.0         # 억원/일. 거래대금 하한
DOM = {1, 2, 3}        # 국내로 볼 탭코드

PERIODS = [('1주', 7), ('1개월', 30), ('3개월', 91), ('6개월', 182), ('1년', 365)]


# ─────────────────────────────── 기준일 ───────────────────────────────
def base_dates(conn, today):
    """(기준일, 직전 영업일). 당일 장중 봉은 기준일로 쓰지 않는다."""
    ds = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM etf_px ORDER BY asof DESC LIMIT 10')]
    if not ds:
        return None, None
    done = [d for d in ds if d < today] or ds
    return done[0], (done[1] if len(done) > 1 else None)


def _universe(conn, d):
    """기준일의 종목 메타 + 시총 + 거래대금. 순위 계산의 공통 모집단."""
    rows = conn.execute("""
        SELECT m.code, m.name, m.tab, p.close, p.volume, a.mktcap, a.nav, a.units
        FROM etf_meta m
        JOIN etf_px  p ON p.code=m.code AND p.asof=?
        LEFT JOIN etf_aum a ON a.code=m.code AND a.asof=(
            SELECT MAX(asof) FROM etf_aum WHERE code=m.code)
    """, (d,)).fetchall()
    out = {}
    for code, name, tab, close, vol, mkt, nav, units in rows:
        out[code] = dict(code=code, name=name, tab=tab or 0, close=close, volume=vol or 0,
                         mktcap=mkt or 0, nav=nav, units=units,
                         lev=is_lev(name, tab), dom=(tab or 0) in DOM,
                         turnover=(close or 0) * (vol or 0) / 1e8)
    return out


LEV_WORDS = ('레버리지', '인버스', '2X', '2x', '선물인버스', '롱', '숏')


def is_lev(name, tab):
    """레버리지·인버스 여부. 순위표 상단을 이들이 독식하므로 표시해서 걸러 볼 수 있게 한다."""
    return bool(tab == 3 or any(w in (name or '') for w in LEV_WORDS))


def _liquid(u):
    return [x for x in u.values()
            if x['mktcap'] >= MIN_MKTCAP and x['turnover'] >= MIN_TURN and x['close']]


# ─────────────────────────────── 집계 ───────────────────────────────
def movers(conn, d, prev_d, n=10):
    """당일 등락률 상·하위."""
    if not prev_d:
        return [], []
    u = _universe(conn, d)
    pv = dict(conn.execute('SELECT code, close FROM etf_px WHERE asof=?', (prev_d,)))
    out = []
    for x in _liquid(u):
        p = pv.get(x['code'])
        if not p:
            continue
        out.append({**x, 'chg': (x['close'] / p - 1) * 100})
    out.sort(key=lambda z: -z['chg'])
    return out[:n], list(reversed(out[-n:]))


def _ref_date(conn, d, days):
    """d 로부터 days 일 전 이하의 가장 가까운 거래일."""
    target = (date.fromisoformat(d) - timedelta(days=days)).isoformat()
    r = conn.execute('SELECT MAX(asof) FROM etf_px WHERE asof<=?', (target,)).fetchone()[0]
    return r


def period_returns(conn, d, n=5):
    """기간별 수익률 상·하위. 기준일 이전 데이터가 없는 신규 상장은 자동으로 빠진다."""
    u = _universe(conn, d)
    liq = _liquid(u)
    res = {}
    windows = list(PERIODS) + [('YTD', (date.fromisoformat(d) - date(date.fromisoformat(d).year, 1, 1)).days)]
    for label, days in windows:
        ref = _ref_date(conn, d, days)
        if not ref:
            continue
        base = dict(conn.execute('SELECT code, close FROM etf_px WHERE asof=?', (ref,)))
        rows = []
        for x in liq:
            b = base.get(x['code'])
            if not b:
                continue
            rows.append({**x, 'ret': (x['close'] / b - 1) * 100})
        if not rows:
            continue
        rows.sort(key=lambda z: -z['ret'])
        res[label] = {'ref': ref, 'up': rows[:n], 'down': list(reversed(rows[-n:]))}
    return res


def flows(conn, d, n=10):
    """설정·환매 추정. 상장좌수 변화 × NAV.

    좌수는 시가총액÷NAV 로 구한다. 두 값이 같은 스냅샷에서 오므로 주가 변동이
    비율에서 상쇄되고, 남는 건 실제 좌수 증감이다.
    """
    ds = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM etf_aum ORDER BY asof DESC LIMIT 2')]
    if len(ds) < 2:
        return [], [], None
    cur, prv = ds[0], ds[1]
    a = {r[0]: r for r in conn.execute(
        'SELECT code, nav, mktcap, units FROM etf_aum WHERE asof=? AND units IS NOT NULL', (cur,))}
    b = {r[0]: r for r in conn.execute(
        'SELECT code, nav, mktcap, units FROM etf_aum WHERE asof=? AND units IS NOT NULL', (prv,))}
    meta = {r[0]: r for r in conn.execute('SELECT code, name, tab FROM etf_meta')}
    out = []
    for code in a.keys() & b.keys():
        _, nav, mkt, u1 = a[code]
        _, _, _, u0 = b[code]
        if not u0 or not u1 or (mkt or 0) < MIN_MKTCAP:
            continue
        amt = (u1 - u0) * nav / 1e8                      # 억원
        if abs(amt) < 1.0:
            continue
        m = meta.get(code)
        out.append(dict(code=code, name=m[1] if m else code, tab=(m[2] if m else 0) or 0,
                        amt=amt, mktcap=mkt, pct=(u1 / u0 - 1) * 100))
    out.sort(key=lambda z: -z['amt'])
    return out[:n], list(reversed(out[-n:])), (prv, cur)


def volume_spikes(conn, d, n=10, look=20):
    """거래 급증 — 당일 거래량 ÷ 직전 20거래일 중앙값."""
    u = _universe(conn, d)
    days = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM etf_px WHERE asof<? ORDER BY asof DESC LIMIT ?', (d, look))]
    if len(days) < 5:
        return []
    hist = {}
    for code, vol in conn.execute(
            'SELECT code, volume FROM etf_px WHERE asof IN (%s)' % ','.join('?' * len(days)), days):
        hist.setdefault(code, []).append(vol or 0)
    out = []
    for x in _liquid(u):
        h = [v for v in hist.get(x['code'], []) if v > 0]
        if len(h) < 5:
            continue
        m = st.median(h)
        # 평소 거래가 사실상 없던 종목은 배수가 무한대로 튄다. 중앙값 거래대금에도
        # 하한을 걸어야 '거래 급증'이 의미를 갖는다. (미적용 시 중앙값 8주 종목이 1위)
        if m <= 0 or m * x['close'] / 1e8 < MIN_TURN:
            continue
        out.append({**x, 'mult': x['volume'] / m, 'med': m})
    out = [z for z in out if z['mult'] >= 2.0]
    out.sort(key=lambda z: -z['mult'])
    return out[:n]


def new_listings(conn, d, days=60, n=12):
    """신규 상장 — 시세 이력이 최근에 시작된 종목."""
    cut = (date.fromisoformat(d) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT m.code, m.name, m.tab, MIN(p.asof) first_px, MAX(p.close)
        FROM etf_meta m JOIN etf_px p ON p.code=m.code
        GROUP BY m.code HAVING first_px >= ? ORDER BY first_px DESC LIMIT ?
    """, (cut, n)).fetchall()
    u = _universe(conn, d)
    return [dict(code=c, name=nm, tab=t or 0, first=f,
                 mktcap=(u.get(c) or {}).get('mktcap', 0)) for c, nm, t, f, _ in rows]


def size_top(conn, d, n=10):
    u = _universe(conn, d)
    rows = sorted([x for x in u.values() if x['mktcap']], key=lambda z: -z['mktcap'])
    return rows[:n]


def turnover_top(conn, d, n=10):
    u = _universe(conn, d)
    rows = sorted(u.values(), key=lambda z: -z['turnover'])
    return rows[:n]


def _prior_holders(conn, run_date):
    """비교 대상 펀드들이 '이전 스냅샷'에서 보유했던 종목별 펀드 수."""
    return dict(conn.execute("""
        SELECT h.code, COUNT(DISTINCT h.fund_id)
        FROM holding h
        JOIN (SELECT DISTINCT fund_id, prev_asof FROM change_log WHERE run_date=?) p
          ON p.fund_id = h.fund_id AND p.prev_asof = h.asof
        GROUP BY h.code""", (run_date,)))


def _cur_holders(conn, run_date):
    return dict(conn.execute("""
        SELECT h.code, COUNT(DISTINCT h.fund_id)
        FROM holding h
        JOIN (SELECT DISTINCT fund_id, asof FROM change_log WHERE run_date=?) p
          ON p.fund_id = h.fund_id AND p.asof = h.asof
        GROUP BY h.code""", (run_date,)))


def _agg(conn, run_date, kinds, n):
    q = """SELECT c.code, c.name, COUNT(DISTINCT c.fund_id) n_etf,
                  GROUP_CONCAT(DISTINCT f.name), MAX(c.cur_wt)
           FROM change_log c JOIN fund f ON f.fund_id=c.fund_id
           WHERE c.run_date=? AND c.kind IN (%s)
           GROUP BY c.code ORDER BY n_etf DESC, c.code""" % ','.join('?' * len(kinds))
    return [dict(code=a, name=b, n=c, funds=(d or '').split(','), wt=e)
            for a, b, c, d, e in conn.execute(q, (run_date, *kinds))][:n * 3]


def holdings_new(conn, run_date, n=12):
    """ETF가 새로 편입한 종목. 종목 단위로 묶어 '몇 개 ETF가 담았나'로 센다."""
    rows = _agg(conn, run_date, ('NEW', 'IN10'), n)
    prior = _prior_holders(conn, run_date)
    for x in rows:
        # 이전에 아무도 안 들고 있었는데 여러 곳이 한꺼번에 담았다 → 신규 상장·지수 편입
        x['event'] = (prior.get(x['code'], 0) == 0 and x['n'] >= 5)
    return rows[:n]


def holdings_out(conn, run_date, n=12):
    """제외된 종목. '전 보유처에서 동시 소멸'은 운용사 판단이 아니라 기업 이벤트다.

    실측 예: 한화(000880)가 55개 ETF 전부에서 같은 날 사라졌다. 다른 한화 계열은
    그대로 남아 있었다. 거래정지·합병·분할 같은 종목 사유이지 매도 시그널이 아니다.
    섞어서 보여주면 가장 큰 숫자가 항상 가짜 시그널이 된다.
    """
    rows = _agg(conn, run_date, ('DROP', 'OUT10'), n)
    prior = _prior_holders(conn, run_date)
    cur = _cur_holders(conn, run_date)
    for x in rows:
        p = prior.get(x['code'], 0)
        x['prior'] = p
        x['event'] = (p >= 3 and cur.get(x['code'], 0) == 0)
    real = [x for x in rows if not x['event']][:n]
    events = [x for x in rows if x['event']][:6]
    return real, events


def theme_moves(conn, run_date, n=10):
    """비중 확대·축소가 여러 ETF에서 겹친 종목."""
    rows = conn.execute("""
        SELECT c.code, c.name, c.kind, COUNT(DISTINCT c.fund_id) n_etf,
               AVG(c.qty_pct_adj), GROUP_CONCAT(DISTINCT f.name)
        FROM change_log c JOIN fund f ON f.fund_id=c.fund_id
        WHERE c.run_date=? AND c.kind IN ('ADD','CUT')
        GROUP BY c.code, c.kind HAVING n_etf>=2
        ORDER BY n_etf DESC, ABS(AVG(c.qty_pct_adj)) DESC LIMIT ?""", (run_date, n)).fetchall()
    return [dict(code=a, name=b, kind=k, n=c, avg=d, funds=(e or '').split(','))
            for a, b, k, c, d, e in rows]


# ─────────────────────────── 대시보드 데이터 조립 ───────────────────────────
def collect(conn, today, run_date):
    base, prev = base_dates(conn, today)
    if not base:
        return None
    u = _universe(conn, base)
    pv = dict(conn.execute('SELECT code, close FROM etf_px WHERE asof=?', (prev,))) if prev else {}
    liq = _liquid(u)
    ups = sum(1 for x in liq if pv.get(x['code']) and x['close'] > pv[x['code']])
    dns = sum(1 for x in liq if pv.get(x['code']) and x['close'] < pv[x['code']])
    aum = sum(x['mktcap'] for x in u.values())

    k200 = next((x for x in u.values() if x['name'] == 'KODEX 200'), None)
    k200c = ((k200['close'] / pv[k200['code']] - 1) * 100
             if k200 and pv.get(k200['code']) else None)

    up, down = movers(conn, base, prev)
    _all_up, _all_dn = movers(conn, base, prev, n=10 ** 6)
    _nl = [x for x in _all_up if not x['lev']]
    _ho = holdings_out(conn, run_date)
    fi, fo, span = flows(conn, base)
    pairs = conn.execute('SELECT COUNT(DISTINCT fund_id) FROM change_log WHERE run_date=?',
                         (run_date,)).fetchone()[0]

    def _k(v):
        return ('up' if v > 0 else 'dn') if v else ''

    kpi = [('KODEX 200', f'{k200c:+.2f}%' if k200c is not None else '–',
            _k(k200c or 0)),
           ('상승 / 하락', f'<span class="up">{ups}</span> / <span class="dn">{dns}</span>', ''),
           ('전체 순자산', _amt_plain(aum), ''),
           ('구성종목 변동 ETF', f'{pairs}개', '')]

    return dict(
        base=base, prev=prev, n_etf=len(u),
        kpi=kpi, up=up, down=down,
        up_nl=_nl[:10], down_nl=list(reversed(_nl[-10:])),
        periods=period_returns(conn, base),
        flow_in=fi, flow_out=fo, flow_span=span,
        spikes=volume_spikes(conn, base),
        new_etf=new_listings(conn, base),
        size=size_top(conn, base), turn=turnover_top(conn, base),
        h_new=holdings_new(conn, run_date), h_out=_ho[0], h_events=_ho[1],
        new_detail=new_detail(conn, run_date),
        wt_up=weight_moves(conn, run_date)[0], wt_down=weight_moves(conn, run_date)[1],
        h_moves=theme_moves(conn, run_date),
        hold_note=_hold_note(conn, run_date),
        generated=kst_now().strftime('%Y-%m-%d %H:%M'))


def _amt_plain(v):
    return f'{v/10000:,.1f}조' if abs(v) >= 10000 else f'{v:,.0f}억'


def _pair_count(conn, max_gap=14):
    """비교 가능한 펀드 수. tracker 를 import 하면 순환이 되므로 여기서 직접 센다."""
    seq = {}
    for fid, a in conn.execute(
            'SELECT fund_id, asof FROM holding GROUP BY fund_id, asof ORDER BY fund_id, asof DESC'):
        seq.setdefault(fid, []).append(a)
    n = 0
    for dates in seq.values():
        if len(dates) >= 2 and (date.fromisoformat(dates[0])
                                - date.fromisoformat(dates[1])).days <= max_gap:
            n += 1
    return n


def _hold_note(conn, run_date):
    n = _pair_count(conn)
    if n == 0:
        return '아직 비교할 이전 스냅샷이 없습니다. 다음 영업일부터 집계됩니다.'
    span = conn.execute('SELECT MIN(prev_asof), MAX(asof) FROM change_log WHERE run_date=?',
                        (run_date,)).fetchone()
    if not span or not span[0]:
        return f'{n}개 ETF 비교 — 감지된 변동 없음'
    return f'{n}개 ETF 비교 · {span[0]} → {span[1]} · 운용사 공시 기준일이 달라 ETF마다 비교 구간이 다릅니다'


# ══════════════════════ 장중 갱신용 분리 구조 ══════════════════════
# 무거운 것(구성종목 PDF·1년 일봉)은 아침에 한 번만 돌려 base.json 으로 굳히고,
# 장중에는 목록 API 한 번(1.7초)만 받아서 그 위에 얹는다. 15분 주기가 가능한 이유다.

def export_base(conn, run_date):
    """장중 갱신이 참조할 확정 데이터. 하루 한 번 생성한다."""
    base, prev = base_dates(conn, run_date)
    if not base:
        return None
    ref_dates, ref_close = {}, {}
    wins = list(PERIODS) + [('YTD', (date.fromisoformat(base)
                                     - date(date.fromisoformat(base).year, 1, 1)).days)]
    for label, days in wins:
        r = _ref_date(conn, base, days)
        if not r:
            continue
        ref_dates[label] = r
        for code, close in conn.execute('SELECT code, close FROM etf_px WHERE asof=?', (r,)):
            ref_close.setdefault(code, {})[label] = close

    # 거래 급증 판정용 20거래일 중앙값
    days = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM etf_px WHERE asof<=? ORDER BY asof DESC LIMIT 20', (base,))]
    vols = {}
    for code, v in conn.execute(
            'SELECT code, volume FROM etf_px WHERE asof IN (%s)' % ','.join('?' * len(days)), days):
        vols.setdefault(code, []).append(v or 0)

    u = _universe(conn, base)
    etf = {}
    for code, x in u.items():
        h = [v for v in vols.get(code, []) if v > 0]
        etf[code] = {'n': x['name'], 't': x['tab'], 'l': int(x['lev']),
                     'pc': x['close'], 'r': ref_close.get(code, {}),
                     'mv': (st.median(h) if len(h) >= 5 else None)}
    fi, fo, span = flows(conn, base)
    ho, hev = holdings_out(conn, run_date)
    _wm = weight_moves(conn, run_date)
    return {
        'base_date': base, 'prev_date': prev, 'ref_dates': ref_dates,
        'generated': kst_now().strftime('%Y-%m-%d %H:%M'),
        'etf': etf,
        'flows': {'span': span, 'in': fi, 'out': fo},
        'holdings': {'note': _hold_note(conn, run_date),
                     'new': holdings_new(conn, run_date), 'out': ho,
                     'events': hev, 'moves': theme_moves(conn, run_date),
                     'new_detail': new_detail(conn, run_date),
                     'wt_up': _wm[0], 'wt_down': _wm[1]},
        'new_etf': new_listings(conn, base),
    }


def live_view(base, live, n=10):
    """base.json + 장중 시세 → 대시보드 데이터. DB 를 건드리지 않는다."""
    now = kst_now()
    B = base['etf']
    rows = []
    for code, L in live.items():
        b = B.get(code)
        if not b:
            continue
        x = dict(code=code, name=L['name'], tab=L['tab'], lev=bool(b['l']),
                 close=L['price'], mktcap=L['mktcap'], turnover=L['turnover'],
                 volume=L['volume'], nav=L['nav'], units=L['units'],
                 chg=L['chg'], ref=b['r'], medvol=b['mv'])
        rows.append(x)
    liq = [x for x in rows
           if x['mktcap'] >= MIN_MKTCAP and x['turnover'] >= MIN_TURN and x['close']]

    mv = [x for x in liq if x['chg'] is not None]
    mv.sort(key=lambda z: -z['chg'])
    up, down = mv[:n], list(reversed(mv[-n:]))
    # 상승 상위가 전부 레버리지인 날이 흔하다. 전체에서 다시 걸러야 목록이 채워진다.
    nl = [x for x in mv if not x['lev']]
    up_nl, down_nl = nl[:n], list(reversed(nl[-n:]))

    periods = {}
    for label, refd in base['ref_dates'].items():
        got = [{**x, 'ret': (x['close'] / x['ref'][label] - 1) * 100}
               for x in liq if x['ref'].get(label)]
        if not got:
            continue
        got.sort(key=lambda z: -z['ret'])
        periods[label] = {'ref': refd, 'up': got[:5], 'down': list(reversed(got[-5:]))}

    sp = [{**x, 'mult': x['volume'] / x['medvol'], 'med': x['medvol']}
          for x in liq if x['medvol'] and x['medvol'] > 0
          and x['medvol'] * x['close'] / 1e8 >= MIN_TURN]
    sp = sorted([z for z in sp if z['mult'] >= 2.0], key=lambda z: -z['mult'])[:n]

    ups = sum(1 for x in liq if (x['chg'] or 0) > 0)
    dns = sum(1 for x in liq if (x['chg'] or 0) < 0)
    k200 = next((x for x in rows if x['name'] == 'KODEX 200'), None)
    aum = sum(x['mktcap'] for x in rows)
    fl = base['flows']
    hd = base['holdings']
    return dict(
        base=base['base_date'], prev=base.get('prev_date'), n_etf=len(rows),
        live_date=now.strftime('%Y-%m-%d'), live_ts=now.strftime('%H:%M'),
        is_live=_market_open(now),
        kpi=[('KODEX 200', f"{k200['chg']:+.2f}%" if k200 and k200['chg'] is not None else '–',
              ('up' if k200 and (k200['chg'] or 0) > 0 else 'dn')),
             ('상승 / 하락', f'<span class="up">{ups}</span> / <span class="dn">{dns}</span>', ''),
             ('전체 순자산', _amt_plain(aum), ''),
             ('구성종목 변동 ETF', f"{len({f for x in hd['moves'] for f in x['funds']})}개", '')],
        up=up, down=down, up_nl=up_nl, down_nl=down_nl, periods=periods, spikes=sp,
        flow_in=fl['in'], flow_out=fl['out'], flow_span=fl['span'],
        new_etf=base['new_etf'],
        size=sorted([x for x in rows if x['mktcap']], key=lambda z: -z['mktcap'])[:n],
        turn=sorted(rows, key=lambda z: -z['turnover'])[:n],
        h_new=hd['new'], h_out=hd['out'], h_events=hd['events'], h_moves=hd['moves'],
        new_detail=hd.get('new_detail', []), wt_up=hd.get('wt_up', []),
        wt_down=hd.get('wt_down', []),
        hold_note=hd['note'], base_generated=base['generated'],
        generated=now.strftime('%Y-%m-%d %H:%M'))


def _market_open(now):
    """평일 09:00~15:40 만 '장중'. 그 밖의 시각은 마지막 값이므로 그렇게 표시한다."""
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return 9 * 60 <= m <= 15 * 60 + 40


def new_detail(conn, run_date, n=15):
    """개별 편입 — 어떤 ETF가 몇 % 비중으로 담았나. 종목 단위 집계와 같이 봐야 그림이 산다."""
    return [dict(name=a, code=b, fund=c, wt=d) for a, b, c, d in conn.execute("""
        SELECT c.name, c.code, f.name, c.cur_wt
        FROM change_log c JOIN fund f ON f.fund_id=c.fund_id
        WHERE c.run_date=? AND c.kind IN ('NEW','IN10') AND c.cur_wt IS NOT NULL
        ORDER BY c.cur_wt DESC LIMIT ?""", (run_date, n))]


def weight_moves(conn, run_date, n=12, floor=0.5):
    """보유비중이 크게 바뀐 종목 (%p 기준).

    비중은 주가가 올라도 늘어난다. 그래서 수량 증감(qty_pct_adj)을 함께 내보내
    실제로 사고판 것인지 가격 때문인지 구분할 수 있게 한다.
    """
    rows = conn.execute("""
        SELECT c.name, c.code, f.name, c.prev_wt, c.cur_wt, c.qty_pct_adj,
               (c.cur_wt - c.prev_wt) d
        FROM change_log c JOIN fund f ON f.fund_id=c.fund_id
        WHERE c.run_date=? AND c.kind IN ('ADD','CUT')
          AND c.prev_wt IS NOT NULL AND c.cur_wt IS NOT NULL
          AND ABS(c.cur_wt - c.prev_wt) >= ?
        ORDER BY ABS(c.cur_wt - c.prev_wt) DESC""", (run_date, floor))
    out = [dict(name=a, code=b, fund=c, pw=p, cw=q, qty=(r or 0), dpp=d)
           for a, b, c, p, q, r, d in rows]
    return ([x for x in out if x['dpp'] > 0][:n], [x for x in out if x['dpp'] < 0][:n])
