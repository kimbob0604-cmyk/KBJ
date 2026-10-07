#!/usr/bin/env python3
"""
HTTP 공통 계층 — 세션·재시도·동시성.

수집기가 소스마다 재시도 로직을 따로 갖지 않게 한 곳에 모은다.
CLAUDE.md 2장 6번(에러를 삼키지 않는다)에 따라 실패는 예외로 올리거나
빈 값 + 사유 문자열로 돌려주고, 조용히 None 을 반환하지 않는다.
"""
import concurrent.futures as cf
import time

import requests

TIMEOUT = 25
UA = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ko-KR,ko;q=0.9',
}


class Fetch(Exception):
    """수집 실패. 호출자가 부분 리포트로 갈지 중단할지 결정한다."""


def session(referer=None):
    s = requests.Session()
    s.headers.update(UA)
    if referer:
        s.headers['Referer'] = referer
    return s


# 쿼리 파라미터로 보내는 인증키 지우기(`scrub`)와 본문에서 사유 뽑기(`why`)는 KBJ 정본
# (`kbj.data.http`)을 다시 내보낸다(KBJ P2 — 설계 §1.11 두 벌 금지). 가린 자리는 `***` 다
# (옛 `<SECRET>`). 정규식 백트래킹 회귀(NoBacktrack)·사유 추출(Why) 시험은 kbj 쪽
# tests/unit/data/test_http.py 로 옮겼다.
from kbj.data.http import BODY_SCAN, BODY_SNIP, SECRET_PARAMS, scrub, why  # noqa: E402,F401


def get(s, url, params=None, retries=3, backoff=0.8, want='json', timeout=None):
    """재시도 포함 GET. want='json' 이면 파싱까지, 'text' 면 본문 그대로.

    `timeout` 을 주면 그 시도의 대기 상한(초)이 된다. 기본은 TIMEOUT(25초)이다 —
    종목마다 소스 넷을 도는 트리거 수집(ingest/triggers.py)은 전체 시간 예산이
    240초라 25초 × 재시도 3회를 종목마다 기다릴 수 없어 8초·2회로 줄여 부른다.
    """
    last = None
    timeout = TIMEOUT if timeout is None else timeout
    for i in range(retries):
        try:
            r = s.get(url, params=params, timeout=timeout)
        except Exception as ex:                      # noqa: BLE001 - 사유를 문자열로 보존
            last = scrub(f'{type(ex).__name__}: {ex}', params)
        else:
            if r.status_code == 200:
                if want == 'text':
                    return r.text
                try:
                    return r.json()
                except ValueError:
                    # 200 인데 JSON 이 아니다. 재시도해도 같은 게 온다.
                    # 파싱 예외만 올리면 본문의 사유가 사라지므로 붙여서 올린다.
                    raise Fetch(scrub(
                        f'{url} 200 이지만 JSON 이 아니다 — '
                        f'{why(r) or "본문 비어 있음"}', params)) from None
            # 본문이 비었다는 것도 사실이다. 조용히 두면 상태코드만 남아
            # "사유를 못 읽은 것" 과 "사유가 없는 것" 이 구분되지 않는다.
            last = f'HTTP {r.status_code} · {why(r) or "본문 없음"}'
        if i < retries - 1:
            time.sleep(backoff * (i + 1))
    raise Fetch(scrub(f'{url} 실패: {last}', params))


def gather(fn, items, workers=8, log=None, label='', on_result=None):
    """items 를 병렬로 fn 에 태운다. 개별 실패는 (item, 사유) 로 모아 돌려준다.

    on_result 를 주면 결과를 **들고 있지 않고** 콜백에 넘기고 버린다.
    전 종목 전 구간 일봉처럼 결과가 큰 경우에 필요하다 — 전부 모으면
    2,800종목 x 수천 봉이 메모리에서 십수 GB가 되어 러너가 죽는다.
    이때 반환되는 성공 목록에는 결과 대신 item 만 담긴다.

    반환: (성공 [(item, 결과 또는 None)], 실패 [(item, 사유)])
    한 종목이 실패했다고 전체 수집을 멈추지 않되, 무엇이 빠졌는지는 남긴다.
    """
    ok, bad = [], []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        done = 0
        for f in cf.as_completed(futs):
            it = futs[f]
            done += 1
            try:
                r = f.result()
            except Exception as e:                   # noqa: BLE001
                bad.append((it, f'{type(e).__name__}: {e}'))
                continue
            if on_result is None:
                ok.append((it, r))
                continue
            # 콜백이 터져도 그 항목만 실패로 잡고 나머지는 계속한다.
            # 여기서 예외가 새면 남은 수집이 통째로 날아간다 — 전 종목 적재를
            # 40분 돌리다 마지막에 잃는 일이 생긴다.
            try:
                on_result(it, r)
            except Exception as e:                   # noqa: BLE001
                bad.append((it, f'적재 실패 {type(e).__name__}: {e}'))
            else:
                ok.append((it, None))                # 결과는 버린다
            if log and done % 300 == 0:
                log(f'   {label} {done}/{len(items)} (실패 {len(bad)})')
    return ok, bad


def pick(d, *keys, default=None):
    """응답 키 이름이 소스 개편으로 바뀌는 것에 대비한 다중 키 조회.

    첫 번째로 존재하고 None 이 아닌 키의 값을 돌려준다.
    """
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v is not None and v != '':
            return v
    return default


def num(v, default=None):
    """'1,234' · '+1.23%' · 1234 를 float 로. 실패하면 default."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(',', '').replace('%', '').replace('+', '').strip()
    if s in ('', '-', 'N/A'):
        return default
    try:
        return float(s)
    except ValueError:
        return default
