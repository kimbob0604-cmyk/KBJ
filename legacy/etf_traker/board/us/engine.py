#!/usr/bin/env python3
"""
미국장 엔진 — 일봉 + 스냅샷 → 브리프가 읽는 수치 전부.

신고가 판정은 국장과 **같은 코드**를 쓴다(`board/engine/newhigh.py`). 정의가
갈리면 두 보드를 나란히 읽을 수 없다. 미국장에만 있는 것은 셋이다.

  52주 신저가   직전 252거래일(당일 제외) 최저가를 당일 하회. 신고가의 거울
  연속 신고가   기준일부터 거슬러 신고가 라벨이 끊기지 않은 거래일 수
  첫 진입      최근 N거래일 안에 52주 신고가가 처음 뜬 종목 (그 이전 구간에 없음)

모든 함수는 순수 함수다. 네트워크도 DB 도 모른다 — 입력은 리스트, 출력은 dict.
계산되지 않는 값은 None 이고 절대 0 으로 채우지 않는다 (CLAUDE.md 2장 1번).
"""
from ..engine import newhigh as NH

BASES = ('close', 'high')


# ─────────────────────────── 작은 통계 ───────────────────────────
def median(vals):
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def breadth(rows, key='chg_pct'):
    """상승 / 보합 / 하락. 등락률을 모르는 종목은 어느 쪽에도 넣지 않는다."""
    vals = [r.get(key) for r in rows]
    known = [v for v in vals if v is not None]
    up = sum(1 for v in known if v > 0)
    dn = sum(1 for v in known if v < 0)
    return dict(up=up, flat=len(known) - up - dn, down=dn,
                known=len(known), unknown=len(vals) - len(known),
                up_ratio=round(up / len(known) * 100, 1) if known else None)


def ret_n(rows, idx, n):
    """n거래일 수익률 %. 창을 못 채우면 None."""
    if idx - n < 0:
        return None
    a, b = rows[idx - n].get('close'), rows[idx].get('close')
    if not a or b is None:
        return None
    return round((b / a - 1) * 100, 2)


def avg_turnover(rows, idx, n=20):
    """직전 n거래일 평균 거래대금(종가×거래량 추정). 창을 못 채우면 None."""
    if idx - n < 0:
        return None
    vals = [r['close'] * r['volume'] for r in rows[idx - n:idx]
            if r.get('close') is not None and r.get('volume') is not None]
    if len(vals) < n:
        return None
    m = sum(vals) / len(vals)
    return m or None


# ─────────────────────────── 라벨 (당일 + 최근 며칠) ───────────────────────────
def _max_in(rows, i, n, key, floor=0):
    """직전 n거래일(당일 제외) 최고가. 창이 floor 를 넘어가야 채워지면 None."""
    if i - n < floor:
        return None
    vals = [r[key] for r in rows[i - n:i] if r.get(key) is not None]
    return max(vals) if vals else None


def _min_in(rows, i, n, key, floor=0):
    if i - n < floor:
        return None
    vals = [r[key] for r in rows[i - n:i] if r.get(key) is not None]
    return min(vals) if vals else None


def guard_floor(rows, cfg):
    """수정주가 미반영 의심 지점 이후의 시작 인덱스. `newhigh.split_guard` 와 같은 값."""
    return NH.split_guard(rows, cfg['integrity']['split_guard_ratio'])[3]


