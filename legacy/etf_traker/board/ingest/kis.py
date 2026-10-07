#!/usr/bin/env python3
"""
한국투자증권 KIS Developers — 투자자별 수급.

레퍼런스 코멘트의 첫 단락과 종목별 수급 한 줄이 전부 여기서 나온다.

  "기타법인 코스피에서만 1조 넘게 순매수 + 기관 7,604억 / 개인 2조 순매도"
  "두산에너빌리티는 기관 960억 순매수로 이 날 기관 매수 1위"

KRX 정보데이터시스템이 로그인을 요구하게 된 뒤로 인증 없이 받을 수 없던 값이라
docs/APIS.md B1 에 최대 구멍으로 적어 뒀던 것이고, 앱키가 생겨 열렸다.

## 엔드포인트가 확실하지 않다는 점

이 환경은 외부 접속이 막혀 있어 **실호출로 한 번도 검증하지 못했다.** 아래
ENDPOINTS 표의 path 와 tr_id 는 KIS 문서 기준으로 적은 것이고, 개편됐거나 내가
잘못 안 게 있으면 그대로 실패한다. 그래서 두 가지를 해 뒀다.

  1. path·tr_id·응답 키를 전부 표로 뺐다. 틀린 게 있으면 이 표만 고치면 된다.
  2. run.py --check 가 토큰 발급과 각 엔드포인트를 실제로 찔러 보고 결과를 찍는다.

## 토큰 (KBJ P2 — 설계 §3.8 K3)

접근토큰은 발급 후 24시간 유효하고 **1분에 1회로 발급이 제한된다.** 그래서 발급은
KBJ auth 서비스 한 곳만 하고(ADR 0004), 여기서는 읽기만 한다. 호출은 논리 URL
`kis:` 를 KBJ 브리지(`kbj.data.legacy_bridge`)가 받아 토큰·앱키·시크릿을 넣고 앱키당
레이트리미터(초당 4건)를 지킨다. 옛 토큰 파일(state/.kis_token.json)과 발급 코드는 지웠다.

## 주의

이 앱키는 주문 API 에도 쓰인다. 이 파일은 **조회 계열만** 호출한다.
주문·잔고 엔드포인트를 여기에 추가하지 마라.
"""
import json
import re
import time

from kbj.data import legacy_bridge
from kbj.data.legacy_bridge import session

from .http import Fetch, get, num, pick

SOURCE = 'kis'

# path, tr_id, 그리고 응답에서 결과 배열을 꺼낼 키.
# 개편되면 여기만 고친다. 로직은 건드릴 필요 없다.
ENDPOINTS = {
    # 종목별 투자자 매매동향 (일별). 개인·외국인·기관 순매수 수량과 금액.
    'stock_investor': dict(
        path='/uapi/domestic-stock/v1/quotations/inquire-investor',
        tr_id='FHKST01010900', out='output',
        params=lambda code: {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code}),
    # 기관·외국인 매매종목 가집계. '기관 매수 1위' 가 여기서 나온다.
    'top_flows': dict(
        path='/uapi/domestic-stock/v1/quotations/foreign-institution-total',
        tr_id='FHPTJ04400000', out='output',
        params=lambda market='0000': {
            'FID_COND_MRKT_DIV_CODE': 'V', 'FID_COND_SCR_DIV_CODE': '16449',
            'FID_INPUT_ISCD': market, 'FID_DIV_CLS_CODE': '0',
            'FID_RANK_SORT_CLS_CODE': '0', 'FID_ETC_CLS_CODE': '0'}),
    # 시장별 투자자 매매동향 (일별)
    'market_investor': dict(
        path='/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market',
        tr_id='FHPTJ04040000', out='output1',
        params=lambda market='0001': {
            'FID_COND_MRKT_DIV_CODE': 'U', 'FID_INPUT_ISCD': market,
            'FID_INPUT_DATE_1': '', 'FID_INPUT_ISCD_1': ''}),
}

