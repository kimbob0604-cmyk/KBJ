#!/usr/bin/env python3
"""
집계 — 업종(1층)·테마(2층) 롤업과 히트맵 입력 생성.

모든 함수는 순수 함수다. 입력은 종목 레코드 리스트, 출력은 dict.
LLM 은 여기 결과를 서술만 하고 다시 계산하지 않는다 (CLAUDE.md 2장 3번).
"""

UNMAPPED = '미분류'

# 탐지기 6이 세는 라벨 집합. 20일을 뺀 뒤로 셋이다(D-071).
# 계산하는 라벨을 여기 그대로 두는 것이 규칙이다 — 창을 늘렸다 줄였다 하면서
# 이 집합을 안 맞추면 '3종 이상'의 뜻이 조용히 바뀐다(D-010 이 그랬다).
MULTI_LABEL_SET = ('d60', 'w52', 'hist')


def _wavg(items, val_key, w_key='mktcap'):
    """시가총액 가중 평균. 가중치가 없으면 단순 평균으로 떨어지고 그 사실을 알린다.

    반환 (값, 가중여부). 값이 없으면 (None, False).
    """
    pairs = [(x[val_key], x.get(w_key) or 0) for x in items
             if x.get(val_key) is not None]
    if not pairs:
        return None, False
    tw = sum(w for _, w in pairs)
    if tw > 0:
        return sum(v * w for v, w in pairs) / tw, True
    return sum(v for v, _ in pairs) / len(pairs), False


def breadth(items, flat_eps=0.0):
    """상승 / 보합 / 하락 종목수. 보합은 등락률 정확히 0 인 경우로 좁게 본다.

    등락률을 모르는 종목은 어느 쪽에도 넣지 않고 unknown 으로 뺀다. `or 0` 으로
    두면 값이 없는 종목이 전부 보합으로 잡혀 브레드스가 실제보다 중립으로 보인다.
    """
    vals = [x.get('chg_pct') for x in items]
    known = [v for v in vals if v is not None]
    up = sum(1 for v in known if v > flat_eps)
    dn = sum(1 for v in known if v < -flat_eps)
    return dict(up=up, flat=len(known) - up - dn, down=dn,
                total=len(known), unknown=len(vals) - len(known))


def rollup(name, items, kind='sector'):
    """업종/테마 한 덩어리의 집계 한 줄."""
    d, dw = _wavg(items, 'chg_pct')
    w, _ = _wavg(items, 'ret_5d')
    m, _ = _wavg(items, 'ret_21d')
    # 거래대금을 모르는 종목을 0 으로 더하면 합계가 '그만큼 없다'고 단정하는
    # 것이 된다. 아는 것만 더하고 몇 개를 못 봤는지 함께 낸다.
    known_t = [x.get('turnover') for x in items if x.get('turnover') is not None]
    known_b = [x.get('turnover_avg20') for x in items
               if x.get('turnover_avg20') is not None]
    turn = sum(known_t) if known_t else None
    base = sum(known_b) if known_b else None
    nh = sum(1 for x in items if x.get('label'))
    near = sum(1 for x in items if x.get('near_kind'))
    withT = [x for x in items if x.get('turnover') is not None]
    lead = max(withT, key=lambda x: x['turnover']) if withT else None
    return dict(
        kind=kind, name=name,
        chg_pct=None if d is None else round(d, 2),
        ret_5d=None if w is None else round(w, 2),
        ret_21d=None if m is None else round(m, 2),
        weighted=dw,
        breadth=breadth(items),
        n_newhigh=nh, n_near=near,
        turnover=None if turn is None else round(turn, 1),
        turnover_is_partial=len(known_t) < len(items),
        turnover_mult=(round(turn / base, 2)
                       if (turn is not None and base) else None),
        mktcap=round(sum(x.get('mktcap') or 0 for x in items), 1),
        leader=None if not lead else dict(
            code=lead['code'], name=lead['name'], chg_pct=lead.get('chg_pct')),
        codes=[x['code'] for x in items])


