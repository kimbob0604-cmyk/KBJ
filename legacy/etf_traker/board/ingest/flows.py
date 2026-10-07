#!/usr/bin/env python3
"""
투자자별 매매동향 — 레퍼런스 코멘트 첫 단락의 절반이 이 값이다.

  "지수를 끌어올린 건 기타법인. 코스피에서만 1조 넘게 순매수 + 기관 7,604억 순매수 /
   개인 2조 순매도. 외국인 순매도가 전일 5,000억대에서 1,162억으로 축소된 것도 ..."
  "두산에너빌리티는 기관 960억 순매수로 이 날 기관 매수 1위, 외국인도 64억 순매수"

CLAUDE.md 10장은 pykrx 가 종목별 투자자 구분 순매수거래대금을 준다고 적어 뒀지만,
pykrx 는 KRX 스크래핑 기반이고 그 KRX 가 로그인을 요구하게 됐다. 그래서 인증 없이
쓸 수 있는 후보로 네이버 금융의 두 페이지를 붙여 둔다.

  시장 전체  finance.naver.com/sise/investorDealTrendDay.naver   개인·외국인·기관계
  종목별    finance.naver.com/item/frgn.naver                   외국인·기관 순매매량

**이 두 파서는 이 환경에서 실호출로 검증하지 못했다.** 페이지 구조를 추정해 쓴
정규식이라 그대로 동작하지 않을 가능성이 높다. run.py --check 가 매번 실제로 찔러
보고, 실패하면 리포트 상단에 '수급 미확보'로 남는다. 추정으로 채우지 않는다.

한계를 미리 적어 둔다.
  - 네이버 시장 전체 표에 '기타법인'이 별도 컬럼으로 있는지 확인되지 않았다.
    레퍼런스 코멘트의 핵심 주어가 기타법인이라 이게 없으면 반쪽이다.
  - 종목별은 순매매'량'(주)이라 '순매수 960억'을 쓰려면 종가를 곱해야 한다.
    곱한 값은 추정치이므로 is_estimate 가 붙는다.
  - 확정치 갱신 시각을 모른다 (CLAUDE.md 9장 2번). 첫 주 실측이 필요하다.

제대로 하려면 증권사 오픈API(한국투자증권 KIS 등)를 붙여야 한다. docs/APIS.md B1.
"""
import re

from .http import Fetch, get, num, session

SOURCE = 'naver'
# 시장 전체 투자자별 순매수. **2026-09-18 에 주소가 바뀌었다.**
#
# 옛 주소 finance.naver.com/sise/investorDealTrendDay.naver 는 HTTP 410 이고
# 본문이 'Npay 증권' 페이지다. 410 은 '영구히 없앴다' 는 뜻이라 헤더를 바꿔도
# 안 풀린다 — 맨 요청·UA·UA+Referer·UA+Referer+Accept 네 조합 전부 410 이었다.
# 종목별이 하루 먼저 같은 길을 갔다(D-079). 공식 소스 두 곳도 닫혀 있다:
# KRX 오픈API 는 투자자별 경로가 없고(대조군 200 · 후보 10개 404), 공공데이터
# 포털도 마찬가지다(대조군 2,870종목 · 투자자별 400). 근거는 D-082.
MARKET_URL = 'https://m.stock.naver.com/api/index/{code}/integration'

# 시장 코드. 새 API 는 숫자 코드가 아니라 이름을 쓴다.
MARKET_CODE = {'KOSPI': 'KOSPI', 'KOSDAQ': 'KOSDAQ'}

# 새 API 가 주는 투자자 구분은 **셋뿐**이다. 옛 표에는 기타법인까지 넷이 있었다.
# 없는 것을 역산해 채우지 않는다 — 네 구분의 합이 0 이라는 항등식은 그 넷이
# 전체를 나눌 때만 성립하고, 새 소스가 같은 방식으로 나눈다는 보장이 없다.
DEAL_FIELD = (('개인', 'personalValue'),
              ('외국인', 'foreignValue'),
              ('기관계', 'institutionalValue'))

