#!/usr/bin/env python3
"""
네이버 금융 수집기 — 인증키 없이 쓸 수 있는 기본 소스.

여기서 받는 것
  1) 전 종목 목록 + 종가·등락률·시가총액·거래대금   (m.stock.naver.com 시가총액 목록)
  2) 종목별 일봉 OHLCV                              (api.finance.naver.com/siseJson.naver)
  3) 지수·환율                                       (m.stock.naver.com)
  4) 업종 등락률과 업종별 구성종목                    (finance.naver.com/sise/sise_group)

주의 — 이 소스는 공개 API 가 아니라 서비스 내부 엔드포인트다. 사이트 개편이면 깨진다.
그래서 (a) 응답 키를 pick() 으로 다중 후보 조회하고 (b) run.py --check 가 매 실행
전에 네 엔드포인트를 실제로 찔러보고 (c) 실패 시 무엇이 빠졌는지 리포트에 남긴다.
인증키 기반 대체 소스는 ingest/datago.py 참고.
"""
import ast
import html as H
import re

from .http import Fetch, get, num, pick, session

M_BASE = 'https://m.stock.naver.com/api'
FRONT = 'https://m.stock.naver.com/front-api'
SISE_JSON = 'https://api.finance.naver.com/siseJson.naver'
GROUP_URL = 'https://finance.naver.com/sise/sise_group.naver'
GROUP_DETAIL = 'https://finance.naver.com/sise/sise_group_detail.naver'

# 1억원 = 100백만원. 목록 API 의 거래대금 단위 환산에 쓴다.
MWON_PER_EOK = 100.0

SOURCE = 'naver'
MARKETS = ('KOSPI', 'KOSDAQ')


def _s():
    return session(referer='https://m.stock.naver.com/')