def by_sector(rows):
    """1층 KRX 업종. 전 종목이 정확히 하나씩 배정된다 — 커버리지 보장용."""
    g = {}
    for x in rows:
        g.setdefault(x.get('sector') or UNMAPPED, []).append(x)
    out = [rollup(k, v, 'sector') for k, v in g.items()]
    out.sort(key=lambda r: (r['chg_pct'] is None, -(r['chg_pct'] or 0)))
    return out


def by_theme(rows, mapping, meta):
    """2층 자체 테마. 다대다라 한 종목이 여러 줄에 등장한다."""
    g = {}
    for x in rows:
        for m in mapping.get(x['code']) or []:
            g.setdefault(m['theme'], []).append(dict(x, stage=m.get('stage')))
    out = []
    for tid, items in g.items():
        info = meta.get(tid) or {}
        r = rollup(info.get('name') or tid, items, 'theme')
        r.update(theme=tid, axis=info.get('axis'), parent=info.get('parent'),
                 stages=info.get('stages') or [])
        # 축이 단계를 가지면 오늘 반응한 단계 집합을 남긴다.
        # 탐지기 1(valuechain_diffusion)이 어제 집합과 비교할 입력이다.
        r['stages_reacted'] = sorted({i['stage'] for i in items
                                      if i.get('stage') and (i.get('chg_pct') or 0) > 0})
        r['stages_newhigh'] = sorted({i['stage'] for i in items
                                      if i.get('stage') and i.get('label')})
        out.append(r)
    out.sort(key=lambda r: (r['chg_pct'] is None, -(r['chg_pct'] or 0)))
    return out


def heatmap(rows, groups, cfg, group_key='theme'):
    """히트맵 입력.

    트레이딩뷰와 같은 세 컨트롤(크기·색상·그룹핑)을 화면에서 고를 수 있도록
    셀마다 거래대금·시가총액·기간별 등락률을 모두 실어 보낸다.
    색은 화면에서 칠한다. 여기서는 숫자만 만든다.
    """
    lim_g = cfg['display']['heatmap_groups']
    lim_c = cfg['display']['heatmap_cells']
    by_code = {x['code']: x for x in rows}
    # groups 는 등락률 내림차순이다. 앞에서만 자르면 **하락 섹터가 한 칸도
    # 안 나온다.** CLAUDE.md 10장이 '하락 청색'을 못박았는데 청색 셀이 나올 수
    # 없는 화면이 된다. 위아래에서 반씩 뜬다.
    if len(groups) > lim_g:
        half = lim_g // 2
        picked = list(groups[:lim_g - half]) + list(groups[-half:])
    else:
        picked = list(groups)
    out = []
    for g in picked:
        items = [by_code[c] for c in g['codes'] if c in by_code]
        items.sort(key=lambda x: -(x.get('turnover') or 0))
        cells = [dict(code=x['code'], name=x['name'],
                      chg_pct=x.get('chg_pct'), ret_5d=x.get('ret_5d'),
                      ret_21d=x.get('ret_21d'), ret_250d=x.get('ret_250d'),
                      turnover=x.get('turnover'), mktcap=x.get('mktcap'),
                      newhigh=x.get('label'), stage=x.get('stage'))
                 for x in items[:lim_c]]
        if not cells:
            continue
        out.append(dict(group=g['name'], key=g.get('theme') or g['name'],
                        chg_pct=g.get('chg_pct'), turnover=g.get('turnover'),
                        n_total=len(g['codes']), cells=cells))
    return out


