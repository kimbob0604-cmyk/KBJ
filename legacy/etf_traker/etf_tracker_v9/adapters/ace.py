"""한국투자신탁운용 ACE ETF 어댑터 (구 KINDEX).

사이트: https://www.aceetf.co.kr/  (Next.js SPA)
API   : https://papi.aceetf.co.kr  ← 프런트 번들 pages/_app 청크에 하드코딩된 별도 호스트
        (www.aceetf.co.kr/api/… 는 Next 라우트라 404 가 난다. 반드시 papi 를 써야 한다)

  목록: /api/funds?page=1&size=1000
        -> {data:[{fundCd, stockCd(ISIN), fundNm, …}], page:{size,totalElements,…}}
        기본 size 가 10 이라 size 를 안 주면 10개만 온다. size=1000 이면 110개 전량.
  PDF : /api/funds/{fundCd}/pdf?page=1&size=5000&std_dt=YYYYMMDD
        -> {pdfList:[{jm_KSC_CD, sec_NM, wg, cu_ITEM_CNT, val_AM, rank, std_DT}],
            page:{…}, std_DT:'YYYY-MM-DD', last_STD_DT:'YYYYMMDD'}

과거 일자 조회 지원(HISTORY=True). std_dt 를 YYYYMMDD 로 넘기면 그 날짜 PDF 가 나오고
응답 std_DT 에 실제 기준일이 담긴다. 1년 전(20250801)까지 정상 확인.

주의 1 — 페이지네이션을 쓰면 안 된다.
  page 파라미터는 동작하지만 서버 정렬이 wg 동률에서 불안정해서 페이지 간 중복/누락이 난다.
  (ACE 코스피: size=100 으로 8페이지를 다 긁어도 739행 중 고유 코드는 557개 → 182개 유실)
  그래서 size 를 크게 잡아 한 번에 받는다. size 는 서버가 상한 없이 그대로 받아준다
  (size=100000 도 200). 최대 보유종목은 ACE 코스피의 739개라 5000이면 충분하다.

주의 2 — 휴장일/주말은 서버가 직전 영업일로 폴백해 주지 않는다.
  pdfList=[] 와 std_DT=None 이 온다. _back_days 만큼 하루씩 거슬러 올라가며 재시도한다.

주의 3 — page.totalElements 가 실제 행 수와 어긋나는 펀드가 있다
  (ACE 구글밸류체인액티브: totalElements=33 인데 행은 34개). 검증에 쓰지 않는다.

주의 4 — 해외 자산 코드가 섞여 온다.
  'AAPL US'(미국) / '9888 HK'(홍콩) / '3443 TT'(대만) / 'CNE000000479'(ISIN) 등.
  ACE 미국S&P500 은 500종목 전부가 해외라 _is_kr 통과 행이 0개다 → 정상적으로 빈 dict 를 준다.
  ACE 글로벌반도체TOP4 Plus 처럼 국내/해외가 섞인 것은 국내분만 남아 합계가 100 미만이 된다.

주의 4-1 — 국내 코드 중에도 주식이 아닌 게 대량으로 섞여 온다. _is_kr 만으로는 못 거른다.
  jm_KSC_CD 는 ISIN 이 아니라 6자리 단축코드라 hanaro 처럼 'KR7' 접두로 거를 수가 없다.
  실제로 _is_kr 을 그냥 통과하는 비주식:
    A01690 코스피200지수선물 / A11680 삼성전자개별선물 / AF6680 카카오뱅크개별선물
    BAFBQ8 코스피위클리 콜옵션 / ZFXSG7 FX스왑 USD
    E00601 국민은행(CD) / F14017 KB국민카드(CP) / S06362 한국서부발전(전단채)
    03502G 국고채 / 10101G 통안채 / 50101G 한국전력 회사채
  110개 전수 조회 결과 14개 펀드가 오염됐고 비중도 작지 않다
  (ACE 삼성전자단일종목레버리지 83.7%가 개별선물, ACE 미국30년국채액티브(H) 48.4%가 FX스왑,
   ACE 고배당주Plus커버드콜액티브 5.1%가 위클리 콜옵션, ACE 레버리지 97.2%가 지수선물).
  더 나쁜 건 이것들이 만기마다 코드가 바뀐다는 점이다 — 실측으로 코스피200선물이
  A01630(3월물) → A01660(6월물) → A01690(9월물) 로 갈아탔고 위클리 옵션은 매주 바뀐다.
  tracker 의 NEW/DROP 판정에는 QTY_FLOOR 가 걸리지 않아서 롤오버 때마다 🆕/❌ 가 그대로 뜬다.
  게다가 밸류체인액티브·커버드콜액티브 같은 액티브 ETF 라 리포트 최상단으로 올라간다.
  그래서 _STOCK 으로 형태를 한 번 더 거른다(아래).
  ※ 이전 버전 주석의 "다른 어댑터와 필터 기준을 맞추려고 선물을 안 거른다"는 사실과 반대다.
    rise/plus 는 ISIN 접두 KR4(선물옵션)·KR1/KR3/KR6(채권)·KRD(원화예금)를 전부 걸러내고
    hanaro 는 아예 KR7(주식·ETF)만 남긴다. 안 거르던 쪽은 ace 뿐이었다.

주의 5 — 레버리지/인버스는 비중 합계가 100 이 아니다.
  ACE 레버리지 ≈ 199.8, ACE SK하이닉스단일종목레버리지 ≈ 200.0, ACE 인버스 ≈ -100.
  wg 단위 자체는 퍼센트(33.42 = 33.42%)가 맞다.

주의 6 — 채권형은 jm_KSC_CD 가 종목 고유코드가 아니라 '종목분류코드'라 한 펀드 안에서 겹친다.
  ACE 국고채10년: 서로 다른 국고채 3종이 전부 '03502G'. ACE 단기통안채: 8종이 전부 '10101G'.
  {코드: …} 딕셔너리 규약상 그냥 대입하면 마지막 것만 남아 비중이 100 → 32.43 으로 깨진다.
  그래서 같은 코드는 qty/val/wt 를 합산한다.
  _STOCK 필터를 통과하는 잔존 중복은 숫자형 회사채뿐이라(008561 메리츠증권2506-1 등)
  합산 로직은 그대로 둔다. 참고로 예전 주석의 "주식형에는 중복이 없다"는 틀렸었다 —
  ACE 고배당주Plus커버드콜액티브(주식형)가 행사가만 다른 위클리 콜옵션 2건을 BAFBQ8 하나로
  내려보내고 있었다. 지금은 _STOCK 이 옵션을 먼저 걸러서 사라진다.

주의 7 — 채권은 cu_ITEM_CNT(수량)가 항상 "0" 으로 내려온다. val_AM(평가금액)과 wg 만 유효하다.
  tracker 의 수량 변동 분석은 QTY_FLOOR 에서 자연히 걸러지므로 그대로 0 을 넣는다.

남는 한계 — 숫자 6자리로 내려오는 회사채/특수채는 형태만으로 주식과 구분할 방법이 없다.
  '145765' BNK캐피탈363-5, '008276' 산은캐피탈786-2, '001051' 서울특별시채권2020-11 등
  9개 채권·혼합형 펀드에 남는다. 다만 이 코드들이 실제 상장 종목코드와 겹치지는 않는 것을
  확인했다(코스피 전종목 897 + 상장 ETF 1155 = 2,054개와 대조, 충돌 0건).
  주식 시그널을 오염시키지는 않고 채권형 ETF 안에서만 잡음으로 남는다.
"""
import re
import sys, os, time
from datetime import date as _date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import _session, _f, _is_kr, _ymd, TIMEOUT