# ─────────────────────────── 1. 전 종목 시세 ───────────────────────────
def fetch_universe(page_size=100, max_pages=60, log=None):
    """시가총액 목록을 전 페이지 훑어 전 종목 스냅샷을 만든다.

    반환: {code: dict(...)}  — 값 단위는 아래 주석대로 정규화한다.
      close      원
      chg_pct    %
      mktcap     억원
      volume     주
      turnover   억원 (소스가 주면 그대로, 없으면 close*volume 로 추정하고 is_estimate)
    """
    s = _s()
    out = {}
    for market in MARKETS:
        # 페이지 훑기가 조용히 짧게 끝나는 사고가 있었다(D-051 — LIG넥스원이
        # 마스터에서 통째로 빠졌는데 아무 경고도 안 났다). 몇 페이지를 받아
        # 몇 행을 봤고 그중 몇이 중복이었는지 남긴다. 중복이 많다는 것은
        # 페이지 경계가 흔들린다는 뜻이고, 흔들리면 빠지는 행도 생긴다.
        pages = rows_seen = dup = 0
        for page in range(1, max_pages + 1):
            js = get(s, f'{M_BASE}/stocks/marketValue/{market}',
                     params={'page': page, 'pageSize': page_size})
            rows = pick(js, 'stocks', 'result', 'datas', default=[])
            if isinstance(rows, dict):
                rows = pick(rows, 'stocks', 'datas', default=[])
            if not rows:
                break
            pages += 1
            for x in rows:
                code = pick(x, 'itemCode', 'code', 'stockCode')
                if not code or not re.fullmatch(r'\d{6}', str(code)):
                    continue
                rows_seen += 1
                if str(code) in out:
                    dup += 1
                close = num(pick(x, 'closePrice', 'nowVal', 'price'))
                # 이 API 는 거래대금을 **백만원 단위**로 준다 (2026-08-28 실측).
                # 예: 실리콘투 110,338 = 1,103억. 시총 34,952억 대비 3.2% 회전으로
                # 앞뒤가 맞는다. 원 단위로 보고 1e8 로 나누면 0.0011 이 되어 화면에
                # '0억' 으로 찍히고, 유동성 하한에 전 종목이 걸려 랭킹 표가 빈다.
                # 못 받으면 None 으로 두고 engine 이 일봉에서 채우게 한다.
                # 0 으로 채우면 "거래 없음"과 "값 못 받음"이 구분되지 않는다.
                vol = num(pick(x, 'accumulatedTradingVolume', 'acc_trade_volume',
                               'quant', 'volume', 'tradingVolume'))
                tv = num(pick(x, 'accumulatedTradingValue', 'acc_trade_value',
                              'tradingValue', 'amount', 'amonut'))
                # **0 은 '안 줬다'는 뜻이다.** 이 API 는 키를 빼는 게 아니라 0 을
                # 채워 보낸다. 0 을 값으로 받아들이면 engine 의 폴백이 발동하지
                # 않아(0 은 None 이 아니므로) 전 종목 거래대금이 0 이 되고,
                # 유동성 하한에 전부 걸려 랭킹 표가 통째로 빈다.
                # 다만 정말 거래가 없었으면 0 이 사실이다 — 거래량으로 가른다.
                if tv == 0 and vol:
                    tv = None
                # 시가총액은 억원 단위로 온다 (실측: GS 114,447 = 11.4조).
                # 예전에는 '1e6 넘으면 원 단위'로 추정해 나눴는데, 그러면 시총
                # 100조 넘는 종목만 골라서 망가진다. 추정을 걷어내고 억원으로 고정한다.
                mv = num(pick(x, 'marketValue', 'marketSum', 'marketValueKrw'))
                out[str(code)] = dict(
                    code=str(code),
                    name=pick(x, 'stockName', 'itemname', 'name', default=''),
                    market=market,
                    close=close, chg_pct=num(pick(x, 'fluctuationsRatio', 'changeRate')),
                    volume=vol,
                    turnover=(tv / MWON_PER_EOK) if tv is not None else None,
                    mktcap=mv,
                    turnover_is_estimate=tv is None,
                    source=SOURCE)
            if len(rows) < page_size:
                break
        if log:
            n = sum(1 for v in out.values() if v['market'] == market)
            tail = f' / {pages}페이지 {rows_seen}행'
            if dup:
                tail += f' · 중복 {dup}행 — 페이지 경계가 흔들린다'
            if pages >= max_pages:
                tail += f' · max_pages({max_pages}) 에 닿음 — 더 있을 수 있다'
            log(f'   {market} {n}종목{tail}')
    if not out:
        raise Fetch('전 종목 목록이 비었다 — 엔드포인트 개편 의심')
    return out


# ─────────────────────────── 2. 일봉 OHLCV ───────────────────────────
def _parse_sise(text):
    """siseJson 은 JS 배열 리터럴로 온다.

    헤더: ['날짜','시가','고가','저가','종가','거래량','외국인소진율']
    값은 날짜 문자열과 숫자뿐이라 literal_eval 로 안전하게 읽는다.
    """
    s = (text or '').strip()
    if not s.startswith('['):
        return []
    try:
        rows = ast.literal_eval(s.replace("'", '"').replace('\n', '').replace('\t', ''))
    except Exception:
        return []
    out = []
    for r in rows[1:]:
        try:
            d = str(r[0])
            if len(d) != 8:
                continue
            out.append(dict(
                asof=f'{d[:4]}-{d[4:6]}-{d[6:8]}',
                open=float(r[1]), high=float(r[2]), low=float(r[3]),
                close=float(r[4]), volume=float(r[5])))
        except (IndexError, TypeError, ValueError):
            continue
    return out


def fetch_ohlcv(code, start, end, s=None):
    """일봉. start/end 는 'YYYY-MM-DD'. 오름차순 리스트를 돌려준다.

    수정주가 여부는 이 함수가 판단하지 않는다. 시계열 자체의 이상 점프는
    engine/newhigh.py 의 split_guard 가 잡는다 (CLAUDE.md 9장 미확정 1번).
    """
    s = s or _s()
    txt = get(s, SISE_JSON, want='text', params={
        'symbol': code, 'requestType': 1, 'timeframe': 'day',
        'startTime': start.replace('-', ''), 'endTime': end.replace('-', '')})
    rows = _parse_sise(txt)
    if not rows:
        raise Fetch(f'{code} 일봉 없음')
    rows.sort(key=lambda r: r['asof'])
    return rows


