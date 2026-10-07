#!/usr/bin/env python3
"""X(트위터) 게시물 자동 수집 경로 — 실측 진단.

## 왜 이 파일이 진단부터인가

사용자가 (b) 경로를 쓰기로 정했다 (2026-09-22). 손 공유를 하지 않고 계정
타임라인을 자동으로 받는다. **이 경로는 X 이용약관의 스크래핑 금지 조항에
걸린다** — 사용자가 그 사실을 알고 고른 것이고, 계약서(`docs/XDIGEST.md` 8장
9번)에 그렇게 적혀 있다.

**로그인은 하지 않는다.** 쿠키 GraphQL((c) 경로)은 여기 없고 앞으로도 넣지
않는다 — 사용자 계정이 정지될 수 있고 자격증명이 레포 시크릿에 남는다.
여기 있는 것은 인증 없이 공개 게시물을 받는 경로뿐이다.

조사 시점(2026-09) 보고로는 이 경로들이 대부분 막혔거나 불안정하다.

  - syndication 프로필 타임라인 — 임베드 API 에서 제거됐다는 보고, 429 보고
  - Nitter — 2026-08 X Corp 가 C&D 를 보냈고, 살아 있는 공개 인스턴스가 한 자리 수
  - RSSHub — 무료 경로는 upstream 인증을 요구해 사실상 막혔다는 보고

그래서 **수집기를 먼저 쓰지 않는다.** 러너에서 눌러 보고, 통과한 것만 붙인다
(CLAUDE.md 0장 — 추측하지 말고 묻거나 실측한다).

## 예의

공개 인스턴스는 자원봉사 서버다. 여기서는 후보마다 **한 번**만 부르고, 순차로
돌고, 429 를 받으면 그 인스턴스를 더 부르지 않는다. 수집기를 붙일 때도 같다.
"""
import os
import json
import re
import time

from .http import session

# ─────────────────────────── 후보 ───────────────────────────
# 살아 있는지 **모른다**. 이름만 적어 두고 눌러 본 결과로 판정한다.
# 인스턴스 목록은 status.d420.de 등이 공개하던 것들이다 — 수시로 바뀐다.
NITTER = [
    'nitter.net',
    'xcancel.com',
    'nitter.poast.org',
    'lightbrd.com',
    'nitter.privacyredirect.com',
    'nitter.tiekoetter.com',
]
RSSHUB = ['rsshub.app']

# 블루스카이 — AT Protocol 공개 엔드포인트. **인증 없이 공개 데이터를 주는 것이
# 설계다**(문서화된 공개 API). 규약 위반이 아니고 무료다. X 경로가 다 막힌 뒤의
# 유일한 합법 자동 수집 후보라 여기서 함께 재 본다.
#
# 호스트를 둘로 나눠 적는 이유 — 2026-09-22 러너 실측에서 갈렸다.
#   public.api.bsky.app : getProfile 200 JSON · describeServer 501 JSON
#                         **searchPosts 는 403 + HTML 페이지**
#   api.bsky.app        : searchPosts 200 JSON · 25건
# 즉 IP 차단도 일시적 장애도 아니고 **엔드포인트별로 호스트가 다르다.** 검색은
# api 쪽으로 부른다. 한 호스트로 뭉쳐 두면 검색이 조용히 0건이 된다.
BSKY = 'https://public.api.bsky.app/xrpc/{method}'
BSKY_SEARCH_HOST = 'api.bsky.app'

# 프로필 타임라인 (인증 없음). 과거에는 __NEXT_DATA__ 안에 최근 트윗이 있었다.
SYND_TIMELINE = 'https://syndication.twitter.com/srv/timeline-profile/screen-name/{acct}'
# 단건 본문 (id 를 알 때만). react-tweet 가 쓰는 경로.
SYND_TWEET = 'https://cdn.syndication.twimg.com/tweet-result'

