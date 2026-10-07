#!/usr/bin/env python3
"""
사실 팩(fact pack) 조립 — LLM 에 넘길 유일한 입력.

핵심 설계: **수치를 미리 문자열로 렌더해서 넘긴다.**

  {"name": "한전기술", "chg": "+13.63%", "turnover": "3,240억", "vol_mult": "8.2배"}

이렇게 하면 세 가지가 한 번에 해결된다.
  1. LLM 이 숫자를 계산할 일이 없다 (CLAUDE.md 2장 3번)
  2. "수치는 원문 그대로, 반올림해서 바꾸지 않는다" 가 강제된다 (7장 문체 규칙)
  3. 후처리 검증이 정확 문자열 대조로 끝난다 (2장 4번) — writer/verify.py

여기 없는 값은 리포트에 나올 수 없다. 뉴스도 수급도 사실 팩에 자리가 있고,
소스가 없으면 그 자리는 비고 `missing` 에 사유가 적힌다. 추정으로 채우지 않는다.
"""
import os
import statistics as st

import yaml

from .build import read
from .config import ROOT

SCORE_PATH = os.path.join(ROOT, 'config', 'score.yaml')


# ─────────────────────────── 수치 렌더 ───────────────────────────
def pct(v, digits=2, suffix='%'):
    return None if v is None else f'{v:+.{digits}f}{suffix}'


def gap(v, digits=1):
    return None if v is None else f'{v:.{digits}f}%'


def eok(v):
    """억원. 1조 이상은 조로 접는다. 레퍼런스가 '1.28조' / '3,240억' 둘 다 쓴다."""
    if v is None:
        return None
    if abs(v) >= 10000:
        return f'{v/10000:,.2f}조'
    return f'{v:,.0f}억'


def mult(v):
    return None if v is None else f'{v:,.1f}배'


def price(v):
    if v is None:
        return None
    return f'{v:,.2f}' if abs(v) < 10000 else f'{v:,.0f}'


def _clean(d):
    """None 값을 지운다. 빈 필드를 LLM 에 보여 주면 채우고 싶어진다."""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items()
                if v is not None and v != [] and v != {}}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


