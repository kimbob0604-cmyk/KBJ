"""국내 ETF 구성종목(PDF) 수집기 — 운용사별 어댑터.
검증일 2026-08-04. KODEX / TIGER / TIMEFOLIO / SOL 실동작 확인 완료.

어댑터 추가 방법: 아래 클래스와 같은 형태로 universe() / holdings() 두 메서드만 구현하고
파일 맨 아래 ADAPTERS 에 클래스를 추가하면 된다.
  universe() -> [(fund_key, ticker, name), ...]
  holdings(fund_key, 'YYYY-MM-DD') -> ({종목코드: {name, qty, wt, val}}, 실제기준일)
"""
import re, time
from datetime import date as _date, timedelta as _timedelta
import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
TIMEOUT = 30
KRCODE = re.compile(r'[0-9A-Z]{6}')     # 국내 종목코드 (신규 코드에 영문 포함: 0185L0 등)


def _session():
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9'})
    return s


def _f(v):
    try:
        return float(str(v).replace(',', '').strip())
    except Exception:
        return 0.0


def _is_kr(code):
    """국내 상장 종목코드인지. 'ABT US EQUITY' 같은 해외 티커와 현금성 자산(KRD…) 배제"""
    c = (code or '').strip()
    return bool(KRCODE.fullmatch(c)) and any(ch.isdigit() for ch in c) and not c.startswith('KRD')