# 응답 필드명 후보. KIS 는 축약형이라 이름이 헷갈린다.
FIELD = dict(
    date=('stck_bsop_date', 'bsop_date', 'stck_clpr_date'),
    close=('stck_clpr', 'stck_prpr'),
    person=('prsn_ntby_qty', 'prsn_ntby_tr_pbmn'),
    foreign=('frgn_ntby_qty', 'frgn_ntby_tr_pbmn'),
    institution=('orgn_ntby_qty', 'orgn_ntby_tr_pbmn'),
    person_amt=('prsn_ntby_tr_pbmn',),
    foreign_amt=('frgn_ntby_tr_pbmn',),
    institution_amt=('orgn_ntby_tr_pbmn',),
    name=('hts_kor_isnm', 'prdt_name'),
    code=('mksc_shrn_iscd', 'stck_shrn_iscd', 'pdno'),
)


def base():
    """논리 URL — 실전·모의 주소는 KBJ 설정(KBJ_KIS_ENV)을 브리지가 고른다."""
    return 'kis:'


# ─────────────────────────── 토큰 ───────────────────────────
def token(force=False):
    """접근토큰 — KBJ auth 가 Redis 에 둔 값을 읽기만 한다(발급하지 않는다, ADR 0004).

    `force` 는 옛 호출 모양을 위해 받기만 한다 — 다시 받기(갱신)는 auth 몫이다.
    토큰이 없으면 Fetch(사유: auth 대기).
    """
    tok = legacy_bridge.access_token_or_none()
    if not tok:
        raise Fetch('KIS 토큰 없음 — KBJ auth 서비스(Redis kis:token) 대기')
    return tok


def _headers(tr_id):
    """토큰·앱키·시크릿은 넣지 않는다 — 브리지가 KBJ 값으로 넣는다."""
    return {
        'tr_id': tr_id, 'custtype': 'P', 'content-type': 'application/json',
        legacy_bridge.PRIORITY_HEADER: 'P3',
    }


# KIS 는 빠진 필수 파라미터를 이름까지 찍어서 알려준다.
#   rt_cd=2  ERROR INPUT FIELD NOT FOUND [FID_INPUT_ISCD_2]
# 문서만 보고 맞히려면 한 번에 하나씩 실행해 봐야 해서 왕복이 길다. 이름을 읽고
# 빈 값으로 채워 다시 부른 뒤, 무엇을 채웠는지 남긴다 — 그 목록을 보고 ENDPOINTS
# 표에 확정해 넣으면 된다. 추측으로 값을 지어내지는 않는다(빈 문자열만).
MISSING_FIELD = re.compile(r'INPUT FIELD NOT FOUND\s*\[([A-Z0-9_]+)\]')
MAX_FILL = 6

# 실행 중 채워 넣은 필드. --check 가 끝에 보고한다.
FILLED = {}


def call(name, *args, s=None, retries=2):
    """ENDPOINTS 의 한 항목을 호출하고 결과 배열을 돌려준다.

    `retries` 는 http.get 의 재시도 수다. 접속 자체가 안 되는 시간대(23시 전후,
    D-083)에는 한 번에 TIMEOUT 25초를 통째로 기다리므로, 종목을 여러 개 도는
    호출자는 이 값을 줄이고 첫 접속 실패에서 멈춘다(`connect_failed`).
    """
    spec = ENDPOINTS[name]
    # 호출자가 공용 세션을 넘길 수 있다. 세션 헤더에 앱키를 영구히 붙이면 그
    # 세션의 이후 모든 요청(다른 호스트 포함)에 자격증명이 실린다. 매번 새로 만든다.
    s = session()
    s.headers.update(_headers(spec['tr_id']))
    params = dict(spec['params'](*args))
    added = []
    for _ in range(MAX_FILL + 1):
        js = get(s, base() + spec['path'], params=params, retries=retries)
        rt = str(js.get('rt_cd', '0'))
        if rt in ('0', ''):
            break
        msg = js.get('msg1', '') or ''
        m = MISSING_FIELD.search(msg)
        if not m or m.group(1) in params:
            raise Fetch(f'{name} rt_cd={rt} {msg}'
                        + (f' · 채운 필드 {added}' if added else ''))
        params[m.group(1)] = ''
        added.append(m.group(1))
    else:
        raise Fetch(f'{name} 필수 파라미터를 {MAX_FILL}개 채웠는데도 안 된다 — '
                    f'{added} · 마지막 응답 {js.get("msg1", "")}')
    if added:
        FILLED.setdefault(name, []).extend(a for a in added
                                           if a not in FILLED.get(name, []))
    rows = js.get(spec['out'])
    if rows is None:
        rows = pick(js, 'output', 'output1', 'output2', default=[])
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        raise Fetch(f'{name} 결과가 비었다 (필드 {spec["out"]})')
    return rows