def hits_on(rows, i, cfg, basis='close', floor=None):
    """i번째 봉의 신고가·신저가 판정만 가볍게. 과거 며칠을 되짚을 때 쓴다.

    `newhigh.evaluate` 는 저항두께·갭·축소폭까지 전부 계산한다. 연속 일수를
    세려고 30일치를 그걸로 돌리면 같은 값을 수십 번 다시 만든다. 당일 한 번은
    evaluate 로, 과거 되짚기는 이 함수로 한다. **판정식은 같다** —
    직전 N거래일(당일 제외) 최고가를 당일이 넘겼는가.

    floor 를 반드시 함께 넘긴다. evaluate 는 수정주가 의심 지점 이전 구간을
    룩백에서 잘라내는데(D-001) 여기서 안 자르면 ①의 신고가 수와 ②의 추이가
    서로 다른 수를 말한다. 실제로 그랬다 — 같은 날을 50 과 59 로 셌다.
    """
    if floor is None:
        floor = guard_floor(rows, cfg)
    key = 'close' if basis == 'close' else 'high'
    cur = rows[i].get(key)
    out = dict(kind=None, rank=None, low52=False)
    if cur is None:
        return out
    pri = cfg['newhigh']['priority']
    lb = cfg['newhigh']['lookback']
    for rank, kind in enumerate(pri):
        n = lb.get(kind)
        if not n:
            continue
        ref = _max_in(rows, i, n, key, floor)
        if ref is not None and cur > ref:
            out['kind'], out['rank'] = kind, rank
            break
    ln = cfg['newlow']['lookback']['w52']
    lkey = 'close' if cfg['newlow'].get('basis', 'close') == 'close' else 'low'
    lcur = rows[i].get(lkey)
    lref = _min_in(rows, i, ln, lkey, floor)
    out['low52'] = bool(lref is not None and lcur is not None and lcur < lref)
    return out


def streak_and_fresh(rows, idx, cfg, basis='close'):
    """연속 신고가 일수와 '최근 N일 중 52주 첫 진입' 여부.

    첫 진입은 저장된 일봉이 닿는 구간 안에서만 말할 수 있다. 2년치를 들고
    있으면 '최근 2년 중 처음' 이라는 뜻이고, 그 사실을 함께 돌려준다 —
    범위를 안 밝히면 '상장 이후 처음' 으로 읽힌다 (2장 1번).
    """
    fresh_days = cfg['brief']['fresh_days']
    floor = guard_floor(rows, cfg)
    streak = 0
    i = idx
    while i >= 0:
        h = hits_on(rows, i, cfg, basis, floor)
        if not h['kind']:
            break
        streak += 1
        i -= 1

    w52_days = [j for j in range(max(0, idx - fresh_days + 1), idx + 1)
                if hits_on(rows, j, cfg, basis, floor)['kind'] == 'w52']
    fresh = False
    if w52_days:
        first = w52_days[0]
        # 그 이전 구간에 52주 라벨이 하나도 없어야 '첫 진입' 이다.
        fresh = not any(hits_on(rows, j, cfg, basis, floor)['kind'] == 'w52'
                        for j in range(0, first))
    return dict(streak=streak, fresh52=fresh, history_days=len(rows))


# ─────────────────────────── 종목 1개 ───────────────────────────
def evaluate_one(ticker, rows, snap, asof, cfg, basis=None):
    """한 종목의 기준일 지표. 기준일 봉이 없으면 None."""
    basis = basis or cfg['newhigh']['default_basis']
    idx = next((i for i, r in enumerate(rows) if r['asof'] == asof), None)
    if idx is None:
        return None
    ev = NH.evaluate(rows, asof, cfg)
    if not ev:
        return None
    b = ev['basis'][basis]
    today = rows[idx]

    close = today.get('close')
    vol = today.get('volume')
    # 스냅샷(스크리너)이 시총·이름·섹터를 들고 있고 일봉은 가격을 들고 있다.
    # 등락률은 스냅샷 값을 우선한다 — 같은 화면을 보는 사람과 숫자를 맞춘다.
    chg = (snap or {}).get('chg_pct')
    if chg is None:
        chg = ev.get('chg_pct')
    turnover = (close * vol) if (close is not None and vol is not None) else None
    base = avg_turnover(rows, idx, cfg['volume']['avg_days'])

    sf = streak_and_fresh(rows, idx, cfg, basis)
    low = hits_on(rows, idx, cfg, basis)['low52']

    return dict(
        ticker=ticker,
        name=(snap or {}).get('name'),
        sector=(snap or {}).get('sector'),
        industry=(snap or {}).get('industry'),
        mktcap=(snap or {}).get('mktcap'),
        close=close, volume=vol, chg_pct=chg,
        turnover=turnover, turnover_avg20=base,
        turnover_mult=(round(turnover / base, 1) if (turnover and base) else None),
        vol_mult=ev.get('vol_mult'),
        ret_5d=ret_n(rows, idx, 5), ret_21d=ret_n(rows, idx, 21),
        label=b.get('label'), gap=b.get('gap'), narrow5=b.get('narrow5'),
        near_kind=NH.proximity_kind(ev, basis, cfg),
        low52=low, streak=sf['streak'], fresh52=sf['fresh52'],
        history_days=sf['history_days'],
        suspect=ev.get('suspect'), suspect_note=ev.get('suspect_note'),
        basis=basis)