TIMEOUT = 10
# [KBJ P1] 계정 핸들은 개인 데이터라(ADR 0001 U3) 코드에 적지 않는다 — 실측할 때만
# 환경변수로 넣는다(쉼표 구분, `@` 는 있어도 된다). 비어 있으면 X 경로 1~3번과 계정
# 실측은 잴 대상이 없어 건너뛴다(legacy/etf_traker/board/MIGRATION.md).


def _handles(name):
    return tuple(h.strip().lstrip('@') for h in os.environ.get(name, '').split(',')
                 if h.strip().lstrip('@'))


# 표본 계정. 다이제스트 예시에 나온 계정 중 둘 — 하나가 비공개·삭제여도
# 다른 하나로 경로 자체의 생사를 가린다.
SAMPLE = _handles('XSOURCE_SAMPLE_ACCOUNTS')

# 다이제스트 예시(XDIGEST.md 9장)에 인용된 계정. X 핸들이다 —
# 블루스카이 핸들이 같다는 보장이 없어서 `<핸들>.bsky.social` 과 계정 검색
# 둘로 찾아 본다.
DIGEST_ACCOUNTS = _handles('XSOURCE_DIGEST_ACCOUNTS')
# 주제 질의. 계정을 못 찾아도 이쪽이 물량을 주면 다이제스트가 성립한다.
DIGEST_TOPICS = ('HBM', 'semiconductor', 'TSMC', 'DRAM', 'AI datacenter',
                 'SK hynix', 'foundry')


def _probe_topics():
    """실측할 주제 — `[(질의, 추가 파라미터)]`.

    기본은 다이제스트가 실제로 쓰는 질의(xdigest.yaml)다. 예전에는 DIGEST_TOPICS
    고정 목록만 쟀는데, 구획(D-NEXT-Q)이 생긴 뒤로는 그 목록이 실제 질의와 달라
    새 질의의 물량을 이 프로브로 볼 수 없었다.

    `BSKY_PROBE_SPEC` 이 있으면 그것만 잰다 — 후보 질의를 코드 수정 없이 재 보는
    자리다(board.yml `codes` 입력). 꼴은 `질의[|키=값...]` 을 `;` 로 잇는 것:
    `quant trading|lang=en; OpenAI|sort=top|since=24` (since 는 몇 시간 전부터).
    """
    spec = os.environ.get('BSKY_PROBE_SPEC', '').strip()
    if spec:
        out = []
        for item in spec.split(';'):
            parts = [x.strip() for x in item.split('|') if x.strip()]
            if not parts:
                continue
            extra = {}
            for kv in parts[1:]:
                k, _, v = kv.partition('=')
                extra[k.strip()] = v.strip()
            out.append((parts[0], extra))
        return out
    try:
        from . import bsky as BS
        cfg = BS.load_cfg()
        qs = list(cfg.get('queries') or [])
        for sec in cfg.get('sections') or []:
            qs += [q for q in sec.get('queries') or [] if q not in qs]
        return [(q, {}) for q in qs] or [(q, {}) for q in DIGEST_TOPICS]
    except Exception:                             # noqa: BLE001
        return [(q, {}) for q in DIGEST_TOPICS]


def _get(s, url, params=None, want='text'):
    """한 번만 부른다. 재시도하지 않는다 — 진단이고, 남의 서버다."""
    r = s.get(url, params=params, timeout=TIMEOUT, allow_redirects=True)
    body = r.text if want == 'text' else r.content
    return r.status_code, (r.headers.get('content-type') or ''), body, r.url


def _tweet_token(tid):
    """tweet-result 가 요구하는 token. react-tweet 와 같은 계산식이다.

    ((id / 1e15) * π) 를 36진수로 만들고 0 과 . 을 뺀다. 문서화된 규칙이 아니라
    클라이언트 구현을 옮긴 것이라, 바뀌면 조용히 404 가 난다.
    """
    def b36(x):
        digits = '0123456789abcdefghijklmnopqrstuvwxyz'
        out, ip = '', int(x)
        while ip:
            out, ip = digits[ip % 36] + out, ip // 36
        frac, f = x - int(x), ''
        for _ in range(12):
            frac *= 36
            f += digits[int(frac)]
            frac -= int(frac)
        return (out or '0') + '.' + f
    return b36((int(tid) / 1e15) * 3.141592653589793).replace('0', '').replace('.', '')


