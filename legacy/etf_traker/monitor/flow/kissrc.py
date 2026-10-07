#!/usr/bin/env python3
"""
수급 원자료 — KIS (개인·외국인·기관 3구분).

KRX 가 이 실행 환경을 막고 있어(README 의 표) 기관 7구분을 못 받는다. 그래서
KIS 로 **3구분만** 받아 리포트를 낸다. 워크북의 기관 세부 차트와 기관 구분 표는
빠지고, 나머지(누적·가격·일별 막대·문장)는 그대로 나온다.

--------------------------------------------------------------------------
단위를 믿지 않고 확인한다
--------------------------------------------------------------------------
KIS 의 `prsn_ntby_tr_pbmn` 계열이 **백만원** 이라는 것은 board/ingest/kis.py 가
그렇게 적어 둔 것이고 실호출로 확정된 적이 없다. 자릿수가 틀리면 리포트의 모든
숫자가 100만 배로 틀리는데, 그건 '틀린 숫자를 사실처럼 보내는' 바로 그 경우다.

그래서 받은 금액을 **수량 x 종가**와 대조한다. 순매수 금액은 체결가 가중이라
정확히 같지는 않지만 **자릿수는 같아야 한다.** 배수가 RATIO_LO~RATIO_HI 를
벗어나면 단위를 잘못 안 것으로 보고 올린다 — 그 종목은 건너뛴다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from board.ingest import kis as K  # noqa: E402
from board.ingest.http import Fetch, num, pick  # noqa: E402

SOURCE = 'kis'

# KIS 가 주는 3구분. 우리 이름으로 맞춘다 — 화면 표의 '기관합계' 자리다.
WHO = (('개인', 'person_amt', 'person'),
       ('외국인', 'foreign_amt', 'foreign'),
       ('기관합계', 'institution_amt', 'institution'))

# 금액 필드가 백만원이라는 가정. 원으로 편다.
AMT_SCALE = 1_000_000

# 수량 x 종가 대비 허용 배수. 체결가 가중이라 1 에 딱 떨어지지 않지만
# 자릿수가 어긋나면 이 밖으로 나간다.
RATIO_LO, RATIO_HI = 0.2, 5.0

# 자릿수 대조에 쓸 최소 규모(원). 너무 작은 날은 비율이 튄다.
RATIO_FLOOR = 100_000_000


def fetch_daily(code, days=20):
    """종목 하나의 일별 3구분 순매수. (금액{원}, 수량{주}, KIS 종가) 를 돌려준다.

    돌려주는 모양은 KRX 경로와 같다 — {'YYYYMMDD': {'개인': 값, ...}}.
    analyze 가 원/주 단위를 그대로 받으므로 여기서 억원으로 접지 않는다.
    """
    rows = K.call('stock_investor', code)
    amt, qty, closes = {}, {}, {}
    for r in rows:
        d = str(pick(r, *K.FIELD['date'], default='') or '')
        if len(d) != 8:
            continue
        c = num(pick(r, *K.FIELD['close']))
        if c:
            closes[d] = c
        for who, amt_key, qty_key in WHO:
            a = num(pick(r, *K.FIELD[amt_key]))
            q = num(pick(r, *K.FIELD[qty_key]))
            if a is not None:
                amt.setdefault(d, {})[who] = a * AMT_SCALE
            if q is not None:
                qty.setdefault(d, {})[who] = q
    if not amt:
        raise Fetch(f'{code} KIS 일별 수급에 금액이 없다 (행 {len(rows)})')
    return dict(sorted(amt.items())[-days:] if days else amt.items()), \
        dict(sorted(qty.items())[-days:] if days else qty.items()), closes


def unit_check(amt, qty, closes, dates=None):
    """금액이 수량 x 종가와 자릿수가 같은가. (라벨, 배수, 통과여부).

    KRX 기간합계 검산을 대신하지는 못한다. 그건 '일별 합이 기간합계와 맞는가'
    였고 이건 '단위를 잘못 알지 않았는가' 다. 둘은 다른 것을 본다.
    """
    dates = dates or sorted(amt)
    got = ref = 0.0
    for d in dates:
        c = closes.get(d)
        if not c:
            continue
        for who, _, _ in WHO:
            a = (amt.get(d) or {}).get(who)
            q = (qty.get(d) or {}).get(who)
            if a is None or q is None:
                continue
            got += abs(a)
            ref += abs(q) * c
    if ref < RATIO_FLOOR or got <= 0:
        return ('금액 자릿수 대조(수량x종가)', None, False)
    ratio = got / ref
    return ('금액 자릿수 대조(수량x종가)', round(ratio, 3),
            RATIO_LO <= ratio <= RATIO_HI)


def probe(code='005930'):
    """--check 가 부른다. 토큰과 일별 수급을 실제로 찔러 본다."""
    out = []
    try:
        K.token()
        out.append(('KIS 토큰', True, '발급/캐시 확인'))
    except Exception as e:  # noqa: BLE001
        out.append(('KIS 토큰', False, str(e)[:200]))
        return out
    try:
        amt, qty, closes = fetch_daily(code)
        out.append(('KIS 일별 3구분', True,
                    f'{len(amt)}일 · 종가 {len(closes)}일'))
        out.append(unit_check(amt, qty, closes))
    except Exception as e:  # noqa: BLE001
        out.append(('KIS 일별 3구분', False, str(e)[:200]))
    return out
