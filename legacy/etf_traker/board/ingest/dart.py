#!/usr/bin/env python3
"""
DART OpenAPI — 공시와 기업 개황.

쓸 곳
  - 기업개황(`company.json`)의 표준산업분류코드 → KRX 업종의 대안 (docs/APIS.md A2)
  - 공시목록(`list.json`) → 신고가 종목의 당일 공시. 왜 올랐는지의 절반이 여기 있다
  - 사업의 내용 → themes.yaml 부트스트랩 분류의 입력 (CLAUDE.md 5장)

종목코드 ↔ DART 고유번호(corp_code) 매핑은 `corpCode.xml` 을 zip 으로 받아 만든다.
이 파일은 8MB 남짓이고 자주 안 바뀌므로 state/ 에 캐시한다.

이 환경에서 실호출 검증은 못 했다. run.py --check 로 확인하라.
"""
import io
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta

from ..engine.config import ROOT
from . import creds
from .http import Fetch, get, session

BASE = 'https://opendart.fss.or.kr/api'
SOURCE = 'dart'
CORP_CACHE = os.path.join(ROOT, 'state', '.dart_corp.json')


def _key():
    return creds.get('DART_API_KEY', required=True)


# DART 는 실패를 XML 로 준다. 코드만 보면 무슨 일인지 알 수 없어서 풀어 준다.
STATUS_KO = {
    '010': '등록되지 않은 인증키',
    '011': '사용할 수 없는 인증키 (일시 중지)',
    '012': '접근할 수 없는 IP',
    '013': '조회된 데이터가 없음',
    '014': '파일이 존재하지 않음',
    '020': '요청 제한 초과 (일 20,000건)',
    '021': '조회 가능한 회사 개수 초과',
    '100': '필드의 부적절한 값',
    '101': '부적절한 접근',
    '800': '시스템 점검 중 — 인증키 문제가 아니다. 시간을 두고 다시',
    '900': '정의되지 않은 오류',
    '901': '사용 유의사항 위반에 따른 이용제한',
}
_STATUS_RE = re.compile(r'<status>\s*(\d+)\s*</status>')
_MESSAGE_RE = re.compile(r'<message>\s*(.*?)\s*</message>', re.S)


def _decode_status(text):
    """XML 오류 응답을 사람이 읽는 한 줄로."""
    st = _STATUS_RE.search(text or '')
    if not st:
        return f'응답을 해석하지 못했다: {(text or "")[:120]}'
    code = st.group(1)
    msg = _MESSAGE_RE.search(text or '')
    known = STATUS_KO.get(code)
    tail = f' ({msg.group(1)[:80]})' if msg else ''
    return f'status={code} {known or "알 수 없는 코드"}{tail}'


def _check(js):
    st = str(js.get('status', '000'))
    if st not in ('000', '013'):        # 013 = 조회 결과 없음. 실패가 아니다
        raise Fetch(f'DART status={st} '
                    f'{STATUS_KO.get(st, "")} {js.get("message", "")}'.strip())
    return js


