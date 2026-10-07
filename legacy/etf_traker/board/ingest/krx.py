#!/usr/bin/env python3
"""
KRX 오픈API — 전 종목 일별 시세와 업종.

이게 되면 네이버를 안 써도 된다. 차이가 크다.

  네이버   일봉을 종목당 1회씩 = 하루 2,800회. 공개 API 아님. 개편이면 깨짐
  KRX     하루치 전 종목이 1회. 공식 API. 시·고·저·종·거래대금·시가총액·업종 전부

특히 **업종이 응답에 들어 있다.** docs/APIS.md A2 와 DECISIONS.md D-003 에
"KRX 업종분류 원본을 못 받아 네이버 업종으로 임시 대체" 라고 적어 둔 게 이걸로 풀린다.

## 검증 안 됨

이 환경은 외부 접속이 막혀 있어 실호출을 못 했다. 아래 경로와 필드명은 KRX
오픈API 명세 기준으로 적은 것이다. run.py --check 가 실제로 찔러 보고, 필드명이
다르면 FIELD 표만 고치면 된다.

인증키는 헤더 `AUTH_KEY` 로 보낸다.
"""
import re

from . import creds
from .http import Fetch, get, num, pick, session

BASE = 'https://data-dbg.krx.co.kr/svc/apis'
SOURCE = 'krx'

PATHS = {
    'kospi': '/sto/stk_bydd_trd',        # 유가증권 일별매매정보
    'kosdaq': '/sto/ksq_bydd_trd',       # 코스닥 일별매매정보
    'index': '/idx/krx_dd_trd',          # KRX 시리즈 지수 일별시세
}

# 응답 필드명 후보. 개편되면 여기만 고친다.
FIELD = dict(
    date=('BAS_DD',),
    code=('ISU_SRT_CD', 'ISU_CD'),
    name=('ISU_ABBRV', 'ISU_NM'),
    sector=('IDX_IND_NM', 'SECT_TP_NM', 'IDX_NM'),
    market=('MKT_NM',),
    close=('TDD_CLSPRC',),
    open=('TDD_OPNPRC',),
    high=('TDD_HGPRC',),
    low=('TDD_LWPRC',),
    chg_pct=('FLUC_RT',),
    volume=('ACC_TRDVOL',),
    turnover=('ACC_TRDVAL',),
    mktcap=('MKTCAP',),
    shares=('LIST_SHRS',),
    idx_name=('IDX_NM',),
)


def _s():
    s = session()
    s.headers['AUTH_KEY'] = creds.get('KRX_API_KEY', required=True)
    return s


def new_session():
    """여러 날짜를 연달아 찔러 볼 때 세션을 재사용하려고 연다."""
    return _s()


def _rows(js):
    rows = pick(js, 'OutBlock_1', 'output', 'OutBlock1', default=None)
    if rows is None:
        raise Fetch(f'응답에 결과 블록이 없다: {str(js)[:200]}')
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def _f(r, key):
    return num(pick(r, *FIELD[key]))


def _div(v, by):
    return None if v is None else v / by


def _isu_to_code(raw):
    """단축코드 6자리를 뽑는다.

    예전에는 `re.sub(r'^KR\\w+$', '', code)` 였는데 이건 문자열 **전체**에
    매치해서 결과가 빈 문자열이 됐다. 그러면 fetch_day 가 전 종목을 버리는데,
    독스트링이 "휴장일이면 빈 리스트 — 실패가 아니다" 라고 적혀 있어
    **필드명 오판이 휴장일과 구분되지 않는다.** 제일 나쁜 실패 방식이다.

    ISIN(KR7005930003)이 오면 가운데 6자리가 단축코드다.
    """
    c = str(raw or '').strip().upper()
    if re.fullmatch(r'\d{6}', c):
        return c
    m = re.fullmatch(r'KR\w(\d{6})\d*', c)      # KR7 005930 003
    if m:
        return m.group(1)
    m = re.search(r'\d{6}', c)
    return m.group(0) if m else ''


