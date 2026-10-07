"""삼성액티브자산운용 KoAct 어댑터.

사이트: https://www.samsungactive.co.kr/
KODEX(삼성자산운용)와 같은 웹 플랫폼을 쓰지만 API 네임스페이스가 다르다.
  목록: /api/v1/product/etf.do?pageNo=N            (20건/페이지 고정, 총 25종목)
  PDF : /api/v1/product/etf-pdf/{fId}.do?gijunYMD=YYYY.MM.DD

과거 일자 조회 지원(HISTORY=True). 휴장일을 넣으면 직전 영업일로 자동 폴백하고
응답의 pdf.gijunYMD 에 실제 기준일이 담겨온다. PDF 는 페이지네이션 없이
nowCnt == totalCnt 로 전량 내려온다.

주의: Cloudflare 레이트리밋이 걸려 있다. 약 25요청/35초를 넘기면 429 +
"Just a moment..." 챌린지 HTML 이 돌아오고 약 33초 뒤 풀린다.
25종목 전체를 도는 데 정확히 그 한계에 걸리므로 _throttle() 로 페이스를 조절한다.
"""
import sys, os, time, threading
from collections import deque
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import _session, _f, _is_kr, _ymd, TIMEOUT


class KoAct:
    KEY, NAME, HISTORY, DEPTH = 'koact', 'KoAct(삼성액티브)', True, 'full'
    BASE = 'https://www.samsungactive.co.kr'

    WINDOW, MAX_IN_WINDOW, COOLDOWN = 35.0, 20, 35.0   # 실측 한계 25요청/35초의 8할
    RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)

    # 레이트리밋 예산은 클래스 단위로 공유한다. tracker.py 는 build_universe() 와
    # snapshot() 에서 서로 다른 KoAct 인스턴스를 만들기 때문에, 인스턴스별로 세면
    # 두 단계가 연달아 돌 때 합계가 한도를 넘는다.
    _hits = deque()
    _lock = threading.Lock()

    def __init__(self):
        self.s = _session()
        self.s.headers.update({'Accept': 'application/json',
                               'Referer': f'{self.BASE}/etf/list.do'})

    def _throttle(self):
        """tracker.snapshot() 은 워커 6개로 같은 인스턴스의 holdings() 를 동시에 부른다.
        락 없이 deque 를 만지면 '검사 후 갱신' 사이에 다른 스레드가 끼어들어
        창당 허용치를 넘겨버린다(실측: 20 허용인데 25까지 통과 = 차단 임계값과 동일).
        슬립은 락 밖에서 하고 깨어나면 다시 검사한다."""
        while True:
            with self._lock:
                now = time.time()
                while self._hits and now - self._hits[0] > self.WINDOW:
                    self._hits.popleft()
                if len(self._hits) < self.MAX_IN_WINDOW:
                    self._hits.append(now)
                    return
                wait = self.WINDOW - (now - self._hits[0]) + 0.1
            time.sleep(min(max(wait, 0.05), self.WINDOW))

    def _json(self, url, tries=4):
        """429 가 뜨면 본문이 Cloudflare 챌린지 HTML 이라 .json() 이 터진다.
        쿨다운을 두고 재시도한다.

        재시도 대상은 일시적 오류(429/5xx/네트워크 끊김/HTML 응답)뿐이다.
        400·404 처럼 재시도해도 안 풀리는 응답까지 돌면 35+70+105초를 헛되이 버린다."""
        for i in range(tries):
            last = None
            self._throttle()
            try:
                r = self.s.get(url, timeout=TIMEOUT)
            except requests.RequestException:
                # 네트워크 순단은 그냥 터뜨리면 그 ETF 하루치가 통째로 날아간다.
                if i == tries - 1:
                    raise
                time.sleep(2.0 * (i + 1))
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    last = ValueError(f'KoAct: 200 인데 JSON 아님 (챌린지 추정) {url}')
            elif r.status_code not in self.RETRY_STATUS:
                r.raise_for_status()          # 400/404 등 확정 오류 → 즉시 실패
                raise ValueError(f'KoAct: 예상 밖 응답 {r.status_code} {url}')
            else:
                last = ValueError(f'KoAct: 재시도 소진 ({r.status_code}) {url}')
            if i == tries - 1:
                r.raise_for_status()
                raise last
            time.sleep(self.COOLDOWN * (i + 1))

    def universe(self):
        """/api/v1/product/etf.do 는 파라미터를 붙이면 400 을 잘 뱉는다
        (graphTerm/sort/orderType/pageRows 조합 대부분 400). pageNo 만 먹는다.
        페이지당 20건 고정이라 반드시 끝까지 돌아야 25종목 전체가 나온다."""
        out, seen = [], set()
        for pg in range(1, 20):
            j = self._json(f'{self.BASE}/api/v1/product/etf.do?pageNo={pg}')
            items = j.get('etfs') or []
            if not items:
                break
            new = 0
            for x in items:
                fid = x.get('fId')
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                new += 1
                out.append((fid, (x.get('stkTicker') or '').strip() or None,
                            x.get('fNm')))
            if not new:
                break
        return out

    def holdings(self, fund_key, date):
        u = (f'{self.BASE}/api/v1/product/etf-pdf/{fund_key}.do'
             f'?gijunYMD={date.replace("-", ".")}')
        j = self._json(u)
        pdf = j.get('pdf') or {}
        rows = {}
        for x in pdf.get('list') or []:
            # itmNo: '005930' 국내주식 / '0162M0' 국내ETF / 'AVGO US Equity' 해외
            #        'KRD010010001' 원화예금 / 'CASH00000001' 설정현금액 / 'KRG…' ETN
            code = (x.get('itmNo') or '').strip()
            if not _is_kr(code):
                continue
            rows[code] = {'name': x.get('secNm'),
                          'wt': _f(str(x.get('ratio') or '').replace('%', '')),
                          'qty': _f(x.get('applyQ')),
                          'val': _f(x.get('evalA'))}
        return rows, (_ymd(pdf.get('gijunYMD')) or date)
