#!/usr/bin/env python3
"""
매매 시스템 점검 — 원문에서 **규칙이 숫자로 끝까지 적힌** 시스템을 같은 조건으로
돌려, 미리 정한 통과 기준을 넘는 것만 남긴다.

근거 문서는 『유명 트레이더 매매법』(2026-09-23 판). 시스템마다 항목 번호와 값의
두께(원문·[복원]·[2차]·새정의)를 SYSTEMS 표에 적는다. **이 결과는 원작 트레이더의
성과가 아니다** — 미국 선물·주식에서 쓰던 규칙을 국내 일봉에 옮긴 시뮬레이션이다.

공통 조건 (backtest.py 와 같다)
  대상      현재 상장 보통주 · 종가 1,000원 이상 · 20일 평균 거래대금 10억 이상(추정)
  체결      시스템마다 원문 체결 방식을 따른다(close = 신호일 종가 동시호가 가정,
            next_open = 다음 날 시가, stop = 다음 날 예약가 도달 시 max(시가, 예약가)).
            다음 날 시가가 상한가 근처면 체결 불가로 뺀다.
  비용      매도 0.35% + 편도 슬리피지(signals.yaml backtest)
  겹침      같은 시스템·같은 종목은 보유 중에 새로 사지 않는다.

통과 기준은 결과를 보기 전에 signals.yaml `screen` 에 고정했다. 같은 표본에서
여러 시스템을 고르면 운으로 붙는 것이 섞이므로, **앞 절반과 뒤 절반 구간에서
모두** 기준을 넘어야 통과다. 최악 1% 손실(꼬리)을 함께 적는다 — 손절이 없는
역추세 규칙은 승률이 높아도 한 번에 크게 잃는다.
"""
import bisect
from datetime import date, timedelta

from . import backtest as BT
from . import kinds as K

# 시스템 표. 값의 출처와 두께는 docs/SIGNALS.md 11장.
SYSTEMS = {
    'connors_rsi2': dict(
        name='코너스 RSI(2)<5 (#14)', src='#14 원문',
        note='200일선 위 · RSI(2)<5 마감 → 그날 종가 매수 · 종가 > 5일선 매도 · 손절 없음(원문)'),
    'connors_7d': dict(
        name='코너스 7일 최저·최고 (#14 3규칙판)', src='#14 원문',
        note='200일선 위 · 7일 최저 종가 → 종가 매수 · 7일 최고 종가 → 매도 · 손절 없음(원문)'),
    'larry_07': dict(
        name='래리 변동성 돌파 k=0.7 (#12)', src='#12 원문(k 는 채권 화요일판 값)',
        note='시가 + 전일 변동폭×0.7 돌파 매수 · 첫 이익 시가 청산 · 손절 전일 변동폭 50%'),
    'larry_10': dict(
        name='래리 변동성 돌파 k=1.0 (#12)', src='#12 원문(k 는 채권 기본형 값)',
        note='시가 + 전일 변동폭×1.0 돌파 매수 · 첫 이익 시가 청산 · 손절 전일 변동폭 50%'),
    'cooper': dict(
        name='쿠퍼 사흘 눌림 (#10 속편)', src='#10 원문 + 새정의(따라 올리는 손절 = 전일 저가)',
        note='50일선 위 · 사흘 연속 저가·고가 하락(인사이드 1일 허용) → 다음 날 고가+0.1% 돌파 매수 · '
             '손절 눌림 최저 아래 · 전일 저가로 따라 올림 · 20봉 상한'),
    'raschke': dict(
        name='라쉬키 20EMA 첫 되돌림 (#13)', src='#13 원문 + 새정의(첫 닿음 = 10봉 내 닿음 없음 · 목표 전량)',
        note='ADX(14)>30 상승 중 · 20일 EMA 첫 닿음 → 앞 봉 고가 돌파 매수 · 손절 닿은 봉 저가 · '
             '목표 직전 20봉 고가 · 20봉 상한'),
    'turtle20': dict(
        name='터틀 20일 돌파 (#1)', src='#1 [2차] (거르기 규칙 제외)',
        note='20일 최고가 돌파 매수 · 손절 2N · 10일 최저가 이탈 매도'),
    'bb_squeeze': dict(
        name='볼린저 수축 돌파 (#16)', src='#16 원문 + 새정의(10봉 내 6개월 최저 폭)',
        note='띠 폭 6개월 최저 뒤 종가 상단 돌파 → 다음 날 시가 · 손절 직전 10봉 저가 · 종가가 하단 아래면 매도'),
    'moon_disparity': dict(
        name='문병로 이격도<90 (#47)', src='#47 [2차] + 새정의(이격도 = 20일선)',
        note='이격도 90 아래 5일 + 60일선 하락 중 → 다음 날 시가 · 30영업일(6주) 보유 · 손절 없음'),
}