# 종목별 투자자 순매매. **2026-09 에 주소가 바뀌었다.**
#
# 옛 주소 finance.naver.com/item/frgn.naver 는 302 로 stock.naver.com 의
# Next.js 화면으로 넘어가고, 그 응답에는 tr/td/table 이 하나도 없다
# (러너 실측 118,366자 · tr 0개). 이 저장소의 정규식 파서도, flowlab 의
# BeautifulSoup 파서도 똑같이 0행을 받았다 — 파서 문제가 아니라 표가 없어진 것이다.
#
# 그 화면이 값을 받아 오는 JSON 주소로 갈아탄다. 러너에서 후보를 찔러 확인했다
# (.github/workflows/frgn-probe.yml). 응답은 배열이고 한 건이 하루다:
#   {"bizdate":"20260916","closePrice":"29,250",
#    "organPureBuyQuant":"+63,482","foreignerPureBuyQuant":"+7,600",
#    "individualPureBuyQuant":"-74,126","accumulatedTradingVolume":"1,322,448"}
# 기본 10거래일만 주고 pageSize 로 늘어난다(size/count/limit/page 는 안 먹는다).
#
# (이 주석을 쓸 때는 시장 전체가 살아 있었다. 하루 뒤 그쪽도 410 이 됐다 —
#  위 MARKET_URL 주석 참고. 낡은 사실을 지우지 않고 언제 무엇이 바뀌었는지 남긴다.)
STOCK_URL = 'https://m.stock.naver.com/api/stock/{code}/trend'
STOCK_DAYS = 20          # 한 번에 받을 거래일 수

# 투자자 구분 라벨. 페이지 표기가 바뀌면 여기만 고친다.
INVESTORS = ('개인', '외국인', '기관계', '금융투자', '보험', '투신', '은행',
             '기타금융', '연기금등', '사모', '국가지자체', '기타법인')

# 날짜 칸. 네이버 금융은 페이지마다 표기가 다르다 — 종목별 표는 `2026.08.28`,
# 시장 전체 표는 `26.08.28` 이다. 4자리만 받으면 후자에서 **한 행도 안 걸리고**
# "표 파싱 실패" 로 끝난다.
#
# `search` 가 아니라 칸 전체를 맞춘다. 2자리 연도를 아무 데서나 찾게 두면
# 금액 `1.234.567` 같은 문자열이 날짜로 잡힌다.
_DATE = re.compile(r'^\s*(\d{2}|\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*$')


def _ymd(m):
    """정규식 매치 → 'YYYY-MM-DD'. 2자리 연도는 2000년대로 본다.

    1900년대 시세를 이 페이지에서 볼 일은 없다. 그래도 규칙을 적어 둔다 —
    2100년에 이 코드가 살아 있으면 그때 고칠 일이다.
    """
    y = int(m.group(1))
    if y < 100:
        y += 2000
    return f'{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'


_TH_TAG = re.compile(r'<th([^>]*)>(.*?)</th>', re.S)
_COLSPAN = re.compile(r'colspan\s*=\s*"?(\d+)', re.I)


def _expand(row_html):
    """머리글 한 줄을 **열 개수만큼** 편다. colspan 은 그 수만큼 반복한다.

    네이버 표는 머리글이 두 줄이고 '기관' 이 하위 구분(금융투자·보험·투신…)
    위에 colspan 으로 걸려 있다. 펴지 않으면 열 위치가 어긋난다.
    """
    out = []
    for attrs, body in _TH_TAG.findall(row_html):
        m = _COLSPAN.search(attrs)
        out.extend([_text(body)] * max(1, int(m.group(1)) if m else 1))
    return out


def _eok(raw):
    """'+3,020' → 3020.0. 값이 없으면 None."""
    if raw in (None, ''):
        return None
    try:
        return float(str(raw).replace(',', '').replace('+', ''))
    except ValueError:
        return None


def _turnover_eok(total_infos):
    """totalInfos 의 거래대금을 억원으로. 못 읽으면 None.

    이 API 는 거래대금에만 단위를 글자로 붙여 준다 — '25,563,397백만'.
    순매수 쪽(dealTrendInfo)에는 단위 표기가 없어서, 이 값이 단위를 가리는
    유일한 자다. 검산에 쓴다(_check_scale).
    """
    for it in total_infos or []:
        if not isinstance(it, dict) or it.get('code') != 'accumulatedTradingValue':
            continue
        v = str(it.get('value') or '')
        n = _eok(re.sub(r'[^\d,.-]', '', v))
        if n is None:
            return None
        if '백만' in v:
            return n / 100.0          # 백만원 → 억원
        if '억' in v:
            return n
        if '조' in v:
            return n * 10000.0
        return None                   # 모르는 단위는 쓰지 않는다
    return None


