#!/usr/bin/env python3
"""
스윙 시그널 백테스트 — signals.py 의 판정을 과거 날짜마다 다시 세워 거래로 굴린다.

**이 결과는 원작 트레이더의 성과가 아니다.** 원문 규칙에 [복원]·새정의 값을 채워
만든 조합이고(config/signals.yaml), 표본은 이 DB 가 들고 있는 구간뿐이다.

시점 규칙 (원문 CLAUDE.md 5번 체크리스트)
  미래참조   d일 **종가까지의** 일봉으로 판정하고 d+1 일에 체결한다.
             돌파 확인은 d+1 시가, 돌파 감시는 d+1 고가가 돌파가에 닿을 때
             max(시가, 돌파가)에 산다(1일 유효 예약 주문 — 새정의).
  생존편향   DB 에 남아 있는 종목만 본다. 기간 중 상장폐지된 종목의 일봉이 DB 에
             없으면 표본에서 빠진다 — 결과의 `survivorship` 에 몇 종목이 기간 중
             끝났는지 적는다. 0 이면 폐지 종목이 DB 에 없다는 뜻일 수 있다.
  비용       매도 쪽 0.35%(#155) + 편도 슬리피지. 다음 날 시가가 상한가 근처면
             체결 불가로 뺀다(`limit_gap_pct`).
  공시시차   재무를 쓰지 않는다.

라이브와 다른 점 (결과에 그대로 적는다)
  - 시가총액 하한을 걸지 않는다. 과거 시점의 시가총액이 DB 에 없다.
  - 역사적 신고가를 쓰지 않는다. 52주(252영업일)만.
  - 종목 구분(보통주)·시장은 **지금** 이름으로 판정한다.
  - 수급(F7)은 과거 값이 없어 쓰지 않는다. 태그였으므로 판정은 같다.

청산 (#42 원문 + 새정의)
  목표 전   저가가 손절(매수가 −8%)에 닿으면 전량. 같은 날 목표와 손절이 둘 다
            닿으면 손절이 먼저라고 본다(보수적). 목표 없이 time_stop_bars 가
            지나면 그날 종가에 전량.
  목표 뒤   목표(돌파가 × 1.24)에서 절반. 나머지는 손절을 본전으로 올리고, 종가가
            20일선 아래로 마감하면 다음 날 시가에 판다.
"""
import bisect
from array import array
from datetime import date, timedelta

from . import kinds as K
from . import signals as S

REASONS = ('stop', 'time', 'target_then_be', 'target_then_trail', 'max_hold', 'open_end')


# ─────────────────────────── 데이터 ───────────────────────────
class Series:
    """한 종목의 일봉을 배열로. 날짜 → 인덱스 사전을 같이 든다."""
    __slots__ = ('dates', 'o', 'h', 'l', 'c', 'v', 'at')

    def __init__(self):
        self.dates = []
        self.o, self.h, self.l, self.c, self.v = (array('d') for _ in range(5))
        self.at = {}

    def add(self, r):
        if None in (r['open'], r['high'], r['low'], r['close'], r['volume']):
            return
        # 거래정지일에 소스가 시가·고가·저가를 0 으로 주는 봉이 있다(2026-09-25 실측 —
        # 매수가 0 으로 나눠 죽었다). 가격이 없는 봉은 봉이 아니다.
        if min(r['open'], r['high'], r['low'], r['close']) <= 0:
            return
        self.at[r['asof']] = len(self.dates)
        self.dates.append(r['asof'])
        self.o.append(r['open'])
        self.h.append(r['high'])
        self.l.append(r['low'])
        self.c.append(r['close'])
        self.v.append(r['volume'])

    def rows(self, i, n):
        """i 봉까지 최근 n 봉을 signals.stock_factors 가 읽는 dict 목록으로."""
        a = max(0, i - n + 1)
        return [dict(asof=self.dates[k], open=self.o[k], high=self.h[k], low=self.l[k],
                     close=self.c[k], volume=self.v[k]) for k in range(a, i + 1)]


def load(conn, start, end):
    """[start, end] 일봉 {code: Series}. 한 번의 쿼리를 흘려 읽는다."""
    out = {}
    q = ('SELECT code,asof,open,high,low,close,volume FROM px '
         'WHERE asof>=? AND asof<=? ORDER BY code, asof')
    for r in conn.execute(q, (start, end)):
        s = out.get(r['code'])
        if s is None:
            s = out[r['code']] = Series()
        s.add(r)
    return out


