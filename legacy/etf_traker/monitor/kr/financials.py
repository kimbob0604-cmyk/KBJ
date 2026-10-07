#!/usr/bin/env python3
"""
국장 섹터 모니터 — 재무 (`state.fin`).

종목명에 마우스를 올리면 뜨는 카드: 최근 3개 연간 + 최근 분기(3개월 단독)와
그 YoY·QoQ. 단위는 억원.

수집은 `board/ingest/financials.py` 를 그대로 쓴다. 거기 이미 들어 있는 것 —
  - fnlttMultiAcnt 로 100개씩 묶어 호출
  - 손익 누적 → 분기 환산. **회사가 적은 3개월값이 우선**이고 차분은 폴백이다
    (두 보고서 사이 정정을 타서 실데이터 163종목 중 8종목이 1% 넘게 어긋났다)
  - 연결(CFS) 없으면 별도(OFS) 폴백, 어느 쪽인지 fs_div 로 남김
  - 우선주는 DART corp_code 가 없어 같은 회사 보통주 재무를 쓴다
같은 일을 두 번 구현하지 않는다. 여기서는 그 산출물을 화면 계약으로 옮기기만 한다.

화면 계약(배포본에서 확인):
    fin.byCode[code] = {fs, a: {연도: [매출, 영업이익, 순이익]},
                        q: {cur: [...], yoy: [...], qoq: [...]}}
`a`·`q` 는 없으면 키 자체를 넣지 않는다. 화면이 `rec.q` 로 갈라 '분기 실적 없음'
을 찍는다 — 0 으로 채우면 '적자도 흑자도 아닌 0원'이 된다.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from board.ingest import financials as F  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, 'cache', 'financials.json')
SOURCE = 'OpenDART fnlttMultiAcnt (주요계정)'

# 화면 카드에 싣는 연간 개수.
ANNUAL_YEARS = 3

# board/config/settings.yaml 의 financials 블록과 같은 값. 여기서 다시 적는 이유는
# 보드 설정을 통째로 읽어오면 이 모듈이 보드 설정 파일에 묶이기 때문이다.
CFG = {'financials': {
    'annual_years': ANNUAL_YEARS, 'quarters': 4, 'stale_days': 7,
    'batch_size': 100, 'pause_sec': 0.3, 'workers': 3, 'fs_div': 'CFS'}}

VALS = ('rev', 'op', 'ni')


def _triple(row):
    """{rev, op, ni} → [매출, 영업이익, 순이익]. 셋 다 None 이면 None."""
    if not row:
        return None
    out = [row.get(k) for k in VALS]
    return out if any(v is not None for v in out) else None


def _q_key(row):
    return (row['year'], int(str(row['q'])[0]))


def _prev_q(year, q):
    return (year - 1, 4) if q == 1 else (year, q - 1)


def build(by_code, asof=None):
    """board 재무 캐시 → 화면의 `fin`. 받은 게 없으면 None.

    None 을 돌려주는 것이 중요하다. 빈 dict 는 자바스크립트에서 참이라
    `S.fin ? ... : '미적용'` 가드를 통과한 뒤 S.fin.years 에서 죽는다.
    """
    if not by_code:
        return None

    years = sorted({r['year'] for rec in by_code.values()
                    for r in (rec.get('annual') or []) if _triple(r)})[-ANNUAL_YEARS:]

    # 최신 분기는 '가장 많은 종목이 제출한 분기' 로 정한다. 한 회사가 일찍 낸
    # 다음 분기를 기준으로 잡으면 나머지 전부가 '실적 없음' 이 된다.
    tally = {}
    for rec in by_code.values():
        for row in (rec.get('quarterly') or []):
            if _triple(row):
                tally[_q_key(row)] = tally.get(_q_key(row), 0) + 1

    if tally:
        best = max(tally.values())
        # 동률이면 더 최근 분기. 제출이 한창일 때 직전 분기와 표본이 비슷해진다.
        cur_y, cur_q = max(k for k, v in tally.items() if v >= best * 0.8)
    elif years:
        # 분기를 아무도 안 냈어도 연간만으로 카드는 뜬다. 화면이 F.quarter.label 을
        # '… 분기 실적 없음' 문구에 쓰므로 자리는 채워야 한다. 실제로 있는 연도에서
        # 뽑으니 지어낸 값은 아니다.
        cur_y, cur_q = years[-1], 4
    else:
        return None
    prev_y, prev_q = _prev_q(cur_y, cur_q)

    out = {}
    for code, rec in by_code.items():
        a = {}
        for row in (rec.get('annual') or []):
            if row['year'] in years and _triple(row):
                a[str(row['year'])] = _triple(row)

        qs = {_q_key(r): r for r in (rec.get('quarterly') or [])}
        cur = _triple(qs.get((cur_y, cur_q)))
        entry = {'fs': rec.get('fs_div') or 'CFS'}
        if a:
            entry['a'] = a
        if cur:
            q = {'cur': cur}
            yoy = _triple(qs.get((cur_y - 1, cur_q)))
            qoq = _triple(qs.get((prev_y, prev_q)))
            if yoy:
                q['yoy'] = yoy
            if qoq:
                q['qoq'] = qoq
            entry['q'] = q
        # 시총만 있어도 카드는 뜬다. 하지만 재무가 하나도 없으면 넣지 않는다 —
        # 화면이 'DART 재무 데이터가 없습니다' 로 갈라 준다.
        if a or cur:
            out[code] = entry

    if not out:
        return None
    return {
        'unit': '억원',
        'years': years,
        'quarter': {'year': cur_y, 'q': cur_q, 'label': f'{cur_q}Q{str(cur_y)[2:]}'},
        'prevQuarter': {'year': prev_y, 'q': prev_q, 'label': f'{prev_q}Q{str(prev_y)[2:]}'},
        'source': SOURCE,
        'generatedAt': (asof or dt.datetime.now(dt.timezone.utc)).isoformat(),
        'byCode': out,
    }


def collect(codes, log=print, force=False):
    """DART 에서 받아 캐시에 합치고 화면용 `fin` 을 돌려준다.

    실패해도 예외를 올리지 않는다 — 재무가 없다고 화면 전체를 세우지 않는다.
    받은 게 없으면 None 이고, 화면은 '미적용' 으로 간다.
    """
    try:
        n = F.refresh(list(codes), CFG, log=log, path=CACHE, force=force)
        log(f'  재무: {n}종목 갱신')
    except Exception as e:  # noqa: BLE001 — 키 없음·한도 초과·스키마 변경 전부
        log(f'  재무 수집 실패: {e}')
    cache = F.load(CACHE)
    return build(cache.get('by_code') or {})
