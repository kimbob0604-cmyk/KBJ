#!/usr/bin/env python3
"""
실시간 보드가 읽는 JSON — state/YYYYMMDD/*.json 을 화면용 한 덩어리로 묶는다.

왜 따로 두는가. 예전에는 `render.py` 가 데이터와 HTML 을 함께 구워
`docs/index.html` 을 통째로 새로 만들었다. 그러면 링크를 열 때 보이는 것은
**만들어진 시점의 화면**이다. 화면 코드를 한 줄 고쳐도 보드를 다시 돌려야
반영되고, 데이터만 바뀌어도 300KB 짜리 문서가 통째로 커밋된다.

여기서는 둘을 가른다.

    docs/index.html      데이터가 0 인 고정 셸 (web/app.py)
    docs/api/latest.json 데이터만 (이 파일)
    docs/api/d/<날짜>.json 날짜별 보관본
    docs/api/index.json  보관 목록 + 최신 생성 시각

셸은 열릴 때 `latest.json` 을 받아 그리고, 열어 둔 채로 주기적으로 다시 받아
`generated_at` 이 바뀌었으면 그 자리에서 갈아 끼운다. 사람이 새로고침을
누르지 않아도 최신이 된다.

**여기서 계산하지 않는다.** 엔진이 낸 값을 골라 담기만 한다. 화면이 쓰지 않는
필드는 빼고(용량), 화면이 쓰는 필드는 이름을 바꾸지 않는다(대조).
"""
import json
import os

from ..engine import kinds as K
from . import render as R


# 화면이 실제로 그리는 칸만 싣는다. state 의 나머지(시계열·중간값)는 빼야
# 링크를 열 때 받는 양이 몇 배로 늘지 않는다.
ACH_KEYS = ('code', 'name', 'stage', 'theme_name', 'status',
            'chg_pct', 'turnover', 'mktcap', 'vol_mult', 'suspect')
NEAR_KEYS = ('code', 'name', 'stage', 'theme_name', 'near_kind', 'near_gap',
             'near_narrow5', 'turnover', 'mktcap', 'vol_mult')


def _pick(x, keys):
    """None 인 칸은 아예 빼고 담는다. 화면은 없는 키를 '–' 로 그린다.

    0 과 None 은 다르다 — 없는 키를 0 으로 채우면 '계산 안 됨' 이 '0' 이 된다.
    """
    return {k: x[k] for k in keys if x.get(k) is not None}


def _flow_of(sf, code):
    """그 종목의 수급 한 덩어리. 받은 구분만 담는다 — 없는 구분은 0 이 아니다.

    출처(`source`)와 종가 환산 추정 금액(`amt_est`, is_estimate)도 그대로 싣는다.
    네이버 폴백 값은 KIS 와 성격이 다르므로(수량 vs 금액) 화면이 구분할 수 있어야
    한다 (D-083).
    """
    e = ((sf or {}).get('by_code') or {}).get(code)
    if not e:
        return None
    d = dict(unit=e.get('unit'), as_of=e.get('as_of'))
    n = 0
    for who in ('기관', '외국인', '개인'):
        if e.get(who) is not None:
            d[who] = e[who]
            n += 1
    if not n:
        return None
    if e.get('source'):
        d['source'] = e['source']
    if isinstance(e.get('amt_est'), dict):
        d['amt_est'] = e['amt_est']
    return d


def _trigger_of(tr, code):
    """그 종목의 당일 재료 목록. 링크는 여기(화면)에만 싣는다 — 텔레그램에는 없다.

    없는 종목은 None 이다. 빈 리스트를 넣으면 '수집했는데 없음' 과 '수집 안 함' 이
    같은 모양이 된다 — 전자는 사실이고 후자는 결손이라 notices.miss 가 말한다.
    """
    e = ((tr or {}).get('by_code') or {}).get(code)
    if not e or not e.get('items'):
        return None
    out = []
    for it in e['items']:
        d = dict(title=it.get('title'), publisher=it.get('publisher'),
                 link=it.get('link'), source=it.get('source'),
                 date=(it.get('published_at') or '')[:10] or None)
        if it.get('kind'):
            d['kind'] = it['kind']
        out.append({k: v for k, v in d.items() if v is not None})
    return out