# ─────────────────────────── 지표 (종목당 한 번) ───────────────────────────
def sma_arr(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema_arr(x, n):
    out, e, a = [None] * len(x), None, 2 / (n + 1)
    for i, v in enumerate(x):
        if i == n - 1:
            e = sum(x[:n]) / n
        elif i >= n:
            e = e + a * (v - e)
        out[i] = e
    return out


def rsi_arr(c, n=2):
    """와일더 평활 RSI. 코너스는 2일 RSI 를 쓴다."""
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g, l = g / n, l / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def adx_arr(h, l, c, n=14):
    """와일더 ADX. 방향성 지표·과열도 지표는 열나흘이다(#15 원문)."""
    m = len(c)
    out = [None] * m
    if m < 2 * n + 1:
        return out
    tr, pdm, ndm = [0.0] * m, [0.0] * m, [0.0] * m
    for i in range(1, m):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        ndm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr, p, q = sum(tr[1:n + 1]), sum(pdm[1:n + 1]), sum(ndm[1:n + 1])
    dx = []
    adx = None
    for i in range(n, m):
        if i > n:
            atr = atr - atr / n + tr[i]
            p = p - p / n + pdm[i]
            q = q - q / n + ndm[i]
        pdi = 100 * p / atr if atr else 0
        ndi = 100 * q / atr if atr else 0
        d = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0
        dx.append(d)
        if len(dx) == n:
            adx = sum(dx) / n
        elif len(dx) > n:
            adx = (adx * (n - 1) + d) / n
        out[i] = adx
    return out


def turtle_n(h, l, c, n=20):
    """터틀 변동폭 N — 처음 한 번 20일 단순평균, 이후 (19×어제 + 오늘)/20 (#1 [2차])."""
    m = len(c)
    out = [None] * m
    tr = [None] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
                   for i in range(1, m)]
    if m <= n:
        return out
    v = sum(tr[1:n + 1]) / n
    out[n] = v
    for i in range(n + 1, m):
        v = (v * (n - 1) + tr[i]) / n
        out[i] = v
    return out


class Ind:
    """한 종목의 지표를 한 번 계산해 든다."""

    def __init__(self, s):
        c, h, l = list(s.c), list(s.h), list(s.l)
        self.sma5, self.sma20 = sma_arr(c, 5), sma_arr(c, 20)
        self.sma50, self.sma60, self.sma200 = sma_arr(c, 50), sma_arr(c, 60), sma_arr(c, 200)
        self.rsi2 = rsi_arr(c, 2)
        self.ema20 = ema_arr(c, 20)
        self.adx = adx_arr(h, l, c, 14)
        self.n = turtle_n(h, l, c, 20)
        tv = [c[i] * s.v[i] / 1e8 for i in range(len(c))]
        self.tv20 = sma_arr(tv, 20)
        # 볼린저 폭 (20, 2σ)
        w = [None] * len(c)
        for i in range(19, len(c)):
            win = c[i - 19:i + 1]
            m = sum(win) / 20
            sd = (sum((x - m) ** 2 for x in win) / 20) ** 0.5
            w[i] = (m, sd, 4 * sd / m if m else None)
        self.bb = w


# ─────────────────────────── 신호 ───────────────────────────
def _stack_ok(v, i):
    return v[i] is not None