def names(conn):
    """종목별 가장 최근 스냅샷의 이름·시장. 폐지 종목도 스냅이 있으면 나온다."""
    q = ('SELECT s.code, s.name, s.market FROM snap s JOIN '
         '(SELECT code, MAX(asof) m FROM snap GROUP BY code) t '
         'ON s.code=t.code AND s.asof=t.m')
    return {r['code']: (r['name'], r['market']) for r in conn.execute(q)}


# ─────────────────────────── 판정 ───────────────────────────
def _ret(c, i, n):
    return None if i - n < 0 or not c[i - n] else (c[i] / c[i - n] - 1) * 100


def _sma(c, i, n):
    return None if i - n + 1 < 0 else sum(c[i - n + 1:i + 1]) / n


def day_signals(day, data, meta, sc):
    """하루치 후보. 반환 [(code, setup, ref, i)] — setup: breakout · watch · base.

    `base` 는 필터 없는 52주 신고가 전체(대조군)다. 대상 조건(가격·거래대금·
    보통주)만 같고 RS·정배열을 걸지 않는다.
    """
    u, lb = sc['universe'], 252
    w = sc['rs']['weights']
    nmap = dict(ret_250d=250, ret_126d=126, ret_63d=63)
    pre, groups = [], {}
    for code, s in data.items():
        i = s.at.get(day)
        if i is None or i < lb:
            continue
        c = s.c
        if c[i] < u['min_price']:
            continue
        nm, mk = meta.get(code, (None, None))
        if K.of(code, nm) not in u['kinds']:
            continue
        tv = sum(c[j] * s.v[j] for j in range(i - 20, i)) / 20 / 1e8
        if tv < u['min_turnover_avg20_eok']:
            continue
        rets = [_ret(c, i, nmap[k]) for k in w]
        if any(r is None for r in rets):
            continue
        score = sum(r * wt for r, wt in zip(rets, w.values()))
        ref = max(c[i - lb:i])
        g = mk if sc['rs']['by_market'] else 'all'
        groups.setdefault(g, []).append((code, score))
        pre.append((code, i, ref))
    pct = {}
    for pairs in groups.values():
        pct.update(S.percentile_ranks(pairs))

    out = []
    mx = sc_proximity(sc)
    for code, i, ref in pre:
        s = data[code]
        brk = s.c[i] > ref
        gap = (ref - s.c[i]) / ref * 100
        if brk:
            out.append((code, 'base', ref, i))
        near = (not brk) and 0 <= gap <= mx
        if not (brk or near) or pct.get(code, 0) < sc['rs']['min_pct']:
            continue
        f = S.stock_factors(s.rows(i, 300), sc)
        if not f['trend_daily'] or (sc['trend']['require_weekly'] and not f['trend_weekly']):
            continue
        if brk:
            out.append((code, 'breakout', ref, i))
        elif (f['contraction_today'] or f['contraction_recent']) or \
                not sc['setups']['watch']['require_contraction']:
            out.append((code, 'watch', ref, i))
    return out


def sc_proximity(sc):
    """근접 갭 상한. 라이브는 settings.yaml proximity.max_gap_pct 를 쓴다 — 같은 값."""
    return sc.get('_max_gap_pct', 5.0)


# ─────────────────────────── 거래 ───────────────────────────
def simulate(s, i, setup, ref, sc):
    """i 봉 신호 한 건을 굴린다. 거래가 안 되면 (None, 사유)."""
    bt, rk = sc['backtest'], sc['risk']
    j = i + 1
    if j >= len(s.dates):
        return None, 'no_next_bar'
    o = s.o[j]
    if o >= s.c[i] * (1 + bt['limit_gap_pct'] / 100):
        return None, 'limit_gap'
    if setup == 'watch':
        if s.h[j] < ref:
            return None, 'no_fill'
        raw = max(o, ref)
    else:
        raw = o
    if raw <= 0:
        return None, 'bad_price'
    slip = bt['slippage_pct'] / 100
    entry = raw * (1 + slip)
    stop = entry * (1 - rk['stop_pct'] / 100)
    target = ref * (1 + rk['target_r'] * rk['stop_pct'] / 100)
    half = bt['partial_at_target']
    legs, hit, reason, k = [], False, None, j
    last = min(len(s.dates) - 1, j + bt['max_hold_bars'] - 1)
    while k <= last:
        lo, hi, op, cl = s.l[k], s.h[k], s.o[k], s.c[k]
        first = k == j
        if not hit:
            if lo <= stop:
                legs.append((1.0, stop if first else min(op, stop)))
                reason = 'stop'
                break
            if hi >= target:
                px = max(raw, target) if first else max(op, target)
                legs.append((half, px))
                hit, stop = True, entry
                # 목표를 친 날은 나머지를 그날 더 판정하지 않는다(장중 순서를 모른다)
            elif k - j + 1 >= bt['time_stop_bars']:
                legs.append((1.0, cl))
                reason = 'time'
                break
        else:
            if lo <= stop:
                legs.append((1 - half, min(op, stop)))
                reason = 'target_then_be'
                break
            ma = _sma(s.c, k, bt['trail_ma'])
            if ma is not None and cl < ma:
                if k + 1 < len(s.dates):
                    legs.append((1 - half, s.o[k + 1]))
                    k += 1
                else:
                    legs.append((1 - half, cl))
                reason = 'target_then_trail'
                break
        k += 1
    if reason is None:
        k = min(k, last)
        rest = 1.0 - sum(w for w, _ in legs)
        legs.append((rest, s.c[k]))
        reason = 'open_end' if k == len(s.dates) - 1 else 'max_hold'
    cost = bt['cost_sell_pct'] / 100
    ret = sum(w * (px * (1 - slip) * (1 - cost) / entry - 1) for w, px in legs) * 100
    return dict(entry_date=s.dates[j], exit_date=s.dates[min(k, len(s.dates) - 1)],
                entry=round(entry, 2), ret_pct=round(ret, 3), bars=k - j + 1,
                reason=reason, hit_target=hit), None