def _f(row, key):
    return num(pick(row, *FIELD[key]))


# ─────────────────────────── 공개 함수 ───────────────────────────
def market_flows(market='0001', s=None):
    """시장 전체 투자자별 순매수. market: 0001 코스피 / 1001 코스닥.

    단위는 KIS 가 백만원으로 주므로 억원으로 접는다. 단위가 다르면 --check 에서
    자릿수가 어긋나 보이므로 바로 알아챌 수 있다.
    """
    rows = call('market_investor', market, s=s)
    out = {}
    for r in rows:
        d = str(pick(r, *FIELD['date'], default='') or '')
        if len(d) != 8:
            continue
        asof = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        vals = {}
        for who, key in (('개인', 'person_amt'), ('외국인', 'foreign_amt'),
                         ('기관계', 'institution_amt')):
            v = _f(r, key)
            if v is not None:
                vals[who] = v / 100.0        # 백만원 → 억원
        if vals:
            out[asof] = vals
    if not out:
        raise Fetch('시장 수급 파싱 결과가 비었다 — FIELD 매핑 확인 필요')

    # 어느 영업일이든 개인·외국인·기관 순매수가 **셋 다 정확히 0** 일 수는 없다.
    # 셋은 서로를 상쇄해 합이 0 에 가까울 뿐, 각각은 수천억이다. 셋 다 0 이면
    # 질의가 성립하지 않은 것이다 — 필수 파라미터를 빈 값으로 채워 넣으면
    # (MISSING_FIELD 자동 보충) 호출은 200 으로 성공하면서 0 만 돌아온다.
    # 그걸 그대로 내보내면 화면에 '기관계 +0억원' 이 **사실처럼** 찍힌다.
    # 모르는 것은 0 이 아니다 (CLAUDE.md 2장 1번).
    last = max(out)
    if all(v == 0 for v in out[last].values()):
        filled = FILLED.get('market_investor') or []
        why = (f' — 필수 파라미터 {filled} 를 빈 값으로 채워 부른 결과다. '
               '올바른 값을 넣어야 한다' if filled else '')
        raise Fetch(f'시장 수급이 {last} 에 전부 0 이다. 질의가 성립하지 '
                    f'않은 것으로 본다{why}')
    return dict(market=market, source=SOURCE, unit='억원', by_date=out)


def stock_flows(code, s=None, retries=2):
    """종목별 투자자 매매동향 (일별). 순매수 금액이 있으면 금액을, 없으면 수량을."""
    rows = call('stock_investor', code, s=s, retries=retries)
    out = {}
    for r in rows:
        d = str(pick(r, *FIELD['date'], default='') or '')
        if len(d) != 8:
            continue
        asof = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        rec = dict(close=_f(r, 'close'))
        for who, amt_key, qty_key in (('개인', 'person_amt', 'person'),
                                      ('외국인', 'foreign_amt', 'foreign'),
                                      ('기관', 'institution_amt', 'institution')):
            a = _f(r, amt_key)
            if a is not None:
                rec[who] = a / 100.0                 # 백만원 → 억원
                rec.setdefault('_unit', '억원')
            else:
                q = _f(r, qty_key)
                if q is not None:
                    rec[who] = q
                    rec.setdefault('_unit', '주')
        out[asof] = rec
    if not out:
        raise Fetch(f'{code} 종목 수급 파싱 결과가 비었다')
    return dict(code=code, source=SOURCE, by_date=out)