# ─────────────────────────── 3. 지수·환율 ───────────────────────────
def fetch_index(symbol, start, end, s=None):
    """지수 일봉. symbol 은 'KOSPI' / 'KOSDAQ'. siseJson 이 지수도 받아준다."""
    s = s or _s()
    txt = get(s, SISE_JSON, want='text', params={
        'symbol': symbol, 'requestType': 1, 'timeframe': 'day',
        'startTime': start.replace('-', ''), 'endTime': end.replace('-', '')})
    rows = _parse_sise(txt)
    if not rows:
        raise Fetch(f'{symbol} 지수 일봉 없음')
    rows.sort(key=lambda r: r['asof'])
    return rows


# 환율 엔드포인트 후보. 2026-08-28 실행에서 JSON 후보가 전부 실패했다(HTTP 400).
# front-api 는 서비스 내부용이라 예고 없이 바뀐다. 그래서 마지막 후보로 10년째
# 형태가 그대로인 marketindex HTML 을 둔다 — 느리지만 잘 안 깨진다.
FX_CANDIDATES = [
    (f'{FRONT}/marketIndex/prices',
     {'category': 'exchange', 'reutersCode': 'FX_USDKRW', 'page': 1}),
    (f'{FRONT}/marketIndex/productDetail',
     {'category': 'exchange', 'reutersCode': 'FX_USDKRW'}),
    ('https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/basicInfo', {}),
    ('https://api.stock.naver.com/marketindex/exchange/FX_USDKRW', {}),
    (f'{FRONT}/marketIndex/exchangeJson', {}),
]

FX_HTML = 'https://finance.naver.com/marketindex/'
# <ul id="exchangeList"> 첫 항목이 USD 다. value=현재가, change=전일 대비 '절댓값'.
# 방향은 클래스로만 온다(point_up / point_dn) — 부호를 여기서 붙여야 한다.
_FX_LI = re.compile(r'id="exchangeList".*?</li>', re.S)
_FX_VALUE = re.compile(r'class="value">([\d,.]+)<')
_FX_CHANGE = re.compile(r'class="change">\s*([\d,.]+)\s*<')
_FX_DIR = re.compile(r'class="head_info\s+(point_up|point_dn)')


def _fx_from_html(s):
    """marketindex 페이지에서 USD/KRW 를 긁는다. JSON 후보가 다 죽었을 때의 바닥."""
    html = get(s, FX_HTML, want='text', retries=1)
    block = _FX_LI.search(html)
    if not block:
        raise Fetch('exchangeList 를 못 찾았다 — 페이지 구조가 바뀌었다')
    seg = block.group(0)
    m = _FX_VALUE.search(seg)
    v = num(m.group(1)) if m else None
    if not v:
        raise Fetch('환율 값을 못 읽었다')
    chg = _FX_CHANGE.search(seg)
    direction = _FX_DIR.search(seg)
    pct = None
    if chg and direction:
        d = num(chg.group(1))
        if d is not None:
            sign = -1 if direction.group(1) == 'point_dn' else 1
            prev = v - sign * d               # 전일 종가
            if prev:
                pct = round(sign * d / prev * 100, 2)
    return dict(value=v, chg_pct=pct, asof=None,
                source=SOURCE, endpoint=FX_HTML)