# ─────────────────────────── 스코어 ───────────────────────────
def load_score():
    with open(SCORE_PATH, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _norm(vals):
    """0~1 정규화. 전부 같은 값이면 0."""
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return [0.0] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def score_themes(themes, prev_themes, sc):
    """서술 대상 선정용 점수. 계산은 코드가 하고 LLM 은 결과만 받는다."""
    if not themes:
        return []
    w = sc['weights']
    chgs = [t.get('chg_pct') or 0.0 for t in themes]
    mean = st.mean(chgs)
    sd = st.pstdev(chgs) or 1.0
    prev = {t['theme']: set(t.get('stages_reacted') or []) for t in (prev_themes or [])}

    raw = dict(
        chg_z=[abs((c - mean) / sd) for c in chgs],
        newhigh_count=[float(t.get('n_newhigh') or 0) for t in themes],
        turnover_mult=[float(t.get('turnover_mult') or 0) for t in themes],
        new_stages=[float(len(set(t.get('stages_reacted') or [])
                              - prev.get(t['theme'], set()))) if prev else 0.0
                    for t in themes],
        flow_align=[0.0] * len(themes),      # 수급 미확보. 소스가 붙으면 채운다
        proximity=[float(t.get('n_near') or 0) for t in themes],
    )
    parts = {k: _norm(v) for k, v in raw.items()}
    out = []
    for i, t in enumerate(themes):
        s = sum(w.get(k, 0) * parts[k][i] for k in parts)
        out.append(dict(t, score=round(s, 4),
                        new_stages=sorted(set(t.get('stages_reacted') or [])
                                          - prev.get(t['theme'], set())) if prev else []))
    out.sort(key=lambda t: -t['score'])
    return out


def pick_narrate(scored, sc, forced):
    """서술 대상 선정. 강제 포함(전일 주장·당일 이벤트)이 우선이다."""
    sel = [t for t in scored if t['theme'] in forced]
    for t in scored:
        if len(sel) >= sc['select']['narrate_max']:
            break
        if t['theme'] in forced:
            continue
        if len(t.get('codes') or []) < sc['select']['min_stocks']:
            continue
        if abs(t.get('chg_pct') or 0) < sc['select']['min_abs_chg']:
            continue
        sel.append(t)
    return sel[:sc['select']['narrate_max']]


# ─────────────────────────── 팩 조립 ───────────────────────────
def _stock(x, labels, notes, flows_by_code, trig_by_code=None):
    n = notes.get(x['name']) or {}
    d = dict(
        name=x['name'], code=x['code'], stage=x.get('stage'),
        chg=pct(x.get('chg_pct')), cum_2d=pct(x.get('ret_2d')),
        cum_5d=pct(x.get('ret_5d')),
        turnover=eok(x.get('turnover')), vol_mult=mult(x.get('vol_mult')),
        newhigh=labels.get(x.get('label')), newhigh_status=x.get('status'),
        # 여러 창을 동시에 돌파한 경우 전부 적는다. 레퍼런스 문체가 그렇다 —
        # "제닉이 52주·120일·60일·20일 동시 신고가". 그때 쓰던 120일·20일은
        # 뺐으므로(D-060, D-071) 지금 나오는 건 52주·60일·역사적이다.
        newhigh_all=[labels.get(k, k) for k, v in (x.get('hits') or {}).items() if v] or None,
        note=n.get('what'), structure=n.get('structure'))
    f = flows_by_code.get(x['code'])
    if f:
        d['flows'] = f
    # 종목별 재료 (D-085). 이 종목의 당일 기사·공시·X 포워딩이다. 서술 모델은
    # 여기 있는 것만 트리거로 적을 수 있고, 없으면 트리거 문장을 쓰지 않는다.
    t = (trig_by_code or {}).get(x['code'])
    if t:
        d['trigger'] = t
    return _clean(d)


def _trigger_facts(tj):
    """triggers.json → {code: [{title, publisher, date, source, kind}]}.

    url 은 뺀다 — 서술에 주소를 옮겨 적을 일이 없다(뉴스와 같은 이유). **`name` 키는
    넣지 않는다** — 검증(writer/verify.py)이 `name` 키 아래 값을 종목명으로 등록해
    제목 속 수치를 그 종목의 값으로 오인한다. 제목의 수치는 검증의 허용 집합에도
    들지 않는다(`verify.NUMBER_SKIP_KEYS`) — 프롬프트가 옮기지 말라는 것을 기계도
    막는다. 여기서 마스킹하지는 않는다 — 모델이 제목의 요지를 읽어야 한다.
    """
    out = {}
    for code, e in ((tj or {}).get('by_code') or {}).items():
        items = []
        for it in (e or {}).get('items') or []:
            items.append(_clean(dict(
                title=it.get('title'), publisher=it.get('publisher'),
                date=(it.get('published_at') or '')[:10] or None,
                source=it.get('source'), kind=it.get('kind'))))
        if items:
            out[code] = items
    return out


def _flows_by_code(market, universe_by_code):
    """종목별 수급을 억원 문자열로. 소스가 없으면 빈 dict.

    소스는 `{code: {unit, by_date: {날짜: {기관, 외국인, ...}}}}` 모양으로 준다.
    예전에는 최상위에서 `v.get('기관')` 을 찾아 아무것도 못 꺼냈고, 단위 검사도
    없이 무조건 `x * close / 1e8` 을 해서 KIS 의 억원 값을 0억으로 만들었다.

    단위가 '주'면 종가를 곱해 억원으로 바꾸고 **(추정)** 을 붙인다. 체결가
    가중이 아니라 종가 기준이라 실제 순매수 금액과 다르다.
    """
    src = (market or {}).get('stock_flows') or {}
    out = {}
    for code, v in src.items():
        by = (v or {}).get('by_date') or {}
        if not by:
            continue
        cur = by[max(by)]
        unit = (v.get('unit') or cur.get('_unit') or '').strip()
        close = (universe_by_code.get(code) or {}).get('close') or cur.get('close')
        d = {}
        for who in ('기관', '외국인', '개인'):
            amt = cur.get(who)
            if amt is None:
                continue
            if unit == '억원':
                d[who] = eok(amt)
            elif close:
                d[who] = eok(amt * close / 1e8) + ' (추정)'
        if d:
            out[code] = d
    return out


def _theme_news(nt):
    """news.json 의 테마 항목을 서술 입력으로 접는다.

    url 은 뺀다 — 서술에 주소를 옮겨 적을 일이 없고 토큰만 쓴다. 화면의 뉴스
    탭이 링크를 따로 단다. outlet 은 남긴다 — 문체 규칙이 단독 보도·미확인
    루머를 명시하라고 하므로 어디가 보도했는지가 재료다.
    """
    arts = (nt or {}).get('articles') or []
    if not arts:
        return None
    return [dict(title=a.get('title'), outlet=a.get('outlet'),
                 date=a.get('date'), summary=a.get('summary'),
                 query=a.get('query'))
            for a in arts[:6]]


def build(asof, cfg, log=print):
    """state/YYYYMMDD 를 읽어 사실 팩을 만든다. 반환 dict."""
    nhj = read(asof, 'newhigh.json') or {}
    sec = read(asof, 'sectors.json') or {}
    mkt = read(asof, 'market.json') or {}
    evj = read(asof, 'events.json') or {}
    uni = read(asof, 'universe.json') or {}
    prev_asof = nhj.get('prev_asof')
    prev_sec = read(prev_asof, 'sectors.json') if prev_asof else None
    claims = read(prev_asof, 'claims.json') if prev_asof else None

    labels = nhj.get('labels') or {}
    sc = load_score()
    with open(os.path.join(ROOT, 'knowledge', 'notes.yaml'), encoding='utf-8') as f:
        notes = (yaml.safe_load(f) or {}).get('notes') or {}
    with open(os.path.join(ROOT, 'knowledge', 'events.yaml'), encoding='utf-8') as f:
        cal = (yaml.safe_load(f) or {}).get('events') or []

    by_code = {x['code']: x for x in uni.get('stocks') or []}
    fl = _flows_by_code(mkt, by_code)
    # 종목별 재료 (D-085). 파일이 없으면 load() 가 '수집되지 않음' 결손 한 줄짜리
    # 대역을 준다 — 조용히 빈 팩이 되지 않는다.
    from ..ingest import triggers as TR
    tj = TR.load(asof)
    trig = _trigger_facts(tj)

    # ── 시장 ─────────────────────────────────────────
    idx = {}
    for sym, x in (mkt.get('indices') or {}).items():
        st_n = x.get('streak') or 0
        idx[x.get('label') or sym] = _clean(dict(
            open=price(x.get('open')), low=price(x.get('low')),
            high=price(x.get('high')), close=price(x.get('close')),
            chg=pct(x.get('chg_pct')),
            streak=(f'{abs(st_n)}일 연속 ' + ('상승' if st_n > 0 else '하락')
                    if st_n else None)))
    fx = mkt.get('fx') or {}
    market = _clean(dict(
        indices=idx,
        fx=_clean(dict(name='USD/KRW', value=price(fx.get('value')),
                       chg=pct(fx.get('chg_pct')))),
        flows=_market_flows(mkt)))

    # ── 업종 ─────────────────────────────────────────
    sectors = [_clean(dict(name=s['name'], chg=pct(s.get('chg_pct')),
                           breadth=f'{s["breadth"]["up"]}/{s["breadth"]["flat"]}'
                                   f'/{s["breadth"]["down"]}',
                           n_newhigh=s.get('n_newhigh'),
                           turnover_mult=mult(s.get('turnover_mult'))))
               for s in (sec.get('sectors') or [])]

    # ── 테마 ─────────────────────────────────────────
    scored = score_themes(sec.get('themes') or [], (prev_sec or {}).get('themes'), sc)
    forced = set()
    for c in (claims or {}).get('claims') or []:
        if c.get('theme'):
            forced.add(c['theme'])
    cal_today = [e for e in cal if e.get('date') == asof]
    cal_soon = [e for e in cal if e.get('date', '') > asof]
    for e in cal_today + cal_soon:
        forced.update(e.get('themes') or [])
    ev_by_theme = {}
    for e in evj.get('events') or []:
        if e.get('theme'):
            ev_by_theme.setdefault(e['theme'], []).append(e)

    narrate = pick_narrate(scored, sc, forced)
    # 수집된 뉴스를 테마에 잇는다. 예전에는 news=None 으로 자리만 두어서,
    # 수집기(D-044)가 기사를 붙여도 서술 모델에는 한 건도 안 들어갔다 —
    # 트리거를 쓸 재료가 없으니 레퍼런스처럼 나올 수가 없다.
    news_by_id = {t.get('theme'): t for t in
                  ((read(asof, 'news.json') or {}).get('themes') or [])}
    themes = []
    for t in narrate:
        stocks = [by_code[c] for c in (t.get('codes') or []) if c in by_code]
        stocks.sort(key=lambda x: -(x.get('chg_pct') or 0))
        themes.append(_clean(dict(
            id=t['theme'], name=t['name'], axis=t.get('axis'),
            chg=pct(t.get('chg_pct')),
            turnover=eok(t.get('turnover')),
            n_newhigh=t.get('n_newhigh'), n_near=t.get('n_near'),
            stages=t.get('stages'),
            stages_reacted=t.get('stages_reacted'),
            stages_new_today=t.get('new_stages'),
            stocks=[_stock(x, labels, notes, fl, trig) for x in stocks[:12]],
            detected=[_event_fact(e, by_code, labels) for e in ev_by_theme.get(t['theme'], [])[:6]],
            prior_claims=[c for c in ((claims or {}).get('claims') or [])
                          if c.get('theme') == t['theme']],
            news=_theme_news(news_by_id.get(t['theme'])),
            score=t['score'])))

    table = [_clean(dict(name=t['name'], chg=pct(t.get('chg_pct')),
                         n_newhigh=t.get('n_newhigh'),
                         turnover=eok(t.get('turnover'))))
             for t in scored[len(narrate):len(narrate) + 20]]

    # ── 신고가 ────────────────────────────────────────
    top = sorted((uni.get('stocks') or []),
                 key=lambda x: -(x.get('chg_pct') or 0))[:15]
    bottom = sorted((uni.get('stocks') or []),
                    key=lambda x: (x.get('chg_pct') or 0))[:10]

    # ── 결손 ─────────────────────────────────────────
    missing = list(mkt.get('missing') or [])
    # 테마 기사도 종목 재료도 없을 때만 결손이다. 둘 중 하나라도 있으면 트리거를
    # 적을 재료가 있는 것이고, 재료가 없는 종목은 결손이 아니라 그날의 사실이다.
    has_trigger = any(s.get('trigger') for t in themes for s in (t.get('stocks') or []))
    if not any(t.get('news') for t in themes) and not has_trigger:
        missing.append('뉴스·재료: 테마 기사도 종목 재료도 없다 — 상승·하락의 트리거를 적을 수 없다')
    for m in tj.get('missing') or []:
        if m not in missing:
            missing.append(m)
    if nhj.get('n_suspect'):
        # '판정 제외' 는 내부 용어다. 읽는 사람이 알아야 할 것은 이 종목들의
        # 역사적 신고가가 이 리포트에 없다는 사실이다.
        missing.append(f'{nhj["n_suspect"]}종목은 역사적 신고가를 내지 못했습니다 — '
                       '수정주가가 반영되지 않은 것으로 보입니다')
    # `n_split_cleared` 는 '계단을 보고 공시를 찾아봤더니 없어서 실제 등락으로
    # 처리했다' 는 **엔진의 처리 경위**다. 결과적으로 빠진 데이터가 없으므로
    # 결손이 아니고, 읽는 사람에게는 할 말이 없다. 경위는 universe.json 과
    # run_log 에 남는다(2장 6번은 '빠진 것' 을 적으라는 규칙이지 처리 경위를
    # 적으라는 규칙이 아니다).

    pack = dict(
        as_of=asof, prev_asof=prev_asof,
        title_date=asof.replace('-', '')[2:],
        basis='장중 고가' if nhj.get('basis') == 'high' else '종가',
        counts={labels.get(k, k): v for k, v in (nhj.get('counts') or {}).items()},
        market=market, sectors=sectors[:12],
        sectors_bottom=_bottom(sectors, 12, 5),
        themes=themes, themes_table=table,
        movers=dict(up=[_stock(x, labels, notes, fl) for x in top],
                    down=[_stock(x, labels, notes, fl) for x in bottom]),
        calendar=dict(
            review=[_calendar(e, by_code, 'review') for e in cal_today],
            preview=[_calendar(e, by_code, 'preview') for e in cal_soon[:3]]),
        prior_claims=(claims or {}).get('claims') or [],
        missing=missing)
    log(f'  사실 팩 — 서술 테마 {len(themes)} / 수치 테이블 {len(table)} · '
        f'결손 {len(missing)}건')
    return pack


def _bottom(sectors, top_n, n):
    """하위 n개. 상위 목록과 겹치지 않고, 등락률을 못 구한 섹터는 뺀다.

    예전에는 같은 리스트에서 `[:12]` 와 `[-5:]` 를 떠서 섹터가 14개면 셋이
    양쪽에 동시에 실렸다. 그리고 by_sector 가 값 없는 섹터를 맨 뒤로 보내므로
    등락률을 못 구한 섹터가 '하위 5업종' 자리를 차지했다.
    """
    known = [s for s in sectors if s.get('chg') is not None]
    tail = known[top_n:]
    return tail[-n:] if tail else []


def _market_flows(mkt):
    """시장 전체 수급을 억원 문자열로. 소스 없으면 None."""
    fl = (mkt or {}).get('flows')
    if not fl:
        return None
    out = {}
    for market, d in fl.items():
        by = d.get('by_date') or {}
        if not by:
            continue
        cur = max(by)
        prev = sorted(by)[-2] if len(by) > 1 else None
        row = {k: eok(v) for k, v in by[cur].items() if v is not None}
        if prev:
            row['_전일'] = {k: eok(v) for k, v in by[prev].items() if v is not None}
        out[market] = row
    return out or None


def _event_fact(e, by_code, labels):
    """탐지기 출력을 서술용 문장 재료로. 수치는 이미 계산된 것만 옮긴다."""
    names = [(by_code.get(c) or {}).get('name') or c for c in (e.get('tickers') or [])]
    ev = e.get('evidence') or {}
    d = dict(type=e['type'], stocks=names)
    if e['type'] == 'material_giveback':
        # 레퍼런스: "장중 재료를 대부분 반납. 고가 대비 종가 괴리 11.2%p"
        d['fact'] = (f'장중 고가 {pct(ev.get("high_chg_pct"))} 까지 갔다가 '
                     f'종가 {pct(ev.get("chg_pct"))} · '
                     f'{pct(ev.get("giveback_pp"), 1, "%p")} 반납')
    elif e['type'] == 'volume_anomaly':
        d['fact'] = f'거래량 {mult(ev.get("vol_mult"))}'
    elif e['type'] == 'multi_label_high':
        d['fact'] = '·'.join(labels.get(k, k) for k in ev.get('labels') or []) + ' 동시 신고가'
    elif e['type'] == 'proximity_cluster':
        d['fact'] = f'근접 {ev.get("n")}종목'
    elif e['type'] == 'breakout_fail':
        d['fact'] = f'갭이 {gap(ev.get("gap_prev"))} 에서 {gap(ev.get("gap_now"))} 로 다시 벌어짐'
    return _clean(d)


def _calendar(e, by_code, kind):
    by_name = {}
    for x in by_code.values():
        by_name[x['name']] = x
    reactions = []
    for t in e.get('tickers') or []:
        x = by_name.get(t) or by_code.get(t)
        if x:
            reactions.append(dict(name=x['name'], chg=pct(x.get('chg_pct'))))
    return _clean(dict(
        kind=kind, name=e.get('name'), date=e.get('date'), time=e.get('time'),
        question=e.get('question'), facts=e.get('facts'), quotes=e.get('quotes'),
        source=e.get('source'), reactions=reactions or None))
