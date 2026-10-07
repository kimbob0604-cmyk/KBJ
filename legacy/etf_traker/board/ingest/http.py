#!/usr/bin/env python3
"""
HTTP 공통 계층 — 세션·재시도·동시성.

수집기가 소스마다 재시도 로직을 따로 갖지 않게 한 곳에 모은다.
CLAUDE.md 2장 6번(에러를 삼키지 않는다)에 따라 실패는 예외로 올리거나
빈 값 + 사유 문자열로 돌려주고, 조용히 None 을 반환하지 않는다.
"""
import concurrent.futures as cf
import re
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


# 쿼리 파라미터로 보내는 인증키. 예외 메시지에 URL 이 통째로 실려 오므로
# 사유를 만들기 전에 값을 지운다. DART 는 crtfc_key, 공공데이터포털은 serviceKey 다.
SECRET_PARAMS = ('crtfc_key', 'serviceKey', 'apiKey', 'auth_key', 'AUTH_KEY',
                 'key', 'token', 'access_token', 'appkey', 'appsecret')


def scrub(msg, params=None):
    """예외 메시지에서 자격증명을 지운다.

    requests 의 연결 예외 메시지에는 **쿼리스트링이 포함된 URL** 이 들어 있다.
    그대로 사유로 올리면 --check 출력과 run_log 에 인증키가 남는다.
    """
    out = str(msg)
    for k in SECRET_PARAMS:
        v = (params or {}).get(k)
        if v:
            out = out.replace(str(v), '<SECRET>')
    # 파라미터를 못 받은 경로에서도 URL 안의 키=값 형태를 잘라낸다.
    return re.sub(r'((?:' + '|'.join(SECRET_PARAMS) + r')=)[^&\s\'"]+',
                  r'\1<SECRET>', out, flags=re.I)


BODY_SNIP = 300
# 정규식에 물리는 본문 길이 상한. 에러 봉투는 사유를 맨 앞에 담으므로 뒤를
# 볼 이유가 없고, 상한이 없으면 큰 본문에서 탐색이 길어진다.
BODY_SCAN = 20_000

# 서버는 사유를 **본문**에 담아 보낸다. 상태코드만 올리면 "HTTP 403" 이 되어
# 무엇을 해야 하는지 알 수 없다 — 2026-09-01 공공데이터포털 403 이 정확히
# 그랬다. 인증키는 붙었는데 로그에는 숫자 403 뿐이라 사유를 못 읽었다.
# CLAUDE.md 2장 6번(에러를 삼키지 않는다)은 상태코드가 아니라 사유를 요구한다.
#
# 값 자리는 `[^<\]]*` 하나뿐이다. 같은 구간을 훑는 수량자를 겹치면 안 된다.
#
#   `(.*?)`                  닫히지 않은 여는 태그가 여러 개인 본문에서 시작
#                            위치마다 남은 본문을 끝까지 훑어 O(n²).
#                            `'<message>' * 20000` 에 물려 돌아오지 않았다.
#   `\s*(...)[^<\]]*(...)\s*`  앞뒤 `\s*` 가 값 자리와 공백을 나눠 갖는 경우의
#                            수를 만든다. `'<message>' + ' ' * 300000` 에서
#                            같은 구간을 세 수량자가 겹쳐 훑어 다시 멈췄다.
#
# 값의 앞뒤 공백은 정규식이 아니라 아래에서 strip 으로 턴다.
_ERR_TAG = re.compile(
    r'<(returnAuthMsg|returnReasonCode|errMsg|resultMsg|resultCode|message)>'
    r'(?:<!\[CDATA\[)?([^<\]]*)(?:\]\]>)?</\1>', re.I)
_ERR_KEY = ('message', 'msg', 'resultMsg', 'error_description', 'error', 'msg1')


def why(r):
    """응답 본문에서 사람이 읽을 사유를 뽑는다. 못 뽑으면 앞부분을 그대로.

    공공데이터포털은 에러일 때 `resultType=json` 을 무시하고 XML 을 준다.
    그래서 JSON 파서로만 보면 사유가 통째로 사라진다.

    본문이 비어 있으면 빈 문자열이다 — 호출자가 '본문 없음' 을 따로 적는다.
    사유가 없는데 구분자만 붙으면 무엇이 잘렸는지 헷갈린다.
    """
    try:
        body = (r.text or '')[:BODY_SCAN]
    except Exception:                                # noqa: BLE001
        return ''
    hits = [f'{k}={v.strip()}' for k, v in _ERR_TAG.findall(body) if v.strip()]
    if hits:
        return ' '.join(dict.fromkeys(hits))
    try:
        js = r.json()
    except Exception:                                # noqa: BLE001
        js = None
    if isinstance(js, dict):
        for k in _ERR_KEY:
            if js.get(k):
                return f'{k}={js[k]}'
    return ' '.join(body.split())[:BODY_SNIP]


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