def top_flows(market='0000', s=None):
    """기관·외국인 순매수 상위 종목. '기관 매수 1위' 의 근거.

    금액 필드가 있으면 억원, 없으면 수량(주)으로 떨어진다. **어느 쪽인지 반드시
    함께 낸다.** 예전에는 단위 없이 숫자만 내보내서, 뒤집히면 자릿수가 대여섯 자리
    틀린 채로 "기관 960억 순매수" 같은 문장이 나갔다.
    """
    rows = call('top_flows', market, s=s)
    out, unit = [], None
    for r in rows:
        rec = dict(code=str(pick(r, *FIELD['code'], default='') or ''),
                   name=pick(r, *FIELD['name'], default=''))
        for who, amt_key, qty_key in (('기관', 'institution_amt', 'institution'),
                                      ('외국인', 'foreign_amt', 'foreign')):
            a = num(pick(r, *FIELD[amt_key]))
            if a is not None:
                rec[who] = a / 100.0                # 백만원 → 억원
                unit = unit or '억원'
            else:
                q = num(pick(r, *FIELD[qty_key]))
                rec[who] = q
                if q is not None:
                    unit = unit or '주'
        out.append(rec)
    return dict(source=SOURCE, unit=unit,
                rows=[x for x in out if x['code']])


# 접속 단계에서 실패했다는 표식. requests 예외 **이름**과 urllib3 문구다. http.get 이
# 예외를 `Fetch('<url> 실패: ConnectTimeout: ...')` 로 감싸 올리므로 문자열로 본다.
# rt_cd 오류·HTTP 4xx·파싱 실패는 여기 없다 — 그건 붙은 뒤의 실패다.
#
# 'Timeout' 한 단어는 넣지 않는다. 비 200 응답은 `HTTP {code} · {본문}` 으로 오는데
# 게이트웨이 본문에 'Gateway Timeout'(504) 이 실리면 TCP 가 붙었는데도 접속 불가로
# 읽혔다. ReadTimeout(붙은 뒤 응답 없음)은 넣는다 — 서버가 답을 안 하는 것도
# 종목마다 25초씩 기다릴 이유가 없기는 같다. 사유 문구는 stockflows.down_reason 이
# '응답 시간 초과' 로 구분해 적는다.
CONNECT_MARKS = ('ConnectTimeout', 'ReadTimeout', 'ConnectionError', 'Max retries',
                 'timed out', 'Connection refused', 'Connection reset',
                 'NewConnectionError', 'RemoteDisconnected')


def connect_failed(e):
    """이 예외가 접속 단계(TCP·TLS·응답 대기)에서 난 것인가.

    2026-09-21 run 109 에서 11종목이 전부 `ConnectTimeout ... connect timeout=25`
    였다. 이런 실패는 종목의 문제가 아니라 서버가 안 받는 것이라, 다음 종목을
    또 부르면 25초씩 더 버릴 뿐이다(D-083).

    상태코드가 있는 실패(`HTTP 5xx · …`)는 붙은 뒤다 — 본문에 무슨 말이 있든
    접속 불가가 아니다.
    """
    msg = str(e)
    tail = msg.split(' 실패: ', 1)[1] if ' 실패: ' in msg else msg
    if tail.startswith('HTTP '):
        return False
    return any(m in tail for m in CONNECT_MARKS)


def _why(e):
    """토큰 실패 사유. 붙지도 못한 것과 거부당한 것을 구분해 준다."""
    msg = str(e)
    if connect_failed(e):
        # 인증 문제가 아니다. TCP 가 안 붙었다. GitHub 러너(해외 IP)에서는
        # 붙지 않고 국내에서는 붙는 경우가 있어, 어디서 돌렸는지가 단서다.
        return (f'접속 자체가 안 됨 (인증 이전) — {msg[:120]} '
                '· 국내 IP(로컬)에서 --check 를 돌려 같은 증상인지 보라')
    return msg[:200]


def _ymd(days_ago=0):
    from datetime import timedelta
    from .pipeline import today_kst
    return (today_kst() - timedelta(days=days_ago)).strftime('%Y%m%d')


