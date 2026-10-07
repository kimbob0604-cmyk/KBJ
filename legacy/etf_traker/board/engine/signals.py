#!/usr/bin/env python3
"""
스윙 시그널 — 장 마감 확정값으로 **다음 거래일에 볼 종목과 가격**을 고른다.

규칙의 출처는 『유명 트레이더 매매법』(2026-09-23 판)이고, 항목 번호(#42 깡토 등)와
근거의 두께(원문·[복원]·[2차]·비원전·새정의)는 `config/signals.yaml` 에 값마다
붙어 있다. 사양은 `docs/SIGNALS.md`, 결정 근거는 `docs/DECISIONS.md` D-NEXT-S.

**이 모듈은 성과를 검증하지 않는다.** 원문 문서가 스스로 "성과를 검증한 자료가
아니다" 라고 적었고, 여기 고른 조합도 백테스트 전 후보 규칙이다. 메시지 끝에
그 사실을 매번 적는다.

구획 둘 — 사용자가 고른 1차 범위는 F1 신고가 돌파 + F2 수축 + F7 수급 + F9 시장 게이트다.

  breakout  오늘 종가 기준 52주 이상 신고가 + 추세 정렬 + 상대강도 통과.
            수축이 직전에 있었는지·수급·거래량은 **태그**로 단다(거르지 않는다).
  watch     52주 이상 신고가 근접(보드 정의, 갭 5% 이내) + 추세 + 상대강도 +
            **수축 필수**. 돌파가(기준 최고가)를 적어 다음 날 감시 레벨로 쓴다.

판정은 전부 장 마감 뒤 확정된 값으로 하고 체결은 다음 거래일이다. 원문 공통 함정
7번(장중 체결인데 판정값이 종가에 확정)을 피하려고 장중 판정을 1차 범위에서 뺐다.

계산되지 않은 값은 만들지 않는다 (CLAUDE.md 2장 1번). 이력이 짧아 평균선을 못
채우면 그 조건은 None 이고, None 은 통과로 치지 않는다.
"""
import os
from datetime import date

import yaml

from . import db as DB
from .build import now_kst, read
from .config import ROOT

SIGNALS_CFG = os.path.join(ROOT, 'config', 'signals.yaml')
SOURCE = 'board-signals'
KIND_RANK = {'hist': 0, 'w52': 1, 'd60': 2}
DISCLAIMER = ('백테스트 전 후보 규칙 · 성과 검증 아님 · [복원]/[2차]/새정의 값 포함 '
              '(docs/SIGNALS.md)')


def load_cfg(path=SIGNALS_CFG):
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


# ─────────────────────────── 지표 ───────────────────────────
def sma(vals, n, end=None):
    """vals[end-n+1 .. end] 의 단순평균. 모자라거나 None 이 섞이면 None."""
    end = len(vals) - 1 if end is None else end
    if n <= 0 or end - n + 1 < 0:
        return None
    w = vals[end - n + 1:end + 1]
    if any(v is None for v in w):
        return None
    return sum(w) / n


def pstdev(vals, n, end):
    """모표준편차. 볼린저는 평균과 같은 창의 표준편차를 쓴다 (#16)."""
    m = sma(vals, n, end)
    if m is None:
        return None
    w = vals[end - n + 1:end + 1]
    return (sum((v - m) ** 2 for v in w) / n) ** 0.5


def true_range(rows, i):
    r = rows[i]
    if r.get('high') is None or r.get('low') is None:
        return None
    if i == 0 or rows[i - 1].get('close') is None:
        return r['high'] - r['low']
    pc = rows[i - 1]['close']
    return max(r['high'] - r['low'], abs(r['high'] - pc), abs(r['low'] - pc))


def bb_width(closes, i, n, k):
    """(상단 − 하단) / 가운데. 가운데 선은 단순평균이어야 한다 (#16 원문)."""
    m = sma(closes, n, i)
    sd = pstdev(closes, n, i)
    if not m or sd is None:
        return None
    return (2 * k * sd) / m


