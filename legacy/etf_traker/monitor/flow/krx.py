#!/usr/bin/env python3
"""
KRX 정보데이터시스템 — 개별종목 투자자별 거래실적.

KIS 는 개인·외국인·기관 **3구분**만 준다. 예시 워크북이 요구하는 투신·사모·
연기금 같은 **기관 내부 7구분**은 여기서만 나온다.

    금융투자 · 보험 · 투신 · 사모 · 은행 · 기타금융 · 연기금 등
    + 기타법인 · 개인 · 외국인 · 기타외국인 (= 12구분)

--------------------------------------------------------------------------
로그인은 필요할 때만 한다
--------------------------------------------------------------------------
board/docs/APIS.md B1 에 "KRX 정보데이터시스템은 로그인 필수화. 불가" 라고 적혀
있지만, 그건 **화면의 엑셀 다운로드** 쪽 이야기다. 화면이 표를 그릴 때 쓰는
`getJsonData.cmd` 는 대개 인증 없이 열린다.

그래서 **익명으로 먼저 찌르고, 그게 막힐 때만** KRX_ID/KRX_PW 로 로그인해
재시도한다. 비밀정보를 덜 쓰는 쪽이 안전하고, 계정은 폴백으로 살아 있다.
어느 경로로 받았는지는 `AUTH_USED` 에 남겨 --check 가 보고한다.

--------------------------------------------------------------------------
실호출로 검증하지 못했다
--------------------------------------------------------------------------
이 환경은 외부 접속이 막혀 있다. 아래 `BLD` 의 화면 코드와 `PARAMS` 의 필드명은
KRX 화면 규약 기준으로 적은 것이고, 개편됐거나 내가 잘못 안 게 있으면 그대로
실패한다. board/ingest/kis.py 와 같은 자세로 두 가지를 해 뒀다.

  1. 화면 코드·파라미터·응답 키를 전부 표로 뺐다. 틀리면 이 표만 고치면 된다.
  2. `probe()` 가 실제로 찔러 보고 무엇이 왔는지 찍는다.

응답 필드명이 다르면 `FIELD` 의 후보 목록에 한 줄 더하는 것으로 끝난다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from board.ingest.http import Fetch, num, pick, session, why  # noqa: E402

# **https 여야 한다.** http 로 부르면 KRX 가 301 로 https 에 넘기는데, requests 는
# 301 을 따라갈 때 POST 를 GET 으로 바꾼다(RFC 의 관행). 그러면 본문 파라미터가
# 통째로 사라진 GET 이 되어 JSON 대신 화면 HTML 이 오고, 파서는 그제서야
# "Expecting value: line 1 column 1" 로 죽는다 — 2026-09-15 첫 실호출이 이거였다.
BASE = 'https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd'
LOGIN = 'https://data.krx.co.kr/comm/member/login/login.cmd'
REFERER = 'https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd'
HOME = 'https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd'
SOURCE = 'krx-data'

# 화면 번호. 로더는 menuId 로 어느 화면인지 알고, 그 화면을 연 흔적이 세션에
# 남아야 getJsonData 가 응답한다 — 그렇지 않으면 HTTP 400 에 'LOGOUT'.
# 개별종목 투자자별 거래실적(12009) 의 menuId 다.
MENU = 'MDC0201020403'

# 화면 코드. 개편되면 여기만 고친다.
BLD = {
    # 종목 검색(finder). 단축코드 -> 표준코드(KR7...) 를 얻는다.
    'finder': 'dbms/comm/finder/finder_stkisu',
    # 개별종목 투자자별 거래실적 — 일별추이 (화면 12009)
    'daily': 'dbms/MDC/STAT/standard/MDCSTAT02303',
    # 개별종목 투자자별 거래실적 — 기간합계 (같은 화면의 합계 탭)
    'total': 'dbms/MDC/STAT/standard/MDCSTAT02301',
}

# 조회 단위. KRX 는 금액과 수량을 따로 준다 — 한 번에 안 준다.
#   '1' 거래량(주) · '2' 거래대금(원)
TRD = {'qty': '1', 'amt': '2'}

# 어느 경로로 받았는지. probe() 가 읽어서 보고한다.
AUTH_USED = {}

# 응답 필드명 후보. KRX 는 표시용 문자열(쉼표 포함)로 주므로 num() 이 벗긴다.
FIELD = dict(
    date=('TRD_DD',),
    # 투자자 구분별 순매수. KRX 는 매도/매수/순매수를 각각 준다.
    netbuy=('NETBID_TRDVAL', 'NETBID_TRDVOL', 'NETBID'),
    sell=('ASK_TRDVAL', 'ASK_TRDVOL'),
    buy=('BID_TRDVAL', 'BID_TRDVOL'),
    investor=('INVST_TP_NM', 'INVST_TP'),
)

# 응답의 투자자 이름 → 우리가 쓰는 이름. KRX 표기가 화면마다 조금씩 다르다.
INVESTORS = {
    '금융투자': '금융투자', '보험': '보험', '투신': '투신', '투신(뮤추얼)': '투신',
    '사모': '사모', '사모펀드': '사모', '은행': '은행', '기타금융': '기타금융',
    '기타금융기관': '기타금융', '연기금': '연기금 등', '연기금 등': '연기금 등',
    '연기금등': '연기금 등', '기관합계': '기관합계', '기타법인': '기타법인',
    '개인': '개인', '외국인': '외국인', '외국인투자자': '외국인',
    '기타외국인': '기타외국인', '전체': '전체',
}

# 기관합계를 이루는 7구분. 예시 워크북의 '기관 구분' 표가 이 순서다.
INSTITUTIONS = ('금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금 등')

# 화면이 쓰는 전체 구분 순서.
ALL = INSTITUTIONS + ('기관합계', '기타법인', '개인', '외국인', '기타외국인', '전체')


def creds():
    """(아이디, 비밀번호). 둘 다 없으면 (None, None) — 익명으로만 간다."""
    return (os.environ.get('KRX_ID') or None,
            os.environ.get('KRX_PW') or None)


def _s(logged_in=False):
    s = session(referer=REFERER)
    s.headers.update({'X-Requested-With': 'XMLHttpRequest'})
    # **화면을 먼저 한 번 연다.** getJsonData 는 화면이 심어 준 세션 쿠키를
    # 요구한다. 없으면 HTTP 400 에 본문 'LOGOUT' 만 돌려준다 — 로그인하라는
    # 말처럼 보이지만 계정 문제가 아니라 쿠키가 없는 것이다(2026-09-15 실호출).
    # 브라우저가 화면을 열고 나서 표를 그리는 순서를 그대로 따른다.
    try:
        s.get(HOME, timeout=20)
        s.get(REFERER, params={'menuId': MENU}, timeout=20)
    except Exception as e:  # noqa: BLE001
        raise Fetch(f'KRX 화면을 열지 못했다: {type(e).__name__}: {e}') from None
    if logged_in:
        uid, pw = creds()
        if not (uid and pw):
            raise Fetch('KRX_ID/KRX_PW 가 없다. 익명 경로가 막히면 계정을 넣어야 한다.')
        # 로그인은 세션 쿠키만 얻으면 된다. 응답 본문은 쓰지 않는다.
        r = s.post(LOGIN, data={'userId': uid, 'pwd': pw}, timeout=20)
        if r.status_code != 200:
            raise Fetch(f'KRX 로그인 거부: HTTP {r.status_code}')
    return s


def call(params, s=None, allow_login=True):
    """getJsonData 한 번. 익명으로 먼저, 막히면 로그인해 한 번 더.

    '막혔다' 의 기준은 HTTP 오류이거나 결과 배열이 비어 있는 것이다. KRX 는
    권한이 없을 때 200 에 빈 배열을 주기도 한다 — 그걸 '데이터 없음' 으로 읽으면
    로그인 경로를 영영 안 타게 된다.
    """
    last = None
    for logged in (False, True):
        if logged and not allow_login:
            break
        if logged and not all(creds()):
            break
        try:
            ss = s if (s is not None and not logged) else _s(logged_in=logged)
            js = _post(ss, params)
        except Exception as e:  # noqa: BLE001 — 사유를 문자열로 보존
            last = f'{type(e).__name__}: {e}'
            continue
        rows = _rows(js)
        if rows:
            AUTH_USED['mode'] = '로그인' if logged else '익명'
            return rows
        last = '결과가 비었다'
    raise Fetch(f'KRX 조회 실패 ({last})')


def _post(s, params):
    """getJsonData POST 하나. 응답이 JSON 이 아니면 **본문의 사유를 들고** 올린다.

    `.json()` 만 부르면 JSONDecodeError 하나가 올라오고 서버가 무엇을 말했는지는
    사라진다. 로그인 화면인지, WAF 차단인지, 화면코드가 없다는 것인지 구분이
    안 되면 고칠 데를 못 찾는다 — board/CLAUDE.md 2장 6번.

    리다이렉트는 따라가지 않는다. POST 가 GET 으로 바뀌어 엉뚱한 HTML 을 받고
    그걸 '응답' 으로 읽느니, 어디로 넘기려 했는지를 사유에 적는 쪽이다.
    """
    r = s.post(BASE, data=params, timeout=30, allow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308):
        raise Fetch(f'HTTP {r.status_code} 리다이렉트 → {r.headers.get("Location")}')
    if r.status_code != 200:
        raise Fetch(f'HTTP {r.status_code} · {why(r) or "본문 없음"}')
    try:
        return r.json()
    except ValueError:
        ct = r.headers.get('Content-Type', '?')
        raise Fetch(f'200 인데 JSON 이 아니다 (Content-Type: {ct}) — '
                    f'{why(r) or "본문 비어 있음"}') from None


def _rows(js):
    if not isinstance(js, dict):
        return []
    for key in ('output', 'OutBlock_1', 'block1', 'list'):
        v = js.get(key)
        if isinstance(v, list) and v:
            return v
    return []


def _investor(row):
    raw = str(pick(row, *FIELD['investor'], default='') or '').strip()
    return INVESTORS.get(raw, raw)


def fetch_daily(code, start, end, unit='amt', s=None):
    """종목 하나의 일별 투자자 순매수. unit 은 'amt'(원) 또는 'qty'(주).

    돌려주는 것: {'YYYYMMDD': {'투신': 값, ...}}
    KRX 는 한 행이 하루이고 투자자별 값이 열로 오는 화면과, 한 행이 투자자인
    화면이 둘 다 있다. 둘 다 받아들인다 — 어느 쪽인지 응답을 보고 가른다.
    """
    rows = call({
        'bld': BLD['daily'], 'locale': 'ko_KR',
        'isuCd': _isu(code, s=s), 'tboxisuCd_finder_stkisu0_0': code,
        'strtDd': start.replace('-', ''), 'endDd': end.replace('-', ''),
        'askBid': '3',                 # 3 = 순매수
        'trdVolVal': TRD[unit],
        'money': '1', 'csvxls_isNo': 'false',
    }, s=s)

    out = {}
    for r in rows:
        d = str(pick(r, *FIELD['date'], default='') or '').replace('/', '').replace('-', '')
        if len(d) != 8:
            continue
        day = out.setdefault(d, {})
        who = _investor(r)
        if who:
            # 행이 투자자인 모양
            day[who] = num(pick(r, *FIELD['netbuy']))
            continue
        # 행이 하루인 모양 — 투자자 이름이 열 이름으로 온다
        for raw, name in INVESTORS.items():
            if raw in r:
                day[name] = num(r.get(raw))
    if not out:
        raise Fetch(f'{code} 일별 투자자 실적이 비었다')
    return out


def fetch_total(code, start, end, s=None):
    """기간합계. 투자자별 매도·매수·순매수를 주(qty)와 원(amt)으로.

    돌려주는 것: {'투신': {'sell_qty':…, 'buy_qty':…, 'net_qty':…,
                          'sell_amt':…, 'buy_amt':…, 'net_amt':…}}
    예시 워크북의 '기간합계 원자료' 블록이 이것이고, 일별 합과 맞는지
    대조하는 검산의 기준이 된다.
    """
    out = {}
    for unit, suffix in (('qty', 'qty'), ('amt', 'amt')):
        rows = call({
            'bld': BLD['total'], 'locale': 'ko_KR',
            'isuCd': _isu(code, s=s), 'tboxisuCd_finder_stkisu0_0': code,
            'strtDd': start.replace('-', ''), 'endDd': end.replace('-', ''),
            'askBid': '3', 'trdVolVal': TRD[unit],
            'money': '1', 'csvxls_isNo': 'false',
        }, s=s)
        for r in rows:
            who = _investor(r)
            if not who:
                continue
            rec = out.setdefault(who, {})
            rec[f'sell_{suffix}'] = num(pick(r, *FIELD['sell']))
            rec[f'buy_{suffix}'] = num(pick(r, *FIELD['buy']))
            rec[f'net_{suffix}'] = num(pick(r, *FIELD['netbuy']))
    if not out:
        raise Fetch(f'{code} 기간합계가 비었다')
    return out


# 단축코드 -> 표준코드. 같은 종목을 네 번(일별 금액·수량, 합계 금액·수량)
# 부르므로 한 번 찾아 두고 쓴다.
_ISU = {}

# finder 가 왜 실패했는지. probe() 가 읽어서 보고한다 — 조용히 단축코드로
# 물러나면 나중에 '왜 빈 결과냐' 를 여기서 찾지 못한다.
FINDER_NOTE = {}


def find_isu(code, s=None):
    """단축코드(005930) -> KRX 표준코드(KR7005930003).

    화면의 종목 검색창이 쓰는 finder 다. 투자자별 거래실적 화면은 `isuCd` 에
    표준코드를 기대한다 — 단축코드로도 받아 주는 화면이 있어 처음엔 그대로
    넣었지만, 받아 주지 않으면 **빈 배열**이 와서 '데이터 없음' 처럼 보인다.
    그 구분이 안 되는 실패가 제일 고약하므로 제대로 찾아서 넣는다.
    """
    code = str(code).strip()
    if code in _ISU:
        return _ISU[code]
    rows = call({'bld': BLD['finder'], 'locale': 'ko_KR', 'mktsel': 'ALL',
                 'typeNo': '0', 'searchText': code}, s=s)
    for r in rows:
        short = str(pick(r, 'short_code', 'shortCode', default='') or '').strip()
        full = str(pick(r, 'full_code', 'fullCode', 'isu_cd', default='') or '').strip()
        if full and (short.endswith(code) or code in short or not short):
            _ISU[code] = full
            return full
    raise Fetch(f'{code} 표준코드를 찾지 못했다 ({len(rows)}건 조회)')


def _isu(code, s=None):
    """표준코드. 못 찾으면 단축코드로 시도하되 사유를 남긴다.

    여기서 예외를 올려 버리면 finder 만 개편돼도 리포트 전체가 멎는다.
    단축코드를 받아 주는 화면이면 그대로 돌아가므로 물러서되, 무엇이
    일어났는지는 FINDER_NOTE 에 적어 --check 가 찍는다.
    """
    try:
        return find_isu(code, s=s)
    except Exception as e:  # noqa: BLE001
        FINDER_NOTE[code] = str(e)[:160]
        return code


def handshake():
    """화면을 여는 단계가 실제로 무엇을 받는지 그대로 적는다.

    'LOGOUT' 만 보고는 쿠키를 못 받은 것인지, 받았는데 화면 흔적이 없는 것인지,
    아예 차단당한 것인지 갈라지지 않는다. 한 번 돌려 사실을 모은다 —
    자격증명은 여기 오지 않는다(아이디로 로그인하는 단계가 아니다).
    """
    rows, s = [], session(referer=REFERER)
    s.headers.update({'X-Requested-With': 'XMLHttpRequest'})
    for label, url, params in (('메인', HOME, None),
                               ('로더', REFERER, {'menuId': MENU})):
        try:
            r = s.get(url, params=params, timeout=20)
            rows.append((f'GET {label}', r.status_code == 200,
                         f'HTTP {r.status_code} · {r.headers.get("Content-Type", "?")} · '
                         f'{len(r.content)}바이트 · 쿠키 [{", ".join(sorted(s.cookies.keys())) or "없음"}]'))
        except Exception as e:  # noqa: BLE001
            rows.append((f'GET {label}', False, f'{type(e).__name__}: {e}'))
    try:
        r = s.post(BASE, data={'bld': BLD['finder'], 'locale': 'ko_KR',
                               'mktsel': 'ALL', 'typeNo': '0',
                               'searchText': '005930'},
                   timeout=30, allow_redirects=False)
        rows.append(('POST finder', r.status_code == 200,
                     f'HTTP {r.status_code} · {r.headers.get("Content-Type", "?")} · '
                     f'{(r.text or "")[:200]!r}'))
    except Exception as e:  # noqa: BLE001
        rows.append(('POST finder', False, f'{type(e).__name__}: {e}'))
    return rows


# 화면코드를 좁히기 위한 후보들. finder 는 익명으로 열리는데 MDCSTAT 만
# 'LOGOUT' 이 오므로, 막힌 것이 (가) STAT 화면 전체인지 (나) 우리 파라미터가
# 모자란 것인지 갈라야 한다. 한 번 돌려 사실을 모은다.
SURVEY = (
    ('개별 일별 (지금 쓰는 것)', BLD['daily'], {}),
    ('개별 일별 + detailView', BLD['daily'], {'detailView': '1', 'inqTpCd': '2'}),
    ('개별 기간합계', BLD['total'], {}),
    ('전체시장 일별', 'dbms/MDC/STAT/standard/MDCSTAT02203', {}),
    ('개별종목 시세', 'dbms/MDC/STAT/standard/MDCSTAT01602', {}),
)


def survey(code='KR7005930003', start='20260901', end='20260914'):
    """후보 화면을 하나씩 찔러 상태와 본문 앞부분을 그대로 적는다."""
    rows, s = [], None
    try:
        s = _s()
    except Exception as e:  # noqa: BLE001
        return [('화면 열기', False, f'{type(e).__name__}: {e}')]
    for label, bld, extra in SURVEY:
        params = {'bld': bld, 'locale': 'ko_KR', 'isuCd': code, 'isuCd2': code,
                  'strtDd': start, 'endDd': end, 'askBid': '3', 'trdVolVal': '2',
                  'share': '1', 'money': '1', 'csvxls_isNo': 'false'}
        params.update(extra)
        try:
            r = s.post(BASE, data=params, timeout=30, allow_redirects=False)
            body = ' '.join((r.text or '').split())[:140]
            rows.append((f'화면 {label}', r.status_code == 200 and 'LOGOUT' not in body,
                         f'HTTP {r.status_code} · {body!r}'))
        except Exception as e:  # noqa: BLE001
            rows.append((f'화면 {label}', False, f'{type(e).__name__}: {e}'))
    return rows



def menu_candidates(keyword='투자자별', limit=8):
    """KRX 메뉴 트리에서 화면 번호를 **직접 찾아온다**.

    모든 STAT 화면이 400 LOGOUT 인데 finder(dbms/comm/…)는 열린다. 세션은
    멀쩡하다는 뜻이고, 남는 설명은 화면이 세션에 등록되지 않았다는 것이다 —
    로더를 열 때 쓰는 menuId 가 내 짐작이었다. 짐작을 또 얹지 않고 메인
    페이지가 들고 있는 메뉴 목록에서 이름으로 찾는다.
    """
    import re
    s = session(referer=REFERER)
    html = s.get(HOME, timeout=25).text or ''
    out, seen = [], set()
    for m in re.finditer(r'MDC\d{6,}', html):
        mid = m.group(0)
        near = html[max(0, m.start() - 160):m.start() + 160]
        if keyword not in near or mid in seen:
            continue
        seen.add(mid)
        out.append((mid, ' '.join(near.split())[:120]))
        if len(out) >= limit:
            break
    return out


def menu_probe(bld=None, code='KR7005930003'):
    """후보 menuId 로 화면을 열고 나서 찔러 본다.

    2026-09-15 에 돌려 봤고 답은 '아니다' 였다 — 메뉴에서 긁어 온 진짜 화면
    번호(MDC0201020301·0302·0303·MDC0201030106) 넷 다 400 LOGOUT 이다.
    menuId 는 원인이 아니다. 기록으로 남기고 --check 기본 경로에서는 뺀다.
    """
    bld = bld or BLD['daily']
    rows = []
    cands = menu_candidates()
    rows.append(('메뉴 후보', bool(cands),
                 '; '.join(f'{mid} {snip[:40]}' for mid, snip in cands) or '못 찾음'))
    for mid, _ in cands[:4]:
        try:
            s = session(referer=REFERER)
            s.headers.update({'X-Requested-With': 'XMLHttpRequest'})
            s.get(HOME, timeout=20)
            s.get(REFERER, params={'menuId': mid}, timeout=20)
            r = s.post(BASE, data={
                'bld': bld, 'locale': 'ko_KR', 'isuCd': code, 'isuCd2': code,
                'strtDd': '20260901', 'endDd': '20260914', 'askBid': '3',
                'trdVolVal': '2', 'share': '1', 'money': '1',
                'csvxls_isNo': 'false'}, timeout=30, allow_redirects=False)
            body = ' '.join((r.text or '').split())[:120]
            rows.append((f'menuId {mid}', r.status_code == 200 and 'LOGOUT' not in body,
                         f'HTTP {r.status_code} · {body!r}'))
        except Exception as e:  # noqa: BLE001
            rows.append((f'menuId {mid}', False, f'{type(e).__name__}: {e}'))
    return rows


def bld_probe():
    """화면이 실제로 쓰는 bld 문자열을 **로더 화면에서 읽어 온다**.

    여기까지 알아낸 것을 모으면 이렇다. 전송은 멀쩡하고(finder 는 JSON 을
    준다), 세션도 산다(JSESSIONID 를 받는다), menuId 를 진짜 값으로 바꿔도
    STAT 화면은 전부 400 'LOGOUT' 이다. 그러면 남는 설명은 둘 중 하나다 —
    통계 화면이 로그인을 요구하거나, 'LOGOUT' 이 그냥 **모르는 화면**에
    대한 대답이거나.

    그래서 두 가지를 한 번에 본다.

      1. 있을 리 없는 bld 를 찔러 본다. 이것도 'LOGOUT' 이면 그 말은
         '로그인하라' 가 아니라 '그런 화면 없다' 는 뜻이다.
      2. 로더가 화면을 그릴 때 내려주는 HTML·JS 안에 그 화면의 bld 가
         적혀 있다. 긁어서 적는다 — 짐작한 MDCSTAT 번호와 대조할 수 있다.
    """
    import re
    rows = []
    try:
        s = _s()
    except Exception as e:  # noqa: BLE001
        return [('bld 조사', False, f'{type(e).__name__}: {e}')]

    try:
        r = s.post(BASE, data={'bld': 'dbms/MDC/STAT/standard/MDCSTAT99999',
                               'locale': 'ko_KR'},
                   timeout=30, allow_redirects=False)
        body = ' '.join((r.text or '').split())[:120]
        rows.append(('없는 bld (대조군)', True, f'HTTP {r.status_code} · {body!r}'))
    except Exception as e:  # noqa: BLE001
        rows.append(('없는 bld (대조군)', False, f'{type(e).__name__}: {e}'))

    bld_re = re.compile(r'dbms/[A-Za-z0-9_/]+')
    for mid, _ in menu_candidates(limit=6):
        try:
            html = s.get(REFERER, params={'menuId': mid}, timeout=20).text or ''
            found = sorted(set(bld_re.findall(html)))
            rows.append((f'로더 {mid}', bool(found),
                         f'{len(html)}자 · bld {found[:6]}'))
        except Exception as e:  # noqa: BLE001
            rows.append((f'로더 {mid}', False, f'{type(e).__name__}: {e}'))
    return rows


def login_form_probe():
    """로그인 화면을 찾아 **폼이 요구하는 필드 이름**을 적는다.

    menuId 를 바꿔도 STAT 화면은 전부 LOGOUT 인데 finder(dbms/comm/…)는 열린다.
    세션·메뉴 문제가 아니라 통계 화면이 정말로 로그인을 요구하는 쪽이다
    (board/docs/APIS.md B1 이 그렇게 적어 뒀고, 내가 '엑셀 내려받기 얘기' 로
    읽은 게 틀렸다). 그런데 우리 로그인 POST 는 200 에 2,952바이트 HTML 만
    돌려주고 쿠키도 그대로다 — 로그인 폼을 되받은 모양이다.

    그래서 주소와 필드 이름을 짐작하지 않고 화면에서 읽어 온다. 값은 찍지
    않는다 — 이름만 본다.
    """
    import re
    rows = []
    s = session(referer=REFERER)
    try:
        html = s.get(HOME, timeout=25).text or ''
    except Exception as e:  # noqa: BLE001
        return [('로그인 화면', False, f'{type(e).__name__}: {e}')]

    link = re.compile(r'''["'](/[^"'\s]*(?:ogin|ignIn|member)[^"'\s]*)["']''')
    urls, seen = [], set()
    for m in link.finditer(html):
        u = 'https://data.krx.co.kr' + m.group(1)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    rows.append(('로그인 링크', bool(urls), '; '.join(urls[:6]) or '못 찾음'))

    form_re = re.compile(r'''<form[^>]*action=["']([^"']+)["']''', re.I)
    name_re = re.compile(r'''<input[^>]*name=["']([^"']+)["']''', re.I)
    for u in (urls[:3] or [LOGIN]):
        try:
            r = s.get(u, timeout=20)
            body = r.text or ''
            rows.append((f'폼 {u[-40:]}', r.status_code == 200,
                         f'HTTP {r.status_code} · {len(r.content)}바이트 · '
                         f'action {form_re.findall(body)[:3]} · '
                         f'필드 {sorted(set(name_re.findall(body)))[:12]}'))
        except Exception as e:  # noqa: BLE001
            rows.append((f'폼 {u[-40:]}', False, f'{type(e).__name__}: {e}'))
    return rows


def login_probe():
    """로그인이 실제로 되는지. 본문은 상태와 길이만 — 계정 정보를 찍지 않는다."""
    uid, pw = creds()
    if not (uid and pw):
        return [('로그인', False, 'KRX_ID/KRX_PW 가 없다')]
    s = session(referer=REFERER)
    try:
        s.get(HOME, timeout=20)
        r = s.post(LOGIN, data={'userId': uid, 'pwd': pw}, timeout=20,
                   allow_redirects=False)
    except Exception as e:  # noqa: BLE001
        return [('로그인', False, f'{type(e).__name__}: {e}')]
    ok = r.status_code in (200, 302)
    return [('로그인', ok, f'HTTP {r.status_code} · {r.headers.get("Content-Type", "?")} · '
                           f'{len(r.content)}바이트 · 쿠키 [{", ".join(sorted(s.cookies.keys())) or "없음"}]')]


def probe(code='005930', start='2026-09-01', end='2026-09-14'):
    """실제로 찔러 보고 무엇이 왔는지 돌려준다. run --check 가 부른다."""
    out = []
    uid, _ = creds()
    out.append(('자격증명', bool(uid), 'KRX_ID 있음' if uid else 'KRX_ID 없음 — 익명만 시도'))
    out.extend(handshake())
    out.extend(bld_probe())
    out.extend(login_probe())
    try:
        out.append(('표준코드', True, f'{code} → {find_isu(code)} · '
                                     f'{AUTH_USED.get("mode", "?")} 경로'))
    except Exception as e:  # noqa: BLE001
        out.append(('표준코드', False, str(e)[:240]))
    for label, fn in (('일별', lambda: fetch_daily(code, start, end)),
                      ('기간합계', lambda: fetch_total(code, start, end))):
        try:
            got = fn()
            mode = AUTH_USED.get('mode', '?')
            out.append((label, True, f'{len(got)}건 · {mode} 경로'))
        except Exception as e:  # noqa: BLE001
            out.append((label, False, str(e)[:240]))
    for c, note in FINDER_NOTE.items():
        out.append((f'finder({c})', False, note))
    return out