def market_param_candidates(market='0001'):
    """시장 수급 파라미터 후보. 값은 실측으로 좁힌다 (CLAUDE.md 9장 2번).

    2026-09-17 Actions 실측(run 97·99)이 세 가지를 닫았다.

      시장구분  `FID_COND_MRKT_DIV_CODE` = **'U' 로 확정.** 'J' 는 rt_cd=2
                `ERROR INVALID FID_COND_MRKT_DIV_CODE` 로 거절당한다
      날짜      `FID_INPUT_DATE_1/_2` 를 당일~당일에서 30일~당일로 바꿔도
                **같은 300행**이 온다. 이 TR 은 날짜 파라미터를 무시하고
                최근 300영업일을 준다 — 흔들 이유가 없다
      필드명    우리가 읽던 prsn/frgn/orgn_ntby_tr_pbmn 는 **응답에 실제로
                있다.** 게다가 증권·보험·투신·은행·기금·기타법인까지 30개
                주체별 칼럼이 다 있다. 이름이 틀린 게 아니었다

    남은 사실은 이것이다 — 지수 시세(bstp_nmix_*)는 정확한데(코스피 종가
    6715.41 이 그날 값과 일치) **30개 ntby 칼럼이 300행 전부 0 이다.**

    그래서 이제 흔들 축은 업종코드 하나다. `FID_INPUT_ISCD` 가 가리키는 대상과
    `FID_INPUT_ISCD_1` 이 투자자 구분을 고르는 자리인지를 함께 본다.
    """
    t0, m1 = _ymd(), _ymd(30)
    base = {'FID_COND_MRKT_DIV_CODE': 'U',
            # 날짜는 무시되지만 필수 필드다. 빈 값으로 두면 rt_cd=2 로 거절당한다.
            'FID_INPUT_DATE_1': m1, 'FID_INPUT_DATE_2': t0}
    out = []
    for il, iscd in (('전체0000', '0000'), (f'시장{market}', market),
                     ('코스피200', '2001')):
        for jl, i1 in (('_1 빈값', ''), ('_1 0000', '0000'),
                       ('_1 1000', '1000'), ('_1 9000', '9000')):
            for kl, i2 in (('_2 빈값', ''), (f'_2 {market}', market)):
                out.append((f'{il} · {jl} · {kl}',
                            {**base, 'FID_INPUT_ISCD': iscd,
                             'FID_INPUT_ISCD_1': i1, 'FID_INPUT_ISCD_2': i2}))
    return out


# 한 번의 진단에서 부를 상한. KIS 는 유량 제한이 있고, 이 진단은 사람이 읽으려고
# 도는 것이라 결과가 24개를 넘으면 표가 아니라 로그가 된다.
MAX_PROBE_CALLS = 24

# 결과 배열이 어느 키에 들어 있는지도 모른다. 후보를 전부 열어 본다.
OUT_KEYS = ('output', 'output1', 'output2')


def _rows_of(js, key):
    rows = js.get(key) or []
    return [rows] if isinstance(rows, dict) else rows