def _achieved(rows, sf=None, tr=None):
    out = []
    for x in rows:
        d = _pick(x, ACH_KEYS)
        d['kind'] = x.get('kind') or K.of(x.get('code'), x.get('name'))
        # **두 기준을 이름으로 싣는다.** `label` 은 기본 기준이라 설정
        # (newhigh.default_basis)에 따라 가리키는 기준이 바뀐다 — 그걸 '고가' 로
        # 짐작해 실으면 화면의 토글이 종가 라벨을 고가라고 보여 준다.
        d['high_label'] = (x.get('high_basis') or {}).get('label')
        d['close_label'] = (x.get('close_basis') or {}).get('label')
        # 종목별 수급(52주 이상만 받는다). 없는 종목은 키 자체가 없다 —
        # 빈 dict 를 넣으면 화면이 '받았는데 0' 으로 읽는다.
        fl = _flow_of(sf, x.get('code'))
        if fl:
            d['flow'] = fl
        # 종목별 재료(52주 이상만 모은다, D-085). 없는 종목은 키 자체가 없다.
        t = _trigger_of(tr, x.get('code'))
        if t:
            d['trigger'] = t
        out.append(d)
    return out


def _proximity(rows):
    out = []
    for x in rows:
        d = _pick(x, NEAR_KEYS)
        d['kind'] = x.get('kind') or K.of(x.get('code'), x.get('name'))
        k = x.get('near_kind')
        # 저항두께는 기준별로 따로 계산돼 있다. 그 종목이 쓰는 기준 것만 싣는다.
        rv = (x.get('resistance') or {}).get(k)
        if rv is not None:
            d['resistance'] = rv
            d['resistance_label'] = (x.get('resistance_label') or {}).get(k)
        out.append(d)
    return out


def _market(market):
    """머리말 띠 — 지수·환율·수급. 못 받은 것은 담지 않는다.

    '수집 실패' 라는 문구를 여기서 만들지 않는 이유는, 화면이 값의 유무만 보고
    같은 말을 한 곳에서 쓰기 위해서다.
    """
    m = market or {}
    idx = []
    for sym in ('KOSPI', 'KOSDAQ'):
        x = (m.get('indices') or {}).get(sym)
        if not x:
            continue
        idx.append(dict(sym=sym, label=x.get('label') or sym,
                        close=x.get('close'), chg_pct=x.get('chg_pct')))
    fx = m.get('fx') or {}
    flows = []
    d = (m.get('flows') or {}).get('KOSPI') or (m.get('flows') or {}).get('0001') \
        or next(iter((m.get('flows') or {}).values()), None)
    by = (d or {}).get('by_date') or {}
    if by:
        cur = by[max(by)]
        for who in R.FLOW_ORDER:
            if cur.get(who) is not None:
                flows.append(dict(who=who, value=cur[who]))
    return dict(indices=idx,
                fx=dict(value=fx.get('value'), chg_pct=fx.get('chg_pct')) if fx.get('value') is not None else None,
                flows=flows, unit=(d or {}).get('unit') or '억원',
                has_flows=bool(m.get('flows')))


def _clusters(events, tmeta):
    """돌파 임박 — 탐지 9(근접 클러스터) 상위 4개."""
    cl = [x for x in events if x.get('type') == 'proximity_cluster']
    out = []
    for x in sorted(cl, key=lambda y: -y['severity'])[:4]:
        nm = (tmeta.get(x['theme']) or {}).get('name') or x['theme']
        out.append(dict(name=nm, n=x['severity']))
    return out


def _news(news, narratives):
    ts = (news or {}).get('themes') or []
    out = []
    for t in ts:
        arts = [dict(title=a.get('title'), url=a.get('url'),
                     outlet=a.get('outlet'), summary=a.get('summary'),
                     query=a.get('query'))
                for a in (t.get('articles') or [])]
        out.append(dict(name=t.get('name'), chg_pct=t.get('chg_pct'),
                        n_newhigh=t.get('n_newhigh'), n_found=t.get('n_found'),
                        queries=t.get('queries') or [], articles=arts,
                        narrative=(narratives or {}).get(t.get('name'))))
    return out