def _check_scale(row, turnover_eok, market, day):
    """순매수가 억원 눈금인지 거래대금으로 검산한다.

    새 API 는 순매수에 단위를 안 적는다. 억원이라고 **짐작하면 안 된다** — 옛
    수집기가 단위를 못 읽을 때 기본값을 골랐다가 100배 어긋난 값이 성공으로
    나간 적이 있다. 그래서 아는 값으로 상한을 건다.

    한 사람이 산 만큼 다른 사람이 팔았으므로 순매수 절대값의 합은 거래대금의
    두 배를 넘을 수 없다. 눈금이 100배 어긋나면 이 상한을 바로 넘는다.
    2026-09-18 실측: 코스피 거래대금 255,634억, 순매수 절대합 55,476억 (21.7%).

    거래대금을 못 읽었으면 검산을 건너뛰되 그 사실을 사유에 남긴다 — 조용히
    통과시키면 다음에 눈금이 바뀌어도 모른다.
    """
    vals = [v for v in row.values() if v is not None]
    if not vals:
        raise Fetch(f'{market} {day} 투자자별 값이 하나도 없다')
    if turnover_eok is None:
        return '거래대금을 못 읽어 눈금 검산을 못 했다'
    s_abs = sum(abs(v) for v in vals)
    if s_abs > turnover_eok * 2:
        raise Fetch(
            f'{market} {day} 순매수 절대합 {s_abs:,.0f}억이 거래대금 '
            f'{turnover_eok:,.0f}억의 두 배를 넘는다 — 눈금이 억원이 아닐 수 '
            '있다. 값을 쓰지 않는다')
    return None


def fetch_market(market='KOSPI', s=None, asof=None):
    """시장 전체 투자자별 순매수. 단위는 억원.

    반환 {asof: {투자자: 억원}} — **하루치뿐이다.** 옛 페이지는 며칠치를 줬는데
    새 API 는 당일만 준다(pageSize·page·size·count·limit 일곱 가지를 다 걸어도
    같은 103바이트). 과거가 필요하면 매일 받아 쌓는 수밖에 없다.

    `asof` 는 받지만 API 가 날짜 지정을 지원하지 않아 무시한다. 응답이 제 날짜를
    싣고 오므로 그걸 그대로 쓴다 — 우리가 원하는 날짜를 덮어씌우지 않는다.
    """
    code = MARKET_CODE.get(market)
    if not code:
        raise Fetch(f'모르는 시장이다: {market}')
    s = s or session(referer=f'https://stock.naver.com/domestic/index/{code}/total')
    js = get(s, MARKET_URL.format(code=code))
    if not isinstance(js, dict):
        raise Fetch(f'{market} integration 응답이 객체가 아니다')
    deal = js.get('dealTrendInfo')
    if not isinstance(deal, dict):
        raise Fetch(f'{market} 응답에 dealTrendInfo 가 없다 — 키 {sorted(js)[:10]}')
    day = str(deal.get('bizdate') or '')
    if len(day) != 8 or not day.isdigit():
        raise Fetch(f'{market} bizdate 를 못 읽었다: {deal.get("bizdate")!r}')
    day = f'{day[:4]}-{day[4:6]}-{day[6:]}'
    row = {}
    for ko, key in DEAL_FIELD:
        v = _eok(deal.get(key))
        if v is not None:
            row[ko] = v
    missing = [ko for ko, key in DEAL_FIELD if ko not in row]
    if missing:
        raise Fetch(f'{market} {day} 투자자 구분을 못 읽었다: {missing} · '
                    f'받은 키 {sorted(deal)}')
    note = _check_scale(row, _turnover_eok(js.get('totalInfos')), market, day)
    return dict(market=market, source=SOURCE, unit='억원',
                params='integration.dealTrendInfo',
                columns=[ko for ko, _ in DEAL_FIELD],
                # 새 소스가 안 주는 구분. 화면·리포트가 '없다' 와 '0' 을 구분하게
                # 이름을 남긴다. 역산하지 않는다.
                columns_absent=['기타법인'],
                scale_note=note,
                by_date={day: row})