# ─────────────────────────── 파서 ───────────────────────────
_ITEM = re.compile(r'<item>(.*?)</item>', re.S)
_TAG = re.compile(r'<[^>]+>')


def parse_rss(xml):
    """Nitter·RSSHub 의 RSS 2.0 → [{text, url, at}]. 제목이 본문이다.

    수집과 분리해 둔다 — 파서는 픽스처로 시험하고, 수집은 러너에서만 돈다.
    """
    import html as H
    out = []
    for raw in _ITEM.findall(xml or ''):
        def one(tag):
            m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', raw, re.S)
            if not m:
                return ''
            v = m.group(1)
            v = re.sub(r'^<!\[CDATA\[|\]\]>$', '', v.strip())
            return H.unescape(_TAG.sub('', v)).strip()
        text, link = one('title'), one('link')
        if not text and not link:
            continue
        out.append(dict(text=text, url=link, at=one('pubDate')))
    return out


def parse_timeline(html):
    """syndication 프로필 타임라인의 __NEXT_DATA__ → 트윗 수. 없으면 0."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or '', re.S)
    if not m:
        return None, '__NEXT_DATA__ 없음'
    try:
        js = json.loads(m.group(1))
    except ValueError as e:
        return None, f'__NEXT_DATA__ 파싱 실패: {e}'
    # 경로는 개편마다 바뀐다. 'entries' 를 어디서든 찾는다.
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ('entries', 'timeline') and isinstance(v, list):
                    found.append(len(v))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(js)
    return (max(found) if found else 0), None


# ─────────────────────────── 진단 ───────────────────────────
def probe_table(log=print):
    """--x-probe 용. 후보를 순서대로 눌러 PASS/FAIL/기록으로 남긴다.

    반환 [(status, label, note)] — status 는 'PASS' / 'FAIL' / '기록'.
    """
    rows = []

    def put(st, label, note):
        rows.append((st, label, note))
        log(f'  {st:<4}  {label:<34} {note}')

    s = session()
    log('X 자동 수집 경로 실측 — 규약 위반 경로 (사용자 확인). 로그인은 하지 않는다')
    log('')
    if not SAMPLE:
        put('기록', 'X 표본 계정', '없음 — XSOURCE_SAMPLE_ACCOUNTS(쉼표 구분)를 넣어야 '
                                 'X 경로 1~3번을 잰다')

    # 1. syndication 프로필 타임라인
    for acct in SAMPLE:
        try:
            code, ctype, body, final = _get(s, SYND_TIMELINE.format(acct=acct))
        except Exception as e:                       # noqa: BLE001
            put('FAIL', f'syndication 타임라인 @{acct}', f'{type(e).__name__}: {e}')
            continue
        if code != 200:
            put('FAIL', f'syndication 타임라인 @{acct}', f'HTTP {code} · {len(body):,}자')
            continue
        n, why = parse_timeline(body)
        if n:
            put('PASS', f'syndication 타임라인 @{acct}', f'entries {n}건 · {len(body):,}자')
        else:
            put('FAIL', f'syndication 타임라인 @{acct}',
                f'HTTP 200 인데 트윗이 없다 — {why or "entries 0"} · {len(body):,}자')
        time.sleep(1)

    # 2. Nitter 인스턴스 RSS
    alive = []
    for host in (NITTER if SAMPLE else []):       # [KBJ P1] 표본 계정 없으면 건너뜀
        url = f'https://{host}/{SAMPLE[0]}/rss'
        try:
            code, ctype, body, final = _get(s, url)
        except Exception as e:                       # noqa: BLE001
            put('FAIL', f'nitter {host}', f'{type(e).__name__}: {str(e)[:70]}')
            time.sleep(1)
            continue
        items = parse_rss(body) if 'xml' in ctype or '<rss' in body[:400] else []
        if code == 200 and items:
            alive.append(host)
            put('PASS', f'nitter {host}',
                f'{len(items)}건 · 최신 "{items[0]["text"][:40]}" · {items[0]["at"][:31]}')
        else:
            put('FAIL', f'nitter {host}',
                f'HTTP {code} · {ctype.split(";")[0]} · {len(body):,}자'
                + (' · RSS 아님' if code == 200 and not items else ''))
        time.sleep(1)

    # 3. RSSHub
    for host in (RSSHUB if SAMPLE else []):       # [KBJ P1] 표본 계정 없으면 건너뜀
        url = f'https://{host}/twitter/user/{SAMPLE[0]}'
        try:
            code, ctype, body, final = _get(s, url)
        except Exception as e:                       # noqa: BLE001
            put('FAIL', f'rsshub {host}', f'{type(e).__name__}: {str(e)[:70]}')
            continue
        items = parse_rss(body)
        put('PASS' if code == 200 and items else 'FAIL', f'rsshub {host}',
            f'HTTP {code} · {len(items)}건'
            + (f' · 최신 "{items[0]["text"][:40]}"' if items else f' · {body[:90]}'))
        time.sleep(1)

    # 4. 단건 본문 — 위에서 id 를 하나라도 얻었을 때만 의미가 있다
    tid = None
    for host in alive:
        try:
            _, _, body, _ = _get(s, f'https://{host}/{SAMPLE[0]}/rss')
            for it in parse_rss(body):
                m = re.search(r'/status/(\d+)', it['url'] or '')
                if m:
                    tid = m.group(1)
                    break
        except Exception:                            # noqa: BLE001
            pass
        if tid:
            break
    if not tid:
        put('기록', 'tweet-result 단건 본문', '시험할 게시물 id 를 못 얻었다 — 위 경로가 다 막혔다')
    else:
        try:
            code, ctype, body, _ = _get(
                s, SYND_TWEET, params={'id': tid, 'token': _tweet_token(tid), 'lang': 'en'})
            has = '"text"' in body
            put('PASS' if code == 200 and has else 'FAIL', 'tweet-result 단건 본문',
                f'id {tid} · HTTP {code}' + (' · text 있음' if has else f' · {body[:90]}'))
        except Exception as e:                       # noqa: BLE001
            put('FAIL', 'tweet-result 단건 본문', f'{type(e).__name__}: {e}')

    # 5. 블루스카이 — X 가 다 막혔을 때의 **합법** 자동 수집 후보.
    #    인증 없는 공개 API 가 설계이므로 규약 위반이 아니다.
    log('')
    log('  ── 대안: 블루스카이 (공개 API · 무료 · 규약 위반 아님) ──')
    bsky_ok = 0
    # API 거부와 **IP 차단**은 다르다. 공개 API 는 오류도 JSON 으로 준다 —
    # 폰트 링크가 붙은 HTML 403 이 오면 그건 앞단(클라우드 IP 차단)이다.
    # 그 구분이 안 되면 '블루스카이가 안 된다' 와 '러너에서 안 된다' 를 헷갈린다.
    for label, host, method, params in (
            # 검색은 api 호스트로. public 쪽은 403 + HTML 이다 (위 주석의 실측).
            ('searchPosts "HBM memory"', BSKY_SEARCH_HOST,
             'app.bsky.feed.searchPosts', {'q': 'HBM memory', 'limit': 25}),
            ('searchPosts "semiconductor"', BSKY_SEARCH_HOST,
             'app.bsky.feed.searchPosts', {'q': 'semiconductor', 'limit': 25}),
            # public 쪽이 아직 searchPosts 를 막는지 매번 같이 본다 — 열리면
            # 공식 공개 호스트로 돌아가는 편이 낫다.
            ('searchPosts (public 호스트)', 'public.api.bsky.app',
             'app.bsky.feed.searchPosts', {'q': 'TSMC', 'limit': 25}),
            ('getProfile @bsky.app', 'public.api.bsky.app',
             'app.bsky.actor.getProfile', {'actor': 'bsky.app'}),
    ):
        url = BSKY.format(method=method).replace('public.api.bsky.app', host)
        try:
            code, ctype, body, _ = _get(s, url, params=params or None)
        except Exception as e:                       # noqa: BLE001
            put('FAIL', f'bsky {label}', f'{type(e).__name__}: {str(e)[:80]}')
            time.sleep(1)
            continue
        is_json = 'json' in ctype or body[:1] in ('{', '[')
        if code == 200 and is_json:
            try:
                js = json.loads(body)
            except ValueError:
                js = {}
            posts = js.get('posts') or []
            bsky_ok = max(bsky_ok, len(posts))
            detail = f'{len(posts)}건' if posts else f'키 {sorted(js)[:5]}'
            put('PASS', f'bsky {label}', f'HTTP 200 · JSON · {detail}')
        else:
            kind = ('JSON 오류(API 거부)' if is_json
                    else 'HTML(앞단 차단 — 러너 IP 로 보인다)')
            put('FAIL', f'bsky {label}',
                f'HTTP {code} · {ctype.split(";")[0]} · {kind} · {body[:120]}')
        time.sleep(1)

    log('')
    ok = [r for r in rows if r[0] == 'PASS']
    log(f'PASS {len(ok)} · FAIL {sum(1 for r in rows if r[0] == "FAIL")} · '
        f'기록 {sum(1 for r in rows if r[0] == "기록")}')
    if alive:
        log(f'살아 있는 nitter 인스턴스: {", ".join(alive)} — 수집기를 붙일 수 있다')
    else:
        log('X 자동 수집 경로는 전부 막혔다. 손 공유(인박스)를 안 쓴다면 X 는 소스가 '
            '될 수 없다 — XDIGEST.md 8장 4번의 결론을 그렇게 적는다')
    if bsky_ok:
        log(f'블루스카이는 인증 없이 {bsky_ok}건을 줬다 — 합법·무료 자동 수집이 '
            '가능하다. 다만 X 계정들이 거기 있는지는 계정별로 확인해야 한다')
    return rows


# ─────────────────────── 블루스카이 설계 실측 ───────────────────────
# 막힌 응답과 '없는 것' 은 다르다. 이 코드가 오면 **판정하지 않는다** —
# 403/429 를 '계정이 없다' 로 읽으면 없는 계정을 보고하게 된다(2026-09-22 실측에서
# 실제로 그랬다: 주제 질의가 전부 403 인데 계정 10건은 '없다' 로 찍혔다).
#
# 호출 자체는 `ingest/bsky.call` 하나를 쓴다. 예전에는 여기에 같은 호스트 목록·같은
# 막힘 판정을 한 벌 더 두었고, 검토가 수집 쪽에서 잡아 고친 결함 둘이 이쪽에는
# 그대로 남아 있었다 — 막힘을 누적 플래그(`blocked or code in BLOCKED`)로 두어
# '앞 호스트 403 + 뒤 호스트 전송 오류' 를 막힘으로 라벨한 것과, 뒤 호스트 사유로
# 앞 호스트 사유를 덮어 'api 429 · public 403' 을 '둘 다 403' 과 같게 만든 것이다.


def _bsky(s, method, params, hosts=None, log=None):
    """공개 엔드포인트 한 번. 반환 `(json, 사유, blocked)`.

    `bsky.call` 의 얇은 어댑터다 — 이 모듈의 호출부가 기대하는 3튜플로만 바꾼다.
    호스트를 둘 다 눌러 보는 것, 403/429/503 을 막힘으로 보되 **둘 다** 막혔을
    때만 `blocked=True` 를 내는 것, 사유를 호스트마다 따로 남기는 것은 모두
    그쪽 규칙이다.

    실측은 재시도하지 않는다(`retries=0`). 잰 값이 '한 번 눌러서 무엇이 왔나'
    여야 하는데 재시도가 섞이면 그 값이 흐려진다. 호스트를 바꿀 때 2초 쉰다 —
    짧은 시간에 여러 메서드를 눌러 한도에 걸리기 쉽다.

    **받아 냈어도 앞 호스트의 사유는 버리지 않는다.** 이 모듈의 호출부는 `why`
    가 있으면 '못 받았다' 로 읽으므로 반환에 실을 수 없다 — 그래서 `log` 로
    내보낸다. 폴백 호스트가 받아 준 날의 앞 호스트 429 는 주 호스트가 죽기
    시작한 신호인데, 200 을 받은 순간 버리면 어디에도 남지 않는다(2장 6번).
    """
    from . import bsky as BS
    js, ok, blocked, why_ = BS.call(method, params, s=s, hosts=hosts,
                                    retries=0, gap=2)
    if ok and why_ and log:
        log(f'  {method} 은 폴백 호스트로 받았다 — {why_}')
    return (js, None, False) if ok else (None, why_, blocked)


def _age_hours(iso, now):
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (now - d).total_seconds() / 3600


def probe_bsky(log=print):
    """A 안(블루스카이)의 설계를 가르는 두 질문을 잰다.

    1. 예시의 12계정이 블루스카이에 있나 — 있으면 계정별 수집(getAuthorFeed)을
       더한다. 없으면 주제 검색만으로 간다.
    2. 주제 검색이 하루치 물량을 주나 — 24시간 안의 건수를 센다. 다이제스트의
       밀도가 곧 이 숫자다.

    판정하지 않고 **숫자를 남긴다**. 어느 쪽으로 설계할지는 이 표를 보고 정한다.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    rows = []

    def put(st, label, note):
        rows.append((st, label, note))
        log(f'  {st:<4}  {label:<30} {note}')

    s = session()
    log('블루스카이 설계 실측 — 계정이 있나 · 주제가 물량을 주나')
    log('')
    # 주제부터 잰다. 계정 열둘을 먼저 돌면 그동안 한도에 걸려 주제가 통째로
    # 403 이 된다 — 2026-09-22 첫 실측이 그랬다. 물량이 더 중요한 숫자다.
    log('  ── 1. 주제 검색이 하루치를 주나 (24시간 안 건수) ──')
    day_total, topic_blocked = 0, 0
    topics = _probe_topics()
    custom = bool(os.environ.get('BSKY_PROBE_SPEC', '').strip())
    for q, extra in topics:
        params = {'q': q, 'limit': 100, 'sort': extra.get('sort', 'latest')}
        if extra.get('lang'):
            params['lang'] = extra['lang']
        if extra.get('since'):
            params['since'] = (now - timedelta(hours=float(extra['since']))
                               ).strftime('%Y-%m-%dT%H:%M:%SZ')
        label = q + ''.join(f' {k}={v}' for k, v in extra.items())
        js, why, blocked = _bsky(s, 'app.bsky.feed.searchPosts', params, log=log)
        if js is None:
            topic_blocked += 1 if blocked else 0
            put('기록' if blocked else 'FAIL', f'"{label}"',
                ('막힘(한도로 본다) — ' if blocked else '') + (why or '사유 없음'))
            time.sleep(5 if blocked else (4 if custom else 2))
            continue
        posts = js.get('posts') or []
        fresh, langs = 0, {}
        for p in posts:
            rec = p.get('record') or {}
            hrs = _age_hours(rec.get('createdAt'), now)
            if hrs is not None and hrs <= 24:
                fresh += 1
            for lg in (rec.get('langs') or ['?'])[:1]:
                langs[lg] = langs.get(lg, 0) + 1
        day_total += fresh
        top = ', '.join(f'{k} {v}' for k, v in
                        sorted(langs.items(), key=lambda kv: -kv[1])[:3])
        likes = sorted(((p.get('likeCount') or 0) for p in posts), reverse=True)
        put('PASS' if fresh else 'FAIL', f'"{label}"',
            f'{len(posts)}건 중 24시간 안 {fresh}건 · 언어 {top}'
            + (f' · 좋아요 상위 {likes[:3]}' if likes else ''))
        if custom and posts:
            # 후보 질의를 고를 때는 건수만으로 모자란다 — 무엇이 걸리는지 본다.
            for p in posts[:3]:
                t = ((p.get('record') or {}).get('text') or '').replace('\n', ' ')
                log(f'          · {t[:110]}')
        time.sleep(4 if custom else 2)
    if custom:
        log('')
        log(f'주제 24시간 합 {day_total}건 (BSKY_PROBE_SPEC 질의 {len(topics)}개) — 계정 실측은 건너뛴다')
        return rows

    log('')
    log('  ── 2. 예시 12계정을 찾을 수 있나 ──')
    found, acct_blocked = [], 0
    for h in DIGEST_ACCOUNTS:
        js, why, blocked = _bsky(s, 'app.bsky.actor.getProfile',
                                 {'actor': f'{h.lower()}.bsky.social'}, log=log)
        hit = (js['handle'], js.get('followersCount'), '직접') if (js and js.get('handle')) else None
        if hit is None and not blocked:
            time.sleep(2)
            js2, _w2, blocked = _bsky(s, 'app.bsky.actor.searchActors',
                                      {'q': h, 'limit': 5}, log=log)
            for a in (js2 or {}).get('actors') or []:
                # 핸들·표시이름에 그 이름이 든 것만 후보로 본다. 지어내지 않는다.
                blob = f'{a.get("handle", "")} {a.get("displayName", "")}'.lower()
                if h.lower().replace('_', '') in blob.replace('_', '').replace('.', ''):
                    hit = (a['handle'], a.get('followersCount'), '검색')
                    break
        if hit:
            found.append(hit[0])
            put('PASS', f'@{h}', (f'{hit[0]} · 팔로워 {hit[1]:,} · {hit[2]}'
                                  if hit[1] is not None else f'{hit[0]} · {hit[2]}'))
        elif blocked:
            # **없는 것으로 세지 않는다.** 막힌 것과 없는 것은 다르다.
            acct_blocked += 1
            put('기록', f'@{h}', f'막혀서 판정 못 함 — {why}')
            time.sleep(5)
            continue
        else:
            put('FAIL', f'@{h}', '블루스카이에 없다 (직접·검색 둘 다 200, 결과 없음)')
        time.sleep(2)

    log('')
    judged = len(DIGEST_ACCOUNTS) - acct_blocked
    log(f'계정 {len(found)}/{judged}판정' + (f' · {acct_blocked}건은 막혀서 판정 못 함'
                                          if acct_blocked else '')
        + f' · 주제 24시간 합 {day_total}건(질의 {len(topics)}개, 중복 포함)'
        + (f' · 주제 {topic_blocked}건 막힘' if topic_blocked else ''))
    if found:
        log(f'찾은 계정: {", ".join(found)} — getAuthorFeed 로 계정별 수집을 더한다')
    if acct_blocked or topic_blocked:
        log('막힌 줄은 "없다" 가 아니다. 공개 API 의 한도로 본다 — 수집기는 질의를 '
            '줄이고(주제 3~4개) 사이를 벌려야 한다. 이 프로브를 시간을 두고 다시 돈다')
    if not found and not acct_blocked:
        log('예시 계정은 블루스카이에 없다. 주제 검색만으로 간다 — 다이제스트의 '
            '"└ 출처 계정" 줄은 X 계정이 아니라 블루스카이 계정이 된다')
    return rows
