#!/usr/bin/env python3
"""
국장 섹터 모니터 — 수집.

공공데이터포털(data.go.kr)에서 '하루치 전 종목'을 받아 cache/daily/ 에 쌓는다.
board/ingest/datago.py 를 그대로 쓴다 — 인증키 형태 자동 판별, 재시도, 예외
메시지에서 키를 지우는 scrub 까지 이미 거기 있다. 같은 일을 두 번 구현하지
않는다.

하루치가 한 파일이고 한 번 받은 날은 다시 받지 않는다. 확정된 과거는 바뀌지
않기 때문이다. 다만 **최근 RECHECK_DAYS 일은 매번 다시 받는다** — 포털이 기준일
다음 영업일 13시 이후에 갱신하므로 처음 받은 값이 비어 있거나 부분일 수 있다.

주의 — 이 재조회 창은 board 가 D-056 에서 데인 부분과 성격이 다르다. 거기서는
소스가 기업행위 때 과거 전체를 재조정하는데 창 밖을 그대로 둬서 가짜 계단이
생겼다. 여기서는 종가가 아니라 등락률을 쓰므로 소급 재조정 자체가 없다. 창은
'아직 안 들어온 값'만 노린다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from board.ingest import datago, naver  # noqa: E402
from board.ingest.http import Fetch, session  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, 'cache', 'daily')

# 최근 이 일수는 캐시가 있어도 다시 받는다. 포털 갱신이 늦게 들어오기 때문이다.
RECHECK_DAYS = 5

# 화면이 1Y(252영업일)까지 쓰므로 여유를 두고 받는다.
HISTORY_DAYS = 400


def path_for(day):
    return os.path.join(CACHE, f'{day}.json')


def load_day(day):
    p = path_for(day)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        # 깨진 캐시는 없는 것으로 친다. 조용히 옛 값을 쓰느니 다시 받는다.
        return None


def save_day(day, rows):
    os.makedirs(CACHE, exist_ok=True)
    tmp = path_for(day) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path_for(day))


def slim(row):
    """캐시에 넣을 것만 남긴다. 400일치를 통째로 두면 레포가 감당 못 한다."""
    return dict(
        c=row['code'], n=row['name'], m=row['market'],
        p=row['close'], f=row['chg_pct'], v=row['volume'],
        # datago 는 억원으로 접어서 준다. 원으로 되돌려 화면 단위와 맞춘다.
        t=(row['turnover'] * 1e8) if row['turnover'] is not None else None,
        k=(row['mktcap'] * 1e8) if row['mktcap'] is not None else None,
        s=row['shares'])


def business_days(end, n):
    """end 에서 거슬러 올라가며 주말을 뺀 날짜 문자열 n 개. 내림차순."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime('%Y%m%d'))
        d -= dt.timedelta(days=1)
    return out


def collect(end=None, days=HISTORY_DAYS, recheck=RECHECK_DAYS, log=print):
    """캐시를 채우고 {날짜: [행]} 을 돌려준다.

    공휴일은 빈 리스트로 저장한다. 그래야 다음 실행이 다시 찌르지 않는다.
    한 날짜가 실패해도 나머지는 계속 간다 — 하루가 비면 그날만 빠지지 화면
    전체가 서지 않는다. 실패한 날짜는 모아서 돌려준다.
    """
    end = end or dt.date.today()
    wanted = business_days(end, days)
    s = session()
    fetched, failed, hit = 0, [], 0

    for i, day in enumerate(wanted):
        cached = load_day(day)
        fresh_needed = i < recheck  # 최근 며칠은 캐시가 있어도 다시
        if cached is not None and not fresh_needed:
            hit += 1
            continue
        try:
            rows = datago.fetch_day(f'{day[:4]}-{day[4:6]}-{day[6:8]}', s=s)
        except Fetch as e:
            # 키 문제·한도 초과는 여기서 뭉개고 넘어가면 안 된다. 하지만 하루
            # 실패로 전체를 세우지도 않는다. 모아서 위로 올린다.
            failed.append((day, str(e)))
            continue
        save_day(day, [slim(r) for r in rows])
        fetched += 1

    log(f'수집: 새로 {fetched}일 · 캐시 {hit}일 · 실패 {len(failed)}일')
    for day, err in failed[:5]:
        log(f'  실패 {day}: {err}')

    out = {}
    for day in wanted:
        rows = load_day(day)
        if rows:  # 빈 날(공휴일)은 넣지 않는다
            out[day] = rows
    return out, failed