def evaluate_all(series, snaps, asof, cfg, log=None):
    """유니버스 전체. 기준일 봉이 없는 종목은 사유와 함께 뺀다."""
    rows, skipped = [], dict(no_bar=0, short_history=0)
    for ticker, bars in series.items():
        r = evaluate_one(ticker, bars, (snaps or {}).get(ticker), asof, cfg)
        if r is None:
            skipped['no_bar'] += 1
            continue
        if r['history_days'] < cfg['newhigh']['lookback']['d60']:
            skipped['short_history'] += 1
        rows.append(r)
    rows.sort(key=lambda r: r['ticker'])
    return rows, skipped


# ─────────────────────────── 유니버스 필터 ───────────────────────────
def in_universe(r, cfg):
    """레퍼런스 화면 머리말과 같은 하한. 생성 단계에서 건다."""
    u = cfg['universe']
    if (r.get('mktcap') or 0) < u['min_mktcap_usd']:
        return False
    if (r.get('close') or 0) < u['min_price_usd']:
        return False
    if (r.get('turnover') or 0) < u['min_turnover_usd']:
        return False
    return True


# ─────────────────────────── 집계 ───────────────────────────
def cap_tiers(rows, cfg):
    """④ 등락 온도 — 시총 구간별 중앙 등락과 상승 비율."""
    out = []
    for t in cfg['brief']['cap_tiers']:
        lo, hi = t['min'], t.get('max')
        items = [r for r in rows
                 if (r.get('mktcap') or 0) >= lo and (hi is None or (r.get('mktcap') or 0) < hi)]
        b = breadth(items)
        out.append(dict(name=t['name'], n=len(items),
                        median=median([r.get('chg_pct') for r in items]),
                        median_5d=median([r.get('ret_5d') for r in items]),
                        up_ratio=b['up_ratio'], up=b['up'], down=b['down']))
    return out


def sector_table(rows, cfg):
    """③ 섹터별 신고가 수와 중앙 등락. 구성종목이 적은 섹터는 순위에서 뺀다."""
    by = {}
    for r in rows:
        by.setdefault(r.get('sector') or '미분류', []).append(r)
    out = []
    mn = cfg['brief']['sector_min_members']
    for name, items in by.items():
        b = breadth(items)
        out.append(dict(
            sector=name, n=len(items),
            newhigh=sum(1 for r in items if r.get('label')),
            w52=sum(1 for r in items if r.get('label') == 'w52'),
            low52=sum(1 for r in items if r.get('low52')),
            median=median([r.get('chg_pct') for r in items]),
            median_5d=median([r.get('ret_5d') for r in items]),
            up=b['up'], down=b['down'], up_ratio=b['up_ratio'],
            thin=len(items) < mn))
    out.sort(key=lambda x: (-x['newhigh'], -(x['median'] or 0)))
    return out