def corp_codes(refresh=False):
    """{종목코드(6자리): corp_code} 매핑. zip 을 받아 만들고 캐시한다."""
    if not refresh and os.path.exists(CORP_CACHE):
        try:
            with open(CORP_CACHE, encoding='utf-8') as f:
                return json.load(f)
        except ValueError:
            pass
    s = session()
    r = s.get(f'{BASE}/corpCode.xml', params={'crtfc_key': _key()}, timeout=60)
    if r.status_code != 200:
        raise Fetch(f'corpCode HTTP {r.status_code}')
    if r.content[:2] != b'PK':
        raise Fetch(f'zip 이 아니다 — {_decode_status(r.text)}')
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read(z.namelist()[0])
    out = {}
    for el in ET.fromstring(xml).iter('list'):
        stock = (el.findtext('stock_code') or '').strip()
        corp = (el.findtext('corp_code') or '').strip()
        if stock and corp:
            out[stock] = corp
    if not out:
        raise Fetch('corpCode 에 상장 종목이 없다')
    os.makedirs(os.path.dirname(CORP_CACHE), exist_ok=True)
    with open(CORP_CACHE, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    return out


def company(code, s=None):
    """기업개황. 표준산업분류코드(induty_code)가 여기 있다."""
    corp = corp_codes().get(code)
    if not corp:
        raise Fetch(f'{code} 의 corp_code 를 못 찾았다')
    js = _check(get(s or session(), f'{BASE}/company.json',
                    params={'crtfc_key': _key(), 'corp_code': corp}))
    return dict(code=code, corp_code=corp, name=js.get('corp_name'),
                induty_code=js.get('induty_code'), source=SOURCE)


def disclosures(bgn, end=None, page=1, count=100, s=None):
    """기간 공시목록. 신고가 종목에 당일 공시가 있었는지 대조하는 데 쓴다.

    **한계 둘 — 고치지 않고 적어 둔다.** `corp_cls='Y'` 라 코스피만 온다(코스닥은
    'K'). 그리고 `page` 한 장(기본 100건)만 받는다 — 하루 공시는 수백 건이라 뒷장은
    안 본다. 그래서 종목별 트리거 수집은 이 함수가 아니라 종목의 corp_code 로
    묻는 `disclosures_for()` 를 쓴다. 이 함수는 --check 의 통신 확인용으로 남는다.
    """
    end = end or bgn
    js = _check(get(s or session(), f'{BASE}/list.json', params={
        'crtfc_key': _key(), 'bgn_de': bgn.replace('-', ''),
        'end_de': end.replace('-', ''), 'corp_cls': 'Y',
        'page_no': page, 'page_count': count}))
    by_stock = {}
    for x in js.get('list') or []:
        sc = (x.get('stock_code') or '').strip()
        if not sc:
            continue
        by_stock.setdefault(sc, []).append(dict(
            title=x.get('report_nm'), date=x.get('rcept_dt'),
            url=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.get("rcept_no")}',
            filer=x.get('flr_nm')))
    return dict(source=SOURCE, total=js.get('total_count'), by_stock=by_stock)


# 주가 시계열에 계단을 만드는 기업행위. 제목으로 거른다.
#
# split_guard 는 하루 ±31% 넘는 점프를 '수정주가 미반영 의심' 으로 잡는데,
# 점프 자체는 두 가지를 뜻할 수 있다 — 분할·병합처럼 주식 수가 바뀐 것이거나,
# 그냥 그날 크게 움직인 것이거나. 시세만 봐서는 갈리지 않는다.
# 공시는 그 답을 직접 갖고 있다.
CORP_ACTION = ('분할', '병합', '감자', '무상증자', '액면')
# 공시는 사건 당일에만 나오지 않는다. 결정 공시가 앞서고 변경상장이 뒤따른다.
ACTION_WINDOW = 400