def squeeze_on(rows, closes, i, c):
    """볼린저 띠가 켈트너 채널 안에 완전히 들어갔는가 (#17)."""
    n, k, kn, km = c['bb_period'], c['bb_k'], c['kc_period'], c['kc_mult']
    m, sd = sma(closes, n, i), pstdev(closes, n, i)
    km_mid = sma(closes, kn, i)
    if m is None or sd is None or km_mid is None or i - kn + 1 < 1:
        return None
    trs = [true_range(rows, j) for j in range(i - kn + 1, i + 1)]
    if any(t is None for t in trs):
        return None
    atr = sum(trs) / kn
    return (m + k * sd < km_mid + km * atr) and (m - k * sd > km_mid - km * atr)


def is_nr(rows, i, n):
    """당일 변동폭이 최근 n일(당일 포함) 중 가장 좁은가 (#11 NR7)."""
    if i - n + 1 < 0:
        return None
    rng = [(r.get('high') or 0) - (r.get('low') or 0) for r in rows[i - n + 1:i + 1]]
    return rng[-1] <= min(rng[:-1]) if len(rng) > 1 else None


def is_inside(rows, i):
    """당일 고가·저가가 전일 범위 안 (#11)."""
    if i < 1:
        return None
    a, b = rows[i], rows[i - 1]
    if None in (a.get('high'), a.get('low'), b.get('high'), b.get('low')):
        return None
    return a['high'] <= b['high'] and a['low'] >= b['low']


def weekly_closes(rows):
    """주(ISO 주차)마다 마지막 거래일 종가. 기준일이 든 주는 기준일 종가로 끝난다."""
    out, key = [], None
    for r in rows:
        y, w, _ = date.fromisoformat(r['asof']).isocalendar()
        if (y, w) != key:
            out.append(r['close'])
            key = (y, w)
        else:
            out[-1] = r['close']
    return out


def ma_stack(vals, periods):
    """짧은 평균선이 긴 평균선보다 차례로 위(정배열). 하나라도 못 채우면 None."""
    ms = [sma(vals, p) for p in periods]
    if any(m is None for m in ms):
        return None
    return all(a > b for a, b in zip(ms, ms[1:]))


def contraction_at(rows, closes, i, c, widths=None):
    """i 봉의 수축 상태 넷. 값이 없는 것은 None.

    widths 는 {봉 번호: 띠 폭} 캐시다. 6개월 최저를 보려면 봉마다 126개 폭이
    필요해 캐시 없이는 종목당 수만 번 다시 계산한다.
    """
    lb = c['bw_lookback']
    widths = {} if widths is None else widths

    def wd(j):
        if j not in widths:
            widths[j] = bb_width(closes, j, c['bb_period'], c['bb_k'])
        return widths[j]

    w = wd(i)
    near_min = None
    if w is not None and i - lb + 1 >= 0:
        ws = [wd(j) for j in range(i - lb + 1, i + 1)]
        if all(x is not None for x in ws):
            near_min = w <= min(ws) * (1 + c['bw_near_min'])
    return dict(bw_near_min=near_min, squeeze=squeeze_on(rows, closes, i, c),
                nr=is_nr(rows, i, c['nr_days']), inside=is_inside(rows, i))


CONTRACTION_NAMES = dict(bw_near_min='BB폭 6개월 최저권', squeeze='스퀴즈',
                         nr='NR7', inside='인사이드데이')


def _any_true(states):
    return sorted({k for s in states for k, v in s.items() if v})


def stock_factors(rows, c):
    """한 종목의 기준일 factor. rows 는 기준일로 끝나는 오름차순 일봉."""
    i = len(rows) - 1
    closes = [r.get('close') for r in rows]
    vols = [r.get('volume') for r in rows]
    cfgc, cfgt = c['contraction'], c['trend']

    widths = {}
    today = contraction_at(rows, closes, i, cfgc, widths)
    recent = [contraction_at(rows, closes, j, cfgc, widths)
              for j in range(max(0, i - cfgc['recent_days']), i)]
    d5 = sma(vols, cfgc['dryup_short'], i - 1) if i >= 1 else None
    d20 = sma(vols, 20, i - 1) if i >= 1 else None
    dryup = round(d5 / d20, 2) if (d5 is not None and d20) else None

    adr_n = c['risk']['adr_days']
    adr = None
    if i - adr_n + 1 >= 0:
        w = rows[i - adr_n + 1:i + 1]
        if all(r.get('high') and r.get('low') for r in w):
            adr = round(sum(r['high'] / r['low'] - 1 for r in w) / adr_n * 100, 2)

    return dict(
        trend_daily=ma_stack(closes, cfgt['daily_ma']),
        trend_weekly=ma_stack(weekly_closes(rows), cfgt['weekly_ma']),
        contraction_today=_any_true([today]),
        contraction_recent=_any_true(recent),
        dryup=dryup, adr_pct=adr)