def build(newhigh, sectors, market, events, universe, meta,
          draft=None, rankings=None, news=None, narratives=None, xlsx=None,
          stockflows=None, triggers=None):
    """화면이 받는 한 덩어리. `render.build` 와 같은 입력을 받는다."""
    labels = newhigh.get('labels') or {'hist': '역사적', 'w52': '52주', 'd60': '60일'}
    evs = (events or {}).get('events') or []
    tmeta = {t['theme']: t for t in (sectors or {}).get('themes') or []}
    rk = rankings or {}
    ach = newhigh.get('achieved') or []
    near = newhigh.get('proximity') or []

    # 종목별 재료의 결손(소스를 접음·예산 초과·인박스 없음·단계 실패)도 배너다 —
    # 합류는 notices 한 곳에서 한다. 구운 HTML 과 같은 말을 해야 한다.
    miss, scope = R.notices(newhigh, market, rankings, news, universe, triggers)

    hm = dict(theme=(sectors or {}).get('heatmap_theme') or [],
              sector=(sectors or {}).get('heatmap_sector') or [])
    R._attach_financials(hm)

    return dict(
        as_of=newhigh.get('as_of'),
        generated_at=newhigh.get('generated_at'),
        basis='hi' if newhigh.get('basis') == 'high' else 'cl',
        # 종가가 KRX 정규장 확정치인지 (D-080). render.build 는 이걸 읽어 머리에
        # '종가 확정'·'종가 잠정' 을 붙이는데 payload 가 안 실어서, 사람이 실제로
        # 여는 실시간 보드에는 그 경고가 한 번도 뜬 적이 없다.
        # 옛 state 에는 키가 없다 — None 을 그대로 넘겨 화면이 '모른다' 로 다룬다.
        close_confirmed=newhigh.get('close_confirmed'),
        close_source=newhigh.get('close_source'),
        labels=labels,
        displayed=newhigh.get('displayed') or ['hist', 'w52', 'd60'],
        # 알약의 수도 기준별로 나뉜다. `counts` 는 기본 기준의 수라 같은 이유로
        # 짐작하면 안 된다 — 없을 때만 폴백으로 쓴다(옛 state).
        counts_high=newhigh.get('counts_high') or newhigh.get('counts') or {},
        counts_close=newhigh.get('counts_close') or {},
        thresholds=newhigh.get('thresholds') or {},
        min_turnover_eok=newhigh.get('min_turnover_eok'),
        universe_n=universe.get('n'),
        n_events=len(evs),
        market=_market(market),
        notices=dict(miss=miss, scope=scope),
        clusters=_clusters(evs, tmeta),
        # 우선주·스팩·리츠 수. 두 표에 같은 종목이 있어 코드로 한 번만 센다.
        other_kinds=K.counts({x['code']: x for x in list(ach) + list(near)}.values()),
        kind_labels={k: v for k, v in K.LABEL.items()},
        achieved=_achieved(ach, stockflows, triggers),
        # 수급을 못 받은 사유는 배너가 아니라 여기에 따로 둔다 —
        # 52주 이상만 받는 값이라 '빠진 데이터' 와 층이 다르다. 못 받은 것만이
        # 아니라 네이버로 대체한 것의 안내도 든다(D-083) — app.js 의 배너 제목이
        # 그 둘을 다 가리킨다.
        flow_missing=(stockflows or {}).get('missing') or [],
        flow_kind=(stockflows or {}).get('kind'),
        proximity=_proximity(near),
        taxonomy=rk.get('taxonomy') or '',
        sector_boards=rk.get('sector_boards') or [],
        stock_boards=rk.get('stock_boards') or [],
        cross_n=len(rk.get('cross_codes') or []),
        themes=(sectors or {}).get('themes') or [],
        heatmap=hm,
        news=_news(news, narratives),
        draft=draft or '',
        xlsx=os.path.basename(xlsx) if xlsx else None,
    )


def from_state(asof, xlsx=None):
    """state/YYYYMMDD 를 읽어 페이로드 dict 를 돌려준다."""
    from ..engine.build import read, read_text
    from ..ingest import triggers as TR
    nhj = read(asof, 'newhigh.json')
    if not nhj:
        raise RuntimeError(f'state/{asof} 에 newhigh.json 이 없다. 엔진을 먼저 돌려라.')
    return build(nhj, read(asof, 'sectors.json') or {}, read(asof, 'market.json') or {},
                 read(asof, 'events.json') or {}, read(asof, 'universe.json') or {},
                 meta={}, draft=read_text(asof, 'draft.md'),
                 rankings=read(asof, 'rankings.json') or {},
                 news=read(asof, 'news.json') or {},
                 narratives=(read(asof, 'narratives.json') or {}).get('themes'),
                 xlsx=xlsx, stockflows=read(asof, 'stockflows.json'),
                 # 파일이 없으면 '수집되지 않음(단계 실패)' 결손 한 줄짜리 대역이 온다.
                 triggers=TR.load(asof))


def dump(obj):
    """파일에 쓸 문자열. 한글을 이스케이프하지 않아 그대로 읽힌다."""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