def stock_actions(code, bgn, end, s=None):
    """한 종목의 기간 공시 중 주식 수가 바뀌는 사건만.

    반환: [{title, date, url}] — 날짜 오름차순.
    빈 리스트는 '그런 공시가 없었다' 이고, 그건 조회 실패와 다르다.
    실패는 예외로 올린다 (CLAUDE.md 2장 6번).
    """
    corp = corp_codes().get(code)
    if not corp:
        raise Fetch(f'{code} 의 corp_code 를 못 찾았다')
    js = _check(get(s or session(), f'{BASE}/list.json', params={
        'crtfc_key': _key(), 'corp_code': corp,
        'bgn_de': bgn.replace('-', ''), 'end_de': end.replace('-', ''),
        'page_no': 1, 'page_count': 100}))
    out = []
    for x in js.get('list') or []:
        title = (x.get('report_nm') or '').strip()
        if not any(w in title for w in CORP_ACTION):
            continue
        d = str(x.get('rcept_dt') or '')
        out.append(dict(
            title=title,
            date=f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else d,
            url=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.get("rcept_no")}'))
    out.sort(key=lambda r: r['date'])
    return out


# 공시 제목 → 종류. 앞에 있는 것이 우선이다 — '자기주식취득 신탁계약 체결' 은
# buyback 이지 contract 가 아니다. 어디에도 안 걸리면 'other'.
# 종류는 서술 프롬프트가 '공시:' 표기를 고르는 데만 쓴다. 판정은 하지 않는다.
KINDS = (
    ('inquiry', ('조회공시', '풍문', '현저한시황변동', '시황변동')),
    ('buyback', ('자기주식', '자사주')),
    ('capital', ('유상증자', '무상증자', '전환사채', '신주인수권부사채', '교환사채',
                 '감자', '액면', '주식분할', '주식병합', '합병', '분할')),
    ('owner', ('최대주주', '주식등의대량보유', '임원ㆍ주요주주', '임원·주요주주',
               '경영권', '지분')),
    ('clinical', ('임상', '품목허가', '승인', '허가')),
    ('earnings', ('영업(잠정)실적', '잠정실적', '매출액또는손익', '실적', '결산')),
    ('contract', ('공급계약', '단일판매', '수주', '판매ㆍ공급', '판매·공급', '계약')),
)


def kind_of(title):
    """공시 제목의 종류. 위 표의 순서대로 처음 걸리는 것."""
    t = (title or '')
    for kind, words in KINDS:
        if any(w in t for w in words):
            return kind
    return 'other'


def _ymd(d):
    d = str(d or '')
    return f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else d


def disclosures_for(code, asof, s=None, timeout=None, retries=None):
    """한 종목의 **기준일 당일** 공시. corp_code 로 묻는다 — 시장 구분과 무관하다.

    반환 [{title, link, date, publisher:'DART', kind, filer}]. 빈 리스트는 '그날
    공시가 없었다' 이고 조회 실패와 다르다 — 실패는 예외로 올린다(CLAUDE.md
    2장 6번). 접수 **시각**은 응답에 없다(rcept_dt 는 날짜뿐). 그래서 소비자는
    수집 시각을 '공시는 HH:MM 접수분까지' 로 적는다 — 그 뒤 접수분은 못 본 것이다.
    `timeout`·`retries` 는 http.get 그대로. None 이면 기본값이다.
    """
    corp = corp_codes().get(code)
    if not corp:
        raise Fetch(f'{code} 의 corp_code 를 못 찾았다')
    kw = {}
    if timeout is not None:
        kw['timeout'] = timeout
    if retries is not None:
        kw['retries'] = retries
    ymd = asof.replace('-', '')
    js = _check(get(s or session(), f'{BASE}/list.json', params={
        'crtfc_key': _key(), 'corp_code': corp,
        'bgn_de': ymd, 'end_de': ymd,
        'page_no': 1, 'page_count': 100}, **kw))
    out = []
    for x in js.get('list') or []:
        title = (x.get('report_nm') or '').strip()
        if not title:
            continue
        out.append(dict(
            title=title,
            link=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.get("rcept_no")}',
            date=_ymd(x.get('rcept_dt')), publisher='DART', kind=kind_of(title),
            filer=(x.get('flr_nm') or '').strip() or None))
    return out


def probe():
    if not creds.has('DART_API_KEY'):
        return [('DART 인증키', False, 'DART_API_KEY 없음')]
    out = [('DART 인증키', True, creds.mask(creds.get('DART_API_KEY')))]
    try:
        m = corp_codes()
        out.append(('종목코드 매핑', True, f'{len(m):,}종목'))
    except Exception as e:                        # noqa: BLE001
        out.append(('종목코드 매핑', False, str(e)[:160]))
        return out
    try:
        d = (date.today() - timedelta(days=3)).isoformat()
        r = disclosures(d, date.today().isoformat())
        out.append(('공시목록', True, f'{r["total"]}건 · 종목 {len(r["by_stock"])}'))
    except Exception as e:                        # noqa: BLE001
        out.append(('공시목록', False, str(e)[:160]))
    try:
        c = company('005930')
        out.append(('기업개황', True, f'{c["name"]} 업종코드 {c["induty_code"]}'))
    except Exception as e:                        # noqa: BLE001
        out.append(('기업개황', False, str(e)[:160]))
    return out