# ─────────────────────────── 집계 ───────────────────────────
def stats(trades):
    if not trades:
        return dict(n=0)
    r = sorted(t['ret_pct'] for t in trades)
    wins = [x for x in r if x > 0]
    loss = [x for x in r if x <= 0]
    gl = -sum(loss)
    reasons = {}
    for t in trades:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
    n = len(r)
    return dict(
        n=n, win_rate=round(len(wins) / n * 100, 1), mean=round(sum(r) / n, 2),
        median=round(r[n // 2] if n % 2 else (r[n // 2 - 1] + r[n // 2]) / 2, 2),
        p10=round(r[int(n * 0.1)], 2), p90=round(r[min(n - 1, int(n * 0.9))], 2),
        profit_factor=None if gl == 0 else round(sum(wins) / gl, 2),
        avg_bars=round(sum(t['bars'] for t in trades) / n, 1),
        target_hit=round(sum(t['hit_target'] for t in trades) / n * 100, 1),
        reasons=reasons)


def gate_at(index, day, n):
    """day 종가 기준 지수 > n일선. 지수 일봉이 없거나 모자라면 None."""
    if not index:
        return None
    ds, cs = index
    k = bisect.bisect_right(ds, day) - 1
    if k < n - 1:
        return None
    return cs[k] > sum(cs[k - n + 1:k + 1]) / n


def run(conn, sc, cfg, index_hist=None, end=None, log=print):
    """백테스트 전체. 반환 payload (state/backtest.json)."""
    bt = sc['backtest']
    sc = dict(sc, _max_gap_pct=cfg['proximity']['max_gap_pct'])
    end = end or conn.execute('SELECT MAX(asof) FROM px').fetchone()[0]
    test_start = (date.fromisoformat(end) - timedelta(days=int(365.25 * bt['years']))).isoformat()
    days_all = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM px WHERE asof<=? ORDER BY asof', (end,))]
    k0 = bisect.bisect_left(days_all, test_start)
    # 워밍업을 못 채우면 검증 시작을 뒤로 민다. 앞 252봉은 52주 신고가 자체가 없어
    # 거래가 0 인데, 그 구간을 '검증 구간' 으로 적으면 기간이 부풀려 보인다.
    k0 = max(k0, bt['warmup_bars'])
    load_from = days_all[max(0, k0 - bt['warmup_bars'])]
    test_days = days_all[k0:]
    if not test_days:
        raise RuntimeError(f'일봉이 {len(days_all)}영업일뿐이라 워밍업 {bt["warmup_bars"]}봉을 '
                           '채우지 못한다. --backtest --live 로 긴 이력을 받아라')
    log(f'  백테스트 — 적재 {load_from} ~ {end} · 검증 {len(test_days)}영업일 '
        f'({test_days[0] if test_days else "–"} ~ {end})')
    data = load(conn, load_from, end)
    meta = names(conn)
    log(f'  종목 {len(data):,}')

    idx = {}
    for sym, ser in ((index_hist or {}).get('series') or {}).items():
        rows = [r for r in ser if r.get('close') is not None]
        idx[sym] = ([r['asof'] for r in rows], [r['close'] for r in rows])

    trades = {'breakout': [], 'watch': [], 'base': []}
    skipped = {}
    busy = {}                       # (setup, code) → 마지막 보유일. 겹치는 신호는 새 거래가 아니다
    n_sig = {'breakout': 0, 'watch': 0, 'base': 0}
    for di, day in enumerate(test_days):
        for code, setup, ref, i in day_signals(day, data, meta, sc):
            n_sig[setup] += 1
            key = (setup, code)
            if busy.get(key, '') >= day:
                continue
            t, why = simulate(data[code], i, setup, ref, sc)
            if t is None:
                skipped[(setup, why)] = skipped.get((setup, why), 0) + 1
                continue
            mk = (meta.get(code) or (None, None))[1]
            n = (sc['regime']['index_ma'] or {}).get(mk)
            t.update(code=code, name=(meta.get(code) or (None,))[0], setup=setup,
                     signal_date=day, market=mk,
                     gate_open=gate_at(idx.get(mk), day, n) if n else None)
            trades[setup].append(t)
            busy[key] = t['exit_date']
        if di % 50 == 0:
            log(f'    {day} · 누적 거래 ' + ' · '.join(f'{k} {len(v)}' for k, v in trades.items()))

    ended = sum(1 for s in data.values() if s.dates and s.dates[-1] < end)
    res = {}
    for setup, ts in trades.items():
        by_year = {}
        for t in ts:
            by_year.setdefault(t['signal_date'][:4], []).append(t)
        res[setup] = dict(
            all=stats(ts),
            gate_open=stats([t for t in ts if t['gate_open'] is True]),
            gate_closed=stats([t for t in ts if t['gate_open'] is False]),
            by_year={y: stats(v) for y, v in sorted(by_year.items())},
            signals=n_sig[setup],
            skipped={w: c for (s_, w), c in skipped.items() if s_ == setup})
    warn = None
    if len(test_days) < 250:
        warn = (f'검증 구간이 {len(test_days)}영업일뿐입니다 — 한 해도 안 되는 표본이라 '
                '결과를 일반화하면 안 됩니다')
    return dict(
        source='board-backtest', as_of=end, warning=warn,
        test_start=test_days[0] if test_days else None,
        test_days=len(test_days), n_codes=len(data),
        survivorship=dict(ended_before_end=ended,
                          note='DB 에 없는 폐지 종목은 표본에 없다 — 결과가 좋게 잡힐 수 있다'),
        deviations=['시가총액 하한 미적용(과거 시총 없음)', '역사적 신고가 미사용(52주만)',
                    '보통주·시장 판정은 현재 이름 기준', '수급 태그 미사용(과거 값 없음)'],
        params=dict(backtest=bt, risk=sc['risk'], rs=sc['rs'], trend=sc['trend']),
        disclaimer='원작 트레이더의 성과가 아니다 — [복원]·새정의 값을 채운 조합의 과거 시뮬레이션',
        results=res,
        trades={k: v[-200:] for k, v in trades.items()})


def summary_lines(p):
    """로그·텔레그램용 요약 줄."""
    out = [f'백테스트 {p["test_start"]} ~ {p["as_of"]} ({p["test_days"]}영업일 · '
           f'{p["n_codes"]:,}종목 · 폐지 추정 {p["survivorship"]["ended_before_end"]})']
    if p.get('warning'):
        out.append('⚠ ' + p['warning'])
    label = dict(breakout='돌파 확인', watch='돌파 감시', base='대조군(52주 신고가 전체)')
    for k in ('breakout', 'watch', 'base'):
        r = p['results'][k]
        a = r['all']
        if not a.get('n'):
            out.append(f'{label[k]}: 거래 0')
            continue
        line = (f'{label[k]}: {a["n"]}건 · 승률 {a["win_rate"]}% · 평균 {a["mean"]:+.2f}% · '
                f'중앙 {a["median"]:+.2f}% · PF {a["profit_factor"]} · 평균 {a["avg_bars"]}일 · '
                f'목표 도달 {a["target_hit"]}%')
        out.append(line)
        for g, nm in (('gate_open', '게이트 위'), ('gate_closed', '게이트 아래')):
            x = r[g]
            if x.get('n'):
                out.append(f'   {nm}: {x["n"]}건 · 승률 {x["win_rate"]}% · 평균 {x["mean"]:+.2f}%')
        yrs = ' · '.join(f'{y} {v["mean"]:+.2f}%({v["n"]})' for y, v in r['by_year'].items()
                         if v.get('n'))
        if yrs:
            out.append(f'   연도별 평균: {yrs}')
        if r['skipped']:
            out.append('   제외: ' + ' · '.join(f'{w} {c}' for w, c in r['skipped'].items()))
    out.append('비용: 매도 0.35% + 편도 슬리피지 · 라이브와 다른 점: ' + ' / '.join(p['deviations']))
    out.append(p['disclaimer'])
    return out