def fetch_fx(s=None):
    """USD/KRW 매매기준율. 실패하면 예외 — 헤더 한 칸이 비는 것으로 끝난다."""
    s = s or _s()
    why = []
    for url, params in FX_CANDIDATES:
        try:
            js = get(s, url, params=params or None, retries=1)
        except Exception as ex:                     # noqa: BLE001
            why.append(f'{url.rsplit("/", 1)[-1]}: {ex}')
            continue
        rows = js
        for key in ('result', 'datas', 'prices', 'marketIndexInfos'):
            if isinstance(rows, dict) and key in rows:
                rows = rows[key]
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            why.append(f'{url.rsplit("/", 1)[-1]}: 응답이 비었다')
            continue
        x = rows[0]
        v = num(pick(x, 'closePrice', 'nv', 'value', 'basePrice'))
        if v:
            return dict(value=v,
                        chg_pct=num(pick(x, 'fluctuationsRatio', 'changeRate')),
                        asof=pick(x, 'localTradedAt', 'dt', 'date'),
                        source=SOURCE, endpoint=url)
        why.append(f'{url.rsplit("/", 1)[-1]}: 값 없음')

    try:
        return _fx_from_html(s)
    except Exception as ex:                         # noqa: BLE001
        why.append(f'HTML: {ex}')
    raise Fetch('환율 후보 전부 실패 — ' + ' / '.join(why))


# ─────────────────────────── 4. 업종 ───────────────────────────
# 업종 목록의 앵커. 예전 정규식은 `sise_group_detail.naver?type=upjong&no=NN"` 처럼
# 번호 **바로 뒤에 닫는 따옴표**가 오고 이름이 태그 없는 글자라고 봤다. 주소에
# 파라미터가 하나만 더 붙거나 따옴표가 작은따옴표면 한 건도 안 걸리고 목록이
# 통째로 빈다 — 2026-09-21 --check 가 '구조 변경 의심' 으로 끝난 것이 그것이다.
#
# 앵커를 먼저 뽑고 주소에서 번호를, 텍스트에서 이름을 따로 읽는다. 주소 모양이
# 조금 바뀌어도 살아남고, 그래도 한 건도 못 뽑으면 _sector_why 가 사유를 적는다.
_RE_ANCHOR = re.compile(
    r'<a\s[^>]*href=["\']([^"\']*sise_group_detail[^"\']*)["\'][^>]*>(.*?)</a>',
    re.S | re.I)
_RE_NO = re.compile(r'[?&]no=(\d+)')
_RE_UPJONG = re.compile(r'[?&]type=upjong(?:&|$)')
_RE_TAG = re.compile(r'<[^>]+>')


def _sector_rows(html):
    """업종 목록 HTML → [{no, name}]. 순서를 지키고 번호로 중복을 없앤다."""
    out, seen = [], set()
    for href, inner in _RE_ANCHOR.findall(html):
        # HTML 안의 주소는 `&` 가 `&amp;` 로 적혀 있는 경우가 있다. 풀지 않으면
        # 두 번째 파라미터부터 앞 글자가 `;` 라 `[?&]no=` 에 걸리지 않는다.
        href = H.unescape(href)
        if not _RE_UPJONG.search(href):
            continue
        m = _RE_NO.search(href)
        if not m:
            continue
        no = int(m.group(1))
        name = H.unescape(_RE_TAG.sub('', inner)).strip()
        if not name or no in seen:
            continue
        seen.add(no)
        out.append(dict(no=no, name=name))
    return out


def _sector_why(html):
    """한 건도 못 뽑은 이유. 러너를 한 번 더 돌리지 않고 고칠 수 있게 남긴다.

    '구조 변경 의심' 여섯 글자만 남기면 다음 사람이 페이지를 다시 받아 봐야 한다.
    무엇이 몇 번 나왔는지와 실제 주소 몇 개를 함께 적는다 (CLAUDE.md 2장 6번).
    """
    bits = [f'{len(html):,}자']
    for mark in ('sise_group_detail', 'type=upjong', '<a '):
        bits.append(f'{mark} {html.count(mark)}회')
    cand = [h for h in re.findall(r'href=["\']([^"\']{0,120})["\']', html)
            if 'upjong' in h or 'group' in h]
    bits.append('후보 주소 ' + (' | '.join(cand[:3]) if cand else '없음'))
    return ' · '.join(bits)