def signal(key, s, ind, i, p=None):
    """i 봉 종가까지로 판정한 신호. 반환 dict 또는 None.

    mode   close      i 봉 종가에 산다
           next_open  i+1 봉 시가
           stop       i+1 봉에 level 예약 매수
           larry      i+1 봉 시가 + (i 봉 변동폭 × k) 예약 매수
    """
    c, h, l = s.c, s.h, s.l
    p = p or {}
    # 추세 필터(새정의 — engine/search.py 가 거는 조합). 없으면 원문 그대로.
    tm = p.get('trend_ma')
    if tm:
        ma = getattr(ind, f'sma{tm}')[i]
        if ma is None or c[i] <= ma:
            return None
    if key == 'connors_rsi2':
        th = p.get('rsi_th', 5)
        if ind.sma200[i] and c[i] > ind.sma200[i] and ind.rsi2[i] is not None and ind.rsi2[i] < th:
            return dict(mode='close')
    elif key == 'connors_7d':
        if i >= 6 and ind.sma200[i] and c[i] > ind.sma200[i] and c[i] <= min(c[i - 6:i]):
            return dict(mode='close')
    elif key in ('larry_07', 'larry_10'):
        k = p.get('k', 0.7 if key == 'larry_07' else 1.0)
        return dict(mode='larry', k=k, rng=h[i] - l[i])
    elif key == 'cooper':
        if i < 3 or not ind.sma50[i] or c[i] <= ind.sma50[i]:
            return None
        inside = 0
        for d in (i - 2, i - 1, i):
            down = l[d] < l[d - 1] and h[d] < h[d - 1]
            ins = h[d] <= h[d - 1] and l[d] >= l[d - 1]
            if ins:
                inside += 1
            elif not down:
                return None
        if inside > 1:
            return None
        return dict(mode='stop', level=h[i] * 1.001, stop=min(l[i - 2:i + 1]) * 0.999)
    elif key == 'raschke':
        a = ind.adx
        if i < 12 or a[i] is None or a[i - 1] is None or a[i] <= 30 or a[i] <= a[i - 1]:
            return None
        e = ind.ema20
        if e[i] is None or l[i] > e[i]:
            return None
        if any(e[d] is None or l[d] <= e[d] for d in range(i - 10, i)):
            return None
        return dict(mode='stop', level=h[i - 1], stop=l[i], target=max(h[i - 20:i]))
    elif key == 'turtle20':
        ne = p.get('entry_n', 20)                  # 느린 판은 55일 (#1 [2차])
        if i < ne or ind.n[i] is None:
            return None
        return dict(mode='stop', level=max(h[i - ne + 1:i + 1]), n=ind.n[i], breakout=True)
    elif key == 'bb_squeeze':
        b = ind.bb
        if i < 136 or b[i] is None or b[i][2] is None:
            return None
        m, sd, _ = b[i]
        if c[i] <= m + 2 * sd:
            return None
        # 이력이 짧은 종목은 앞쪽 봉에 띠가 없다(첫 실데이터 실행이 여기서 죽었다)
        if any(b[d] is None for d in range(i - 135, i + 1)):
            return None
        ws = [b[d][2] for d in range(i - 135, i + 1)]
        if any(x is None for x in ws):
            return None
        lo = min(ws[:-10])                     # 그 전 126봉의 최저
        if min(ws[-11:-1]) > lo:
            return None
        return dict(mode='next_open', stop=min(l[i - 10:i]))
    elif key == 'moon_disparity':
        if i < 5 or ind.sma60[i] is None or ind.sma60[i - 1] is None:
            return None
        if ind.sma60[i] >= ind.sma60[i - 1]:
            return None
        for d in range(i - 4, i + 1):
            if ind.sma20[d] is None or c[d] / ind.sma20[d] * 100 >= 90:
                return None
        return dict(mode='next_open')
    return None