def max_daily_ret(rows, n):
    """직전 n영업일(당일 포함) 일별 수익률의 최댓값(%) (#178)."""
    if len(rows) < n + 1:
        return None
    best = None
    for a, b in zip(rows[-n - 1:-1], rows[-n:]):
        if a.get('close') and b.get('close') is not None:
            r = (b['close'] / a['close'] - 1) * 100
            best = r if best is None else max(best, r)
    return None if best is None else round(best, 2)


def new_low_at(rows, i, lb):
    """i 봉 종가가 직전 lb 영업일(당일 제외) 종가 최저를 깼는가."""
    if i - lb < 0:
        return None
    w = [r.get('close') for r in rows[i - lb:i]]
    if any(v is None for v in w) or rows[i].get('close') is None:
        return None
    return rows[i]['close'] < min(w)


def prior_high_volume(rows, ref):
    """기준 최고가를 찍은 날의 거래량 (#67: 돌파 거래량 > 전고점 당시 거래량)."""
    if ref is None:
        return None
    for r in reversed(rows[:-1]):
        if r.get('close') is not None and abs(r['close'] - ref) <= 1e-9 * max(1, ref):
            return r.get('volume')
    return None


def percentile_ranks(pairs):
    """[(key, score)] → {key: 백분위}. score 이하 비율 × 100."""
    xs = sorted(s for _, s in pairs)
    n = len(xs)
    if not n:
        return {}
    import bisect
    return {k: round(bisect.bisect_right(xs, s) / n * 100, 1) for k, s in pairs}


# ─────────────────────────── 시장 게이트 ───────────────────────────
def regime(index_hist, rc, asof):
    """지수 종가와 N일 평균선 (#42). 지수 일봉이 없으면 그 시장은 None."""
    out, missing = {}, []
    if index_hist is None:
        # 받지 못한 날은 호출자가 한 줄로 적는다. 시장마다 '0일뿐' 을 또 적지 않는다.
        return {sym: None for sym in rc.get('index_ma') or {}}, missing
    ser = index_hist.get('series') or {}
    for sym, n in (rc.get('index_ma') or {}).items():
        rows = [r for r in ser.get(sym) or [] if r.get('asof') and r['asof'] <= asof
                and r.get('close') is not None]
        if len(rows) < n:
            missing.append(f'{sym} {n}일선 — 지수 일봉 {len(rows)}일뿐이라 계산하지 않았습니다')
            out[sym] = None
            continue
        closes = [r['close'] for r in rows]
        ma = sma(closes, n)
        out[sym] = dict(asof=rows[-1]['asof'], close=closes[-1], ma=round(ma, 2), ma_n=n,
                        above=closes[-1] > ma, stale=rows[-1]['asof'] != asof,
                        source=index_hist.get('source'))
        if rows[-1]['asof'] != asof:
            missing.append(f'{sym} 지수가 {rows[-1]["asof"]} 자입니다 (기준일 {asof})')
    return out, missing


# ─────────────────────────── 수급 ───────────────────────────
def flow_share(entry, volume, turnover):
    """(기관 + 외국인) 순매수 ÷ 그날 거래량(또는 거래대금) × 100 (#42).

    단위가 '주' 면 거래량으로, '억원' 이면 거래대금(억원)으로 나눈다. 둘 다 같은
    비율의 다른 표현이지만 금액 쪽은 체결가 가중이라 조금 다르다 — basis 에 적는다.
    """
    if not entry:
        return None
    inst, frgn = entry.get('기관'), entry.get('외국인')
    if inst is None or frgn is None:
        return None
    unit = entry.get('unit')
    if unit == '주' and volume:
        den, basis = volume, '순매수량 ÷ 거래량'
    elif unit == '억원' and turnover:
        den, basis = turnover, '순매수금액 ÷ 거래대금'
    else:
        return None
    return dict(pct=round((inst + frgn) / den * 100, 1), inst=inst, frgn=frgn, unit=unit,
                basis=basis, as_of=entry.get('as_of'), source=entry.get('source'))