def _rows_from_table(html, col_code=0, col_name=1, col_qty=2, col_val=3, col_wt=4):
    out = {}
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        c = [re.sub(r'<[^>]+>', '', x).strip()
             for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
        if len(c) > max(col_code, col_name, col_qty, col_val, col_wt) and _is_kr(c[col_code]):
            out[c[col_code]] = {'name': c[col_name], 'qty': _f(c[col_qty]),
                                'val': _f(c[col_val]), 'wt': _f(c[col_wt])}
    return out



# 우선주 ISIN → 단축코드 보정표.
# ISIN 본체 6번째 자리가 우선주 차수(1·2·3)인데 실제 단축코드 끝자리는 5·7·9 다.
#   삼성전자우 KR7005931001 → '005931' 이 아니라 005935
#   현대차우   KR7005381005 → 005385   현대차2우B KR7005382003 → 005387
# 이걸 안 고치면 같은 종목이 소스마다 다른 코드로 잡혀 리포트에서 합쳐지지 않는다.
_PREF = {'1': '5', '2': '7', '3': '9'}
_ISIN_KR = re.compile(r'KR7([0-9A-Z]{5})([0-9])[0-9]{3}')


def isin_to_code(isin):
    """국내 주식·ETF ISIN(KR7…) → 6자리 단축코드. 그 외에는 None."""
    m = _ISIN_KR.fullmatch((isin or '').strip().upper())
    if not m:
        return None
    code = m.group(1) + _PREF.get(m.group(2), m.group(2))
    return code if _is_kr(code) else None


def _ymd(d):
    d = (d or '').replace('.', '').replace('-', '')
    return f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else None


# ────────────────────────── 삼성자산운용 KODEX ──────────────────────────
class Kodex:
    KEY, NAME, HISTORY, DEPTH = 'kodex', '삼성 KODEX', True, 'full'
    BASE = 'https://www.samsungfund.com'

    def __init__(self):
        self.s = _session()

    def universe(self):
        out, seen = [], set()
        for pg in range(1, 30):
            u = f'{self.BASE}/api/v1/kodex/product.do?ordrColm=NAV&ordrSort=DESC&pageNo={pg}&srchTerm=w'
            j = self.s.get(u, headers={'Accept': 'application/json'}, timeout=TIMEOUT).json()
            if not j:
                break
            for x in j:
                if x['fId'] in seen:
                    continue
                seen.add(x['fId'])
                out.append((x['fId'], x.get('stkTicker'), x.get('fNm')))
            time.sleep(0.15)
        return out

    def holdings(self, fund_key, date):
        u = f'{self.BASE}/api/v1/kodex/product-pdf/{fund_key}.do?gijunYMD={date.replace("-", ".")}'
        j = self.s.get(u, headers={'Accept': 'application/json'}, timeout=TIMEOUT).json()
        pdf = j.get('pdf') or {}
        rows = {}
        for x in pdf.get('list') or []:
            code = (x.get('itmNo') or '').strip()
            if not _is_kr(code):
                continue
            rows[code] = {'name': x.get('secNm'), 'wt': _f(x.get('ratio')),
                          'qty': _f(x.get('applyQ')), 'val': _f(x.get('evalA'))}
        return rows, (_ymd(pdf.get('gijunYMD')) or date)


# ────────────────────────── 미래에셋 TIGER ──────────────────────────
class Tiger:
    KEY, NAME, HISTORY, DEPTH = 'tiger', '미래에셋 TIGER', True, 'full'
    BASE = 'https://investments.miraeasset.com'

    def __init__(self):
        self.s = _session()

    def universe(self):
        u = f'{self.BASE}/tigeretf/ko/product/search/list.ajax?listCnt=1000&pageIndex=1'
        t = self.s.get(u, headers={'X-Requested-With': 'XMLHttpRequest',
                                   'Referer': f'{self.BASE}/tigeretf/ko/product/search/list.do'},
                       timeout=90).text
        out, seen = [], set()
        for isin in re.findall(r'ksdFund=(KR[0-9A-Z]{10})', t):
            if isin in seen:
                continue
            seen.add(isin)
            out.append((isin, isin[3:9], None))   # ISIN = KR7 + 종목코드(6자리) + 체크(3자리)
        return out

    BACK_DAYS = 7      # 주말·공휴일·당일 미공시 시 거슬러 올라갈 최대 일수

    def _fetch(self, fund_key, date):
        u = (f'{self.BASE}/tigeretf/ko/product/search/detail/pdfListAjax.ajax'
             f'?ksdFund={fund_key}&listCnt=1000&pageIndex=1&fixDate={date.replace("-", ".")}')
        t = self.s.get(u, headers={'X-Requested-With': 'XMLHttpRequest',
                                   'Referer': f'{self.BASE}/tigeretf/ko/product/search/detail/index.do?ksdFund={fund_key}'},
                       timeout=TIMEOUT).text
        return _rows_from_table(t)

    def holdings(self, fund_key, date):
        # 이 API 는 데이터가 없는 날짜에 빈 표를 돌려주고 직전 영업일로 폴백해 주지 않는다.
        # 당일 아침에 아직 공시 전이면 빈 결과가 오므로, 실제 데이터가 있는 날까지 거슬러 올라간다.
        d = _date.fromisoformat(date)
        for i in range(self.BACK_DAYS + 1):
            day = (d - _timedelta(days=i)).isoformat()
            rows = self._fetch(fund_key, day)
            if rows:
                return rows, day          # 요청일이 아니라 '실제 데이터가 있는 날'을 돌려준다
        return {}, date


# ────────────────────────── 타임폴리오 TIME ──────────────────────────
class TimeFolio:
    KEY, NAME, HISTORY, DEPTH = 'timefolio', '타임폴리오 TIME', True, 'full'
    BASE = 'https://timeetf.co.kr'
    CATES = ['001', '002']                        # 001 해외투자 / 002 국내투자

    def __init__(self):
        self.s = _session()

    def universe(self):
        out, seen = [], set()
        for cate in self.CATES:
            t = self.s.get(f'{self.BASE}/m11_list.php?cate={cate}', timeout=TIMEOUT).text
            for idx, nm, code in re.findall(
                    r'm11_view\.php\?idx=(\d+)&cate=\d+".*?<div class="name">([^<]+)</div>'
                    r'.*?<div class="codeNum"><span>([^<]+)</span>', t, re.S):
                if idx in seen:
                    continue
                seen.add(idx)
                out.append((idx, code.strip(), _unesc(nm.strip())))
        return out

    def holdings(self, fund_key, date):
        t = self.s.get(f'{self.BASE}/m11_view.php?idx={fund_key}&pdfDate={date}',
                       timeout=TIMEOUT).text
        m = re.search(r'id="constituentItems".*?<tbody>(.*?)</tbody>', t, re.S)
        if not m:
            return {}, date
        d = re.search(r'id="pdfDate"[^>]*value="([\d\-]+)"', t)
        return _rows_from_table(m.group(1)), (d.group(1) if d else date)


# ────────────────────────── 신한자산운용 SOL ──────────────────────────
class Sol:
    KEY, NAME, DEPTH = 'sol', '신한 SOL', 'full'
    HISTORY = False        # 최신 영업일만 제공 → 매일 돌려서 자체 히스토리를 쌓아야 함
    BASE = 'https://www.soletf.com'

    def __init__(self):
        self.s = _session()

    def universe(self):
        out, seen = [], set()
        for pg in range(1, 10):
            j = self.s.get(f'{self.BASE}/api/etf/pds?page={pg}',
                           headers={'Accept': 'application/json'}, timeout=TIMEOUT).json()
            items = j.get('items') or []
            if not items:
                break
            for x in items:
                fc = x.get('FUND_CD')
                if fc in seen:
                    continue
                seen.add(fc)
                out.append((fc, x.get('ETF_CD6'), x.get('ETF_NAME')))
        return out

    def holdings(self, fund_key, date):
        j = self.s.get(f'{self.BASE}/api/etf/pds/pdf/{fund_key}',
                       headers={'Accept': 'application/json'}, timeout=TIMEOUT).json()
        rows = {}
        for x in j.get('items') or []:
            code = str(x.get('STOCK_CODE') or '').strip()
            if not _is_kr(code):
                continue
            rows[code] = {'name': x.get('SEC_NM'), 'qty': _f(x.get('QTY')),
                          'val': _f(x.get('PRICE')),
                          'wt': _f(str(x.get('WT_DISP') or '').replace('%', ''))}
        return rows, (_ymd(j.get('workDt')) or date)



# ───────────── 네이버 폴백 (전 운용사 TOP10) ─────────────
class NaverTop10:
    """운용사 전용 어댑터가 없는 곳(DS·KB·한투·한화·키움·NH·KoAct 등)을 메우는 폴백.
    상위 10종목만 제공하고 과거 일자 조회는 불가하다. 대신 상장된 국내 ETF 전부를 커버한다.
    테마형 ETF는 TOP10이 비중의 98~99%를 차지해 사실상 전량이고,
    시장대표형은 70% 안팎, 분산형 액티브는 55~70% 수준이다."""
    KEY, NAME, HISTORY, DEPTH = 'naver', '네이버(TOP10)', False, 'top10'
    LIST = 'https://finance.naver.com/api/sise/etfItemList.nhn'
    API = 'https://m.stock.naver.com/api/stock/{}/etfAnalysis'

    def __init__(self):
        self.s = _session()
        self.s.headers.update({
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                          'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
            'Referer': 'https://m.stock.naver.com/'})

    def universe(self):
        j = self.s.get(self.LIST, timeout=30,
                       headers={'Referer': 'https://finance.naver.com/sise/etf.naver'}).json()
        return [(x['itemcode'], x['itemcode'], x['itemname'])
                for x in j['result']['etfItemList'] if x['etfTabCode'] in (1, 2)]

    def holdings(self, fund_key, date):
        j = self.s.get(self.API.format(fund_key), timeout=TIMEOUT).json()
        rows = {}
        for x in j.get('etfTop10MajorConstituentAssets') or []:
            code = (x.get('itemCode') or '').strip()
            if not _is_kr(code):
                continue
            rows[code] = {'name': x.get('itemName'),
                          'qty': _f(x.get('stockCount')),
                          'val': 0.0,
                          'wt': _f(str(x.get('etfWeight') or '').replace('%', ''))}
        return rows, (_ymd(j.get('navPerformanceReferenceDate')) or date)

    def issuer_name(self, code):
        try:
            return self.s.get(self.API.format(code), timeout=TIMEOUT).json().get('issuerName')
        except Exception:
            return None


def _unesc(s):
    return (s.replace('&amp;', '&').replace('&lt;', '<')
             .replace('&gt;', '>').replace('&quot;', '"'))


_BUILTIN = (Kodex, Tiger, TimeFolio, Sol, NaverTop10)
ADAPTERS = {c.KEY: c for c in _BUILTIN}


def _load_plugins():
    """adapters/ 폴더의 어댑터를 자동 등록한다.
    운용사를 추가할 때 이 파일을 건드릴 필요가 없다 — adapters/{key}.py 만 넣으면 된다."""
    import importlib, pkgutil, os
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'adapters')
    if not os.path.isdir(d):
        return
    import sys
    sys.path.insert(0, os.path.dirname(d))
    for m in pkgutil.iter_modules([d]):
        if m.name.startswith('_'):
            continue
        try:
            mod = importlib.import_module(f'adapters.{m.name}')
        except Exception as e:
            print(f'[collectors] adapters/{m.name}.py 로드 실패: {type(e).__name__}: {e}')
            continue
        for obj in vars(mod).values():
            if (isinstance(obj, type) and hasattr(obj, 'KEY') and hasattr(obj, 'holdings')
                    and getattr(obj, 'KEY', None) and obj.__module__ == mod.__name__):
                ADAPTERS[obj.KEY] = obj


_load_plugins()
