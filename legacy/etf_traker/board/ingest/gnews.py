#!/usr/bin/env python3
"""
Google News RSS 검색 — 종목별 트리거의 **2차** 소스.

    https://news.google.com/rss/search?q=<질의>+when:1d&hl=ko&gl=KR&ceid=KR:ko

인증키가 없고 공식 API 도 아니다. 러너에서 아직 검증되지 않았다(D-085) — 그래서
1차 소스(네이버 검색 API·DART)가 있는 상태에서 보태는 자리이고, 실패는 결손으로
적되 단계를 막지 않는다. 429 나 consent 페이지 리다이렉트(200 이지만 XML 이
아니다)는 `Fetch` 로 올리고 호출자가 그 소스를 접는다.

응답 항목의 `<title>` 은 '제목 - 매체' 꼴이라 마지막 ' - ' 에서 가른다. `<source>`
가 있으면 그것이 매체다. `pubDate` 는 GMT 라 KST 로 바꿔 날짜를 낸다 — 당일
기사만 남기는 필터가 그 날짜를 본다. 링크는 그대로 둔다(google 리다이렉트 주소
그대로. 풀려면 요청이 한 번 더 들고, 우리는 링크를 저장만 한다).
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .http import Fetch, get, session

URL = 'https://news.google.com/rss/search'
SOURCE = 'google_news'
KST = timezone(timedelta(hours=9))
TIMEOUT = 8          # 한 시도 대기 상한. 트리거 수집의 시간 예산 안에서 돈다
RETRIES = 2          # 시도 횟수(재시도 1회)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}
# 'Mon, 21 Sep 2026 06:12:00 GMT' · '... +0000' · '... +0900'
_PUB = re.compile(r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?'
                  r'\s*(GMT|UTC|Z|[+-]\d{4})?')


def params(q, when='1d', hl='ko', gl='KR', ceid='KR:ko'):
    """질의 파라미터. `when:1d` 를 질의에 붙인다 — 별도 기간 파라미터가 없다."""
    qq = f'{q} when:{when}' if when else q
    return {'q': qq, 'hl': hl, 'gl': gl, 'ceid': ceid}


def to_kst(pub):
    """RSS pubDate → KST datetime. 못 읽으면 None. 로케일에 의존하지 않는다."""
    m = _PUB.search(str(pub or ''))
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).title())
    if not mon:
        return None
    tz = m.group(7) or 'GMT'
    if tz in ('GMT', 'UTC', 'Z'):
        off = timedelta(0)
    else:
        sign = 1 if tz[0] == '+' else -1
        off = sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5]))
    try:
        dt = datetime(int(m.group(3)), mon, int(m.group(1)), int(m.group(4)),
                      int(m.group(5)), int(m.group(6) or 0),
                      tzinfo=timezone(off))
    except ValueError:
        return None
    return dt.astimezone(KST)


def split_title(title, source_name=None):
    """'제목 - 매체' 를 (제목, 매체) 로. `<source>` 가 있으면 그것이 매체다."""
    t = (title or '').strip()
    if source_name and t.endswith(' - ' + source_name):
        return t[:-(len(source_name) + 3)].rstrip(), source_name
    if ' - ' in t:
        head, tail = t.rsplit(' - ', 1)
        return head.rstrip(), (source_name or tail.strip())
    return t, (source_name or None)


def parse(xml_text):
    """RSS 본문 → [{title, outlet, url, published_at(KST ISO), date(KST), source}].

    XML 이 아니면 Fetch — 429 본문이나 consent 리다이렉트 페이지가 200 으로 오는
    경우가 그렇다. 조용히 빈 목록을 내면 '오늘 기사 없음' 과 구분되지 않는다.
    """
    txt = (xml_text or '').lstrip()
    if not txt.startswith('<'):
        raise Fetch(f'XML 이 아니다 — {" ".join(txt.split())[:120] or "본문 비어 있음"}')
    try:
        root = ET.fromstring(txt)
    except ET.ParseError as e:
        raise Fetch(f'XML 파싱 실패 — {e}; 앞부분: {" ".join(txt.split())[:120]}') from None
    if root.tag.lower() == 'html' or root.find('.//channel') is None:
        raise Fetch('RSS 가 아니다 — consent 페이지 또는 차단 응답으로 본다')
    out = []
    for it in root.iter('item'):
        src_el = it.find('source')
        src = (src_el.text or '').strip() if src_el is not None and src_el.text else None
        title, outlet = split_title(it.findtext('title'), src)
        dt = to_kst(it.findtext('pubDate'))
        out.append(dict(
            title=title, outlet=outlet, url=(it.findtext('link') or '').strip(),
            published_at=dt.isoformat(timespec='minutes') if dt else None,
            date=dt.date().isoformat() if dt else None,
            source=SOURCE))
    return out


def search(q, s=None, timeout=TIMEOUT, retries=RETRIES, when='1d'):
    """검색 한 번. 반환은 parse() 의 목록. 실패는 Fetch 로 올린다."""
    txt = get(s or session(), URL, params=params(q, when=when), want='text',
              timeout=timeout, retries=retries)
    return parse(txt)


def probe(q='케이씨'):
    try:
        r = search(q)
    except Exception as e:                         # noqa: BLE001
        return [('Google News RSS', False, str(e)[:160])]
    ko = sum(1 for x in r if re.search(r'[가-힣]', x.get('title') or ''))
    return [('Google News RSS', True,
             f'{len(r)}건 · 한국어 제목 {ko}건 · 예: '
             f'{(r[0]["title"][:40] + "…") if r else ""}')]