def fetch_sector_index(s=None):
    """업종 목록과 업종 등락률. 네이버 업종 분류 기준이다.

    CLAUDE.md 5장은 1층을 'KRX 업종분류'로 못박았다. 네이버 업종은 KRX 업종과
    이름이 대체로 겹치지만 동일 분류가 아니다. 그래서 반환 dict 에
    taxonomy='naver_upjong' 을 달아 두고, KRX 원본을 붙이면 그때 교체한다.
    """
    s = s or session(referer='https://finance.naver.com/sise/')
    html = get(s, GROUP_URL, params={'type': 'upjong'}, want='text')
    out = _sector_rows(html)
    if not out:
        raise Fetch('업종 목록 파싱 실패 — ' + _sector_why(html))
    return dict(taxonomy='naver_upjong', source=SOURCE, sectors=out)


_RE_MEMBER = re.compile(r'/item/main\.naver\?code=(\d{6})')


def fetch_sector_members(no, s=None):
    """업종 하나의 구성 종목코드. 종목 → 업종 역매핑을 만드는 데 쓴다."""
    s = s or session(referer='https://finance.naver.com/sise/')
    html = get(s, GROUP_DETAIL, params={'type': 'upjong', 'no': no}, want='text')
    codes = sorted(set(_RE_MEMBER.findall(html)))
    if not codes:
        raise Fetch(f'업종 {no} 구성종목 없음')
    return codes


# ─────────────────────────── 진단 ───────────────────────────
# 목록이 제대로 왔는지 확인할 표본. 코스피·코스닥의 대형주라 어느 날에도 빠질
# 이유가 없다. 종목수만 세면 "2,687종목 받았다" 로 정상처럼 보이는데, 실제로
# 2026-08-31 에 그 안에 LIG넥스원이 없었다 — 개수는 맞고 내용이 빠지는 경우가
# 있다는 뜻이다. 코드로 대조한다(이름은 사명 변경으로 흔들린다).
SPOT_CHECK = {
    '005930': '삼성전자', '000660': 'SK하이닉스', '012450': '한화에어로스페이스',
    '079550': 'LIG넥스원', '064350': '현대로템', '047810': '한국항공우주',
    '247540': '에코프로비엠', '196170': '알테오젠',
}


def probe():
    """run.py --check 가 부른다. 엔드포인트별 (이름, ok, 설명)."""
    out = []
    s = _s()
    try:
        u = fetch_universe(page_size=100, max_pages=1)
        out.append(('전 종목 목록', True, f'1페이지 {len(u)}종목 파싱'))
    except Exception as e:                            # noqa: BLE001
        out.append(('전 종목 목록', False, str(e)))
    try:
        full = fetch_universe()
        miss = {c: n for c, n in SPOT_CHECK.items() if c not in full}
        out.append((
            '표본 종목 확인', not miss,
            f'{len(full):,}종목 · 표본 {len(SPOT_CHECK)}개 중 '
            + (f'{len(miss)}개 없음: '
               + ', '.join(f'{n}({c})' for c, n in miss.items())
               if miss else '전부 있음')
            + (f" · 예: {full.get('079550', {}).get('name', '-')}" if not miss else '')))
    except Exception as e:                            # noqa: BLE001
        out.append(('표본 종목 확인', False, str(e)))
    for name, fn in (('일봉 OHLCV', lambda: fetch_ohlcv('005930', '2020-01-01', '2039-12-31', s)),
                     ('지수', lambda: fetch_index('KOSPI', '2020-01-01', '2039-12-31', s)),
                     ('환율', lambda: fetch_fx(s)),
                     ('업종 목록', lambda: fetch_sector_index())):
        try:
            r = fn()
            n = len(r) if isinstance(r, (list, tuple)) else len(r.get('sectors', [1]))
            out.append((name, True, f'{n}건'))
        except Exception as e:                        # noqa: BLE001
            out.append((name, False, str(e)))
    return out
