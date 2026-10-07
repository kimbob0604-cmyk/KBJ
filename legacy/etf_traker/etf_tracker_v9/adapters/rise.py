"""KB자산운용 RISE (구 KBSTAR) 구성종목(PDF) 수집 어댑터.

사이트: https://www.riseetf.co.kr/  (서버 렌더링 + jQuery ajax, JSON API 없음)
  목록  POST /prod/finder/listJquery                    (page=N 이 1~N 누적 반환)
  PDF   POST /prod/finder/productViewSearchTabJquery3   (searchDate, fundCd)

주의 두 가지:
 1) 종목코드가 6자리가 아니라 12자리 ISIN 으로 온다. 국내 상장주식은 KR7+종목코드6+체크3.
    KRD(현금성자산)·KR1/KR3/KR6(채권)·KR4(선물옵션)·US/CNE/KYG…(해외) 는 전부 걸러낸다.
 2) 응답 어디에도 기준일이 없다. 게다가 휴장일을 요청하면 직전 영업일 자료로 조용히 대체된다.
    (예: 2026-08-02(일) 요청 → 2026-07-31(금) 자료) 그래서 같은 내용이 반복되는 구간의
    시작일을 되짚어 실제 기준일을 확정한다. 사이트 전체가 같은 달력을 쓰므로 요청일 단위로 캐시.
"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import _session, _f, _is_kr, TIMEOUT, isin_to_code

import html as _html
import hashlib
import threading
import time as _time
from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

KST = _tz(_td(hours=9))          # 사이트 기준 시간대. 컨테이너가 UTC여도 '오늘'을 정확히 잡는다


class Rise:
    KEY, NAME, HISTORY, DEPTH = 'rise', 'KB RISE', True, 'full'
    BASE = 'https://www.riseetf.co.kr'

    LIST = BASE + '/prod/finder/listJquery'
    PDF = BASE + '/prod/finder/productViewSearchTabJquery3'

    PER_PAGE = 12        # 목록 API 페이지당 건수 (page=N 이 1~N 누적 반환)
    PAGE0 = 50           # 첫 시도 페이지. 50*12=600건까지 커버
    LOOKBACK = 12        # 기준일 역추적 최대 일수 (설·추석 최장 휴장 + 주말 커버)

    ISIN = re.compile(r'KR7([0-9A-Z]{6})[0-9]{3}')   # KR7 + 종목코드 + 체크숫자
    ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
    CELL = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
    ITEM = re.compile(r'/prod/finderDetail/([0-9A-Z]{3,8})">([^<]*)</a>\s*</p>\s*'
                      r'<span class="code">\(([0-9A-Z]{6})\)', re.S)
    TOTAL = re.compile(r'전체\s*<span[^>]*>\s*([\d,]+)\s*</span>\s*건')

    def __init__(self):
        self.s = _session()
        self.s.headers.update({'X-Requested-With': 'XMLHttpRequest',
                               'Referer': self.BASE + '/prod/finder'})
        self._asof = {}          # 요청일 -> 실제 기준일
        self._lock = threading.Lock()   # tracker 가 인스턴스 1개를 여러 스레드로 공유한다

    # ────────────────────────── 종목 목록 ──────────────────────────
    def universe(self):
        """목록 API 는 페이지당 12건이지만 page=N 이 1~N 을 통째로 돌려주는 더보기 방식이라
        충분히 큰 page 를 한 번 때리면 전량이 온다.
        응답 상단의 '전체 N 건' 과 대조해서 모자라면 page 를 키워 재시도한다.
        (상장 수가 PAGE0*12 를 넘어가도 조용히 잘리지 않게 하는 안전장치)"""
        page, out = self.PAGE0, []
        for _ in range(4):                     # 유한 루프 — 무한 재시도 없음
            t = self._list(page)
            out, seen = [], set()
            for fund_key, name, ticker in self.ITEM.findall(t):
                if fund_key in seen:
                    continue
                seen.add(fund_key)
                out.append((fund_key, ticker, _html.unescape(name).strip()))
            m = self.TOTAL.search(t)
            if not m:
                break                          # 총건수 표기가 사라졌으면 대조 불가 — 받은 만큼 사용
            total = int(m.group(1).replace(',', ''))
            if len(out) >= total:
                break
            page = max(page * 2, -(-total // self.PER_PAGE) + 2)
        return out

    def _list(self, page):
        return self.s.post(self.LIST, timeout=180, data={
            'searchText': '', 'searchType1': '', 'searchType2': '', 'page': page,
            'searchOrder': '', 'searchBoardType': '', 'searchFieldType': 'list'}).text

    # ────────────────────────── 구성종목 ──────────────────────────
    def holdings(self, fund_key, date):
        t = self._pdf(fund_key, date)
        rows = {}
        for tr in self.ROW.findall(t):
            c = [re.sub(r'<[^>]+>', '', x).strip() for x in self.CELL.findall(tr)]
            if len(c) < 6:                       # 번호 종목명 종목코드 수량 비중 평가금액
                continue
            code = self._code(c[2])
            if not code:
                continue
            rows[code] = {'name': _html.unescape(c[1]), 'qty': _f(c[3]),
                          'wt': _f(c[4]), 'val': _f(c[5])}   # 비중은 이미 25.04 형태, 공란은 '-'
        if not rows:                            # 순수 해외·채권형이면 국내종목이 하나도 없다
            return {}, self._asof.get(date, date)
        return rows, self._real_date(fund_key, date, t)

    @classmethod
    def _code(cls, raw):
        c = (raw or '').strip()
        if cls.ISIN.fullmatch(c):
            # 우선주는 ISIN 본체와 단축코드가 다르다 (KR7005382003 → 005382 아니라 005387).
            # collectors.isin_to_code 가 차수 1·2·3 을 5·7·9 로 보정한다.
            return isin_to_code(c)
        return c if _is_kr(c) else None

    # ────────────────────────── 실제 기준일 확정 ──────────────────────────
    def _pdf(self, fund_key, date):
        last = None
        for i in range(3):                    # 일시적 네트워크 오류로 기준일 추적이 통째로 죽지 않게
            try:
                return self.s.post(self.PDF, timeout=TIMEOUT,
                                   data={'searchDate': date, 'fundCd': fund_key}).text
            except Exception as e:
                last = e
                _time.sleep(0.5 * (i + 1))
        raise last

    @staticmethod
    def _sig(t):
        return hashlib.md5(re.sub(r'\s+', '', t).encode()).hexdigest()

    @staticmethod
    def _parse(date):
        """'2026-08-04' / '20260804' / '2026.08.04' 를 date 로. 날짜가 아니면 None.
        (사이트는 형식에 관대해서 아무거나 넣어도 최신 자료를 돌려주므로,
         파싱 실패를 그대로 기준일로 되돌려주면 DB 에 쓰레기 날짜가 박힌다)"""
        s = str(date or '')
        for cand in (s, re.sub(r'\D', '', s)):
            try:
                return _date.fromisoformat(cand)
            except (ValueError, TypeError):
                pass
        return None

    def _real_date(self, fund_key, date, text):
        """휴장일·미래일자를 요청하면 직전 영업일 자료가 그대로 온다. 응답에 기준일이 없으므로
        하루씩 거슬러 올라가며 내용이 바뀌는 지점을 찾아 실제 기준일을 잡는다.
        평일 최신일 조회면 추가 요청 1번이면 끝난다.

        요청일이 미래면 역추적 시작점을 KST 오늘로 당긴다. 그러지 않으면 미래일자가
        LOOKBACK 을 넘는 순간 요청일을 그대로 기준일이라고 돌려주는 가짜 날짜가 생기고,
        캐시도 못 해서 펀드마다 LOOKBACK 번씩 헛요청을 날린다.
        tracker 는 인스턴스 하나를 여러 스레드로 돌리므로 요청일당 한 번만 추적한다."""
        with self._lock:
            if date in self._asof:
                return self._asof[date]
            # 사이트가 가진 최신 자료는 KST 오늘을 넘을 수 없다. 미래 요청은 최신자료로 대체돼
            # 오므로 오늘부터 거슬러 올라가면 같은 내용이 나온다(추가 요청 없이 시작점만 당김).
            today = _dt.now(KST).date()
            d0 = min(self._parse(date) or today, today)
            sig = self._sig(text)
            real = d0.isoformat()             # 못 찾으면 요청일이 아니라 오늘 이하로 클램프한 값
            for k in range(1, self.LOOKBACK + 1):
                if self._sig(self._pdf(fund_key, (d0 - _td(days=k)).isoformat())) != sig:
                    real = (d0 - _td(days=k - 1)).isoformat()
                    break
            self._asof[date] = real           # 실패 케이스도 캐시 — 펀드마다 반복 조회 방지
            return real


if __name__ == '__main__':                    # 자체 점검
    import sys as _s
    a = Rise()
    u = a.universe()
    print(f'universe {len(u)}종목', u[:3])
    day = _s.argv[1] if len(_s.argv) > 1 else _dt.now(KST).date().isoformat()
    for key in [k for k, tk, nm in u if tk in ('148020', '270810', '0093A0', '495050')]:
        h, d = a.holdings(key, day)
        print(f'{key} {d} {len(h):>4}종목 비중합 {sum(v["wt"] for v in h.values()):.2f}')
