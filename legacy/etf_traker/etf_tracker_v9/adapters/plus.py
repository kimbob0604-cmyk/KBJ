"""한화자산운용 PLUS (구 ARIRANG) 구성종목(PDF) 수집 어댑터.

사이트: https://www.plusetf.co.kr/  (Spring Boot + jQuery, 깔끔한 JSON API 두 개면 끝난다)
  목록  POST /api/v1/product/find/list   JSON body, 페이지당 10건 고정 (size/pageSize 무시됨)
  PDF   GET  /api/v1/product/pdf/list?n={펀드코드}&d=YYYYMMDD&page=0&pageSize=N   ← page 는 0부터

응답 행: {num, wkdate, jmCd, krJmCd, jmNm, amount, ratio}
  jmCd   6자리 단축코드 (채권·현금·일부 주식은 null)
  krJmCd 12자리 ISIN
  amount 수량,  ratio 비중(이미 22.2152 형태의 퍼센트),  wkdate 실제 기준일

주의 네 가지:
 1) d 는 반드시 YYYYMMDD. 'YYYY-MM-DD' 를 그대로 넣으면 400 이 아니라 엉뚱한 과거일자
    (2026-08-04 → 20251230) 를 조용히 돌려준다. d 를 아예 빼면 400.
 2) 휴장일·미래일자를 넣으면 직전 영업일 자료로 대체된다. 실제 기준일은 행의 wkdate 에 있다.
 3) jmCd 가 6자리 단축코드가 아니라 12자리 ISIN 으로 오는 행이 섞여 있다
    (합성 ETF 의 실물 바스켓, 채권혼합·TDF 가 담은 국내 ETF 등). ISIN 에서 되돌려야 한다.
    이때 우선주는 ISIN 본체와 단축코드가 다르다 — 삼성전자우는 KR7005931001 이지만 005935 다.
    ISIN 6번째 자리(우선주 차수) 1·2·3 을 단축코드 5·7·9 로 옮겨준다 (collectors.isin_to_code).
 4) 해외주식(US·IE·JP·CNE·KYG…), 채권(KR1/KR3/KR6/KRZ), 선물(KR4), 원화예금(KRD) 이 함께 온다.
    _is_kr 로 거른다. 인버스/레버리지는 원화예금 비중이 -87% 같은 값이라 걸러낸 뒤 비중합이
    100 을 크게 넘을 수 있다 (정상이다).
"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import _session, _f, _is_kr, _rows_from_table, _ymd, TIMEOUT, isin_to_code

import json


class Plus:
    KEY, NAME, HISTORY, DEPTH = 'plus', '한화 PLUS', True, 'full'
    BASE = 'https://www.plusetf.co.kr'

    LIST = BASE + '/api/v1/product/find/list'
    PDF = BASE + '/api/v1/product/pdf/list'

    PAGE = 1000          # PDF 한 페이지 요청 건수 (최대 보유가 706종목이라 사실상 1회로 끝난다)
    MAXPAGE = 60         # 무한루프 방지 상한. PAGE=1000 이면 6만 행까지 커버한다
    ISIN = re.compile(r'KR7([0-9A-Z]{5})([0-9A-Z])[0-9]{3}')   # KR7 + 본체6 + 체크3

    def __init__(self):
        self.s = _session()
        self.s.headers.update({'Accept': 'application/json',
                               'Referer': self.BASE + '/product/find'})

    # ────────────────────────── 종목 목록 ──────────────────────────
    def universe(self):
        """페이지당 10건 고정. size·pageSize 를 넣어도 무시하므로 last 가 뜰 때까지 돈다 (9페이지)."""
        out, seen = [], set()
        for pg in range(30):
            j = self.s.post(self.LIST, timeout=TIMEOUT,
                            headers={'Content-Type': 'application/json'},
                            data=json.dumps({'searchSortTy': 'aum', 'searchSort': 'DESC',
                                             'page': pg, 'searchAnnuityOptionTy': None,
                                             'searchWord': ''})).json()
            items = j.get('content') or []
            if not items:
                break
            for x in items:
                fid = x.get('id')
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                out.append((fid, (x.get('nameCode') or '').strip() or None,
                            (x.get('displayName') or '').strip() or None))
            if j.get('last'):
                break
        return out

    # ────────────────────────── 구성종목 ──────────────────────────
    def holdings(self, fund_key, date):
        d = re.sub(r'\D', '', date or '')[:8]          # 하이픈을 남기면 엉뚱한 날짜가 온다
        rows, asof, pg = {}, None, 0
        while True:
            j = self.s.get(self.PDF, timeout=TIMEOUT,
                           params={'n': fund_key, 'd': d, 'page': pg,
                                   'pageSize': self.PAGE}).json()
            items = j.get('content') or []
            for x in items:
                asof = asof or _ymd(x.get('wkdate'))
                code = self._code(x.get('jmCd'), x.get('krJmCd'))
                if not code:
                    continue
                rows[code] = {'name': (x.get('jmNm') or '').strip(),
                              'qty': _f(x.get('amount')), 'wt': _f(x.get('ratio')),
                              'val': 0.0}               # 평가금액은 응답에 없다
            pg += 1
            if len(items) < self.PAGE or pg >= (j.get('totalPages') or 1) or pg > 20:
                break
        return rows, (asof or date)

    @classmethod
    def _code(cls, jm, isin):
        """6자리 단축코드를 뽑는다. 없으면 KR7 ISIN 에서 되돌린다."""
        c = (jm or '').strip()
        if _is_kr(c):
            return c                                   # 정상 경로 (전체 행의 약 99%)
        # 우선주 보정은 collectors.isin_to_code 로 일원화했다
        # (구 PREF 표는 2→6 으로 잘못돼 있어 현대차2우B 가 005386 으로 어긋났다)
        return isin_to_code(c) or isin_to_code(isin)


if __name__ == '__main__':                             # 자체 점검
    import sys as _s
    a = Plus()
    u = a.universe()
    print(f'universe {len(u)}종목  {u[:3]}')
    day = _s.argv[1] if len(_s.argv) > 1 else '2026-08-04'
    want = {'152100', '161510', '449450', '227830', '0184L0'}
    for key, tk, nm in [x for x in u if x[1] in want]:
        h, dt = a.holdings(key, day)
        print(f'{key} {tk} {nm[:24]:<24} {dt} {len(h):>4}종목 '
              f'비중합 {sum(v["wt"] for v in h.values()):.2f}')
