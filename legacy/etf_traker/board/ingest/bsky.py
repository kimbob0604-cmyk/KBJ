#!/usr/bin/env python3
"""
블루스카이 수집 — X 24시간 다이제스트의 (b') 경로.

계약은 `docs/XDIGEST.md` 3-1. 산출은 `state/xdigest/YYYYMMDD/posts.json` 하나다.
다음 단계(주제 묶기·사실 추출)는 그 파일만 읽는다 (CLAUDE.md 3장).

## 왜 X 가 아니라 블루스카이인가

X 자동 수집 아홉 경로가 2026-09-22 러너 실측에서 전부 실패했다 — `xcancel.com`
은 HTTP **451 Unavailable For Legal Reasons** 였다(`XDIGEST.md` 2장 (b),
`ingest/xsource.py` 의 진단표). 손 공유(a)도 사용자가 안 하기로 했다. 남은 것은
AT Protocol 공개 엔드포인트뿐이고, **인증 없이 공개 데이터를 주는 것이 그 API 의
설계**라 규약 위반이 아니다. 키·계정·비용이 없고 로그인도 하지 않는다.

## 호스트를 둘로 적는 이유 — 엔드포인트별로 열린 호스트가 다르다

같은 러너에서 갈렸다 (`xsource.BSKY` · `xsource.BSKY_SEARCH_HOST` 주석의 실측).

    api.bsky.app         searchPosts  HTTP 200 · JSON · 25건
    public.api.bsky.app  searchPosts  HTTP 403 · **HTML 페이지**
    public.api.bsky.app  getProfile   HTTP 200 · JSON

IP 차단도 일시적 장애도 아니다. 그래서 검색은 `api.bsky.app` 을 먼저 부르고
막히면 `public.api.bsky.app` 으로 한 번 더 본다 — 어느 쪽이 열려 있는지는 그날
눌러 봐야 안다. 한 호스트로 뭉쳐 두면 검색이 조용히 0건이 된다.

`xsource` 의 진단 코드를 가져다 쓰지 않는다. 그쪽은 후보를 한 번씩 눌러 보는
**측정**이라 재시도가 없고 예외를 표에 적는 것으로 끝난다. 여기는 매일 도는
수집이라 재시도·간격·상한·중복 제거 규약이 다르다. 가져온 것은 실측 사실뿐이다.

## 계정별 수집은 없다

예시(9장) 12계정 중 10개가 블루스카이에 없고, 있는 둘도 팔로워 0·35 라 그
사람의 계정으로 볼 수 없다(2장). `getAuthorFeed` 를 부르지 않는다 — 동명이인
계정을 그 사람이라고 적으면 `└` 출처 줄이 거짓이 된다. 그래서 이 경로는 주제
기반이고, `└` 에 적히는 것은 **블루스카이 핸들**이다.

## 403/429/503 은 '없다' 가 아니다

막힌 것이다. 건수 0 으로 적고 넘어가면 다이제스트가 "그런 게시물이 없었다" 로
읽힌다. `blocked` 를 올리고 사유를 `errors`·`cut`·`coverage.gaps` 에 남긴다
(CLAUDE.md 2장 6번). 판정은 읽는 쪽이 한다.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

from ..engine.build import STATE
from ..engine.config import ROOT, load
from . import creds
from .http import session, why

# ─────────────────────────── 엔드포인트 ───────────────────────────
XRPC = 'https://{host}/xrpc/{method}'
SEARCH_METHOD = 'app.bsky.feed.searchPosts'
SEARCH = XRPC.format(host='{host}', method=SEARCH_METHOD)
# 순서가 곧 시도 순서다. 앞이 실측에서 200 JSON 을 준 호스트다.
HOSTS = ('api.bsky.app', 'public.api.bsky.app')
# 게시물 링크. 사람이 누르는 주소는 API 호스트가 아니라 앱 도메인이다.
WEB = 'https://bsky.app/profile/{handle}/post/{rkey}'

# **막힌 것**으로 올리는 상태코드. 403 은 앞단 차단, 429 는 한도, 503 은 점검이다.
# 셋 다 재시도해도 같은 답이 오므로 그 호스트는 접고 다음 호스트로 간다.
BLOCKED = (403, 429, 503)

# 설정이 없을 때의 기본값. 값의 근거는 config/xdigest.yaml 주석에 있다 —
# 여기 있는 것은 설정 파일을 못 읽는 경우의 마지막 값이다.
QUERIES = ('HBM', 'semiconductor', 'DRAM', 'TSMC')
# `queries` 로 받은 게시물의 구획 키. 별도 구획(`sections`)이 없던 때의 posts.json
# 에는 `topic` 이 없고, 읽는 쪽은 그것을 이 구획으로 본다(D-NEXT-Q).
PRIMARY = 'semi'
LIMIT = 100
SORT = 'latest'
WINDOW_END_HM = '07:45'
WINDOW_HOURS = 24
PER_CALL_SLEEP = 2
# 막힌 질의 앞에서 쉬는 시간(초)과 한 실행에서 쉬는 횟수 상한 — 설정이 없을 때.
BLOCK_COOLDOWN = 45
BLOCK_COOLDOWNS = 3
TIMEOUT = 10
RETRIES = 1
MAX_POSTS = 400

# 재시도 사이 대기. 전송 오류·5xx 는 잠깐 뒤에 되는 일이 있다.
BACKOFF = 1.0
# 사유 한 줄의 길이 상한. 예전에 requests 스택 500자가 그대로 배너에 실렸다
# (stockflows.REASON_MAX 와 같은 이유).
REASON_MAX = 200

KST = timezone(timedelta(hours=9))
CFG_PATH = os.path.join(ROOT, 'config', 'xdigest.yaml')
XSTATE = os.path.join(STATE, 'xdigest')

# 시험이 갈아 끼우는 자리. 질의 사이 2초를 **실제로** 기다리는지 보려면 대기를
# 주입할 수 있어야 한다 — 시험이 질의마다 2초씩 실제로 자면 --test 가 느려진다.
SLEEP = time.sleep


def load_cfg(path=None):
    """config/xdigest.yaml. 임계값은 코드가 아니라 설정에만 있다(CLAUDE.md 3장)."""
    return load(path or CFG_PATH) or {}


def sections(cfg):
    """설정의 별도 구획(`sections`) → 정규화한 목록. 없으면 빈 목록 (D-NEXT-Q).

    구획 하나는 `{key, title, label, scope, queries, limit, max_items, max_pool,
    keywords}` 다. 수집(질의)·분석(범위·상한)·렌더(제목·머리 표기)가 **이 함수
    하나로** 같은 목록을 읽는다 — 셋이 각자 설정을 풀면 한쪽만 고쳐진다.

    키가 없거나 기본 구획 키(`semi`)와 겹치거나 앞 항목과 겹치는 것, 질의가 없는
    것은 뺀다. 조용히 빼지 않도록 뺀 사유를 `skipped` 로 돌려준다 — 부르는 쪽이
    로그에 남긴다(CLAUDE.md 2장 6번).
    """
    out, seen, skipped = [], {PRIMARY}, []
    for i, raw in enumerate((cfg or {}).get('sections') or []):
        if not isinstance(raw, dict):
            skipped.append(f'sections[{i}] 가 매핑이 아니다')
            continue
        key = str(raw.get('key') or '').strip()
        qs = [str(q) for q in (raw.get('queries') or []) if str(q).strip()]
        if not key or key in seen:
            skipped.append(f'sections[{i}] 키가 없거나 겹친다 ({key or "빈 키"})')
            continue
        if not qs:
            skipped.append(f'구획 {key} 에 질의가 없다')
            continue
        seen.add(key)
        title = str(raw.get('title') or key)
        out.append(dict(
            key=key, title=title, label=str(raw.get('label') or title),
            scope=str(raw.get('scope') or title), queries=qs,
            limit=int(raw.get('limit') or 0) or None,
            max_items=int(raw.get('max_items') or 8),
            max_pool=int(raw.get('max_pool') or 150),
            keywords=[str(k).lower() for k in (raw.get('keywords') or [])
                      if str(k).strip()],
            sort=str(raw.get('sort') or '').strip() or None,
            lang=str(raw.get('lang') or '').strip() or None))
    return out, skipped


def plan(cfg):
    """질의 계획 — `[(질의, 구획 키, limit)]`. 기본 구획이 먼저다.

    같은 질의가 두 구획에 있으면 앞의 것 하나만 누른다. 같은 게시물을 두 번 받아
    봤자 중복 제거에서 앞 구획으로 가므로 호출만 낭비한다.
    """
    limit = int(cfg.get('limit') or LIMIT)
    out, seen = [], set()
    for q in [str(q) for q in (cfg.get('queries') or QUERIES)]:
        if q not in seen:
            seen.add(q)
            out.append((q, PRIMARY, limit))
    for sec in sections(cfg)[0]:
        for q in sec['queries']:
            if q not in seen:
                seen.add(q)
                out.append((q, sec['key'], sec['limit'] or limit))
    return out


def state_path(asof, name='posts.json', make=False):
    """`state/xdigest/YYYYMMDD/<name>` — 단계별 산출물 자리(XDIGEST.md 3장).

    `engine.build.state_dir` 를 쓰지 않는다. 그쪽은 보드 기준일 디렉터리
    (`state/YYYYMMDD/`)라 다이제스트 파일이 보드 산출물과 섞이고, `.gitignore`
    의 `state/*/` 예외도 `state/xdigest/` 하나만 뚫었다. 다음 단계는 이 함수를
    불러 경로를 얻는다 — 경로를 각자 조립하면 한쪽이 바뀔 때 조용히 갈린다.
    """
    d = os.path.join(XSTATE, asof.replace('-', ''))
    if make:
        os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def save(asof, out):
    """posts.json 을 쓰고 경로를 돌려준다."""
    import json
    p = state_path(asof, 'posts.json', make=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return p


# ─────────────────────────── 시각 ───────────────────────────
_ISO = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(.*)$')


def _iso_kst(v):
    """AT Protocol 의 `createdAt` → KST ISO 문자열. 못 읽으면 None.

    `fromisoformat` 에 그대로 넘기지 않는 이유 둘 — 블루스카이는 `Z` 로 끝나는
    값을 주고(파이썬 3.10 이하가 못 읽는다), 소수 초 자리가 3자리인 것과
    6자리를 넘는 것이 섞여 온다. 그래서 초까지 자르고 오프셋만 따로 붙인다.

    시각대가 없는 값은 UTC 로 읽는다. 지어낼 값이 없어 KST 로 가정하면 9시간이
    밀려 창 판정이 통째로 틀어진다.
    """
    s = str(v or '').strip()
    if not s:
        return None
    if s.endswith(('Z', 'z')):
        s = s[:-1] + '+00:00'
    m = _ISO.match(s)
    if not m:
        return None
    tz = m.group(2) or '+00:00'
    try:
        dt = datetime.fromisoformat(m.group(1) + tz)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat(timespec='seconds')


def _dt(iso):
    return datetime.fromisoformat(iso)


def _clock(now):
    """`collected_at` 을 만드는 시계. 시험이 고정 시각을 주입한다."""
    if now is None:
        return lambda: datetime.now(KST)
    if callable(now):
        return lambda: _as_dt(now())
    return lambda: _as_dt(now)


def _as_dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=KST)
    return _dt(str(v))


def window_of(asof, cfg=None):
    """기준일 D 의 창 — D 의 `window_end_hm` 에서 `window_hours` 만큼 거꾸로.

    끝을 발송(08:00)보다 앞인 07:45 로 두는 이유는 분석·검증·렌더에 쓸 시간이
    필요해서다(XDIGEST.md 4장). 창의 끝이 dispatch 시각이다.
    """
    cfg = cfg if cfg is not None else load_cfg()
    hm = str(cfg.get('window_end_hm') or WINDOW_END_HM)
    hh, mm = (hm.split(':') + ['0'])[:2]
    end = datetime.fromisoformat(asof).replace(
        tzinfo=KST, hour=int(hh), minute=int(mm), second=0, microsecond=0)
    start = end - timedelta(hours=float(cfg.get('window_hours') or WINDOW_HOURS))
    return dict(start=start.isoformat(timespec='seconds'),
                end=end.isoformat(timespec='seconds'))


# ─────────────────────────── 수집 ───────────────────────────
class _Counted:
    """호출 수를 센다.

    `sources.bluesky.calls` 는 질의 수가 아니라 **실제 요청 수**다 — 폴백
    호스트로 한 번 더 부른 날과 안 부른 날이 같은 숫자면 그 사실이 사라진다.
    """

    def __init__(self, s):
        self._s, self.calls = s, 0

    def get(self, *a, **kw):
        self.calls += 1
        return self._s.get(*a, **kw)


# ─────────────────────────── 로그인 (선택) ───────────────────────────
# 공개 AppView(api.bsky.app·public.api.bsky.app)는 **로그인 없는 요청**을 앞단에서
# 거른다. 2026-09-23 러너에서 질의 열 개 남짓 뒤부터 둘 다 `403 · HTML(앞단 차단)
# · Request forbidden by administrative rules` 였다 — API 가 JSON 으로 주는 한도
# 거절이 아니라 앞단 방화벽이다. GitHub 러너는 IP 를 여럿이 나눠 써서 우리가 적게
# 불러도 걸릴 수 있다.
#
# 무료 계정의 **앱 비밀번호**로 로그인하면 요청이 계정의 PDS(bsky.social)를 거쳐
# 간다 — 한도가 IP 가 아니라 계정 단위다. 키가 없으면 예전처럼 공개 호스트만 쓴다.
# 앱 비밀번호는 계정 비밀번호가 아니다(설정 → 개인정보 → 앱 비밀번호, 언제든 폐기).
AUTH_HOST = 'bsky.social'
_auth = {'token': None, 'host': None, 'why': None, 'tried': False}


def _auth_reset():
    _auth.update(token=None, host=None, why=None, tried=False)


def _login(sess, timeout):
    """`(토큰, PDS 호스트)` 또는 `(None, None)`. 한 실행에 한 번만 시도한다."""
    if _auth['tried']:
        return _auth['token'], _auth['host']
    _auth['tried'] = True
    ident, pw = creds.get('BSKY_HANDLE'), creds.get('BSKY_APP_PASSWORD')
    if not (ident and pw):
        return None, None
    try:
        r = sess.post(XRPC.format(host=AUTH_HOST, method='com.atproto.server.createSession'),
                      json={'identifier': ident, 'password': pw}, timeout=timeout)
        js = r.json() if r.status_code == 200 else None
    except Exception as e:                               # noqa: BLE001
        _auth['why'] = f'로그인 실패 — {type(e).__name__}'
        return None, None
    if not js or not js.get('accessJwt'):
        # 비밀번호·응답 본문은 남기지 않는다. 상태코드만.
        _auth['why'] = f'로그인 실패 — HTTP {getattr(r, "status_code", "?")}'
        return None, None
    host = AUTH_HOST
    for svc in ((js.get('didDoc') or {}).get('service') or []):
        ep = str(svc.get('serviceEndpoint') or '')
        if svc.get('id', '').endswith('atproto_pds') and ep.startswith('https://'):
            host = ep[len('https://'):].rstrip('/')
    _auth.update(token=js['accessJwt'], host=host)
    return _auth['token'], _auth['host']


def _call_authed(sess, method, params, timeout):
    """로그인 경로 한 번 → `(js, why)`. 로그인이 없으면 `(None, None)`."""
    token, host = _login(sess, timeout)
    if not token:
        return None, _auth['why']
    try:
        r = sess.get(XRPC.format(host=host, method=method), params=params, timeout=timeout,
                     headers={'Authorization': f'Bearer {token}'})
    except Exception as e:                               # noqa: BLE001
        return None, f'{host}(로그인): {type(e).__name__}'
    if r.status_code == 200:
        try:
            return r.json(), None
        except ValueError:
            pass
    return None, f'{host}(로그인): HTTP {r.status_code} · {why(r) or "본문 없음"}'[:REASON_MAX]


def call(method, params, s=None, timeout=TIMEOUT, retries=RETRIES, hosts=None, gap=0):
    """공개 XRPC 메서드 한 번 → `(js, ok, blocked, why)`. **블루스카이 호출은 여기 하나다.**

    `search`(수집)와 `xsource.probe_bsky`(실측)가 같은 호스트 목록·같은 막힘
    판정·같은 사유 표기를 쓰게 하려고 뽑아냈다. 예전에는 둘이 각자 구현해서,
    검토가 수집 쪽에서 잡아 고친 결함 둘(막힘을 누적 플래그로 두어 '403 + 전송
    오류' 를 막힘으로 라벨하던 것, 뒤 호스트 사유로 앞 호스트 사유를 덮던 것)이
    실측 쪽에는 그대로 남아 있었다. 두 벌이면 고침도 두 번 해야 하고 한 번은
    잊는다.

    정상 경로는 메서드당 요청 **1회**다. `retries` 는 전송 오류·기타 비200 에만
    쓰고(시도 = 1 + retries), 403/429/503 은 재시도하지 않고 그 호스트를 접는다 —
    같은 답이 다시 온다.

    `ok` 는 **200 JSON 을 받았는가** 하나다. `blocked=True` 는 '없다' 가 아니라
    '**호스트가 다** 막혔다' 는 뜻이고, 호스트별 판정의 AND 다 — 한 호스트만
    403 인 것을 막힘으로 적으면 모르는 것을 판정한 것이 된다(2장 1번).

    `gap` 은 호스트를 바꿀 때 쉬는 초다(첫 호스트 앞에서는 쉬지 않는다).

    사유는 상태코드만이 아니라 본문에서 뽑은 문장까지 담고(`http.why`),
    **호스트마다 따로 남긴다** — 'api 429 · public 403' 과 '둘 다 403' 은 다른
    사실이다. 200 을 받아도 앞 호스트의 사유를 버리지 않는다(2장 6번).
    """
    sess = s or session()
    whys = {}                                        # 호스트 → 마지막 사유
    blocked_hosts = {}                               # 호스트 → 그 호스트가 막혔는지
    # 로그인 키가 있으면 그 경로가 먼저다(위 '로그인' 절). 실패하면 사유를 남기고
    # 공개 호스트로 내려간다 — 로그인 실패로 그날 수집을 통째로 잃지 않는다.
    if hosts is None:
        js, w = _call_authed(sess, method, params, timeout)
        if js is not None:
            return js, True, False, None
        if w:
            whys['login'] = w
    for n, host in enumerate(hosts or HOSTS):
        # 앞 호스트가 한도(429)로 접혔으면 뒤 호스트를 곧바로 누르면 같은 답을
        # 받는 일이 있다. 다만 기본은 0 이다 — 수집은 질의 사이에 이미 2초를
        # 쉬고(PER_CALL_SLEEP) 폴백은 드물어 4장의 수집 예산에 넣지 않았다.
        # 실측(`probe_bsky`)은 짧은 시간에 여러 메서드를 눌러 gap=2 를 준다.
        if n and gap:
            SLEEP(gap)
        url = XRPC.format(host=host, method=method)
        for i in range(int(retries) + 1):
            try:
                r = sess.get(url, params=params, timeout=timeout)
            except Exception as e:                   # noqa: BLE001 - 사유를 문자열로 보존
                whys[host] = f'{type(e).__name__}: {str(e)[:REASON_MAX]}'
                # 연결 실패는 막힌 것인지 모른다. 판정하지 않는다(2장 1번).
                blocked_hosts[host] = False
            else:
                ctype = (r.headers.get('content-type') or '').split(';')[0]
                if r.status_code == 200:
                    try:
                        js = r.json()
                    except ValueError:
                        # 200 인데 JSON 이 아니다. 재시도해도 같은 게 온다 —
                        # public 호스트의 403 이 HTML 이었던 것과 같은 부류다.
                        whys[host] = (f'HTTP 200 인데 JSON 이 아니다 — '
                                      f'{ctype} · {why(r) or "본문 없음"}'
                                      )[:REASON_MAX]
                        blocked_hosts[host] = False
                        break
                    return js, True, False, _why_line(whys)
                if r.status_code in BLOCKED:
                    blocked_hosts[host] = True
                    # API 거부와 앞단(클라우드 IP) 차단은 다르다. 공개 API 는
                    # 오류도 JSON 으로 주므로, HTML 이 오면 앞단이다.
                    kind = ('JSON 오류(API 거부)' if 'json' in ctype
                            else 'HTML(앞단 차단)')
                    whys[host] = (f'HTTP {r.status_code} · {ctype or "타입 없음"} · '
                                  f'{kind} · {why(r) or "본문 없음"}')[:REASON_MAX]
                    break
                whys[host] = (f'HTTP {r.status_code} · '
                              f'{why(r) or "본문 없음"}')[:REASON_MAX]
                blocked_hosts[host] = False
            if i < int(retries):
                SLEEP(BACKOFF * (i + 1))
    # 한 호스트만 403 인 것은 '막힘' 이 아니다 — 계약이 `blocked` 를 '두 호스트가
    # 다 막힌 질의' 로 못 박아 놨다(3-1). 누적 플래그로 두면 403 + 전송 오류가
    # '막힘' 으로 라벨된다.
    blocked = bool(blocked_hosts) and all(blocked_hosts.values())
    return None, False, blocked, _why_line(whys)


def _utc_z(iso):
    """'2026-09-23T07:45:00+09:00' → '2026-09-22T22:45:00Z'. API 문서의 예시 꼴로 준다."""
    from datetime import timezone as _tz
    return _dt(iso).astimezone(_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def search(q, limit=100, sort='latest', s=None, timeout=TIMEOUT, retries=RETRIES,
           since=None, until=None, lang=None):
    """`app.bsky.feed.searchPosts` 한 질의 → `(posts, ok, blocked, why)`.

    `posts` 는 **응답 원본 목록**이다. 파싱은 `parse` 가 따로 한다 — 그래야
    파서를 픽스처로 못 박고 '파서가 틀린 것' 과 '경로가 막힌 것' 을 가릴 수
    있다(`tests/test_xsource.py` 머리와 같은 이유).

    정상 경로는 질의당 요청 **1회**다. `retries` 는 전송 오류·기타 비200 에만
    쓰는 **재시도 횟수**이고(시도 = 1 + retries), 403/429/503 은 재시도하지
    않고 그 호스트를 접는다 — 같은 답이 다시 온다.

    `ok` 는 **200 JSON 을 받았는가** 하나다. 실패 질의 수를 세는 값이 따로 없으면
    읽는 쪽이 `errors` 줄 수를 세게 되는데, 그 목록에는 파싱에서 뺀 건수 줄도
    섞여 수집이 된 날이 '전부 실패' 로 읽힌다(3-1 `failed`).

    `blocked=True` 는 '게시물이 없다' 가 아니라 '**두 호스트가 다** 막혔다' 는
    뜻이다. 호스트별 판정을 모아 AND 로 낸다 — 앞 호스트 403 · 뒤 호스트 연결
    실패를 '막힘' 으로 적으면 모르는 것을 판정한 것이 된다(3-1, 2장 1번).

    사유는 상태코드만이 아니라 본문에서 뽑은 문장까지 담고(`http.why` — 2장
    6번), **호스트마다 따로 남긴다** — 'api 429 · public 403' 과 '둘 다 403' 은
    다른 사실이다. 뒤 호스트의 사유로 덮으면 한도에 걸린 날과 앞단이 막은 날이
    로그에서 같아진다.

    **성공해도 사유를 버리지 않는다.** 폴백 호스트가 받아 준 날의 앞 호스트
    429 는 주 호스트가 죽기 시작한 신호인데, 200 을 받은 순간 반환하며 버리면
    파일·로그 어디에도 남지 않는다(2장 6번). 그래서 `ok=True` 에도 `why` 가
    붙을 수 있고, 그 줄의 뜻은 '막혔다' 가 아니라 '이 호스트로는 못 받았다' 다.
    """
    params = dict(q=q, limit=int(limit), sort=sort)
    # `since`·`until` 은 ISO 시각, `lang` 은 언어 코드다. `sort=top` 은 창을 주지
    # 않으면 몇 달 전 인기글까지 섞으므로 부르는 쪽이 창을 함께 준다(collect).
    for k, v in (('since', since), ('until', until), ('lang', lang)):
        if v:
            params[k] = v
    js, ok, blocked, why_ = call(SEARCH_METHOD, params,
                                 s=s, timeout=timeout, retries=retries)
    # `posts` 만 꺼낸다. 파싱은 `parse` 가 따로 한다 — 그래야 파서를 픽스처로
    # 못 박고 '파서가 틀린 것' 과 '경로가 막힌 것' 을 가릴 수 있다.
    return list((js or {}).get('posts') or []), ok, blocked, why_


def _why_line(whys):
    """호스트별 사유를 한 줄로. 호스트마다 따로 적는다(3-1)."""
    return ' / '.join(f'{h}: {w}' for h, w in whys.items()) or None


def parse(js, query):
    """응답 → posts.json 의 `posts[]` 항목 목록. 수집과 분리해 둔 파서다.

    응답 한 건은
    `{uri, cid, author:{handle, displayName, did}, record:{text, createdAt, langs}}`
    꼴이다. `url` 은 `uri` 의 마지막 조각(rkey)과 핸들로 만든다 — AT-URI 의 did
    로는 사람이 열 수 있는 주소가 안 나온다.

    `id` 는 AT-URI 문자열 그대로다. 중복 제거의 키이므로 가공하지 않는다.
    `text` 는 **원문 그대로** 남긴다 — 검증(3-3)이 이 문자열과 대조한다.

    `uri` · `handle` · `createdAt` · `text` 중 하나가 없는 항목은 뺀다. id·출처·
    창 판정·검증의 근거가 없어 다음 단계가 쓸 수 없는 항목이다. 몇 건을 뺐는지는
    `collect` 가 `errors` 에 적는다 — 조용히 줄어들면 안 된다(2장 6번).

    **핸들과 빈 `text` 도 드롭 조건이다.** 핸들이 없으면 `account` 가 빈 문자열,
    `url` 이 `null` 인 행이 남는데 그 행을 다음 단계가 고르면 `└` 출처 줄에
    핸들이 비고 링크가 없다 — 3-1 의 `account`(블루스카이 핸들) · `url`
    (`profile/<handle>/post/<rkey>`) 계약을 못 지키고 출처 없는 문장이 된다
    (2장 2번). 사진만 올린 게시물의 `text: ""` 도 검증(3-3)이 대조할 문자열이
    없어 쓸 수 없다.
    """
    recs = js.get('posts') if isinstance(js, dict) else (js or [])
    out = []
    for p in recs or []:
        if not isinstance(p, dict):
            continue
        uri = str(p.get('uri') or '')
        a = p.get('author') or {}
        rec = p.get('record') or {}
        handle = str(a.get('handle') or '')
        text = rec.get('text')
        posted = _iso_kst(rec.get('createdAt'))
        rkey = uri.rsplit('/', 1)[-1] if uri else ''
        if not uri or not rkey or not handle or not posted:
            continue
        if not str(text or '').strip():
            continue
        langs = rec.get('langs')
        out.append(dict(
            id=uri,
            account=handle,
            display_name=a.get('displayName') or None,
            text=text,
            url=WEB.format(handle=handle, rkey=rkey),
            posted_at=posted,
            source='bluesky',
            query=query,
            topic=None,                              # collect 가 질의의 구획으로 채운다
            langs=list(langs) if isinstance(langs, list) else [],
            in_window=None))                         # collect 가 창을 보고 채운다
    return out


def collect(asof, window, cfg=None, log=print, now=None, session_factory=None):
    """질의를 순서대로 눌러 posts.json dict 를 만든다. 계약은 XDIGEST.md 3-1.

    질의마다 요청 1회, 질의 **사이** 2초를 쉰다 — 남의 공개 서버다
    (`xsource` 머리의 '예의' 와 같은 규약).

    한 질의가 막히면 **그 질의만 접는다.** 나머지 질의는 계속 돌고, 막힌 질의는
    `blocked` 를 올리고 사유를 `errors` 와 `coverage.gaps` 에 남긴다. 전부
    막혀도 파일은 남긴다 — 그 사실이 다음 단계가 읽어야 하는 값이다.

    창 밖 게시물은 **버리지 않고** `in_window: false` 로 남긴다(3-1). 검색은
    타임라인을 긁는 것이라 창 밖이 섞여 오는데, 버리면 '왜 그 글이 안 실렸나'
    를 파일에서 확인할 길이 없다.

    게시물마다 `topic` 에 **처음 잡힌 질의의 구획 키**를 적는다(`semi` = 기본
    구획, 그 밖은 `sections[].key`). 분석이 이 값으로 게시물을 구획별로 나눈다 —
    구획을 모델이 가르게 두면 반도체 게시물이 AI 뉴스로 새거나 그 반대가 되고,
    그것을 코드가 되짚을 근거가 없다(D-NEXT-Q). `by_topic` 은 구획별 창 안 건수다.

    세 건수의 뜻은 서로 다르다 — `got` 은 응답 원본 합(중복·창 밖 포함),
    `in_window` 는 중복 제거 뒤 창 안 건수(머리 2행의 '수집 N건'), `kept` 는
    `posts[]` 길이(창 밖 포함, `max_posts` 적용 뒤)다.

    `failed` 는 **200 JSON 을 못 받은 질의 수**다(막힘 + 전송 오류). 읽는 쪽이
    `errors` 줄 수로 세지 않게 값으로 돌려준다 — 그 목록에는 파싱 드롭 줄과
    폴백 사유 줄도 있어, 수집이 된 날도 '질의가 전부 실패' 로 읽힌다.

    `errors` 와 `gaps` 에 넣는 것이 다르다. `errors` 는 이 경로가 남기는 사유
    전부(막힘·실패 · 폴백으로 받음 · 파싱 드롭)이고, `gaps` 는 **결손**만이다
    (막힘·실패 + `cut`). 머리 3행이 `gaps` 에서 만들어지므로(1장) 파서 내부
    문구나 '받았다' 는 줄이 거기 들어가면 정상적인 날의 발송문에 내부 사정이
    실린다(3-1 `coverage`).
    """
    cfg = cfg if cfg is not None else load_cfg()
    # 질의는 기본 구획(`queries`)이 먼저고 별도 구획(`sections`)이 뒤다. 같은
    # 게시물이 두 구획 질의에 걸리면 앞 구획으로 간다 — 반도체 게시물이 'AI' 질의에
    # 한 번 더 걸려 AI 뉴스 구획으로 새지 않는다(D-NEXT-Q).
    todo = plan(cfg)
    queries = [q for q, _k, _l in todo]
    _secs, skipped = sections(cfg)
    for w in skipped:
        log(f'  설정 {w} — 그 구획은 뺐다')
    sort = str(cfg.get('sort') or SORT)
    gap_sec = float(cfg.get('per_call_sleep_sec', PER_CALL_SLEEP))
    timeout = int(cfg.get('timeout_sec') or TIMEOUT)
    retries = int(cfg.get('retries', RETRIES))
    cap = int(cfg.get('max_posts') or MAX_POSTS)

    clock = _clock(now)
    s = _Counted((session_factory or session)())
    start, end = _dt(window['start']), _dt(window['end'])

    got = dropped = blocked_n = failed = 0
    fails, notes, seen, order = [], [], {}, []
    # 구획마다 정렬·언어를 따로 줄 수 있다. 물량이 큰 낱말(OpenAI 등)은 최신순
    # 50건이면 한두 시간치라 24시간 다이제스트가 아니게 된다 — `sort: top` 이면
    # **창 전체에서** 반응이 큰 글을 받는다. 그때는 창을 since·until 로 함께 준다.
    opts = {sec['key']: sec for sec in _secs}
    cool_sec = float(cfg.get('block_cooldown_sec', BLOCK_COOLDOWN))
    max_cooldowns = int(cfg.get('block_cooldowns', BLOCK_COOLDOWNS))
    cooldowns = 0
    for i, (q, topic, q_limit) in enumerate(todo):
        if i:
            SLEEP(gap_sec)
        o = opts.get(topic) or {}
        q_sort = o.get('sort') or sort
        win_kw = (dict(since=_utc_z(window['start']), until=_utc_z(window['end']))
                  if q_sort == 'top' else {})
        raw, ok, blocked, why_ = search(q, limit=q_limit, sort=q_sort, s=s,
                                        timeout=timeout, retries=retries,
                                        lang=o.get('lang'), **win_kw)
        if blocked and cooldowns < max_cooldowns:
            # 막힘(403·429)은 공개 API 의 **한도**다. 곧바로 다음 질의를 눌러 봐야
            # 같은 답이 온다 — 2026-09-23 미리보기에서 LLM 부터 뒤 7개 질의가
            # 줄줄이 막혀 퀀트 구획이 통째로 0건이 됐다. 한 번 쉬고 같은 질의를 다시
            # 누른다. 쉬는 횟수에 상한을 둬 08:00 발송을 넘기지 않는다.
            cooldowns += 1
            log(f'  질의 "{q}" 막힘 — {cool_sec:.0f}초 쉬고 한 번 더 ({cooldowns}/{max_cooldowns})')
            SLEEP(cool_sec)
            raw, ok, blocked, why_ = search(q, limit=q_limit, sort=q_sort, s=s,
                                            timeout=timeout, retries=retries,
                                            lang=o.get('lang'), **win_kw)
        got += len(raw)
        rows = parse(dict(posts=raw), q)
        for r in rows:
            r['topic'] = topic
        dropped += len(raw) - len(rows)
        if not ok:
            failed += 1
            blocked_n += 1 if blocked else 0
            e = f'질의 "{q}" {"막힘" if blocked else "실패"} — {why_ or "사유 없음"}'
            fails.append(e)
            log(f'  {e}')
        else:
            if why_:
                # 폴백 호스트가 받아 준 날의 앞 호스트 사유. **받았으므로 결손이
                # 아니다** — gaps 에 넣지 않고 errors 에만 남긴다. 그래도 남겨야
                # 하는 것은, 주 호스트가 429 를 주기 시작한 날을 이 줄 말고는
                # 알 길이 없어서다(2장 6번). `calls > queries` 는 '왜' 를 말해
                # 주지 않는다.
                n = f'질의 "{q}" 폴백으로 받음 — {why_}'
                notes.append(n)
                log(f'  {n}')
            log(f'  질의 "{q}" [{topic}] {len(rows)}건 (원본 {len(raw)}건)')
        for r in rows:
            # 같은 게시물이 여러 질의에 잡히면 한 건이고 `query` 는 처음 잡힌 질의다.
            if r['id'] in seen:
                continue
            seen[r['id']] = r
            order.append(r['id'])
    if dropped:
        notes.append(f'응답 {dropped}건은 uri·핸들·createdAt·text 중 하나가 없어 '
                     '파싱에서 뺐다 — id·출처·창 판정·검증의 근거가 없다')

    posts = [seen[i] for i in order]
    for p in posts:
        # 끝은 **배타**다. 양끝을 다 포함하면 07:45:00 정각 게시물이 연속된 두
        # 기준일의 창에 동시에 들어 이틀 연속 본문에 실린다(10장 T5 '중복 0').
        p['in_window'] = bool(start <= _dt(p['posted_at']) < end)

    cut = None
    if len(posts) > cap:
        # 창 안을 먼저, 그 안에서 최신부터 남긴다. '창 밖을 버리지 않는다' 는
        # 규칙은 상한 안에서만 지킬 수 있다 — 무엇을 버렸는지 cut 에 적는다.
        posts.sort(key=lambda p: p['posted_at'], reverse=True)
        posts.sort(key=lambda p: not p['in_window'])   # 안정 정렬
        cut = (f'max_posts {cap} 상한 — 중복 제거 뒤 {len(posts)}건 중 '
               f'{len(posts) - cap}건을 버렸다(창 밖·오래된 것 먼저)')
        posts = posts[:cap]
        log(f'  {cut}')
    # 파일 순서는 `posted_at` 내림차순 하나로 고정한다. 다음 단계가 정렬에
    # 기대지 않게 하되, 파일을 사람이 열어 볼 때 최신이 위에 있어야 읽힌다.
    posts.sort(key=lambda p: p['posted_at'], reverse=True)

    inw = [p['posted_at'] for p in posts if p['in_window']]
    # 구획별 창 안 건수. 머리 2행은 분석 쪽 건수를 쓰고, 이 값은 질의를 고르는
    # 근거다 — 새 구획의 질의가 실제로 물량을 주는지 파일 하나로 본다(D-NEXT-Q).
    by_topic = {t: 0 for t in dict.fromkeys(k for _q, k, _l in todo)}
    for p in posts:
        if p['in_window']:
            by_topic[p['topic']] = by_topic.get(p['topic'], 0) + 1
    limits_ = sorted({l for _q, _k, l in todo})
    per = (f'{limits_[0]}' if len(limits_) == 1
           else '·'.join(str(x) for x in limits_))
    gaps = [dict(scope='bluesky',
                 why=f'질의 {len(queries)}개 · 질의당 상위 {per}건까지 — '
                     '그 밖은 알 수 없음')]
    # 막힌·실패한 질의는 **결손**이다. 머리 3행이 coverage.gaps 에서 만들어지므로
    # (1장) 여기 적지 않으면 막혔다는 사실이 발송문에서 사라진다. 폴백 사유·파싱
    # 드롭은 받은 것에 대한 기록이라 gaps 가 아니라 errors 몫이다(3-1).
    gaps += [dict(scope='bluesky', why=e) for e in fails]
    if cut:
        gaps.append(dict(scope='bluesky', why=cut))

    return dict(
        asof=asof,
        window=dict(start=window['start'], end=window['end']),
        collected_at=clock().isoformat(timespec='seconds'),
        sources=dict(bluesky=dict(
            queries=len(queries), calls=s.calls, got=got,
            in_window=len(inw), kept=len(posts), by_topic=by_topic,
            blocked=blocked_n, failed=failed, cut=cut,
            errors=fails + notes)),
        coverage=dict(first=min(inw) if inw else None,
                      last=max(inw) if inw else None, gaps=gaps),
        posts=posts)


# ─────────────────────────── 진단 ───────────────────────────
def probe(cfg=None):
    """`--check` 용. 질의 하나를 눌러 건수·24시간 안 건수를 본다.

    **판정(`ok`)은 200 JSON 을 받았는가 하나다.** 24시간 안 건수는 note 에만
    적는다 — 최근 게시물이 없는 것은 '경로가 막혔다' 와 다른 사실이고, 그것을
    빨강으로 올리면 이 모듈이 경계하는 '막힌 것과 없는 것 섞기' 를 진단 쪽에서
    저지른다(2장 1번). 응답이 온 것은 사실이고 물량이 없는 것은 판정이 아니다.

    프로브 질의는 `probe_query`(없으면 `queries[0]`)다. 질의 목록의 첫 항목
    (HBM)은 24시간 물량이 20건으로 가장 적은 질의라(설정 주석의 실측) 평균
    72분에 1건이다 — 조용한 시간대에 누르면 경로가 멀쩡한데도 0건이 나온다.
    """
    cfg = cfg if cfg is not None else load_cfg()
    queries = [str(x) for x in (cfg.get('queries') or QUERIES)] or ['HBM']
    q = str(cfg.get('probe_query') or queries[0])
    raw, ok, blocked, why_ = search(
        q, limit=int(cfg.get('limit') or LIMIT), sort=str(cfg.get('sort') or SORT),
        timeout=int(cfg.get('timeout_sec') or TIMEOUT),
        retries=int(cfg.get('retries', RETRIES)))
    name = f'searchPosts "{q}"'
    if not ok:
        # 막힌 것을 '0건' 으로 적지 않는다. 사유를 그대로 올린다.
        return [(name, False, ('막힘 — ' if blocked else '') + (why_ or '사유 없음'))]
    rows = parse(dict(posts=raw), q)
    cut = datetime.now(KST) - timedelta(hours=24)
    n24 = sum(1 for r in rows if _dt(r['posted_at']) >= cut)
    note = f'{len(rows)}건 · 24시간 안 {n24}건'
    if len(rows) != len(raw):
        note += f' · 파싱에서 뺀 것 {len(raw) - len(rows)}건'
    if why_:
        # 폴백으로 받은 날의 앞 호스트 사유. 통과 줄에도 남긴다 — 주 호스트가
        # 죽기 시작한 날을 --check 에서 보려면 이 줄이 있어야 한다.
        note += f' · 폴백으로 받음 — {why_}'
    if not n24:
        note += ' — 응답은 왔는데 24시간 안 게시물이 없다'
    return [(name, True, note)]