# ─────────────────────────── 거래 ───────────────────────────
def trade(key, s, ind, i, sig, bt, p=None):
    """신호 한 건을 굴린다. (거래 dict, None) 또는 (None, 사유)."""
    o, h, l, c = s.o, s.h, s.l, s.c
    p = p or {}
    n = len(c)
    slip, cost = bt['slippage_pct'] / 100, bt['cost_sell_pct'] / 100
    mode = sig['mode']
    if mode == 'close':
        j, raw, first_exit = i, c[i], i + 1
    else:
        j = i + 1
        if j >= n:
            return None, 'no_next_bar'
        if o[j] >= c[i] * (1 + bt['limit_gap_pct'] / 100):
            return None, 'limit_gap'
        if mode == 'next_open':
            raw = o[j]
        else:
            lvl = sig['level'] if mode == 'stop' else o[j] + sig['k'] * sig['rng']
            if h[j] < lvl:
                return None, 'no_fill'
            raw = max(o[j], lvl)
        first_exit = j
    if raw <= 0:
        return None, 'bad_price'
    entry = raw * (1 + slip)
    stop = sig.get('stop')
    if key.startswith('larry'):
        stop = raw - 0.5 * sig['rng']
    if key == 'turtle20':
        stop = raw - 2 * sig['n']
    cap = {'cooper': 20, 'raschke': 20, 'moon_disparity': 30}.get(key, bt['max_hold_bars'])
    k, px, reason = first_exit, None, None
    last = min(n - 1, j + cap - 1) if mode != 'close' else min(n - 1, i + cap)
    while k <= last:
        same = (k == j)
        if key == 'cooper' and k > j:
            stop = max(stop, l[k - 1])                 # 따라 올리는 손절(새정의)
        if stop is not None:
            if same and mode in ('stop', 'larry'):
                # 예약 매수일의 저가가 체결 전인지 뒤인지 일봉으로는 모른다. 무조건 손절로
                # 치면 결과가 비관 쪽으로 기운다 — 그날은 종가가 손절가 아래일 때만
                # 종가에 판다(새정의, docs/SIGNALS.md 11장).
                if c[k] <= stop:
                    px, reason = c[k], 'stop'
                    break
            elif l[k] <= stop:
                px, reason = (stop if same else min(o[k], stop)), 'stop'
                break
        if key == 'connors_rsi2':
            # #14 원문: 5일선 위 마감, 또는 과열도 65~75 위(본인이 같이 권함)
            if p.get('exit') == 'rsi':
                if ind.rsi2[k] is not None and ind.rsi2[k] > p.get('rsi_exit', 70):
                    px, reason = c[k], 'rule'
                    break
            elif ind.sma5[k] and c[k] > ind.sma5[k]:
                px, reason = c[k], 'rule'
                break
        if key == 'connors_7d' and k >= 6 and c[k] >= max(c[k - 6:k]):
            px, reason = c[k], 'rule'
            break
        if key.startswith('larry') and k > j and o[k] > entry:
            px, reason = o[k], 'rule'
            break
        if key == 'raschke' and h[k] >= sig['target']:
            px, reason = (max(raw, sig['target']) if same else max(o[k], sig['target'])), 'rule'
            break
        nx = p.get('exit_n', 10)                   # 느린 판은 20일 (#1 [2차])
        if key == 'turtle20' and k > j and k >= nx:
            lo10 = min(l[k - nx:k])
            if l[k] < lo10:
                px, reason = min(o[k], lo10), 'rule'
                break
        if key == 'bb_squeeze' and ind.bb[k] and c[k] < ind.bb[k][0] - 2 * ind.bb[k][1]:
            px, reason = c[k], 'rule'
            break
        if key == 'moon_disparity' and k - j + 1 >= 30:
            px, reason = c[k], 'rule'
            break
        k += 1
    if px is None:
        k = min(k, last)
        px, reason = c[k], ('open_end' if k == n - 1 else 'max_hold')
    ret = (px * (1 - slip) * (1 - cost) / entry - 1) * 100
    return dict(entry_date=s.dates[j], exit_date=s.dates[k], ret_pct=round(ret, 3),
                bars=k - j + 1, reason=reason, hit_target=False), None


