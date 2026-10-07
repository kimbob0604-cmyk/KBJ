#!/usr/bin/env python3
"""
공공데이터포털(data.go.kr) 금융위원회 시세 API — 인증키가 필요한 대체·교차검증 소스.

네이버 대비 장점
  - 하루 한 번 호출로 전 종목 시가·고가·저가·종가·거래량·거래대금·시가총액을 받는다.
    종목당 한 번씩 찌르는 네이버 방식보다 훨씬 가볍고 차단 위험이 없다.
  - 공식 공개 API 라 사이트 개편으로 조용히 깨지지 않는다.

단점
  - 인증키를 신청해야 한다 (무료, 즉시 발급, 일 트래픽 제한 있음).
  - 수정주가가 아니다. 액면분할·무상증자가 과거 시계열에 반영되지 않는다.
    역사적 신고가 판정이 여기 걸린다 — CLAUDE.md 9장 미확정 1번.
    engine/newhigh.py 의 split_guard 가 이 문제를 탐지해서 막는다.

환경변수
  DATAGO_KEY   Decoding 된 일반 인증키 (URL 인코딩된 키를 그대로 넣으면 이중 인코딩된다)

이 환경에서는 외부 접속이 막혀 있어 실호출로 검증하지 못했다. 쓰기 전에
run.py --check 를 돌려 응답 형태를 먼저 확인하라.
"""
import os
import re
from urllib.parse import unquote

from .http import Fetch, get, num, pick, session

BASE = 'https://apis.data.go.kr/1160100/service'
PRICE = f'{BASE}/GetStockSecuritiesInfoService/getStockPriceInfo'
INDEX = f'{BASE}/GetMarketIndexInfoService/getStockMarketIndex'
SOURCE = 'datago'
PAGE = 1000


# 어느 인증키 형태가 통했는지. probe() 가 읽어서 보고한다.
KEY_USED = {}


def key():
    k = os.environ.get('DATAGO_KEY', '').strip()
    if not k:
        raise Fetch('DATAGO_KEY 가 없다. 공공데이터포털에서 발급받아 환경변수로 넣어라.')
    return k


def key_forms():
    """시도할 인증키 형태. 통할 만한 것부터.

    포털은 같은 키를 Encoding / Decoding 두 형태로 보여 준다. Encoding 쪽은
    base64 를 퍼센트 인코딩한 것이라 `%2B` `%2F` `%3D` 가 들어 있고, requests
    는 파라미터를 **한 번 더** 인코딩하므로 `%252B` 가 되어 키가 깨진다.
    (datago.py 첫 줄 주석이 경고하던 그 경우다.)

    어느 쪽이 들어 있는지는 값을 봐야 알 수 있는데 값은 로그에 못 남긴다.
    그래서 묻지 않고 눌러 본다 — KIS 파라미터(D-032)·네이버 수급(D-049)에서
    쓴 것과 같은 방식이다. 통한 형태만 KEY_USED 에 남는다.
    """
    raw = key()
    forms = []
    dec = unquote(raw)
    if dec != raw:                      # 퍼센트 인코딩이 들어 있다 → 푼 쪽이 먼저
        forms.append(('디코딩', dec))
    forms.append(('그대로', raw))
    return forms


def call(s, url, params):
    """인증키 형태를 바꿔 가며 부른다. 한 번 통하면 그 형태만 쓴다."""
    forms = key_forms()
    if KEY_USED.get('form'):
        forms = [f for f in forms if f[0] == KEY_USED['form']] or forms
    last = None
    for label, k in forms:
        try:
            js = get(s, url, params={**params, 'serviceKey': k})
        except Fetch as e:
            last = e
            continue
        KEY_USED.update(form=label, n=len(k))
        return js
    raise Fetch(f'{last} — 인증키 형태 '
                f'{", ".join(f[0] for f in forms)} 를 모두 시도했다')


def _rows(js):
    """공공데이터포털 공통 응답 봉투를 벗긴다. 에러도 이 봉투에 담겨 온다."""
    body = (js or {}).get('response', {}).get('body')
    head = (js or {}).get('response', {}).get('header') or {}
    code = pick(head, 'resultCode')
    if code not in (None, '00', '0'):
        raise Fetch(f'{code} {pick(head, "resultMsg", default="")}')
    if not body:
        raise Fetch('응답 본문이 없다 (인증키·트래픽 초과 확인)')
    items = (body.get('items') or {}).get('item') or []
    if isinstance(items, dict):
        items = [items]
    return items, num(body.get('totalCount'), 0)


def _div(v, by):
    return None if v is None else v / by