# ─────────────────────────── 조립 ───────────────────────────
def _exclude_reason(r, cfg, sc):
    """대상에서 빼는 사유 한 단어. 대상이면 None.

    사유를 세어 signals.json `funnel` 에 남긴다. 전 종목이 빠진 날 무엇이 걸렀는지를
    로그 한 줄로 알 수 있어야 한다 — 첫 실데이터 실행에서 이것이 없어 추측해야 했다.
    """
    u = sc['universe']
    if r.get('suspect'):
        return 'suspect'
    if r.get('close') is None:
        return 'no_close'
    if r.get('kind') not in u['kinds']:
        return 'kind'
    if r['close'] < u['min_price']:
        return 'price'
    if u.get('use_board_mktcap_floor'):
        floor = float(cfg['display'].get('min_mktcap_eok') or 0)
        if (r.get('mktcap') or 0) < floor:
            return 'mktcap'
    ta = r.get('turnover_avg20')
    if ta is None:
        return 'no_turnover_avg20'
    if ta < u['min_turnover_avg20_eok']:
        return 'turnover'
    return None


def _eligible(r, cfg, sc):
    return _exclude_reason(r, cfg, sc) is None


def _rs(stocks, sc):
    w = sc['rs']['weights']
    groups = {}
    for r in stocks:
        if r.get('kind') not in sc['universe']['kinds']:
            continue
        vals = [r.get(k) for k in w]
        if any(v is None for v in vals):
            continue
        s = sum(r[k] * wt for k, wt in w.items())
        g = r.get('market') if sc['rs']['by_market'] else 'all'
        groups.setdefault(g, []).append((r['code'], s))
    out = {}
    for pairs in groups.values():
        out.update(percentile_ranks(pairs))
    return out


def _ref(r, kind, basis):
    """기준 최고가. 보드 기본 기준이 종가면 refs 를 그대로, 아니면 갭에서 되산다."""
    if basis == 'close':
        return (r.get('refs') or {}).get(kind)
    g = ((r.get('close_basis') or {}).get('gap') or {}).get(kind)
    return None if g is None or g >= 100 else r['close'] / (1 - g / 100)


def _close_label(r):
    return (r.get('close_basis') or {}).get('label')


def _near_kind(r, sc, cfg):
    """종가 기준 근접 라벨 — 보드 정의(갭 ≤ max_gap_pct) 그대로, 최소 등급 이상."""
    gaps = (r.get('close_basis') or {}).get('gap') or {}
    mx = cfg['proximity']['max_gap_pct']
    lim = KIND_RANK[sc['setups']['watch']['min_kind']]
    for k in ('hist', 'w52'):
        g = gaps.get(k)
        if KIND_RANK[k] <= lim and g is not None and 0 <= g <= mx:
            return k, g
    return None, None


def levels(entry, low, ref, sc, adr):
    rk = sc['risk']
    stop = round(entry * (1 - rk['stop_pct'] / 100), 2) if entry else None
    tgt_base = ref or entry
    out = dict(stop_pct=rk['stop_pct'], stop=stop,
               target=round(tgt_base * (1 + rk['target_r'] * rk['stop_pct'] / 100), 2)
               if tgt_base else None,
               target_base='돌파가' if ref else '종가',
               max_weight_pct=round(rk['risk_per_trade_pct'] / rk['stop_pct'] * 100, 1),
               risk_per_trade_pct=rk['risk_per_trade_pct'])
    if low is not None and entry:
        dist = round((entry - low) / entry * 100, 2)
        out.update(low_stop=low, low_stop_pct=dist,
                   low_within_adr=None if adr is None else dist <= adr)
    return out


