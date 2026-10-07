#!/usr/bin/env python3
"""
시스템 조합 탐색 — 원문이 허락한 범위 안에서 값·필터를 바꿔 보되, **최근 구간을
봉인해 두고** 그 앞에서만 고른다.

순서 (바꾸면 봉인이 깨진다)
  1. 검증 창을 탐색 구간과 봉인 구간(최근 holdout_days 영업일)으로 자른다.
  2. 탐색 구간만으로 조합마다 성적을 매기고, signals.yaml `screen` 기준을 탐색 구간
     전체·앞 절반·뒤 절반 모두에서 넘는 조합만 '후보' 로 남긴다.
  3. 후보만 봉인 구간에서 **한 번** 잰다. 봉인 구간에서도 `search.holdout` 기준을
     넘어야 '채택' 이다. 봉인 구간 성적으로 후보를 다시 고르거나 순위를 매기지 않는다.

같은 봉인 구간을 보고 조합 표를 고친 뒤 다시 돌리면 그 구간은 더 이상 표본 밖이 아니다.
그래서 조합 표(VARIANTS)와 기준은 코드·설정에 고정하고, 결과에 조합 수를 적는다 —
조합이 많을수록 운으로 통과하는 것이 섞인다.

값의 근거
  코너스 RSI 문턱 2·5·10      #14 원문 "낮출수록 좋아진다 — 10·5·2·1"
  코너스 청산 5일선 / RSI>70  #14 원문(5일선) · 원문 "65~75 위" 의 가운데 70 은 새정의
  래리 k 0.7·1.0              #12 원문(채권 값) · 0.5 는 원문에 없는 새정의
  래리 추세 필터 20·60일선     새정의(#48 이 절대 모멘텀과 섞은 판을 언급할 뿐 값은 [복원])
  터틀 20/10 · 55/20          #1 [2차]
  지수 게이트                  #42 원문(코스피 60일선 · 코스닥 120일선)
"""
import bisect
from datetime import date, timedelta

from . import backtest as BT
from . import kinds as K
from . import systems as SY


def _v(vid, base, gate=False, **p):
    return dict(id=vid + ('+gate' if gate else ''), base=base, gate=gate, p=p)


def variants():
    out = []
    for gate in (False, True):
        for th in (2, 5, 10):
            for ex in ('sma5', 'rsi'):
                out.append(_v(f'connors_rsi2 th{th} exit_{ex}', 'connors_rsi2', gate,
                              rsi_th=th, exit=ex))
        out.append(_v('connors_7d', 'connors_7d', gate))
        for k in (0.5, 0.7, 1.0):
            for tm in (None, 20, 60):
                out.append(_v(f'larry k{k}' + (f' ma{tm}' if tm else ''), 'larry_07', gate,
                              k=k, trend_ma=tm))
        for ne, nx in ((20, 10), (55, 20)):
            out.append(_v(f'turtle {ne}/{nx}', 'turtle20', gate, entry_n=ne, exit_n=nx))
    return out


