#!/usr/bin/env python3
"""
네이버 검색 API — 뉴스.

레퍼런스 코멘트에서 이게 없으면 못 쓰는 문장들:
  "전일 웨스팅하우스 지분 공동인수설로 시작된 자금 유입이 이틀째 지속"
  "러시아 리아노보스티의 미·이란 휴전 합의 보도가 트리거"
  "전진건설로봇은 D-MCR 실증 완료 소식이 같은 날 겹치며"

즉 **왜 올랐는지**가 전부 여기 달려 있다. 등락률과 거래량만으로는 무엇이 일어났는지만
알 수 있고 왜인지는 알 수 없다.

## 저작권

이 API 는 제목·링크·요약(description)만 돌려준다. 우리는 그 셋만 저장한다.
**원문을 따로 긁어 저장하지 않는다.** CLAUDE.md 9장 3번의 방침과 맞다.
서술에 인용할 때는 매체명과 링크를 함께 남긴다.

## 한도

무료 일 25,000회. 테마당 1회씩만 불러도 60여 회라 여유가 크다.
"""
import html as H
import re
from datetime import timedelta, timezone

from . import creds
from .http import Fetch, get, session

URL = 'https://openapi.naver.com/v1/search/news.json'
SOURCE = 'naver_news'
KST = timezone(timedelta(hours=9))
_TAG = re.compile(r'<[^>]+>')


def _s():
    s = session()
    s.headers.update({
        'X-Naver-Client-Id': creds.get('NAVER_CLIENT_ID', required=True),
        'X-Naver-Client-Secret': creds.get('NAVER_CLIENT_SECRET', required=True),
    })
    return s


def _clean(t):
    return H.unescape(_TAG.sub('', t or '')).strip()


# 월 이름 → 숫자. strptime('%b') 는 LC_TIME 로케일에 의존해서, 러너 로케일이
# C/POSIX 가 아니면 전량 파싱 실패하고 기사가 조용히 다 버려진다.
_MONTHS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}
_PUB = re.compile(r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})')


def _pubdate(s):
    """'Tue, 26 Aug 2026 18:03:00 +0900' → 'YYYY-MM-DD'. 로케일에 의존하지 않는다."""
    m = _PUB.search(str(s or ''))
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).title())
    if not mon:
        return None
    return f'{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}'


def search(query, display=30, sort='date', s=None, timeout=None, retries=3):
    """뉴스 검색. 최신순. 반환은 제목·링크·요약·매체·날짜만.

    `timeout`·`retries` 는 http.get 에 그대로 넘긴다. 종목별 트리거 수집은 시간
    예산 안에서 종목마다 두 번씩 부르므로 8초·2회로 줄여 쓴다.
    `outlet` 은 매체명이 아니라 **originallink 의 도메인**이다 — 이 API 는 매체명을
    주지 않는다. 표시명은 소비자가 설정의 도메인 표로 바꾼다.
    """
    js = get(s or _s(), URL, params={'query': query, 'display': display,
                                     'start': 1, 'sort': sort},
             timeout=timeout, retries=retries)
    items = js.get('items') or []
    out = []
    for x in items:
        link = x.get('originallink') or x.get('link') or ''
        out.append(dict(
            title=_clean(x.get('title')),
            summary=_clean(x.get('description')),
            url=link,
            outlet=re.sub(r'^www\.', '', (re.split(r'/', link.split('//')[-1]) or [''])[0]),
            date=_pubdate(x.get('pubDate')),
            source=SOURCE))
    return out


def for_theme(theme_name, stock_names, asof, per_query=20, s=None):
    """테마 하나에 대한 당일 뉴스. 테마명 + 대표 종목명으로 나눠 검색한다.

    당일 기사만 남긴다. 어제 기사가 섞이면 '오늘의 트리거' 가 아니게 된다.
    """
    s = s or _s()
    seen, out, errs = set(), [], []
    queries = [theme_name] + list(stock_names)[:4]
    for q in queries:
        try:
            rows = search(q, display=per_query, s=s)
        except Exception as e:                     # noqa: BLE001
            # 사유 없이 삼키면 '오늘 뉴스가 없음' 과 구분되지 않는다.
            # 그러면 리포트가 "트리거가 없었다"는 **사실 주장**을 하게 된다.
            errs.append(f'{q}: {type(e).__name__}: {str(e)[:80]}')
            continue
        for r in rows:
            if r['date'] != asof or not r['url'] or r['url'] in seen:
                continue
            seen.add(r['url'])
            out.append(dict(r, query=q))
    if errs and not out:
        # 전부 실패했으면 '뉴스 없음'이 아니라 '못 받음'이다. 예외로 올린다.
        raise Fetch('뉴스 검색이 전부 실패했다 — ' + ' / '.join(errs[:2]))
    return out


def probe():
    if not creds.has('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET'):
        miss = [k for k in ('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET')
                if not creds.has(k)]
        return [('네이버 검색 API', False, f'{", ".join(miss)} 없음')]
    try:
        r = search('원전', display=5)
        return [('네이버 검색 API', True,
                 f'{len(r)}건 · 예: {(r[0]["title"][:40] + "…") if r else ""}')]
    except Exception as e:                         # noqa: BLE001
        return [('네이버 검색 API', False, str(e)[:160])]


# ── 수집 단계 ──────────────────────────────────────────────
# CLAUDE.md 3장 계약: sectors.json·universe.json 을 읽어 news.json 을 쓴다.
# 단계 간 함수 호출로 데이터를 넘기지 않는다.