def build(asof, cfg, sc, conn, flows_by_code=None, index_hist=None, log=print):
    """signals.json 페이로드. 입력은 state 파일과 DB 일봉뿐이다 (CLAUDE.md 3장)."""
    uni = read(asof, 'universe.json')
    if not uni or not uni.get('stocks'):
        raise RuntimeError(f'{asof} universe.json 이 없다. --engine 을 먼저 돌려라.')
    stocks = uni['stocks']
    basis = cfg['newhigh']['default_basis']
    # 보드 결손 배너 전체는 보드 메시지가 싣는다. 여기서는 시그널 판정을 흔드는
    # 것만 올린다 — 종가가 잠정이면 종가 기준 신고가 라벨이 이튿날 바뀔 수 있다.
    missing = []
    if uni.get('close_confirmed') is False:
        missing.append('종가가 잠정치입니다(KRX 확정 시세 미수신) — 종가 기준 신고가 '
                       '판정이 이튿날 바뀔 수 있습니다')
    rs = _rs(stocks, sc)
    funnel = {}

    def drop(why):
        funnel[why] = funnel.get(why, 0) + 1

    elig = {}
    for r in stocks:
        why = _exclude_reason(r, cfg, sc)
        if why:
            drop(why)
        else:
            elig[r['code']] = r

    # 한 번 흘려 읽으며 (1) 전 종목 52주 신저가·최대 일수익률 (2) 대상 종목 지표.
    lb, cmp_d = sc['regime']['new_low_lookback'], sc['regime']['new_low_compare_days']
    mr_n = sc['caution']['max_ret_days']
    nl_today = nl_prev = nl_n = nl_n_prev = 0
    max_ret, fac = {}, {}
    for code, rows in DB.iter_series(conn, asof):
        if not rows or rows[-1]['asof'] != asof:
            continue
        i = len(rows) - 1
        v = new_low_at(rows, i, lb)
        if v is not None:
            nl_n += 1
            nl_today += v
        if i - cmp_d >= 0:
            v0 = new_low_at(rows, i - cmp_d, lb)
            if v0 is not None:
                nl_n_prev += 1
                nl_prev += v0
        max_ret[code] = max_daily_ret(rows, mr_n)
        if code in elig:
            f = stock_factors(rows, sc)
            f['max_ret'] = max_ret[code]
            ref = None
            lab = _close_label(elig[code])
            if lab:
                ref = _ref(elig[code], lab, basis)
            f['prior_high_volume'] = prior_high_volume(rows, ref) if ref else None
            fac[code] = f

    # 최대 일수익률 상위 N% (#178 은 5분위 — 가장 높은 묶음)
    in_uni = {r['code'] for r in stocks}
    mr_pct = percentile_ranks([(k, v) for k, v in max_ret.items()
                               if v is not None and k in in_uni])
    top_cut = 100 - sc['caution']['max_ret_top_pct']

    reg, reg_missing = regime(index_hist, sc['regime'], asof)
    missing += reg_missing
    if index_hist is None:
        missing.append('지수 일봉을 받지 못해 코스피·코스닥 평균선 게이트를 계산하지 않았습니다')
    new_low = dict(
        pct=round(nl_today / nl_n * 100, 2) if nl_n else None, n=nl_n,
        pct_prev=round(nl_prev / nl_n_prev * 100, 2) if nl_n_prev else None,
        compare_days=cmp_d, lookback=lb)
    if new_low['pct'] is not None and new_low['pct_prev'] is not None:
        new_low['rising'] = new_low['pct'] > new_low['pct_prev']

    flows_by_code = flows_by_code or {}
    breakout, watch = [], []
    min_rs = sc['rs']['min_pct']
    b_lim = KIND_RANK[sc['setups']['breakout']['min_kind']]
    ff = sc['flows']['min_share_pct']
    for code, r in elig.items():
        f = fac.get(code)
        if not f:
            drop('no_series_today')
            continue
        rsp = rs.get(code)
        if rsp is None:
            drop('rs_missing')
            continue
        if rsp < min_rs:
            drop('rs_low')
            continue
        if not f['trend_daily']:
            drop('trend_daily' if f['trend_daily'] is False else 'trend_daily_short')
            continue
        if sc['trend']['require_weekly'] and not f['trend_weekly']:
            drop('trend_weekly' if f['trend_weekly'] is False else 'trend_weekly_short')
            continue
        fl = flow_share(flows_by_code.get(code), r.get('volume'), r.get('turnover'))
        g = reg.get(r.get('market'))
        gate_open = None if g is None else g['above']
        mrp = mr_pct.get(code)
        item = dict(
            code=code, name=r.get('name'), market=r.get('market'), sector=r.get('sector'),
            theme_name=r.get('theme_name'), close=r['close'], chg_pct=r.get('chg_pct'),
            vol_mult=r.get('vol_mult'), turnover=r.get('turnover'),
            rs_pct=rsp, trend=dict(daily=f['trend_daily'], weekly=f['trend_weekly']),
            contraction=dict(today=f['contraction_today'], recent=f['contraction_recent'],
                             dryup=f['dryup']),
            adr_pct=f['adr_pct'],
            flows=fl, flows_pass=None if fl is None else fl['pct'] >= ff,
            # 수급 소스가 기준일 값을 아직 안 줬으면 이전 영업일 값이다. 오늘 값처럼
            # 적지 않는다 — 태그에 그 날짜를 붙인다.
            flows_stale=None if fl is None else fl.get('as_of') != asof,
            caution=dict(max_ret=f['max_ret'], max_ret_pct=mrp,
                         max_ret_top=None if mrp is None else mrp > top_cut),
            gate_open=gate_open)
        lab = _close_label(r)
        if lab and KIND_RANK.get(lab, 9) <= b_lim:
            ref = _ref(r, lab, basis)
            pv = f.get('prior_high_volume')
            item.update(
                setup='breakout', label=lab, status=r.get('status'), ref=ref,
                vol_over_prior_high=None if (pv is None or not r.get('volume'))
                else r['volume'] > pv,
                levels=levels(r['close'], r.get('low'), ref, sc, f['adr_pct']))
            breakout.append(item)
            continue
        nk, ng = _near_kind(r, sc, cfg)
        if not nk:
            drop('not_high_or_near')
            continue
        cont = f['contraction_today'] or f['contraction_recent']
        if sc['setups']['watch']['require_contraction'] and not cont:
            drop('no_contraction')
            continue
        ref = _ref(r, nk, basis)
        item.update(setup='watch', near_kind=nk, gap=ng, trigger=ref,
                    in_alert_zone=ng <= sc['setups']['watch']['alert_gap_pct'],
                    levels=levels(ref, None, ref, sc, f['adr_pct']))
        watch.append(item)

    breakout.sort(key=lambda x: (-(x['rs_pct'] or 0), x['code']))
    watch.sort(key=lambda x: (x['gap'], x['code']))
    if sc['regime'].get('gate_action') == 'drop':
        breakout = [x for x in breakout if x['gate_open'] is not False]
        watch = [x for x in watch if x['gate_open'] is not False]

    if stocks and not elig:
        # 전 종목이 대상에서 빠지는 것은 시장 상황이 아니라 필터·필드 문제다. 조용히
        # '해당 없음' 으로 보내면 받는 쪽은 그날 후보가 없었다고 읽는다 (2장 6번).
        # 2026-09-25 첫 실데이터 실행이 그랬다 — kind 코드를 화면 라벨로 잘못 적었다.
        missing.append(f'시그널 대상이 0종목입니다(유니버스 {len(stocks):,}종목) — '
                       '결과가 아니라 필터 오류입니다. signals.yaml universe 를 확인하세요')
    log(f'  시그널 — 대상 {len(elig):,} · 돌파 {len(breakout)} · 감시 {len(watch)}')
    log('  탈락 사유 — ' + (' · '.join(f'{k} {v:,}' for k, v in
                                    sorted(funnel.items(), key=lambda kv: -kv[1])) or '없음'))
    return dict(
        source=SOURCE, as_of=asof, generated_at=now_kst(),
        price_source=uni.get('source'), close_confirmed=uni.get('close_confirmed'),
        doc=sc['meta']['doc'], disclaimer=DISCLAIMER,
        regime=dict(index=reg, new_low=new_low),
        counts=dict(universe=len(stocks), eligible=len(elig), evaluated=len(fac),
                    breakout=len(breakout), watch=len(watch)),
        funnel=funnel,
        breakout=breakout, watch=watch, missing=missing)


def need_flows(payload, have):
    """수급이 없는 후보 코드 — 따로 받을 대상."""
    return [x['code'] for x in payload['breakout'] + payload['watch'] if x['code'] not in have]