def _norm(x):
    """응답 한 줄을 우리 스키마로. 단위는 원 → 억원으로 접는다."""
    d = str(pick(x, 'basDt', default=''))
    code = str(pick(x, 'srtnCd', 'shrtnCd', default='')).strip()
    code = re.sub(r'^A', '', code)
    return dict(
        code=code,
        name=pick(x, 'itmsNm', default=''),
        market=pick(x, 'mrktCtg', default=''),
        asof=f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else None,
        open=num(pick(x, 'mkp')), high=num(pick(x, 'hipr')),
        low=num(pick(x, 'lopr')), close=num(pick(x, 'clpr')),
        volume=num(pick(x, 'trqu')),
        chg_pct=num(pick(x, 'fltRt')),
        # 못 받으면 0 이 아니라 None (D-019). 0 으로 채우면 값이 없다는 사실이
        # '거래 없음'으로 둔갑하고 engine 의 일봉 대체도 발동하지 않는다.
        turnover=_div(num(pick(x, 'trPrc')), 1e8),
        mktcap=_div(num(pick(x, 'mrktTotAmt')), 1e8),
        shares=num(pick(x, 'lstgStCnt')),
        turnover_is_estimate=num(pick(x, 'trPrc')) is None,
        source=SOURCE)


def fetch_day(bas_dt, s=None, max_pages=20):
    """하루치 전 종목. bas_dt 는 'YYYY-MM-DD'.

    휴장일이면 빈 리스트를 돌려준다. 이건 실패가 아니다.
    """
    s = s or session()
    out, page = [], 1
    while page <= max_pages:
        js = call(s, PRICE, {
            'resultType': 'json',
            'numOfRows': PAGE, 'pageNo': page, 'basDt': bas_dt.replace('-', '')})
        items, total = _rows(js)
        out.extend(_norm(x) for x in items)
        # totalCount 가 없으면 0 이 되어 즉시 멈춘다. 그러면 3,900종목 중 1,000개만
        # 받고 정상 종료로 보인다. 페이지가 꽉 찼는지로만 판단한다.
        if len(items) < PAGE or (total and len(out) >= total):
            break
        page += 1
    return [x for x in out if x['code'] and x['close']]


def fetch_ohlcv(code, start, end, s=None, max_pages=10):
    """종목 하나의 구간 일봉. 오름차순."""
    s = s or session()
    out, page = [], 1
    while page <= max_pages:
        js = call(s, PRICE, {
            'resultType': 'json',
            'numOfRows': PAGE, 'pageNo': page, 'likeSrtnCd': code,
            'beginBasDt': start.replace('-', ''), 'endBasDt': end.replace('-', '')})
        items, total = _rows(js)
        out.extend(_norm(x) for x in items)
        if len(out) >= total or len(items) < PAGE:
            break
        page += 1
    rows = [x for x in out if x['code'] == code and x['asof'] and x['close']]
    if not rows:
        raise Fetch(f'{code} 일봉 없음')
    rows.sort(key=lambda r: r['asof'])
    return rows


def fetch_index(name, start, end, s=None):
    """지수 시세. name 예: '코스피', '코스닥'."""
    s = s or session()
    js = call(s, INDEX, {
        'resultType': 'json', 'numOfRows': 500, 'pageNo': 1,
        'idxNm': name,
        'beginBasDt': start.replace('-', ''), 'endBasDt': end.replace('-', '')})
    items, _ = _rows(js)
    out = []
    for x in items:
        d = str(pick(x, 'basDt', default=''))
        if len(d) != 8:
            continue
        out.append(dict(asof=f'{d[:4]}-{d[4:6]}-{d[6:8]}',
                        close=num(pick(x, 'clpr')), open=num(pick(x, 'mkp')),
                        high=num(pick(x, 'hipr')), low=num(pick(x, 'lopr')),
                        chg_pct=num(pick(x, 'fltRt')), volume=num(pick(x, 'trqu'), 0.0)))
    if not out:
        raise Fetch(f'{name} 지수 없음')
    out.sort(key=lambda r: r['asof'])
    return out


def probe():
    out = []
    try:
        forms = key_forms()
        out.append(('인증키', True,
                    f'{len(key())}자 · 시도할 형태 '
                    + ', '.join(f'{n}({len(v)}자)' for n, v in forms)))
    except Fetch as e:
        return [('인증키', False, str(e))]
    from datetime import date, timedelta
    d = date.today()
    for _ in range(7):                       # 최근 영업일을 찾아 뒤로 훑는다
        try:
            rows = fetch_day(d.isoformat())
            if rows:
                out.append(('전 종목 일봉', True, f'{d} {len(rows)}종목'))
                break
        except Exception as e:               # noqa: BLE001
            out.append(('전 종목 일봉', False, str(e)))
            break
        d -= timedelta(days=1)
    else:
        out.append(('전 종목 일봉', False, '최근 7일 내 데이터 없음'))
    try:
        r = fetch_index('코스피', (date.today() - timedelta(days=14)).isoformat(),
                        date.today().isoformat())
        out.append(('지수', True, f'{len(r)}건'))
    except Exception as e:                   # noqa: BLE001
        out.append(('지수', False, str(e)))
    if KEY_USED.get('form'):
        out.append(('통한 인증키 형태', True,
                    f'{KEY_USED["form"]} ({KEY_USED["n"]}자)'))
    return out