def _norm(r):
    d = str(pick(r, *FIELD['date'], default='') or '')
    code = _isu_to_code(pick(r, *FIELD['code'], default=''))
    return dict(
        code=code,
        name=(pick(r, *FIELD['name'], default='') or '').strip(),
        market=pick(r, *FIELD['market'], default=''),
        sector=(pick(r, *FIELD['sector'], default='') or '').strip() or None,
        asof=f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else None,
        open=_f(r, 'open'), high=_f(r, 'high'), low=_f(r, 'low'),
        close=_f(r, 'close'), chg_pct=_f(r, 'chg_pct'),
        volume=_f(r, 'volume'),
        # 못 받으면 0 이 아니라 None. 0 은 '거래 없음'이고 None 은 '못 받음'이다.
        # 섞으면 유동성 하한에 전 종목이 걸려 랭킹 표가 통째로 빈다 (D-019).
        turnover=_div(_f(r, 'turnover'), 1e8),        # 원 → 억원
        mktcap=_div(_f(r, 'mktcap'), 1e8),            # 원 → 억원
        shares=_f(r, 'shares'),
        turnover_is_estimate=_f(r, 'turnover') is None, source=SOURCE)


def fetch_day(bas_dt, markets=('kospi', 'kosdaq'), s=None):
    """하루치 전 종목. 휴장일이면 빈 리스트 — 실패가 아니다."""
    s = s or _s()
    out = []
    for m in markets:
        js = get(s, BASE + PATHS[m], params={'basDd': bas_dt.replace('-', '')})
        for r in _rows(js):
            x = _norm(r)
            if x['code'] and x['close']:
                out.append(x)
    return out


def fetch_index(bas_dt, s=None):
    """지수 일별시세. 코스피·코스닥 종가와 시·고·저."""
    s = s or _s()
    js = get(s, BASE + PATHS['index'], params={'basDd': bas_dt.replace('-', '')})
    out = {}
    for r in _rows(js):
        nm = (pick(r, *FIELD['idx_name'], default='') or '').strip()
        d = str(pick(r, *FIELD['date'], default='') or '')
        out[nm] = dict(
            label=nm, asof=f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else None,
            open=_f(r, 'open'), high=_f(r, 'high'), low=_f(r, 'low'),
            close=_f(r, 'close'), chg_pct=_f(r, 'chg_pct'), source=SOURCE)
    if not out:
        raise Fetch('지수 결과가 비었다')
    return out


def sector_map(bas_dt, s=None):
    """종목 → 업종. 1층 분류(CLAUDE.md 5장)의 원본."""
    rows = fetch_day(bas_dt, s=s)
    m = {x['code']: x['sector'] for x in rows if x.get('sector')}
    if not m:
        raise Fetch('업종 필드가 비었다 — FIELD["sector"] 후보 확인 필요')
    return dict(taxonomy='krx', source=SOURCE, by_code=m)


def probe():
    from datetime import date, timedelta
    if not creds.has('KRX_API_KEY'):
        return [('KRX 인증키', False, 'KRX_API_KEY 없음')]
    out = [('KRX 인증키', True, creds.mask(creds.get('KRX_API_KEY')))]
    d = date.today()
    for _ in range(8):                     # 최근 영업일을 뒤로 훑는다
        try:
            rows = fetch_day(d.isoformat())
        except Exception as e:             # noqa: BLE001
            out.append(('전 종목 일별시세', False, str(e)[:160]))
            return out
        if rows:
            n_sec = sum(1 for x in rows if x.get('sector'))
            out.append(('전 종목 일별시세', True,
                        f'{d} {len(rows):,}종목 · 업종 있는 행 {n_sec:,}'))
            break
        d -= timedelta(days=1)
    else:
        out.append(('전 종목 일별시세', False, '최근 8일 내 데이터 없음'))
    try:
        idx = fetch_index(d.isoformat())
        out.append(('지수', True, f'{len(idx)}개 · ' + ', '.join(list(idx)[:4])))
    except Exception as e:                 # noqa: BLE001
        out.append(('지수', False, str(e)[:160]))
    return out