# 순매수 합계가 0 에서 벗어나도 되는 폭. 시장 전체 하루 거래대금의 몇 % 수준을
# 넘으면 열이 밀린 것으로 본다. 표는 반올림된 억원이라 정확히 0 은 아니다.


_FRGN_ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)


# trend 응답의 투자자 키. **개인은 응답에 없는 날이 있다** — 2026-09-16 실측에는
# individualPureBuyQuant 가 있었고 2026-09-21 실측에는 기관·외국인뿐이었다.
# 없으면 그 구분을 만들지 않는다. 0 으로 채우면 '개인이 안 샀다' 는 거짓이 된다.
STOCK_FIELD = (('기관', 'organPureBuyQuant'),
               ('외국인', 'foreignerPureBuyQuant'),
               ('개인', 'individualPureBuyQuant'))


def fetch_stock(code, s=None, days=STOCK_DAYS):
    """종목별 외국인·기관 순매매량(주). 최근 며칠치.

    금액이 아니라 수량이다. 억원으로 바꾸려면 종가를 곱해야 하고 그건 추정치다.
    받은 구분만 담는다 — 응답에 없는 구분은 키 자체가 없다(STOCK_FIELD 주석).
    """
    s = s or session(referer=f'https://stock.naver.com/domestic/stock/{code}/investmentinfo')
    js = get(s, STOCK_URL.format(code=code), params={'pageSize': days})
    if not isinstance(js, list) or not js:
        raise Fetch(f'{code} 투자자별 응답이 배열이 아니거나 비었다 — '
                    f'{type(js).__name__} {str(js)[:160]}')
    out = {}
    for x in js:
        if not isinstance(x, dict):
            continue
        d = str(x.get('bizdate') or '').strip()
        if len(d) != 8 or not d.isdigit():
            continue                       # 날짜가 없으면 그 행은 버린다
        asof = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        rec = dict(close=num(x.get('closePrice')),
                   volume=num(x.get('accumulatedTradingVolume')))
        for ko, key in STOCK_FIELD:
            v = num(x.get(key))
            if v is not None:
                rec[ko] = v
        out[asof] = rec
    first_keys = sorted(js[0]) if isinstance(js[0], dict) else '?'
    if not out:
        raise Fetch(f'{code} 투자자별 값이 비었다 — 응답 {len(js)}건, '
                    f'첫 건 키 {first_keys}')
    if not any(ko in rec for rec in out.values() for ko, _ in STOCK_FIELD):
        # 날짜와 종가는 있는데 투자자 구분이 한 행에도 없다 — 키가 바뀐 것이다.
        # 이대로 돌려주면 받는 쪽이 '성공' 으로 세고 값은 하나도 없다.
        raise Fetch(f'{code} 투자자 구분이 한 행에도 없다 — 응답 {len(js)}건, '
                    f'첫 건 키 {first_keys}')
    return dict(code=code, source=SOURCE, unit='주', by_date=out)


def to_amount(qty, close):
    """순매매량(주) × 종가 → 억원. **추정치다.**

    실제 순매수 금액은 체결가 가중이라 종가를 곱한 값과 다르다. 이 값을 쓰는
    필드에는 반드시 is_estimate 를 단다 (CLAUDE.md 2장 1번).
    """
    if qty is None or not close:
        return None
    return qty * close / 1e8


def probe():
    out = []
    for m in ('KOSPI', 'KOSDAQ'):
        try:
            r = fetch_market(m)
            n = len(r['by_date'])
            out.append((f'시장 수급 {m}', True,
                        f'{n}일 · 파라미터 "{r["params"]}" · '
                        f'구분 {len(r["columns"])}개 {",".join(r["columns"][:5])}'
                        + ('' if '기타법인' in r['columns']
                           else ' · 경고: 기타법인 컬럼 없음')))
        except Exception as ex:                     # noqa: BLE001
            out.append((f'시장 수급 {m}', False, str(ex)))
    try:
        r = fetch_stock('005930')
        out.append(('종목별 수급', True, f'005930 {len(r["by_date"])}일'))
    except Exception as ex:                         # noqa: BLE001
        out.append(('종목별 수급', False, str(ex)))
    return out