# KRX 상장 주식·ETF 단축코드는 '숫자6자리'(005930) 아니면
# '숫자4 + 영문1 + 숫자1'(0126Z0 삼성에피스홀딩스, 0221Z0 ACE K바이오코스닥액티브) 두 형태뿐이다.
# 나머지 형태는 전부 주식이 아니다:
#   영문으로 시작   → 선물(A…) 옵션(B…) CD(E…) CP(F…) 전단채(S…) FX스왑(Z…)
#   끝자리가 영문   → 채권 종목분류코드(03502G 국고채, 10101G 통안채, 01980A 하나캐피탈)
# 실측 검증: 코스피 전종목 897개 + 상장 ETF 1155개 = 2,054개 중 이 정규식에 걸려 잘못
# 탈락하는 건 0건. 반대로 ACE 110개 펀드에서 비주식 코드 98종을 걸러낸다
# (채권 43 · CP 24 · 전단채 18 · CD 6 · 선물 5 · 옵션 1 · FX스왑 1).
_STOCK = re.compile(r'\d{6}|\d{4}[A-Z]\d')


class Ace:
    KEY, NAME, HISTORY, DEPTH = 'ace', '한투 ACE', True, 'full'
    BASE = 'https://papi.aceetf.co.kr'
    SITE = 'https://www.aceetf.co.kr'

    PAGE_SIZE = 5000      # 페이지네이션이 깨져 있어 한 방에 받는다 (최대 보유 739종목 = ACE 코스피)
    BACK_DAYS = 5         # 휴장일이면 직전 영업일까지 거슬러 올라갈 최대 일수

    def __init__(self):
        self.s = _session()
        self.s.headers.update({'Accept': 'application/json',
                               'Origin': self.SITE,
                               'Referer': f'{self.SITE}/'})

    def _json(self, path, params=None, tries=3):
        last = None
        for i in range(tries):
            try:
                r = self.s.get(f'{self.BASE}{path}', params=params, timeout=TIMEOUT)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:      # 미존재 펀드코드
                    return {}
                last = ValueError(f'ACE: HTTP {r.status_code} {path}')
            except Exception as e:
                last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
        raise last

    def universe(self):
        """stockCd 는 ISIN 12자리('KR7105190003') → [3:9] 가 6자리 종목코드.
        신규 코드가 섞인 종목(ACE K바이오코스닥액티브 = '0221Z0')도 이 규칙 그대로 맞는다."""
        j = self._json('/api/funds', {'page': 1, 'size': 1000})
        out, seen = [], set()
        for x in j.get('data') or []:
            fc = (x.get('fundCd') or '').strip()
            isin = (x.get('stockCd') or '').strip()
            if not fc or fc in seen:
                continue
            seen.add(fc)
            tk = isin[3:9] if len(isin) == 12 else None
            out.append((fc, tk if _is_kr(tk or '') else None, x.get('fundNm')))
        return out

    def _fetch(self, fund_key, ymd):
        j = self._json(f'/api/funds/{fund_key}/pdf',
                       {'page': 1, 'size': self.PAGE_SIZE, 'std_dt': ymd})
        return j or {}

    def holdings(self, fund_key, date):
        try:
            d0 = _date(*map(int, date.split('-')))
        except Exception:
            d0 = None

        j, real = {}, None
        for k in range(self.BACK_DAYS + 1):
            if d0 is None:
                ymd = date.replace('-', '').replace('.', '')
                if k:
                    break
            else:
                ymd = (d0 - timedelta(days=k)).strftime('%Y%m%d')
            j = self._fetch(fund_key, ymd)
            if j.get('pdfList'):
                real = _ymd(j.get('std_DT')) or _ymd(ymd)
                break

        rows = {}
        for x in j.get('pdfList') or []:
            # jm_KSC_CD: '005930' 국내 / 'AAPL US','9888 HK' 해외 / 'CNE000000479' 해외ISIN
            code = str(x.get('jm_KSC_CD') or '').strip()
            if not _is_kr(code):
                continue
            if not _STOCK.fullmatch(code):   # 선물·옵션·CD·CP·전단채·FX스왑·채권 (주의 4-1)
                continue
            if not real:
                real = _ymd(x.get('std_DT'))
            wt = _f(str(x.get('wg') or '').replace('%', ''))
            qty = _f(x.get('cu_ITEM_CNT'))
            val = _f(x.get('val_AM'))
            if code in rows:            # 채권형 종목분류코드 중복 → 합산 (주의 6)
                r = rows[code]
                r['wt'] += wt
                r['qty'] += qty
                r['val'] += val
            else:
                rows[code] = {'name': x.get('sec_NM'), 'wt': wt, 'qty': qty, 'val': val}
        return rows, (real or date)