def describe_rows(rows, limit=8):
    """응답 행이 실제로 무엇을 담고 있는지 원문으로 적는다.

    2026-09-17 실측에서 시장 수급 질의는 **성공하고 있었다** — rt_cd=0 에
    output 300행이 왔다. 그런데 우리가 읽는 세 키(prsn/frgn/orgn_ntby_tr_pbmn)가
    첫 행에서 전부 0 이었다. `pick` 은 빈 문자열을 건너뛰고 `num(None)` 은
    None 을 주므로, 0.0 이 나왔다는 것은 **키가 있고 값이 진짜 0** 이라는 뜻이다.

    그러면 남은 가능성은 둘이다 — 첫 행만 0 이거나(다른 행에 값이 있다),
    이 TR 의 순매수가 다른 키에 들어 있거나. 둘은 원문을 봐야 갈린다.
    파라미터를 더 흔드는 것으로는 못 가린다.

    그래서 값이 0 이 아닌 적이 한 번이라도 있는 키를 전부 찾아 예시와 함께
    돌려준다. 숫자를 지어내지 않고 소스가 무엇을 주는지만 적는다.
    """
    out = []
    if not rows:
        return [('원문', False, '행 없음')]
    keys = sorted({k for r in rows[:limit] if isinstance(r, dict) for k in r})
    out.append(('원문 키', True, f'{len(rows)}행 · ' + ', '.join(keys)))
    for i, r in enumerate(rows[:2]):
        out.append((f'표본 행 {i}', True, json.dumps(r, ensure_ascii=False)[:400]))
    live = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if k in live:
                continue
            f = num(v)
            if f:                                   # 0 도 None 도 아닌 값
                live[k] = v
    # 순매수 키만 따로 뽑는다. 2026-09-17 실측에서 응답에는 지수 시세
    # (bstp_nmix_*)가 잔뜩 들어 있어 전체 목록에 섞이면 정작 볼 것이 밀려난다.
    # 주체별 칼럼(bank_ntby_* 등)이 보였으므로 'ntby' 가 든 키가 답이다.
    ntby = {k: live.get(k) for k in keys if 'ntby' in k}
    if ntby:
        got = [f'{k}={v}' for k, v in ntby.items() if v]
        zero = [k for k, v in ntby.items() if not v]
        out.append(('순매수 키 · 값 있음', bool(got),
                    ', '.join(got) if got else '없다 — ntby 키가 전부 0 이다'))
        if zero:
            out.append(('순매수 키 · 전부 0', True, ', '.join(zero)))
    else:
        out.append(('순매수 키', False, "'ntby' 가 든 키가 없다"))
    if live:
        out.append(('값이 0 이 아닌 키 (전체)', True,
                    ', '.join(f'{k}={v}' for k, v in live.items())))
    else:
        out.append(('값이 0 이 아닌 키 (전체)', False,
                    '없다 — 이 블록의 숫자가 전부 0 이다. 파라미터가 아니라 '
                    'TR 자체가 다른 것을 보고 있을 수 있다'))
    return out


