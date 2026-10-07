#!/usr/bin/env python3
"""
미국장 섹터·산업 분류 — 규칙 기반. LLM 을 부르지 않는다.

들어오는 것은 나스닥 스크리너의 `sector`(12개) 와 `industry`(150개 남짓) 두 열이고,
나가는 것은 자체 섹터 18개(1층)와 사람이 읽는 산업 라벨(2층)이다.
규칙은 knowledge/us_sectors.yaml, 종목별 손수정은 knowledge/us_overrides.yaml.

같은 입력이면 늘 같은 결과가 나와야 어제 표와 오늘 표를 비교할 수 있다.
그래서 순서 있는 문자열 규칙이고, 걸리지 않으면 '미분류' 로 남긴다.
못 푼 것을 그럴듯한 섹터에 넣지 않는다 (CLAUDE.md 2장 1번).
"""
import functools
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
KNOW = os.path.join(HERE, 'knowledge')
UNMAPPED = '미분류'


@functools.lru_cache(maxsize=4)
def rules(path=None):
    with open(path or os.path.join(KNOW, 'us_sectors.yaml'), encoding='utf-8') as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=4)
def overrides(path=None):
    p = path or os.path.join(KNOW, 'us_overrides.yaml')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return (yaml.safe_load(f) or {}).get('tickers') or {}


def classify(ticker, sector_raw, industry_raw, rul=None, ovr=None, name=None):
    """(섹터, 산업라벨, 근거) 를 돌려준다.

    근거는 'override' | 'industry' | 'sector' | 'name' | '' 중 하나다. 화면과
    배너가 '이 배정이 어디서 왔는지' 를 그대로 적을 수 있어야 한다 (2장 2번).

    순서는 강한 신호부터다 — 손수정 → 소스 industry → 소스 sector → 회사 이름.
    이름은 마지막이다. 약한 신호이기 때문이고, 그래서 근거에 'name' 이 남는다.
    """
    rul = rul or rules()
    ovr = ovr if ovr is not None else overrides()
    ind_raw = (industry_raw or '').strip()
    sec_raw = (sector_raw or '').strip()

    o = ovr.get((ticker or '').upper()) or {}
    ind_label = o.get('industry') or rul.get('industry_label', {}).get(ind_raw) or ind_raw

    if o.get('sector'):
        return o['sector'], ind_label, 'override'

    low = ind_raw.lower()
    if low:
        for row in rul['sectors']:
            for frag in row.get('any') or ():
                if frag.lower() in low:
                    return row['name'], ind_label, 'industry'

    fb = rul.get('sector_fallback', {}).get(sec_raw)
    if fb and fb != UNMAPPED:
        return fb, ind_label, 'sector'

    nm = f' {(name or "").lower()} '
    if nm.strip():
        for row in rul.get('name_rules') or ():
            for frag in row.get('any') or ():
                if frag.lower() in nm:
                    return row['name'], ind_label, 'name'
    return UNMAPPED, ind_label, ''


def apply(rows):
    """유니버스 행에 sector·industry_label 을 채운다. 원본 열은 남긴다."""
    rul, ovr = rules(), overrides()
    n_unmapped = 0
    for r in rows:
        sec, ind, why = classify(r.get('ticker'), r.get('sector_raw'),
                                 r.get('industry_raw'), rul, ovr, r.get('name'))
        r['sector'], r['industry'], r['sector_src'] = sec, ind, why
        if sec == UNMAPPED:
            n_unmapped += 1
    return n_unmapped


def by_source(rows):
    """배정 근거별 종목 수. 'name' 이 많으면 규칙을 더 손봐야 한다는 뜻이다."""
    from collections import Counter
    return dict(Counter(r.get('sector_src') or 'none' for r in rows))


def unmapped_industries(rows, top=60):
    """미분류로 남은 (sector_raw, industry_raw) 를 많은 순으로.

    규칙은 **실데이터가 알려 준 문자열**로만 늘린다. 소스의 industry 어휘를
    짐작해서 쓰면 안 걸리는 규칙만 쌓인다 — run #2 에서 300종목 중 161종목이
    미분류였고, 원인이 전부 '내가 지어낸 어휘' 였다.
    """
    from collections import Counter
    c = Counter((r.get('sector_raw') or '', r.get('industry_raw') or '')
                for r in rows if r.get('sector') == UNMAPPED)
    out = [dict(sector_raw=a, industry_raw=b, n=n)
           for (a, b), n in c.most_common(top)]
    # 소스가 다 비워 보낸 행은 이름이라도 남겨야 사람이 판단할 수 있다.
    blanks = [r for r in rows if r.get('sector') == UNMAPPED
              and not (r.get('industry_raw') or r.get('sector_raw'))]
    for r in blanks[:top]:
        out.append(dict(sector_raw='', industry_raw='', ticker=r.get('ticker'),
                        name=r.get('name'), n=1))
    return out