def _score(t):
    """뉴스를 붙일 값어치. 신고가가 먼저고, 그다음이 움직인 폭이다.

    거래대금 배수를 섞지 않는 이유: 배수는 시총이 작을수록 쉽게 튀어서
    아무도 안 보는 소형 테마가 상위를 채운다.
    """
    return (t.get('n_newhigh') or 0) * 10 + abs(t.get('chg_pct') or 0)


def pick_themes(themes, cfg):
    """오늘 실제로 움직인 테마만 고른다.

    안 움직인 테마의 기사는 그날의 트리거가 아니라 그냥 기사다.
    """
    c = (cfg or {}).get('news') or {}
    lo = c.get('min_abs_chg_pct', 2.0)
    live = [t for t in (themes or [])
            if (t.get('n_newhigh') or 0) > 0 or abs(t.get('chg_pct') or 0) >= lo]
    live.sort(key=_score, reverse=True)
    return live[:c.get('max_themes', 8)]


def theme_stock_names(theme, stocks, limit):
    """테마 안에서 검색어로 쓸 종목명. 거래대금 상위로 고른다.

    등락률 상위로 고르면 시총 30억짜리 상한가가 뽑혀서, 그 종목 이름으로
    검색해 봐야 그날 시장이 반응한 재료가 안 나온다.
    """
    codes = set(theme.get('codes') or [])
    mine = [s for s in stocks if s.get('code') in codes and s.get('name')]
    mine.sort(key=lambda s: -(s.get('turnover') or 0))
    names, seen = [], set()
    lead = (theme.get('leader') or {}).get('name')
    if lead:
        names.append(lead)
        seen.add(lead)
    for s in mine:
        if len(names) >= limit:
            break
        if s['name'] not in seen:
            names.append(s['name'])
            seen.add(s['name'])
    return names


def rank(arts, theme_name):
    """기사 정렬·선별. **종목명으로 걸린 것이 먼저다.**

    실데이터(2026-08-31)를 보고 뒤집었다. 처음엔 테마명 질의를 앞에 뒀는데,
    화장품 카드 첫 줄이 "제주, 7개월 연속 수출 증가율 전국 1위" 였고 보험
    카드에는 "예보, 신입직원 35명 공개 채용" 이 올라왔다. 테마명은 일반 명사라
    그 단어가 스쳐 지나간 기사가 전부 걸린다.

    종목명 질의는 적어도 그 테마의 실제 회사를 가리킨다. 그리고 제목에 그
    이름이 없으면 본문에 스친 것이므로 뺀다 — 그날의 트리거라면 제목에 있다.

    이걸로 잡음이 없어지지는 않는다. 제대로 하려면 7장의 뉴스 클러스터링이
    필요하고 그건 9장 미확정 3번으로 남아 있다. 여기서는 순서만 바로잡는다.
    """
    out = []
    for a in arts:
        q = a.get('query')
        if q and q != theme_name and q not in (a.get('title') or ''):
            continue                       # 종목명이 제목에 없다 — 스친 기사다
        out.append(a)
    out.sort(key=lambda a: (a.get('query') == theme_name,))
    return out


def collect(asof, sectors, universe, cfg=None, log=print):
    """고른 테마마다 당일 기사를 붙인다. 반환은 news.json 에 그대로 쓸 dict.

    자격증명이 없거나 검색이 실패하면 `missing` 에 사유를 남긴다. 빈 목록을
    내면서 아무 말도 안 하면 화면이 '오늘 재료가 없었다'는 사실 주장을 하게
    된다 — CLAUDE.md 2장 6번.
    """
    c = (cfg or {}).get('news') or {}
    picked = pick_themes((sectors or {}).get('themes'), cfg)
    out = dict(source=SOURCE, as_of=asof, themes=[], missing=[], n_queries=0)

    if not creds.has('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET'):
        miss = [k for k in ('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET')
                if not creds.has(k)]
        out['missing'].append(f'섹터 뉴스 — {", ".join(miss)} 없음')
        return out
    if not picked:
        out['missing'].append(
            f'섹터 뉴스 — 등락률 {c.get("min_abs_chg_pct", 2.0)}% 이상이거나 '
            '신고가가 있는 테마가 없어 검색하지 않았습니다')
        return out

    s = _s()
    stocks = (universe or {}).get('stocks') or []
    n_stocks = c.get('stocks_per_theme', 3)
    for t in picked:
        names = theme_stock_names(t, stocks, n_stocks)
        out['n_queries'] += 1 + len(names)
        try:
            arts = for_theme(t['name'], names, asof,
                             per_query=c.get('per_query', 20), s=s)
        except Exception as e:                     # noqa: BLE001
            out['missing'].append(f'섹터 뉴스 {t["name"]} — {str(e)[:120]}')
            log(f'  뉴스 {t["name"]}: 실패 — {str(e)[:80]}')
            continue
        arts = rank(arts, t['name'])
        out['themes'].append(dict(
            name=t['name'], theme=t.get('theme'), axis=t.get('axis'),
            chg_pct=t.get('chg_pct'), n_newhigh=t.get('n_newhigh'),
            leader=t.get('leader'), queries=[t['name']] + names,
            articles=arts[:c.get('max_articles', 6)],
            n_found=len(arts)))
        log(f'  뉴스 {t["name"]}: {len(arts)}건')

    got = sum(1 for t in out['themes'] if t['articles'])
    if out['themes'] and not got:
        out['missing'].append(
            f'섹터 뉴스 — 테마 {len(out["themes"])}개를 검색했으나 '
            f'{asof} 자 기사가 하나도 없었습니다 (검색은 성공)')
    return out