# ─────────────────────────── 실행 ───────────────────────────
def run(conn, sc, index_hist=None, end=None, keys=None, log=print):
    bt, u = sc['backtest'], sc['universe']
    keys = keys or list(SYSTEMS)
    end = end or conn.execute('SELECT MAX(asof) FROM px').fetchone()[0]
    days_all = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM px WHERE asof<=? ORDER BY asof', (end,))]
    test_start = (date.fromisoformat(end) - timedelta(days=int(365.25 * bt['years']))).isoformat()
    k0 = max(bisect.bisect_left(days_all, test_start), bt['warmup_bars'])
    if k0 >= len(days_all):
        raise RuntimeError('일봉이 워밍업을 채우지 못한다. --screen --live 로 긴 이력을 받아라')
    test_days = days_all[k0:]
    mid = test_days[len(test_days) // 2]
    data = BT.load(conn, days_all[max(0, k0 - bt['warmup_bars'])], end)
    meta = BT.names(conn)
    log(f'  시스템 점검 — {test_days[0]} ~ {end} ({len(test_days)}영업일, 절반 {mid}) · '
        f'종목 {len(data):,} · 시스템 {len(keys)}')
    idx = {}
    for sym, ser in ((index_hist or {}).get('series') or {}).items():
        rows = [r for r in ser if r.get('close') is not None]
        idx[sym] = ([r['asof'] for r in rows], [r['close'] for r in rows])

    trades = {k: [] for k in keys}
    skipped = {k: {} for k in keys}
    for ci, (code, s) in enumerate(data.items()):
        nm, mk = meta.get(code, (None, None))
        if K.of(code, nm) not in u['kinds'] or len(s.c) < 30:
            continue
        ind = Ind(s)
        gate_n = (sc['regime']['index_ma'] or {}).get(mk)
        start = bisect.bisect_left(s.dates, test_days[0])
        busy = {k: -1 for k in keys}
        for i in range(max(start, 1), len(s.c)):
            if s.c[i] < u['min_price'] or not ind.tv20[i] or \
                    ind.tv20[i] < u['min_turnover_avg20_eok']:
                continue
            for key in keys:
                if i <= busy[key]:
                    continue
                sig = signal(key, s, ind, i)
                if not sig:
                    continue
                t, why = trade(key, s, ind, i, sig, bt)
                if t is None:
                    skipped[key][why] = skipped[key].get(why, 0) + 1
                    continue
                t.update(code=code, signal_date=s.dates[i],
                         gate_open=BT.gate_at(idx.get(mk), s.dates[i], gate_n) if gate_n else None)
                trades[key].append(t)
                busy[key] = s.at[t['exit_date']]
        if ci % 500 == 0:
            log(f'    {ci:,}/{len(data):,} · ' + ' · '.join(f'{k} {len(v)}' for k, v in trades.items()))

    res = {}
    for key, ts in trades.items():
        a, b = [t for t in ts if t['signal_date'] < mid], [t for t in ts if t['signal_date'] >= mid]
        r = sorted(t['ret_pct'] for t in ts)
        res[key] = dict(
            all=BT.stats(ts), first=BT.stats(a), second=BT.stats(b),
            gate_open=BT.stats([t for t in ts if t['gate_open'] is True]),
            gate_open_first=BT.stats([t for t in a if t['gate_open'] is True]),
            gate_open_second=BT.stats([t for t in b if t['gate_open'] is True]),
            worst1=round(r[max(0, int(len(r) * 0.01) - 1)], 2) if r else None,
            worst=round(r[0], 2) if r else None,
            skipped=skipped[key])
    return dict(source='board-screen', as_of=end, test_start=test_days[0], mid=mid,
                test_days=len(test_days), n_codes=len(data), results=res,
                systems={k: SYSTEMS[k] for k in keys})


def verdict(r, crit, prefix=''):
    """통과 여부와 걸린 이유. prefix='gate_open' 이면 게이트 위 부분집합으로 본다."""
    names = ('all', 'first', 'second') if not prefix else (prefix, prefix + '_first',
                                                          prefix + '_second')
    a, f, s = (r[x] for x in names)
    why = []
    if (a.get('n') or 0) < crit['min_trades']:
        why.append(f'거래 {a.get("n", 0)}건 < {crit["min_trades"]}')
    for label, x in (('전체', a), ('앞 절반', f), ('뒤 절반', s)):
        if not x.get('n'):
            why.append(f'{label} 거래 0')
            continue
        if x['win_rate'] < crit['min_win_rate']:
            why.append(f'{label} 승률 {x["win_rate"]}%')
        if x['mean'] <= 0:
            why.append(f'{label} 평균 {x["mean"]:+.2f}%')
    if a.get('n') and (a.get('profit_factor') or 0) < crit['min_profit_factor']:
        why.append(f'PF {a.get("profit_factor")}')
    return not why, why


def summary_lines(p, crit):
    out = [f'시스템 점검 {p["test_start"]} ~ {p["as_of"]} ({p["test_days"]}영업일 · '
           f'앞/뒤 절반 경계 {p["mid"]}) · 비용 포함',
           f'통과 기준: 거래 {crit["min_trades"]}건↑ · 승률 {crit["min_win_rate"]}%↑ · 평균>0 · '
           f'PF {crit["min_profit_factor"]}↑ — 전체·앞 절반·뒤 절반 모두']
    rows = []
    for key, r in p['results'].items():
        nm = p['systems'][key]['name']
        a = r['all']
        for scope, label in (('', ''), ('gate_open', ' +게이트')):
            ok, why = verdict(r, crit, scope)
            x = a if not scope else r['gate_open']
            if not x.get('n'):
                continue
            rows.append((ok, x.get('win_rate') or 0,
                         f'{"✅" if ok else "✗"} {nm}{label}: {x["n"]}건 · 승률 {x["win_rate"]}% · '
                         f'평균 {x["mean"]:+.2f}% · PF {x["profit_factor"]} · 평균 {x["avg_bars"]}일'
                         + (f' · 최악1% {r["worst1"]:+.1f}%' if not scope else '')
                         + ('' if ok else ' — ' + ', '.join(why[:3]))))
    rows.sort(key=lambda t: (not t[0], -t[1]))
    out += [t[2] for t in rows]
    out.append('원작 트레이더의 성과가 아니다 — 국내 일봉으로 옮긴 시뮬레이션 · 폐지 종목 없음(생존편향)')
    return out