def industry_groups(rows, cfg, direction='up'):
    """⑧ 등락 상위 — 산업으로 묶는다.

    한 종목만 크게 움직인 산업도 그대로 한 줄이다. 묶음의 순서는 **가장 크게
    움직인 구성종목** 순이고, 묶음 안도 같은 순이다. 평균으로 줄을 세우면
    두 종목짜리 산업이 늘 위에 온다.
    """
    lim = cfg['brief']['movers_min_mktcap_usd']
    items = [r for r in rows
             if (r.get('mktcap') or 0) >= lim and r.get('chg_pct') is not None]
    items = [r for r in items
             if (r['chg_pct'] > 0 if direction == 'up' else r['chg_pct'] < 0)]
    items.sort(key=lambda r: r['chg_pct'], reverse=(direction == 'up'))
    take = items[:cfg['brief']['movers_scan']]
    groups, order = {}, []
    for r in take:
        key = r.get('industry') or '미분류'
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out = []
    for key in order:
        members = groups[key][:cfg['brief']['movers_group_rows']]
        out.append(dict(industry=key, n=len(groups[key]), rows=members))
    return out


def mega_movers(rows, cfg):
    """⑤ 대형주 — 시총 $100B 이상의 상승·하락 상위."""
    lim = cfg['brief']['megacap_usd']
    items = [r for r in rows if (r.get('mktcap') or 0) >= lim]
    known = [r for r in items if r.get('chg_pct') is not None]
    n = cfg['brief']['movers_rows']
    # 오른 종목만 ▲, 내린 종목만 ▼ 에 올린다. 전부 오른 날 '▼' 줄에
    # 플러스 종목이 올라오면 그 줄이 거짓말이 된다.
    up = [r for r in sorted(known, key=lambda r: r['chg_pct'], reverse=True)
          if r['chg_pct'] > 0][:n]
    dn = [r for r in sorted(known, key=lambda r: r['chg_pct']) if r['chg_pct'] < 0][:n]
    b = breadth(items)
    return dict(n=len(items), up_count=b['up'], down_count=b['down'],
                up=up, down=dn)


def leaders(rows, cfg):
    """⑦ 52주 신고가 대장 — 거래대금 순."""
    items = [r for r in rows if r.get('label') == 'w52' and r.get('turnover')]
    items.sort(key=lambda r: r['turnover'], reverse=True)
    return items[:cfg['brief']['leaders_rows']]


def streaks(rows, cfg):
    """⑥ 연속 신고가."""
    mn = cfg['brief']['streak_min']
    items = [r for r in rows if r.get('label') and (r.get('streak') or 0) >= mn]
    items.sort(key=lambda r: (-(r['streak']), r['ticker']))
    return items


def fresh52(rows, cfg):
    """⑥ 최근 N거래일 중 52주 첫 진입."""
    items = [r for r in rows if r.get('fresh52')]
    items.sort(key=lambda r: -(r.get('turnover') or 0))
    return items


def summary(rows, cfg, prev=None):
    """① 한눈에."""
    b = breadth(rows)
    mv = cfg['brief']['move_pct']
    nh = sum(1 for r in rows if r.get('label'))
    secs = [s for s in sector_table(rows, cfg) if not s['thin'] and s['median'] is not None]
    # 부호가 맞는 것만 올린다. 전 섹터가 오른 날의 '약세' 목록에 +0.0% 가
    # 실리면 읽는 사람이 없는 약세를 본다.
    strong = [x for x in sorted(secs, key=lambda s: -s['median']) if x['median'] > 0][:4]
    weak = [x for x in sorted(secs, key=lambda s: s['median']) if x['median'] < 0][:4]
    prev_nh = (prev or {}).get('newhigh')
    return dict(
        universe=len(rows),
        newhigh=nh,
        newhigh_prev=prev_nh,
        newhigh_delta=(nh - prev_nh) if prev_nh is not None else None,
        w52_high=sum(1 for r in rows if r.get('label') == 'w52'),
        w52_low=sum(1 for r in rows if r.get('low52')),
        up=b['up'], down=b['down'], flat=b['flat'], unknown=b['unknown'],
        up_ratio=b['up_ratio'],
        median=median([r.get('chg_pct') for r in rows]),
        surge=sum(1 for r in rows if (r.get('chg_pct') or 0) >= mv),
        plunge=sum(1 for r in rows if (r.get('chg_pct') or 0) <= -mv),
        mega=mega_movers(rows, cfg),
        strong=strong, weak=weak)
