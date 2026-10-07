"""NH-Amundi자산운용 HANARO ETF 어댑터.

사이트: https://www.hanaroetf.com/  (서버사이드 렌더링 + HTML 조각을 돌려주는 AJAX API)

  목록: /api/v1/fund/get-fund-search-list?pageNo=N
        -> <li data-target="fund" data-fund-code="EPM99"> … <a href="/fund/{uid}"> …
             <h5>HANARO 200</h5> … <dt>종목코드</dt><dd>293180</dd> … </li>
        페이지당 10건 고정. 전체 건수는 응답 안의
        <input value="50" data-target="fundSearchListCount"/> 로 온다 (2026-08-04 기준 50개).
        fund_key 로는 상세 URL 의 16자리 대문자 hex uid 를 쓴다 (data-fund-code 'EPM99' 는 안 먹힌다).

  PDF : /api/v1/fund/{uid}/get-fund-holdings-list?baseDate=YYYY.MM.DD
        -> <tr><td>순번</td><td>종목코드</td><th>종목명</th>
              <td>수량</td><td>평가금액</td><td>비중</td></tr> … 의 HTML 조각
        페이지네이션 없음. 한 번에 전량이 온다 (HANARO 미국 S&P500 504행까지 확인).
        조각 끝의 totalFundHoldingsListSize 로 행 수를 교차검증할 수 있다
        (initialFundHoldingsListSize=10 은 화면에 처음 보이는 개수일 뿐 데이터 제한이 아니다.
         11행부터는 <tr class="blind"> 로 CSS 숨김 처리만 되어 있고 값은 다 들어있다).

과거 일자 조회 지원(HISTORY=True). 2024.08.05 까지 정상 확인.

주의 1 — 종목코드가 6자리가 아니라 12자리 ISIN 이다. 반드시 'KR7' 만 통과시켜야 한다.
  KR7005930003(삼성전자) → [3:9] = '005930'. 신규 코드도 이 규칙 그대로 맞는다
  (KR70126Z0002 삼성에피스홀딩스 → '0126Z0', KR70009K0001 에임드바이오 → '0009K0').
  KR7 이외에는 전부 주식이 아니고, 게다가 [3:9] 를 떼면 _is_kr 을 그냥 통과해 버려서 위험하다:
    KRD010010001 원화예금        → 'D01001'
    CASH00000001 설정현금액       → 'H00000'
    KR103501GF30 국고채권         → '03501G'   ← ACE 어댑터가 겪은 채권 가짜코드와 같은 함정
    KR6000014EC9 신한은행 회사채    → '000014'   ← 진짜 종목코드처럼 생겼다
    KR2001024D61 지역개발채권 / KRC…국고채이자분리채권
    KR4A01690002 코스피200선물     → 'A01690'
    KRYZTRSB4N01 스왑(합성ETF)     → 'ZTRSB4'
  해외분은 US…/IE…/JP… 등 자국 ISIN 이라 KR7 필터에서 자연히 빠진다.
  → 채권형·합성형·해외형은 정상적으로 빈 dict 를 돌려준다.

주의 2 — 미래 일자를 요청하면 조용히 최신 기준일 데이터로 대체된다(클램프).
  2026.08.05 / 2026.09.01 / 2027.06.01 요청이 전부 2026.08.03 과 바이트 단위로 동일했다.
  요청일을 그대로 기준일로 돌려주면 오늘 스냅샷이 어제 데이터에 오늘 날짜로 찍힌다.
  응답 조각에는 기준일이 안 들어있어서, 클램프가 의심될 때만 상세페이지
  /fund/{uid} 의 <input id="pdfDate" … value="2026.08.03"> 를 읽어 실제 기준일을 확정한다.
  클램프 판별: 조각(d) 와 조각(d-1) 이 완전히 같으면 d 는 실제 기준일이 아니다
  (실제 기준일이면 전일은 다른 값이거나 휴장일이라 빈 응답이다).
  상세페이지가 280KB 라 매번 받으면 느려서, 이렇게 필요할 때만 받고 결과를 캐시한다.

주의 3 — 휴장일·주말은 직전 영업일로 폴백해 주지 않는다. 빈 조각(0행)이 온다.
  BACK_DAYS 만큼 하루씩 거슬러 올라가며 재시도한다.

주의 4 — baseDate 는 'YYYY.MM.DD' 또는 'YYYYMMDD' 만 먹는다.
  'YYYY-MM-DD' 로 주면 400 이 아니라 0행짜리 정상 응답이 와서 조용히 빈다.

주의 5 — 비중 합계는 국내주식형이 90~100 이다(퍼센트 단위 맞음). 100 에 살짝 못 미치는 건
  원화예금·설정현금액 등 KR7 이 아닌 행을 뺀 만큼이다. HANARO 200 = 198종목 99.70.
  레버리지/인버스는 선물·스왑 비중이 빠져 훨씬 낮게 나온다(HANARO 200 선물레버리지 = 35.6).

주의 6 — 종목명·펀드명에 HTML 엔티티가 그대로 온다('HANARO 미국 S&amp;P500'). unescape 한다.

주의 7 — 2024년 11월 중순 이전 PDF 는 종목코드 칸이 통째로 비어 있다(종목명·수량·비중만 있다).
  2024-11-07 = 202행 전부 코드 없음 / 2024-11-13 = 202행 전부 코드 있음.
  코드가 없으면 매핑할 방법이 없어 그 이전 일자는 빈 dict 가 된다. 실질 히스토리 하한선이다.
"""
import re, sys, os, time
from datetime import date as _date, timedelta
from html import unescape
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import (_session, _f, _is_kr, _rows_from_table, _ymd, TIMEOUT,  # noqa: F401
                        isin_to_code)
