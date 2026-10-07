#!/usr/bin/env python3
"""
ETF 시세·자금흐름 수집 — 대시보드의 수치 절반이 여기서 나온다.

두 개의 소스를 쓴다.
  1) etfItemList  : 전 종목 1회 호출. NAV·시가총액·종목명·탭코드. 상장좌수 추정에 쓴다.
  2) siseJson     : 종목별 일봉. 확정된 종가·거래량. 수익률·거래급증 판정에 쓴다.

왜 나눴나. etfItemList 의 등락률은 호출 시점의 장중 값이라 아침 9시에 받으면 의미가 없다.
확정 종가는 일봉에서만 나온다. 반대로 NAV·시총은 일봉에 없어서 목록 API 가 필요하다.
그래서 '기준일'은 항상 직전 완료 영업일이다. 장중 값을 그날 수치로 쓰지 않는다.
"""
import ast
import concurrent.futures as cf
import time
import requests

LIST_URL = 'https://finance.naver.com/api/sise/etfItemList.nhn'
HIST_URL = 'https://api.finance.naver.com/siseJson.naver'
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
      'Referer': 'https://finance.naver.com/sise/etf.naver'}

DDL = """
CREATE TABLE IF NOT EXISTS etf_meta(
  code TEXT PRIMARY KEY, name TEXT, tab INT, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS etf_px(
  code TEXT, asof TEXT, close REAL, volume REAL, PRIMARY KEY(code, asof));
CREATE INDEX IF NOT EXISTS ix_px_asof ON etf_px(asof);
CREATE TABLE IF NOT EXISTS etf_live(
  code TEXT PRIMARY KEY, ts TEXT, price REAL, chg REAL, nav REAL,
  mktcap REAL, units REAL, volume REAL, turnover REAL);
CREATE TABLE IF NOT EXISTS etf_aum(
  code TEXT, asof TEXT, nav REAL, mktcap REAL, units REAL, PRIMARY KEY(code, asof));
CREATE INDEX IF NOT EXISTS ix_aum_asof ON etf_aum(asof);
"""

# etfTabCode → 사람이 읽는 구분. 네이버 ETF 화면의 탭 순서와 같다.
TAB = {1: '국내시장지수', 2: '국내업종테마', 3: '국내파생', 4: '해외주식',
       5: '원자재', 6: '채권', 7: '기타', 8: '해외파생', 9: '해외혼합'}
DOMESTIC = {1, 2, 3}


def fetch_list(retries=3):
    """전 상장 ETF 1회 조회. 실패하면 대시보드 절반이 비므로 재시도한다."""
    last = None
    for i in range(retries):
        try:
            r = requests.get(LIST_URL, headers=UA, timeout=30)
            r.raise_for_status()
            rows = r.json()['result']['etfItemList']
            if rows:
                return rows
            last = 'empty'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f'ETF 목록 조회 실패: {last}')


def sync_meta_aum(conn, rows, asof):
    """종목 메타와 순자산 스냅샷을 적재한다.

    상장좌수 = 시가총액 ÷ NAV. 두 값 모두 같은 호출에서 온 장중 값이지만
    비율이라 주가 변동이 상쇄된다. 그래서 장중에 받아도 좌수 추정은 견딘다.
    설정/환매 판단은 이 좌수의 전일 대비 변화로 한다.
    """
    meta, aum = [], []
    for x in rows:
        code, name = x['itemcode'], x['itemname']
        nav = x.get('nav') or 0
        mkt = x.get('marketSum') or 0          # 억원
        units = (mkt * 1e8 / nav) if nav > 0 else None
        meta.append((code, name, x.get('etfTabCode'), asof, asof))
        aum.append((code, asof, nav or None, mkt or None, units))
    conn.executemany(
        'INSERT INTO etf_meta(code,name,tab,first_seen,last_seen) VALUES(?,?,?,?,?) '
        'ON CONFLICT(code) DO UPDATE SET name=excluded.name, tab=excluded.tab, '
        'last_seen=excluded.last_seen', meta)
    conn.executemany('INSERT OR REPLACE INTO etf_aum VALUES(?,?,?,?,?)', aum)
    conn.commit()
    return len(meta)


def _parse_hist(text):
    """siseJson 은 JS 배열 리터럴로 온다. 값은 날짜문자열과 숫자뿐이라 그대로 파싱해도 안전하다."""
    s = text.strip()
    if not s.startswith('['):
        return []
    try:
        rows = ast.literal_eval(s.replace("'", '"').replace('\n', '').replace('\t', ''))
    except Exception:
        return []
    out = []
    for r in rows[1:]:                                   # 0행은 헤더
        try:
            d = str(r[0])
            out.append((f'{d[:4]}-{d[4:6]}-{d[6:8]}', float(r[4]), float(r[5])))
        except Exception:
            continue
    return out


def fetch_hist(code, start, end, retries=2):
    for i in range(retries):
        try:
            r = requests.get(HIST_URL, headers=UA, timeout=25, params={
                'symbol': code, 'requestType': 1, 'timeframe': 'day',
                'startTime': start.replace('-', ''), 'endTime': end.replace('-', '')})
            if r.status_code == 200:
                rows = _parse_hist(r.text)
                if rows:
                    return rows
        except Exception:
            pass
        time.sleep(0.8 * (i + 1))
    return []


def sync_px(conn, codes, start, end, workers=12, log=None):
    """종목별 일봉을 모아 적재한다. 일부 실패해도 전체를 멈추지 않는다."""
    buf, fail = [], 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_hist, c, start, end): c for c in codes}
        done = 0
        for f in cf.as_completed(futs):
            c = futs[f]
            rows = f.result()
            done += 1
            if not rows:
                fail += 1
            else:
                buf.extend((c, d, px, vol) for d, px, vol in rows)
            if log and done % 200 == 0:
                log(f'   시세 {done}/{len(codes)}')
    conn.executemany('INSERT OR REPLACE INTO etf_px VALUES(?,?,?,?)', buf)
    conn.commit()
    return len(buf), fail


def last_close_date(conn):
    """확정 종가가 존재하는 가장 최근 날짜. 대시보드의 '기준일'."""
    r = conn.execute('SELECT MAX(asof) FROM etf_px').fetchone()[0]
    return r


def trading_days(conn, upto, n):
    """upto 이하의 거래일을 최신순으로 n개. 기간 수익률의 기준점을 잡는 데 쓴다."""
    return [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM etf_px WHERE asof<=? ORDER BY asof DESC LIMIT ?', (upto, n))]


def live_rows(rows):
    """목록 API 응답을 대시보드가 쓰는 형태로 정규화.

    changeRate 는 전일 종가 대비 등락률이라 그대로 쓴다. amonut 은 거래대금(백만원)
    이라 100 으로 나누면 억원이 된다. close*volume 로 추정하는 것보다 정확하다.
    """
    out = {}
    for x in rows:
        nav = x.get('nav') or 0
        mkt = x.get('marketSum') or 0            # 억원
        out[x['itemcode']] = dict(
            name=x['itemname'], tab=x.get('etfTabCode') or 0,
            price=x.get('nowVal') or 0, chg=x.get('changeRate'),
            nav=nav or None, mktcap=mkt or 0,
            units=(mkt * 1e8 / nav) if nav > 0 else None,
            volume=x.get('quant') or 0,
            turnover=(x.get('amonut') or 0) / 100.0)   # 백만원 → 억원
    return out