# 지수는 공공데이터포털이 아니라 네이버에서 받는다. 포털의 지수 API 는 이
# 계정에서 403 이고 별도 활용신청이 필요하다(기능목록.md '아직 안 된 것').
# 보드도 같은 이유로 네이버를 쓴다.
INDEX_FILE = os.path.join(ROOT, 'cache', 'indices.json')


def collect_indices(start, end, log=print):
    """코스피·코스닥 일봉. 하나가 실패해도 나머지는 살린다.

    화면은 S.indices['코스피'].r 을 **가드 없이** 읽는다. 여기가 비면 페이지가
    통째로 죽으므로, 새로 못 받으면 지난 캐시라도 돌려준다.
    """
    out = {}
    for name, symbol in (('코스피', 'KOSPI'), ('코스닥', 'KOSDAQ')):
        try:
            rows = naver.fetch_index(symbol, start, end)
            out[name] = [dict(d=r['asof'].replace('-', ''), c=r['close']) for r in rows]
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 화면을 세우지 않는다
            log(f'  지수 {name} 실패: {e}')

    if out:
        os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
        prev = {}
        if os.path.exists(INDEX_FILE):
            try:
                with open(INDEX_FILE, encoding='utf-8') as f:
                    prev = json.load(f)
            except (OSError, ValueError):
                prev = {}
        prev.update(out)
        tmp = INDEX_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(prev, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, INDEX_FILE)
        return prev

    if os.path.exists(INDEX_FILE):
        log('  지수를 새로 못 받아 지난 캐시를 쓴다')
        with open(INDEX_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


# ─────────────────────────── 당일 잠정치 ───────────────────────────
#
# 공공데이터포털은 기준일 **다음 영업일 13시 이후**에 갱신한다. 그래서 확정
# 데이터만 쓰면 화면의 최신 거래일이 보통 전전 영업일이다.
#
# 네이버 시가총액 목록은 전 종목을 몇 번의 호출로 준다(board/ingest/naver.py
# :fetch_universe). 그 스냅샷을 **확정치 뒤에 하루 더** 얹어 하루를 당긴다.
#
# 얹은 날은 확정치가 아니다. meta.provisional 에 날짜·시각·출처·거래종목수를
# 실어 화면이 '당일 잠정' 배지와 각주로 그 사실을 밝히게 한다. 조용히 확정치인
# 척하지 않는다.
PROVISIONAL_SOURCE = '네이버 금융'


def collect_provisional(confirmed_through, log=print):
    """오늘(또는 확정일 다음) 전 종목 스냅샷. 실패하면 None — 확정치만 쓴다.

    돌려주는 것: (날짜, [행], 거래종목수) 또는 None.
    행 모양은 cache/daily 와 같다.
    """
    import datetime as dt
    try:
        uni = naver.fetch_universe(log=None)
    except Exception as e:  # noqa: BLE001
        log(f'  잠정치 실패: {e}')
        return None
    if not uni:
        return None

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    day = now.strftime('%Y%m%d')
    if day <= (confirmed_through or ''):
        # 포털이 이미 오늘치를 확정해 줬다. 얹을 이유가 없다.
        return None

    rows, traded = [], 0
    for code, x in uni.items():
        close = x.get('close')
        if close is None:
            continue
        vol = x.get('volume') or 0
        if vol:
            traded += 1
        rows.append(dict(
            c=code, n=x.get('name'), m=x.get('market'),
            p=close, f=x.get('chg_pct'), v=vol,
            # naver 는 억원으로 준다. 캐시는 원 단위다.
            t=(x['turnover'] * 1e8) if x.get('turnover') is not None else None,
            k=(x['mktcap'] * 1e8) if x.get('mktcap') is not None else None,
            s=x.get('shares')))
    if not rows:
        return None
    log(f'  잠정치: {day} {len(rows)}종목 (거래 {traded})')
    return day, rows, traded
