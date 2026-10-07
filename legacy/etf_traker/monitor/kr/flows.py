#!/usr/bin/env python3
"""
국장 섹터 모니터 — 수급 (`stocks[*].fl`, `sectors[*].flow`).

화면의 수급 표는 외국인·기관·개인 **순매수 금액**을 1·5·20일 창으로 본다.

--------------------------------------------------------------------------
왜 KIS 만 쓰고 네이버는 폴백으로도 안 쓰는가
--------------------------------------------------------------------------
네이버 종목별 투자자 표(board/ingest/flows.py:fetch_stock)는 **수량(주)** 만
준다. 금액으로 바꾸려면 종가를 곱해야 하는데 실제 순매수 금액은 체결가 가중이라
값이 다르다 — board 는 그래서 `to_amount()` 결과에 `is_estimate` 를 단다
(CLAUDE.md 2장 1번).

이 화면의 수급 칸에는 '추정'을 표시할 자리가 없다. 수량을 금액 칸에 넣으면
추정치가 사실로 둔갑하므로, **금액을 못 받은 종목은 `fl` 을 아예 안 붙인다.**
화면은 `x.fl && x.fl[w]` 로 거르므로 그 종목이 표에서 빠질 뿐 깨지지 않는다.
몇 종목이 빠졌는지는 meta.flowCoverage 로 같이 싣는다.

--------------------------------------------------------------------------
호출량
--------------------------------------------------------------------------
KIS `stock_investor` 는 종목 하나당 한 번이고 최근 며칠치를 한꺼번에 준다.
유니버스 1,100종목이면 하루 1,100회다. MAX_CALLS 로 상한을 두고 시가총액 큰
순으로 채운다 — 예산이 모자라면 작은 종목부터 빠지지 화면이 서지는 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from board.ingest import kis  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, 'cache', 'flows.json')

# 화면이 쓰는 창(영업일).
WINDOWS = (1, 5, 20)

# 동반 순매수를 재는 창. 화면 툴팁이 '5일 기준' 이라고 못 박는다.
BOTH_WINDOW = '5'

# 한 회차 최대 호출 수. 넘으면 시총 큰 종목부터 채운다.
MAX_CALLS = 1200
WORKERS = 4


def load(path=CACHE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data, path=CACHE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


def _amounts(by_date):
    """KIS 응답 → {날짜: {f, o, p}} (억원). 금액이 아닌 날은 버린다."""
    out = {}
    for asof, rec in (by_date or {}).items():
        if rec.get('_unit') != '억원':
            continue
        f, o, p = rec.get('외국인'), rec.get('기관'), rec.get('개인')
        if f is None and o is None and p is None:
            continue
        out[asof.replace('-', '')] = dict(f=f or 0.0, o=o or 0.0, p=p or 0.0)
    return out


def windows(by_date, dates):
    """{날짜: {f,o,p}} → {'1': {...}, '5': {...}, '20': {...}, streak, lastDate}.

    dates 는 시장 전체의 영업일(오름차순). 종목이 쉰 날은 창에서 빠지고 n 이
    줄어든다 — 없는 날을 0 으로 세면 '그날 아무도 안 샀다' 가 된다.
    """
    have = [d for d in dates if d in by_date]
    if not have:
        return None
    out = {}
    for w in WINDOWS:
        span = have[-w:]
        out[str(w)] = dict(
            f=sum(by_date[d]['f'] for d in span),
            o=sum(by_date[d]['o'] for d in span),
            p=sum(by_date[d]['p'] for d in span),
            n=len(span))
    # 최근일부터 거슬러 외국인이 연속으로 순매수한 일수.
    #
    # 배포본에는 일별 계열이 없어 이 정의만은 **대조하지 못했다.** 화면 표의
    # 열 이름이 '연속'이고 값이 0~7 범위인 것과 맞고, 외국인 칸 옆에 붙는다는
    # 점에서 고른 정의다. 다른 뜻이면 여기만 고치면 된다.
    streak = 0
    for d in reversed(have):
        if by_date[d]['f'] > 0:
            streak += 1
        else:
            break
    out['streak'] = streak
    out['lastDate'] = have[-1]
    return out


def sector_flow(members):
    """구성종목 fl 을 합친다. 동반 비중은 5일 창에서 외국인·기관이 둘 다 순매수."""
    have = [m for m in members if m.get('fl')]
    if not have:
        return None
    out = {}
    for w in WINDOWS:
        k = str(w)
        rows = [m['fl'][k] for m in have if m['fl'].get(k)]
        out[k] = dict(f=sum(r['f'] for r in rows), o=sum(r['o'] for r in rows),
                      p=sum(r['p'] for r in rows), n=len(rows))
    both = sum(1 for m in have
               if m['fl'].get(BOTH_WINDOW)
               and m['fl'][BOTH_WINDOW]['f'] > 0 and m['fl'][BOTH_WINDOW]['o'] > 0)
    out['bothCount'] = both
    out['bothRatio'] = both / len(have) * 100.0
    out['n'] = len(have)
    return out


def collect(codes_by_cap, log=print, max_calls=MAX_CALLS):
    """종목별 일별 수급을 받아 캐시에 합친다. {code: {날짜: {f,o,p}}} 반환.

    codes_by_cap 은 시총 내림차순 종목코드. 예산이 모자라면 뒤쪽이 빠진다.
    실패는 종목 단위로 삼키고 로그만 남긴다 — 한 종목 때문에 수급 전체를
    버리지 않는다.
    """
    cache = load()
    by = cache.get('by_code') or {}
    todo = list(codes_by_cap)[:max_calls]
    got, failed = 0, 0

    def one(code):
        return code, kis.stock_flows(code)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(one, c) for c in todo]
            for fu in as_completed(futs):
                try:
                    code, res = fu.result()
                except Exception:  # noqa: BLE001 — 종목 단위 실패는 흔하다
                    failed += 1
                    continue
                amt = _amounts(res.get('by_date'))
                if amt:
                    by[code] = amt
                    got += 1
                else:
                    failed += 1
    except Exception as e:  # noqa: BLE001 — 토큰 발급 실패 등 전체 실패
        log(f'  수급 수집 중단: {e}')

    log(f'  수급: {got}종목 확보 · {failed}종목 실패/금액없음 (요청 {len(todo)})')
    if got:
        save({'source': 'KIS stock_investor', 'unit': '억원',
              'updated_at': dt.date.today().isoformat(), 'by_code': by})
    return by