# _rows_from_table 은 여기선 못 쓴다. 그건 종목코드 칸이 6자리라고 보고 _is_kr 로 거르는데
# HANARO 는 12자리 ISIN 이 와서 전부 탈락한다. 대신 _parse() 에서 ISIN→6자리 변환까지 같이 한다.


class Hanaro:
    KEY, NAME, HISTORY, DEPTH = 'hanaro', 'NH HANARO', True, 'full'
    BASE = 'https://www.hanaroetf.com'

    PAGE_MAX = 20      # 목록 안전 상한 (페이지당 10건 고정, 현재 5페이지)
    BACK_DAYS = 7      # 휴장일이면 직전 영업일까지 거슬러 올라갈 최대 일수
    TRIES = 3          # 간헐적 커넥션 끊김이 있어 재시도한다

    _LI = re.compile(r'<li data-target="fund"(.*?)</li>', re.S)
    _UID = re.compile(r'/fund/([0-9A-Fa-f]{16})')
    _NAME = re.compile(r'<h5>(.*?)</h5>', re.S)
    _TICK = re.compile(r'종목코드\s*</dt>\s*<dd>([^<]+)</dd>', re.S)
    _TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
    _TD = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
    _PDFDATE = re.compile(r'id="pdfDate"[^>]*\svalue="(\d{4})\.(\d{2})\.(\d{2})"')
    _WS = re.compile(r'\s+')

    def __init__(self):
        self.s = _session()
        self.s.headers.update({'Referer': f'{self.BASE}/fund/fund-list',
                               'X-Requested-With': 'XMLHttpRequest'})
        self._latest = {}     # uid -> 그 펀드의 최신 PDF 기준일(date). 상세페이지에서 확정한 값
        self._smax = None     # 지금까지 확인한 사이트 전체 최신 기준일 (요청 절약용 선클램프)

    def _get(self, url, referer=None):
        last = None
        for i in range(self.TRIES):
            try:
                r = self.s.get(url, timeout=TIMEOUT,
                               headers={'Referer': referer} if referer else None)
                if r.status_code == 200:
                    return r.text
                last = ValueError(f'HANARO: HTTP {r.status_code} {url}')
            except Exception as e:
                last = e
            if i < self.TRIES - 1:
                time.sleep(1.5 * (i + 1))
        raise last

    # ────────────────────────── 목록 ──────────────────────────
    def universe(self):
        out, seen = [], set()
        for pg in range(1, self.PAGE_MAX + 1):
            t = self._get(f'{self.BASE}/api/v1/fund/get-fund-search-list?pageNo={pg}')
            items = self._LI.findall(t)
            if not items:
                break
            for it in items:
                m = self._UID.search(it)
                if not m:
                    continue
                uid = m.group(1).upper()
                if uid in seen:
                    continue
                seen.add(uid)
                tk = self._TICK.search(it)
                nm = self._NAME.search(it)
                tk = unescape(tk.group(1)).strip().upper() if tk else ''
                out.append((uid, tk if _is_kr(tk) else None,
                            unescape(self._WS.sub(' ', nm.group(1))).strip() if nm else None))
            time.sleep(0.15)
        return out

    # ────────────────────────── PDF ──────────────────────────
    def _frag(self, uid, d):
        """d: datetime.date -> 구성종목 HTML 조각"""
        u = (f'{self.BASE}/api/v1/fund/{uid}/get-fund-holdings-list'
             f'?baseDate={d.year:04d}.{d.month:02d}.{d.day:02d}')
        return self._get(u, referer=f'{self.BASE}/fund/{uid}')

    def _parse(self, html):
        """-> (국내주식 rows, 조각에 들어있던 전체 행 수)
        전체 행 수를 같이 돌려주는 이유: 0행이면 '그날 PDF 자체가 없다'(휴장일 → 하루 물러선다)이고,
        행은 있는데 rows 가 비면 '그 펀드에 국내주식이 없다'(채권·해외·합성형 → 더 볼 것 없다)라서
        둘을 구분하지 않으면 채권형 하나마다 BACK_DAYS 만큼 헛질의를 하게 된다."""
        rows, n = {}, 0
        for tr in self._TR.findall(html):
            c = [re.sub(r'<[^>]+>', '', x).strip() for x in self._TD.findall(tr)]
            if len(c) < 6:
                continue
            n += 1
            isin = c[1].strip().upper()
            if not isin.startswith('KR7') or len(isin) != 12:   # 주의 1
                continue
            code = isin_to_code(isin)   # 우선주 보정 포함 (005931 → 005935)
            if not code:
                continue
            if not _is_kr(code):
                continue
            wt = _f(c[5].replace('%', ''))
            qty, val = _f(c[3]), _f(c[4])
            if code in rows:                                     # 방어적 합산
                rows[code]['wt'] += wt
                rows[code]['qty'] += qty
                rows[code]['val'] += val
            else:
                rows[code] = {'name': unescape(c[2]), 'qty': qty, 'wt': wt, 'val': val}
        return rows, n

    def _pdf_date(self, uid):
        """상세페이지에서 그 펀드의 실제 최신 PDF 기준일을 읽는다 (주의 2)."""
        if uid in self._latest:
            return self._latest[uid]
        d = None
        try:
            t = self._get(f'{self.BASE}/fund/{uid}')
            m = self._PDFDATE.search(t)
            if m:
                d = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            d = None
        self._latest[uid] = d
        if d and (self._smax is None or d > self._smax):
            self._smax = d
        return d

    def holdings(self, fund_key, date):
        try:
            y, m, dd = (_ymd(date) or date).split('-')
            d0 = _date(int(y), int(m), int(dd))
        except Exception:
            return {}, date

        want = d0
        known = self._latest.get(fund_key) or self._smax
        if known and want > known:
            want = known

        body, rows, n, d = '', {}, 0, want
        for k in range(self.BACK_DAYS + 1):          # 휴장일 폴백 (주의 3)
            d = want - timedelta(days=k)
            body = self._frag(fund_key, d)
            rows, n = self._parse(body)
            if n:
                break
        if not n:                                    # 그 범위에 PDF 자체가 없다
            return {}, date

        # 미래일 클램프 보정 (주의 2). 그 펀드의 최신일이 이미 d 로 확정돼 있으면 건너뛴다.
        if self._latest.get(fund_key) != d:
            prev = self._WS.sub('', self._frag(fund_key, d - timedelta(days=1)))
            if prev == self._WS.sub('', body):
                real = self._pdf_date(fund_key)
                if real and real < d:
                    r2, n2 = self._parse(self._frag(fund_key, real))
                    if n2:
                        rows, d = r2, real
        return rows, d.isoformat()
