"""KIS 코스피200 선물 — 종목코드·시세·미결제약정 필드를 잰다. **일회성 측정**이다.

stock-dashboard 장마감 시황에 코스피200 선물(근월물·원월물의 시가·고가·저가·종가·
미결제약정)을 싣기 전에, 추측이 아닌 실측으로 세 가지를 확정한다.

  1. 종목코드 형식과 월물 순서 — KIS 지수선물옵션 마스터(fo_idx_code_mts)에서
  2. 현재가 API(FHMIF10000000)가 주는 필드 — 시가·고가·저가·현재가·미결제약정
  3. 일봉 API(FHKIF03020100)가 그날 정규장 봉과 미결제약정을 주는가

토큰·앱키는 찍지 않는다. 응답 값만 찍는다.
"""
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone

from board.ingest import kis as K
from board.ingest.http import session

KST = timezone(timedelta(hours=9))
MASTER = 'https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip'


def call(path, tr, params):
    s = session()
    s.headers.update(K._headers(tr))
    r = s.get(K.base() + path, params=params, timeout=25)
    try:
        js = r.json()
    except ValueError:
        return r.status_code, {'_text': r.text[:300]}
    return r.status_code, js


def show(label, js, rows=3):
    print(f'  rt_cd={js.get("rt_cd")!r} msg={js.get("msg1")!r}')
    for key in ('output', 'output1', 'output2', 'output3'):
        v = js.get(key)
        if v is None:
            continue
        if isinstance(v, list):
            print(f'  [{key}] 행 {len(v)}개')
            for row in v[:rows]:
                print('   ', json.dumps(row, ensure_ascii=False))
        else:
            print(f'  [{key}]', json.dumps(v, ensure_ascii=False))


print('=== 1. 마스터 ===')
r = session().get(MASTER, timeout=40)
print(f'HTTP {r.status_code} · {len(r.content):,}바이트')
z = zipfile.ZipFile(io.BytesIO(r.content))
print('파일', z.namelist())
raw = z.read(z.namelist()[0])
for enc in ('cp949', 'euc-kr', 'utf-8'):
    try:
        text = raw.decode(enc)
        print('인코딩', enc)
        break
    except UnicodeDecodeError:
        continue
lines = text.splitlines()
print('줄', len(lines))
for ln in lines[:6]:
    print('  앞', repr(ln[:160]))
# 2026 표준코드 개편 이후 형식: '1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200'
# f[0]='1' 코스피200 선물, f[1]=단축코드, f[6]=월물 순번(1=근월물)
k200 = []
for ln in lines:
    f = ln.split('|')
    if len(f) > 8 and f[0] == '1' and f[8].strip() == 'KOSPI200':
        k200.append((int(f[6]), f[1].strip(), f[3].strip()))
k200.sort()
print(f'코스피200 선물 {len(k200)}개', k200)
codes = []
for _, c, _n in k200[:2]:
    codes += [c, c[1:], '1' + c]   # 입력 형식 후보 — 통하는 것을 잰다
print('시도할 입력', codes)

today = datetime.now(KST)
d2 = today.strftime('%Y%m%d')
d1 = (today - timedelta(days=10)).strftime('%Y%m%d')
for code in codes:
    print(f'\n=== 2. 현재가 {code} ===')
    st, js = call('/uapi/domestic-futureoption/v1/quotations/inquire-price', 'FHMIF10000000',
                  {'FID_COND_MRKT_DIV_CODE': 'F', 'FID_INPUT_ISCD': code})
    print(f'  HTTP {st}')
    show('price', js)
    print(f'=== 3. 일봉 {code} {d1}~{d2} ===')
    st, js = call('/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice',
                  'FHKIF03020100',
                  {'FID_COND_MRKT_DIV_CODE': 'F', 'FID_INPUT_ISCD': code,
                   'FID_INPUT_DATE_1': d1, 'FID_INPUT_DATE_2': d2,
                   'FID_PERIOD_DIV_CODE': 'D'})
    print(f'  HTTP {st}')
    show('daily', js, rows=4)
sys.exit(0)
