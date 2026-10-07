#!/usr/bin/env python3
"""
ETF·ETN 판별 — 신고가 보드에서 빼야 할 것들.

2026-08-27 첫 실데이터 실행에서 드러난 문제다. 전 종목 3,924개 중 약 1,200개가
ETF·ETN 이었고, **역사적 신고가 49종목 중 44개가 채권형 ETF** 였다.

  KODEX CD금리액티브(합성) · TIGER KOFR금리액티브(합성) · SOL 머니마켓액티브 …

CD금리·KOFR·머니마켓·단기채 ETF 는 구조적으로 매일 우상향한다. 이자가 쌓이니
당연히 매일 사상 최고가다. 그건 신고가가 아니라 이자고, 표에 올라오면 진짜
신고가를 밀어낸다. 실제로 그날 역사적 신고가 표의 90%가 이것이었다.

## 판별 방법 두 가지

1. **ETF 는 목록으로 정확히 거른다.** 네이버 etfItemList 가 전 상장 ETF 코드를
   한 번에 준다. 같은 레포의 etf_tracker_v9 가 이미 쓰고 있는 엔드포인트다.
   이름 추측이 아니라 실제 목록이라 확실하다.
2. **ETN 은 이름으로 거른다.** ETN 전용 목록 엔드포인트를 못 찾았다.
   ETN 은 상품명에 'ETN' 이 반드시 들어가므로 그걸로 잡는다.

둘 다 실패하면 이름 패턴으로 떨어진다. 패턴은 마지막 수단이고, 놓치는 것이
있을 수 있어 걸러낸 수를 리포트에 남긴다.

**근본 해결은 KRX 오픈API 전환이다.** `stk_bydd_trd` 는 주식만 준다.
DECISIONS.md D-011 참고.
"""
import re

from .http import Fetch, get, session

ETF_LIST = 'https://finance.naver.com/api/sise/etfItemList.nhn'

# 운용사 브랜드 접두사. **ETF 목록을 못 받았을 때만** 쓰는 폴백이다.
#
# 반드시 뒤에 공백이 와야 한다. ETF 상품명은 예외 없이 'KODEX 200',
# 'WON 국공채머니마켓액티브' 처럼 브랜드와 나머지가 띄어져 있다.
# 공백을 요구하지 않으면 BNK금융지주·파워로직스·파워넷·WONIK 같은 **실제 주식**이
# 유니버스에서 빠진다. 실측으로 확인한 오탐이다.
BRANDS = (
    'KODEX', 'TIGER', 'RISE', 'ACE', 'PLUS', 'SOL', 'HANARO', 'KOSEF', 'KBSTAR',
    'ARIRANG', 'TIMEFOLIO', 'KIWOOM', 'TREX', 'ITF', 'WON', 'VITA', 'FOCUS',
    'UNICORN', 'BNK', 'DAISHIN', 'TRUE', 'QV', '1Q', 'N2', 'AITF',
    '히어로즈', '마이다스', '네비게이터', '에셋플러스', '파워', '마이티',
)
_BRAND = re.compile(r'^(?:' + '|'.join(re.escape(b) for b in BRANDS) + r')\s', re.I)

# 상품 유형 표시. 종목명 어디에 있어도 펀드로 본다. 주식명에는 안 쓰이는 말들이다.
MARKERS = ('(합성', '액티브', '레버리지', '인버스')

# ETN 은 상품명에 반드시 들어간다. ETF 목록과 무관하게 **항상** 적용한다.
#
# `\b` 를 쓰면 안 된다. 파이썬 정규식에서 한글은 낱말 문자라 '단일종목ETN' 의
# '목' 과 'E' 사이에는 경계가 생기지 않는다. 그래서 띄어쓰기 없이 붙는 상품명이
# 통째로 빠져나갔다 — 2026-09-03 실측에서 520101 '미래에셋 레버리지 SK하이닉스
# 단일종목ETN' 이 주식 유니버스에 들어와 있었다. 레버리지 ETN 이 신고가 표에
# 오르는 것은 이 파일을 쓴 이유 그 자체다(채권형 ETF 44종목 사건).
#
# 라틴 글자·숫자에 붙은 것만 걸러낸다. 'ETNA' 같은 낱말이 우연히 걸리지 않게.
_ETN = re.compile(r'(?<![A-Za-z0-9])ETN(?![A-Za-z0-9])', re.I)
_ETF_WORD = re.compile(r'(?<![A-Za-z0-9])ETF(?![A-Za-z0-9])', re.I)


def fetch_etf_codes(s=None):
    """상장 ETF 코드 집합. 실패하면 예외 — 호출자가 폴백을 결정한다."""
    s = s or session(referer='https://finance.naver.com/sise/etf.naver')
    js = get(s, ETF_LIST, retries=3)
    rows = (js or {}).get('result', {}).get('etfItemList') or []
    codes = {str(x['itemcode']) for x in rows if x.get('itemcode')}
    if not codes:
        raise Fetch('ETF 목록이 비었다 — 엔드포인트 개편 의심')
    return codes


def is_etn(name):
    """ETN 인지. 상품명에 반드시 'ETN' 이 들어간다. 항상 적용한다."""
    return bool(name) and bool(_ETN.search(str(name)))


def looks_like_fund(name):
    """이름만으로 펀드인지. **ETF 목록을 못 받았을 때의 폴백이다.**

    브랜드 접두사는 뒤에 공백을 요구한다. 안 그러면 BNK금융지주·파워로직스처럼
    실제 주식이 걸린다. 이 판정에 걸린 종목 수를 리포트에 남겨서 사람이
    확인할 수 있게 한다.
    """
    if not name:
        return False
    n = str(name)
    if is_etn(n) or _ETF_WORD.search(n) or _BRAND.match(n):
        return True
    u = n.upper()
    return any(m.upper() in u for m in MARKERS)


def split(universe, etf_codes=None):
    """유니버스를 (주식, 펀드) 로 나눈다.

    etf_codes 를 받았으면 **ETF 판정은 그 목록으로만** 한다. 목록이 실제
    상장 ETF 라 추측보다 정확하고, 이름 판정을 함께 돌리면 실제 주식이 빠진다.
    ETN 은 별도 목록이 없으므로 언제나 이름으로 거른다.

    반환 (stocks, funds, how) — how 는 무엇으로 걸렀는지 사람이 읽는 설명.
    이름 판정으로 걸린 종목은 오탐일 수 있으므로 목록을 함께 남긴다.
    """
    stocks, funds = {}, {}
    by_code, by_name = 0, []
    for code, x in universe.items():
        name = x.get('name')
        if etf_codes is not None and code in etf_codes:
            funds[code] = x
            by_code += 1
        elif is_etn(name) or (etf_codes is None and looks_like_fund(name)):
            funds[code] = x
            by_name.append(name)
        else:
            stocks[code] = x
    if etf_codes is not None:
        how = f'ETF 목록 {by_code:,}종목 + ETN 이름 {len(by_name):,}종목'
    else:
        how = f'ETF 목록 미확보 · 이름 판정 {len(by_name):,}종목'
    return stocks, funds, dict(text=how, by_code=by_code, by_name=sorted(by_name))
