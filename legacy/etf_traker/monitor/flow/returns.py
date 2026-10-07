#!/usr/bin/env python3
"""
수익률 — 일간 · 1개월 · 연초 대비.

한 종목의 일봉(네이버 수정주가)만 있으면 계산된다. 여기서 정하는 것은 **무엇을
기준으로 재는가** 하나다. 기준이 흔들리면 같은 숫자가 매일 다른 뜻이 된다.

  일간     직전 거래일 종가 대비 (종가끼리)
  장중 고가 직전 거래일 종가 대비 **그날 고가**. 보드의 신고가 판정이 고가
           기준이라(settings.yaml default_basis: high) 이 값이 있어야 '일간은
           마이너스인데 신고가' 가 설명된다 — 장중에 뚫고 종가는 밀린 날이다
  1개월    달력으로 한 달 전, 그날 또는 그 이전의 마지막 거래일 종가 대비
           (21거래일이 아니라 달력 기준이다. 휴장이 낀 달에도 '한 달 전'이
            사람이 말하는 한 달 전과 같아진다)
  연초     **작년 마지막 거래일** 종가 대비. 올해 첫 거래일 종가로 재면 1월
           2일의 등락이 통째로 빠진다 — 증권사 YTD 도 작년 종가를 쓴다

못 재는 것은 None 으로 두고 왜인지 적는다. 예를 들어 올해 상장한 종목은 작년
종가가 없으므로 연초 대비를 내지 않는다. 대신 상장 후 수익률을 슬쩍 끼워 넣으면
받는 사람은 그것을 연초 대비로 읽는다 (CLAUDE.md 2장 1번).
"""
from __future__ import annotations

import datetime as dt


def _date(asof):
    """'YYYY-MM-DD' 도 'YYYYMMDD' 도 받는다."""
    s = str(asof).replace('-', '')
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _minus_month(d):
    """한 달 전 같은 날. 없는 날짜(3/31 → 2/31)는 그 달의 마지막 날로."""
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    last = [31, 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def close_on_or_before(rows, day):
    """그날 또는 그 이전의 마지막 거래일. 없으면 None."""
    hit = [r for r in rows if _date(r['asof']) <= day and r.get('close')]
    return hit[-1] if hit else None


def _pct(now, before):
    if not before or before <= 0 or now is None:
        return None
    return round((now / before - 1) * 100, 2)


def compute(rows):
    """일봉 목록(오름차순) → 수익률.

    반환 `{'daily': {...}, 'mom': {...}, 'ytd': {...}}`. 각 항목은
    `{'pct': 값|None, 'base': 기준일|None, 'why': 못 잰 사유|None}` 이다.
    기준일을 같이 돌려주는 이유는, 화면에 '한 달 전 대비' 라고만 적으면 어느
    날과 견줬는지 확인할 방법이 없기 때문이다.
    """
    rows = [r for r in (rows or []) if r.get('close')]
    out = {k: dict(pct=None, base=None, why='일봉이 없다')
           for k in ('daily', 'high', 'mom', 'ytd')}
    if len(rows) < 2:
        return out

    last = rows[-1]
    now, today = last['close'], _date(last['asof'])

    prev = rows[-2]
    out['daily'] = dict(pct=_pct(now, prev['close']), base=prev['asof'], why=None)
    out['high'] = (dict(pct=_pct(last.get('high'), prev['close']),
                        base=prev['asof'], why=None) if last.get('high')
                   else dict(pct=None, base=None, why='고가가 없다'))

    m = close_on_or_before(rows[:-1], _minus_month(today))
    out['mom'] = (dict(pct=_pct(now, m['close']), base=m['asof'], why=None) if m
                  else dict(pct=None, base=None, why='한 달 전 일봉이 없다'))

    y = close_on_or_before(rows[:-1], dt.date(today.year - 1, 12, 31))
    out['ytd'] = (dict(pct=_pct(now, y['close']), base=y['asof'], why=None) if y
                  else dict(pct=None, base=None,
                            why='작년 종가가 없다(올해 상장 등)'))
    return out