def detect(rows, cfg, prev_near=None):
    """가격·거래량만으로 결정론적으로 나오는 탐지기.

    뉴스·수급·전일 주장을 요구하는 탐지기(2·5·7·8)와 밸류체인 확산(1)은
    여기 없다. 입력 데이터가 아직 없으므로 만들지 않는다 (CLAUDE.md 2장 1번).
    나오는 이벤트: 3 재료반납 · 4 거래량이상 · 6 다중라벨 · 9 근접클러스터 · 10 돌파실패
    """
    ev = []
    gb = cfg['giveback']
    for x in rows:
        chg, pp = x.get('chg_pct'), x.get('giveback_pp')
        # chg 가 None 이면 판정하지 않는다. `or 0` 으로 두면 등락률을 모르는
        # 종목이 전부 '3% 미만' 조건을 통과해 재료 반납으로 잡힌다.
        if (x.get('giveback') is not None and chg is not None and pp is not None
                and x['giveback'] >= gb['ratio'] and chg < gb['max_chg_pct']
                and pp >= gb.get('min_pp', 0)):
            ev.append(dict(type='material_giveback', theme=x.get('theme'),
                           tickers=[x['code']],
                           severity=round(x.get('giveback_pp') or 0, 2),
                           evidence=dict(giveback=x['giveback'],
                                         giveback_pp=pp,
                                         high_chg_pct=x.get('high_chg_pct'),
                                         chg_pct=x.get('chg_pct'),
                                         high=x.get('high'), close=x.get('close'))))
        if (x.get('vol_mult') or 0) >= cfg['detect']['volume_anomaly_mult']:
            ev.append(dict(type='volume_anomaly', theme=x.get('theme'),
                           tickers=[x['code']], severity=round(x['vol_mult'], 2),
                           evidence=dict(vol_mult=x['vol_mult'], volume=x.get('volume'),
                                         avg_vol_20=x.get('avg_vol_20'))))
        # 60일·252일·역사적 중 multi_label_min 종 이상. hits 전체를 세지 않고
        # 이 집합만 세는 이유는 계산하는 라벨이 늘 때 뜻이 바뀌지 않게 하기
        # 위해서다 — D-010 으로 120일을 넣었을 때 하나만 뚫어도 하위가 따라와
        # 자동으로 3종이 됐고, 이 이벤트 61건이 120일 신고가 수와 정확히 일치했다.
        hits = [k for k in MULTI_LABEL_SET if (x.get('hits') or {}).get(k)]
        if len(hits) >= cfg['detect']['multi_label_min']:
            ev.append(dict(type='multi_label_high', theme=x.get('theme'),
                           tickers=[x['code']], severity=len(hits),
                           evidence=dict(labels=hits)))
    # 9 근접 클러스터 — 한 테마에서 근접 종목 3개 이상
    near = {}
    for x in rows:
        if x.get('near_kind') and x.get('theme'):
            near.setdefault(x['theme'], []).append(x['code'])
    for t, cs in near.items():
        if len(cs) >= cfg['detect']['proximity_cluster_min']:
            ev.append(dict(type='proximity_cluster', theme=t, tickers=sorted(cs),
                           severity=len(cs), evidence=dict(n=len(cs))))
    # 10 돌파 실패 — 어제 근접이었으나 오늘 갭이 다시 벌어진 종목
    #
    # 예전에는 오늘의 near_gap 을 봤는데, near_gap 은 갭이 5%를 넘는 순간
    # None 이 된다. 즉 **크게 벌어진 종목일수록 반드시 제외됐다** — 잡아야 할
    # 것만 골라서 놓치는 구조였다. 이제 어제와 같은 기준(kind)의 오늘 갭을
    # gap 딕셔너리에서 직접 꺼낸다. 기준이 바뀌어도 비교가 어긋나지 않는다.
    if prev_near:
        cur = {x['code']: x for x in rows}
        for code, pv in prev_near.items():
            kind, pg = (pv if isinstance(pv, (tuple, list)) else (None, pv))
            x = cur.get(code)
            if not x or x.get('label') or pg is None:
                continue
            g = (x.get('gap') or {}).get(kind) if kind else x.get('near_gap')
            if g is not None and g > pg:
                ev.append(dict(type='breakout_fail', theme=x.get('theme'),
                               tickers=[code], severity=round(g - pg, 2),
                               evidence=dict(kind=kind, gap_prev=pg, gap_now=g)))
    ev.sort(key=lambda e: -(e['severity'] or 0))
    return ev