def probe_market_params(market='0001', stop_on_hit=True, log=None):
    """후보를 하나씩 실제로 불러 보고 결과를 돌려준다. 진단 전용.

    합격은 사람 눈이 아니라 기계가 정한다 — 개인·외국인·기관이 **셋 다 0 이
    아니어야** 한다. 어느 영업일이든 셋이 정확히 0 일 수는 없다. 셋은 서로를
    상쇄해 합이 0 에 가까울 뿐, 각각은 수천억이다. 그래서 합격한 조합에는
    '합이 0 에 가까운가' 를 함께 적는다 — 엉뚱한 블록을 읽어 숫자만 채워진
    경우와 진짜를 가르는 두 번째 눈이다.

    `stop_on_hit=False` 면 끝까지 돌며 전부 보고한다. 한 번의 CI 실행으로
    격자 전체를 보고 싶을 때 쓴다 — 첫 합격에서 멈추면 그게 우연인지
    알 수 없다.
    """
    spec = ENDPOINTS['market_investor']
    out, hit, described = [], None, False
    for label, params in market_param_candidates(market)[:MAX_PROBE_CALLS]:
        if log:
            log(f'  · {label}')
        try:
            s = session()
            s.headers.update(_headers(spec['tr_id']))
            js = get(s, base() + spec['path'], params=params, retries=1)
        except Exception as e:                      # noqa: BLE001
            out.append((label, False, str(e)[:90]))
            time.sleep(0.4)
            continue
        rt = str(js.get('rt_cd', '0'))
        if rt not in ('0', ''):
            out.append((label, False, f'rt_cd={rt} {js.get("msg1", "")[:70]}'))
            time.sleep(0.4)
            continue
        # 어느 out 키에 결과가 들어오는지도 모른다. 전부 열어 보고 적는다.
        best, note = None, []
        for key in OUT_KEYS:
            rows = _rows_of(js, key)
            if not rows:
                continue
            # 첫 성공 응답의 원문을 한 번만 남긴다. rt_cd=0 인데 값이 0 이면
            # 답은 파라미터가 아니라 응답 안에 있다 — 그걸 볼 수 있어야 한다.
            if not described:
                described = True
                for d in describe_rows(rows):
                    out.append((f'[원문] {key} · {d[0]}', d[1], d[2]))
            # 집계 세 키만 보면 안 된다. 주체별 칼럼(증권·보험·투신…)만
            # 채워져 오는 경우를 '전부 0' 으로 오판한다. ntby 가 든 키를
            # **모든 행에서** 훑어 하나라도 살아 있으면 잡는다.
            live = {}
            for r in rows:
                if not isinstance(r, dict):
                    continue
                for k, v in r.items():
                    if 'ntby' in k and k not in live and num(v):
                        live[k] = num(v)
            vals = [_f(rows[0], k) for k in
                    ('person_amt', 'foreign_amt', 'institution_amt')]
            note.append(f'{key} {len(rows)}행 집계{vals} · ntby 살아있는 키 {len(live)}')
            if best is None and live:
                agg = [live.get(k) for k in
                       ('prsn_ntby_tr_pbmn', 'frgn_ntby_tr_pbmn', 'orgn_ntby_tr_pbmn')]
                total = sum(v for v in agg if v is not None)
                best = (key, list(live.items())[:6], total)
        if best:
            key, vals, total = best
            # 백만원 단위다. 세 주체 합이 수천억(=1e5 백만원)을 넘으면 서로
            # 상쇄하는 순매수 3주체가 아니다 — 다른 뜻의 숫자를 읽고 있다.
            balanced = abs(total) < 1e5
            out.append((label, True,
                        f'{key} · {vals} · 합 {total:,.0f} '
                        f'({"상쇄 정상" if balanced else "합이 큼 — 블록 의심"})'))
            if hit is None and balanced:
                hit = (label, params, key)
            if stop_on_hit and balanced:
                break
        else:
            out.append((label, False,
                        ' / '.join(note) if note else '결과 블록이 비었다'))
        time.sleep(0.4)
    if hit:
        label, params, key = hit
        out.append(('확정 후보', True,
                    f"{label} · out='{key}' · " + json.dumps(params, ensure_ascii=False)))
    return out


def probe():
    out = []
    try:
        token()
        out.append(('KIS 토큰', True, '토큰 있음'))   # 값은 앞자리도 찍지 않는다
    except Exception as e:                       # noqa: BLE001
        return out + [('KIS 토큰', False, _why(e))]
    for label, fn in (('시장 수급 코스피', lambda: market_flows('0001')),
                      ('시장 수급 코스닥', lambda: market_flows('1001')),
                      ('종목 수급', lambda: stock_flows('005930')),
                      ('기관·외국인 상위', lambda: top_flows())):
        try:
            r = fn()
            n = len(r.get('by_date') or r.get('rows') or [])
            sample = ''
            by = r.get('by_date')
            if by:
                k = max(by)
                sample = f' · {k} ' + ', '.join(
                    f'{w} {v:,.0f}' for w, v in list(by[k].items())[:3]
                    if isinstance(v, (int, float)))
            out.append((label, True, f'{n}건{sample}'))
        except Exception as e:                   # noqa: BLE001
            out.append((label, False, str(e)[:160]))
            # 시장 수급이 실패하면 파라미터 후보를 실제로 눌러 보고 결과를 남긴다.
            # 이름은 응답이 알려줬지만 값은 모른다 — 사람이 문서를 뒤지기 전에
            # 기계가 좁혀 준다. 성공 판정은 '셋 다 0 이 아님' 이다.
            if label.startswith('시장 수급 코스피'):
                for lb, ok2, detail in probe_market_params('0001'):
                    out.append((f'  후보 {lb}', ok2, detail))
    # 무엇을 빈 값으로 채워야 붙었는지 남긴다. 이 목록을 ENDPOINTS 표에 확정한다.
    for name, fields in FILLED.items():
        out.append((f'{name} 보충 필드', True,
                    ', '.join(fields) + ' (빈 값으로 채워 통과)'))
    return out