def run(conn, sc, index_hist=None, end=None, log=print):
    bt, u = sc['backtest'], sc['universe']
    end = end or conn.execute('SELECT MAX(asof) FROM px').fetchone()[0]
    days_all = [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM px WHERE asof<=? ORDER BY asof', (end,))]
    meta = BT.names(conn)
    idx = {}
    for sym, ser in ((index_hist or {}).get('series') or {}).items():
        rows = [r for r in ser if r.get('close') is not None]
        idx[sym] = ([r['asof'] for r in rows], [r['close'] for r in rows])

    def tradable(code, s):
        return K.of(code, meta.get(code, (None, None))[0]) in u['kinds']

    def eligible(s, ind, i):
        return s.c[i] >= u['min_price'] and bool(ind.tv20[i]) and \
            ind.tv20[i] >= u['min_turnover_avg20_eok']

    def gate(code, day):
        mk = meta.get(code, (None, None))[1]
        n = (sc['regime']['index_ma'] or {}).get(mk)
        return BT.gate_at(idx.get(mk), day, n) if n else False

    return evaluate(lambda start: BT.load(conn, start, end), days_all, end, variants(),
                    bt, sc['search'], sc['screen'], tradable, eligible, gate, log)


def evaluate(load, days_all, end, vs, bt, se, crit, tradable, eligible, gate, log=print):
    """시장과 무관한 탐색 본체. 국장(run)과 미장(us/btsearch.py)이 같이 쓴다.

    load(start)            → {code: BT.Series} ([start, end] 일봉)
    tradable(code, s)      → 이 종목을 대상으로 삼나 (종류 필터)
    eligible(s, ind, i)    → i 봉에 신호를 볼 자격 (가격·거래대금 하한)
    gate(code, day)        → 그날 시장 게이트가 열렸나 (True 만 연 것으로 본다)
    """
    hcrit = se['holdout']
    start = (date.fromisoformat(end) - timedelta(days=int(365.25 * se['years']))).isoformat()
    k0 = max(bisect.bisect_left(days_all, start), bt['warmup_bars'])
    test_days = days_all[k0:]
    if len(test_days) <= se['holdout_days'] + 100:
        raise RuntimeError(f'검증 창 {len(test_days)}영업일 — 봉인 {se["holdout_days"]}일을 떼면 '
                           '탐색할 날이 모자란다. 긴 이력을 받아라')
    hold_start = test_days[-se['holdout_days']]
    search_days = test_days[:-se['holdout_days']]
    mid = search_days[len(search_days) // 2]
    data = load(days_all[max(0, k0 - bt['warmup_bars'])])
    log(f'  조합 탐색 — 탐색 {search_days[0]} ~ {search_days[-1]} ({len(search_days)}영업일, '
        f'절반 {mid}) · 봉인 {hold_start} ~ {end} ({se["holdout_days"]}영업일) · '
        f'종목 {len(data):,} · 조합 {len(vs)}')

    trades = {v['id']: [] for v in vs}
    for ci, (code, s) in enumerate(data.items()):
        if not tradable(code, s) or len(s.c) < 30:
            continue
        ind = SY.Ind(s)
        first = bisect.bisect_left(s.dates, test_days[0])
        busy = {v['id']: -1 for v in vs}
        for i in range(max(first, 1), len(s.c)):
            if not eligible(s, ind, i):
                continue
            g = None
            for v in vs:
                if i <= busy[v['id']]:
                    continue
                sig = SY.signal(v['base'], s, ind, i, v['p'])
                if not sig:
                    continue
                if v['gate']:
                    if g is None:
                        g = gate(code, s.dates[i])
                    if g is not True:
                        continue
                t, why = SY.trade(v['base'], s, ind, i, sig, bt, v['p'])
                if t is None:
                    continue
                t['signal_date'] = s.dates[i]
                trades[v['id']].append(t)
                busy[v['id']] = s.at[t['exit_date']]
        if ci % 500 == 0:
            log(f'    {ci:,}/{len(data):,}')

    res, cands, adopted = {}, [], []
    for v in vs:
        ts = trades[v['id']]
        # 봉인 구간에서 **끝난** 거래가 아니라 봉인 구간에 **신호가 난** 거래만 봉인 성적이다.
        # 탐색 구간 신호가 봉인 구간에 청산된 것은 탐색 쪽에 남는다 — 그 청산가는 봉인
        # 구간 가격이지만 선택에는 쓰이지 않던 것이 아니다. 둘을 섞지 않게 신호일로 가른다.
        sr = [t for t in ts if t['signal_date'] < hold_start]
        ho = [t for t in ts if t['signal_date'] >= hold_start]
        r = dict(all=BT.stats(sr), first=BT.stats([t for t in sr if t['signal_date'] < mid]),
                 second=BT.stats([t for t in sr if t['signal_date'] >= mid]),
                 holdout=BT.stats(ho))
        rs = sorted(t['ret_pct'] for t in sr)
        r['worst1'] = round(rs[max(0, int(len(rs) * 0.01) - 1)], 2) if rs else None
        ok, why = SY.verdict(r, crit)
        r['search_ok'], r['search_why'] = ok, why
        if ok:
            cands.append(v['id'])
            h = r['holdout']
            hwhy = []
            if (h.get('n') or 0) < hcrit['min_trades']:
                hwhy.append(f'거래 {h.get("n", 0)}건 < {hcrit["min_trades"]}')
            if h.get('n'):
                if h['win_rate'] < hcrit['min_win_rate']:
                    hwhy.append(f'승률 {h["win_rate"]}%')
                if h['mean'] <= 0:
                    hwhy.append(f'평균 {h["mean"]:+.2f}%')
                if (h.get('profit_factor') or 0) < hcrit['min_profit_factor']:
                    hwhy.append(f'PF {h.get("profit_factor")}')
            r['holdout_ok'], r['holdout_why'] = not hwhy, hwhy
            if not hwhy:
                adopted.append(v['id'])
        res[v['id']] = r
    return dict(source='board-search', as_of=end, search_start=search_days[0],
                search_end=search_days[-1], mid=mid, holdout_start=hold_start,
                holdout_days=se['holdout_days'], n_variants=len(vs), n_codes=len(data),
                candidates=cands, adopted=adopted, results=res,
                variants={v['id']: dict(base=v['base'], gate=v['gate'], p=v['p']) for v in vs})


def summary_lines(p, sc):
    return lines(p, sc['screen'], sc['search']['holdout'])


def lines(p, crit, hcrit):
    out = [f'조합 탐색 — {p["n_variants"]}개 조합 · 탐색 {p["search_start"]}~{p["search_end"]} · '
           f'봉인 {p["holdout_start"]}~{p["as_of"]} ({p["holdout_days"]}영업일, 1회만 확인)',
           f'1단계(탐색 구간 전체·앞·뒤 절반): 거래 {crit["min_trades"]}↑ · 승률 {crit["min_win_rate"]}%↑ · '
           f'평균>0 · PF {crit["min_profit_factor"]}↑',
           f'2단계(봉인 구간): 거래 {hcrit["min_trades"]}↑ · 승률 {hcrit["min_win_rate"]}%↑ · 평균>0 · '
           f'PF {hcrit["min_profit_factor"]}↑']
    ranked = sorted(p['results'].items(),
                    key=lambda kv: (not kv[1]['search_ok'], -(kv[1]['all'].get('win_rate') or 0)))
    out.append(f'1단계 통과 {len(p["candidates"])}개 / 최종 채택 {len(p["adopted"])}개')
    for vid, r in ranked:
        a = r['all']
        if not a.get('n'):
            continue
        line = (f'{"✅" if vid in p["adopted"] else ("△" if r["search_ok"] else "✗")} {vid}: '
                f'탐색 {a["n"]}건 · 승률 {a["win_rate"]}% · 평균 {a["mean"]:+.2f}% · '
                f'PF {a["profit_factor"]} · 최악1% {r["worst1"]:+.1f}%')
        if r['search_ok']:
            h = r['holdout']
            line += (f' → 봉인 {h.get("n", 0)}건 · 승률 {h.get("win_rate")}% · '
                     f'평균 {h.get("mean", 0):+.2f}% · PF {h.get("profit_factor")}'
                     + ('' if r.get('holdout_ok') else ' — ' + ', '.join(r['holdout_why'])))
        out.append(line)
    out.append(f'조합을 {p["n_variants"]}개 봤다 — 운으로 1단계를 넘는 것이 섞일 수 있어 봉인 구간이 '
               '최종 판정이다. 원작자의 성과가 아니며 폐지 종목이 없어 결과가 좋게 잡힐 수 있다')
    return out
