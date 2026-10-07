#!/usr/bin/env python3
"""
X 다이제스트 렌더 — `facts.json` + `posts.json` → `digest.txt` 와 발송할 통 목록.

계약은 `docs/XDIGEST.md` 1장(구조·기호·상한·정렬·분할·파스 모드)과 3-4·3-6.
여기가 하는 일은 **조립과 셈**이다. 모델은 `•`·`»` 문장만 만들고, 구획·기호·
`└` 출처 계정·`(@계정)`·건수·순서·종목코드·통 나누기는 전부 이 파일이 한다
(CLAUDE.md 2장 3번 — LLM 에 숫자·순위를 맡기지 않는다).

## 구획은 셋이다

예시(9장)는 넷이었지만 넷째 `■ <계정>의 24시간 인사이트` 는 대상 계정이
블루스카이에 없어서 빼기로 했다(8장 12번). 남은 셋은 내용이 없어도 제목을
남기고 그 아래 사유 한 줄을 적는다 — `telegram.py` `_compose` 는 반대로 빈
구획의 제목을 지우는데, 그쪽은 구획이 가변이고 여기는 고정이라 규약이 다르다.
그래서 `_compose` 를 재사용하지 않는다(1장 표).

## 별도 구획 — `facts.json` 의 `sections` 가 있을 때만 (D-NEXT-Q)

`■ 투자 인사이트` 뒤에 `■ AI 최신 뉴스` · `■ 퀀트·백테스트` 처럼 구획마다 `■` 하나가
붙는다. 줄 모양은 단독 소식과 같다 — `• 사실 (@계정)`. 순서는 모델 출력 순서
(중요도)이고, 이것은 소주제 안 `•` 와 같은 규약이다(1장 표). 게시물이 0건인 날도
제목을 남기고 사유 한 줄(`해당 없음 — 수집 구간 내 게시물 0건`)을 적는다 — 빈
구획을 조용히 지우지 않는 것은 위 셋과 같다. 머리 2행은 구획마다 건수를 적는다
(`반도체·AI 68건 · AI뉴스 5건 · 퀀트 2건 분석`). `sections` 가 없는 facts.json
(구획이 생기기 전)은 예전 모양 그대로 그린다.

## `└` 와 `(@계정)` 은 모델이 아니라 여기가 적는다

줄마다 딸린 `post_ids` 로 `posts.json` 을 찾아 계정을 뽑는다. 순서는 **그
소주제 게시물 수 내림차순 → 계정명 오름차순(대소문자 무시)** — 예시의 `└`
순서에는 규칙이 없어 1장이 이것으로 고정했다. 핸들은 **블루스카이 것**이다.
X 핸들을 적지 않는다 — 수집원이 블루스카이라 우리가 아는 계정이 그것뿐이고,
X 핸들을 적으면 없는 것을 아는 척하는 것이다(2장 1번).

`post_ids` 에 `posts.json` 에 없는 id 가 있으면 그 줄의 출처가 비는데, 조용히
넘기지 않고 결손 줄에 건수를 적는다(2장 6번).

## 모델이 준 문자열은 한 줄로 누른다

`themes[].title` · `facts[].text` · `standalone[].text` · `views[].text` 는
게시물 본문에서 온 문장이고, 수집원이 키워드 검색이라 누구든 `HBM` 이 든 글을
올릴 수 있다(2장 (b')). 본문에 `\n■ …` 이 섞여 있으면 가짜 구획이 생기고
`split` 이 그것을 구획 경계로 읽어 `└` 가 자기 `•` 에서 떨어진다. 검증(3-3)은
수치·계정·티커 문자열만 보므로 줄바꿈은 거기서 걸리지 않는다. 그래서 줄에 넣기
전에 `_one_line` 으로 누르고, 그래도 새는 것이 있는지 `stray()` 로 한 줄
점검한다.

## 분할은 `■` 단위

`■` 하나가 4,096자를 넘을 때만 `▸`(공통 테마) 또는 `•`(다른 구획) 경계에서
나눈다. `▸` 제목과 첫 `•` 사이, `•` 와 `└` 사이에서는 자르지 않는다. 이어지는
통은 첫 줄에 `X 다이제스트 (이어서) · ■ <구획>` 을 붙이고 **통 번호는 붙이지
않는다** — `telegram.send()` 의 `_split` 이 한 번 더 나누면 틀린 번호가 되기
때문이다(1장 '4,096자 분할' 3번).

나누는 입력은 `digest.txt` 본문 하나다(`split`). 발송 재시도가 다시 렌더하지
않고 같은 통 목록을 얻어야 해서다(3-5).

## facts.json 이 어떤 모양인가

3-2 의 ②③④ 출력을 한 파일로 모은 것으로 읽는다.

```json
{"asof": "2026-09-21",
 "counts": {"analyzed": 68},
 "missing": ["본문 미수집 6건"],
 "themes": [{"title": "…", "facts": [{"text": "…", "post_ids": ["at://…"]}]}],
 "standalone": [{"text": "…", "post_ids": ["at://…"]}],
 "views": [{"text": "…", "fact_ids": ["…"]}]}
```

`counts.analyzed` 는 `themes.json` 이 센 '배정된 게시물 수' 다(1장 머리 세 줄
표). 없으면 이 파일이 `•` 줄에 실린 게시물 id 를 세어 쓰고, 그 두 값은 검증에서
빠진 줄만큼 다르다 — 그래서 있으면 그것을 믿는다. `views` 는 `fact_ids` 만
가지므로 출처 계정을 적지 않는다(1장 — 그 구획은 전부 해석이다).
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from ..engine.config import ROOT
from ..report.telegram import TG_LIMIT

KST = timezone(timedelta(hours=9))
# 3장 파일 배치. board/state/xdigest/YYYYMMDD/ 아래에 단계마다 하나씩 남는다.
STATE = os.path.join(ROOT, 'state', 'xdigest')
# 국내 종목 별칭 사전(1장 문체 · 3-3 4번). 커밋된 파일이고, 없으면 코드를 붙이지
# 않는다 — 없는 사전을 짐작해 코드를 지어내지 않는다(2장 1·4번).
NAMES_PATH = os.path.join(ROOT, 'knowledge', 'xdigest_names.yaml')

TITLE = 'X 24시간 다이제스트'
CONT = 'X 다이제스트 (이어서)'
SCOPE = '반도체·AI'
SEND_AT = '08:00'

SEC_THEMES = '공통 테마'
SEC_ALONE = '주목할 단독 소식'
SEC_VIEWS = '투자 인사이트'
SECTIONS = (SEC_THEMES, SEC_ALONE, SEC_VIEWS)

# 빈 구획의 사유 한 줄. 첫 줄은 1장 표의 예시 문구 그대로다.
EMPTY_WHY = {
    SEC_THEMES: '해당 없음 — 2개 이상 계정이 겹친 주제 없음',
    SEC_ALONE: '해당 없음 — 단독으로 남은 게시물 없음',
    SEC_VIEWS: '해당 없음 — 종합할 사실 줄 없음',
}

# 별도 구획(D-NEXT-Q)의 빈 사유 한 줄. 건수가 들어가므로 `stray()` 는 모양(정규식)
# 으로 알아본다. 무엇이 빠졌는지 읽는 사람이 구획 제목 바로 아래서 안다.
SECTION_EMPTY = {
    'none': '해당 없음 — 수집 구간 내 게시물 0건',
    'irrelevant': '해당 없음 — 게시물 {n}건 중 이 구획 소식 없음',
    'no_lines': '해당 없음 — 관련 게시물 {n}건에서 옮길 소식 없음',
    'failed': '해당 없음 — 게시물 {n}건을 받았으나 분석하지 못함',
}
_SECTION_EMPTY_RE = re.compile('^(?:' + '|'.join(
    re.escape(v).replace(re.escape('{n}'), r'\d+') for v in SECTION_EMPTY.values())
    + ')$')

# 1장 '건수 상한과 정렬'. 넘치면 뒤에서 버리고 결손 줄에 건수를 적는다.
# `section` 은 별도 구획 하나의 `•` 상한이다(D-NEXT-Q). 분석이 설정 `max_items` 로
# 먼저 자르고, 여기는 facts.json 을 손으로 고친 날의 안전망이다.
LIMITS = {'themes': 8, 'facts': 5, 'standalone': 12, 'views': 6, 'section': 8}

# 분석이 실패한 날 대신 보내는 것(3-6). 사실이 없으니 사실을 적지 않고, 읽는
# 사람이 직접 가 볼 수 있게 계정별 건수와 URL 만 준다.
FALLBACK_COUNTS = '계정별 건수'
FALLBACK_URLS = '창 안 URL'
FALLBACK_URL_MAX = 40
# 계정 줄 상한. 2026-09-22 첫 실발송에서 187건이 **152개 계정**으로 흩어져 목록이
# 152줄이 됐다 — 1건짜리가 130개였다. 휴대폰에서 스크롤로 다 넘어가지 않는다
# (사용자가 표 21행을 같은 이유로 뺐다, D-084). 건수 많은 순으로 자르고 몇 개를
# 잘랐는지 적는다. URL 목록은 그대로 둔다 — 읽는 사람이 직접 가 볼 곳이라 그쪽이
# 이 경로의 값이다.
FALLBACK_ACCT_MAX = 15
# 결손 줄에 실을 사유의 길이. 전문은 state 의 themes·verify.json 에 남는다.
WHY_MAX = 90

# 본문 줄이 시작할 수 있는 기호. `stray()` 가 이것으로 가짜 구획을 잡는다.
MARKS = ('■ ', '▸ ', '• ', '» ', '└ ')


# ─────────────────────────── 시각 ───────────────────────────
def _dt(v):
    """ISO 문자열 → KST datetime. 못 읽으면 None — 0 으로 채우지 않는다."""
    if isinstance(v, datetime):
        return v.astimezone(KST)
    try:
        d = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.replace(tzinfo=KST)).astimezone(KST)


def _md(d):
    return f'{d.month}/{d.day}'


def _stamp(d):
    return f'{d.month}/{d.day} {d:%H:%M}'


# ─────────────────────────── 모델 문자열 ───────────────────────────
def _one_line(v):
    """모델이 준 문자열을 한 줄로 누른다(`_missing_line` 이 하는 것과 같은 처리).

    수집원이 블루스카이 키워드 검색이라 누구나 `HBM` 이 든 글을 올릴 수 있다
    (2장 (b')). 그 본문을 옮긴 한 줄에 `\\n■ …` 이 섞여 있으면 `digest.txt` 에
    가짜 구획이 생기고, `split` 이 그것을 구획 경계로 읽어 `└` 가 자기 `•` 에서
    떨어진 통에 실린다 — 1장 3번이 금지한 경계다. 기호 없는 줄이 실리면 머리
    2행의 건수와도 어긋난다. 그래서 줄에 넣기 전에 여기서 누른다.
    """
    return ' '.join(str('' if v is None else v).split())


# 줄 앞 기호. 모델이 가끔 자기 기호를 붙여 준다 — 2026-09-23 미리보기의
# `■ AI 최신 뉴스` 8줄이 전부 `• • Anthropic…` 으로 나갔다. 기호는 렌더가 붙인다.
# `-`·`*` 는 뒤에 빈칸이 있을 때만 기호다 — `-3.6%` 의 음수 부호를 떼면 수치가 바뀐다.
_LEAD = re.compile(r'^(?:[•·▸»■]\s*|[-*]\s+)+')


def _item(v):
    """줄 본문 — 한 줄로 누르고 앞에 붙은 기호를 뗀다."""
    return _LEAD.sub('', _one_line(v))


# ─────────────────────────── 설정·사전 ───────────────────────────
def opt(cfg, key, default):
    v = (cfg or {}).get(key)
    return default if v in (None, '') else v


def _limits(cfg):
    out = dict(LIMITS)
    out.update({k: v for k, v in (opt(cfg, 'limits', {}) or {}).items()
                if k in LIMITS and isinstance(v, int) and v > 0})
    return out


def load_names(path=NAMES_PATH, log=print):
    """별칭 사전 → `{표제어: 종목코드}`. 파일이 없으면 빈 것.

    사전 모양은 표제어마다 `{code, aliases}` 다(3-3 4번). `names:` 래퍼 아래에서는
    코드만 적힌 짧은 모양도 받는다 — 사람이 손으로 채우는 파일이라 둘 다 나온다.

    **코드는 6자리 숫자만 받는다.** `names:` 래퍼 없이 쓴 파일(이 레포는
    `themes.yaml` 이 `meta:`, `sector_map.yaml` 이 `map:` 래퍼라 관례가 섞인다)
    에서는 `version: 1` 같은 메타 한 줄이 표제어로 들어와, `with_codes` 가
    3글자 이상인 'version' 뒤에 `(1)` 을 끼워 넣는다 — 지어낸 코드가 사실 줄에
    실리는 것이라 여기서 막는다(2장 4번). 래퍼가 없으면 스칼라 값도 받지
    않는다(메타 줄과 구분할 근거가 없다). 매핑이 아닌 파일(리스트 등)은 빈
    사전으로 보고 사유를 남긴다 — `.items()` 에서 터지면 그날 다이제스트가 없다.
    """
    if not os.path.exists(path):
        return {}
    import yaml
    with open(path, encoding='utf-8') as f:
        y = yaml.safe_load(f) or {}
    wrapped = isinstance(y, dict) and isinstance(y.get('names'), dict)
    body = y['names'] if wrapped else y
    if not isinstance(body, dict):
        log(f'  별칭 사전이 매핑이 아니다 ({path}) — 종목코드를 붙이지 않는다')
        return {}
    out, bad = {}, 0
    for name, v in body.items():
        if not wrapped and not isinstance(v, dict):
            continue                    # 래퍼 없는 파일의 스칼라는 메타 줄일 수 있다
        code = v.get('code') if isinstance(v, dict) else v
        if not name or code is None:
            continue
        if not re.fullmatch(r'\d{6}', str(code)):
            bad += 1
            continue
        out[str(name)] = str(code)
    if bad:
        log(f'  별칭 사전에 6자리가 아닌 코드 {bad}건 ({path}) — 그 표제어는 뺀다')
    return out


def with_codes(text, names):
    """국내 종목 표제어 뒤에 사전의 코드를 붙인다 — `한미반도체(042700)`.

    모델은 코드를 쓰면 검증에 걸리고(3-3 5번) 여기가 붙인다. 한 줄에 같은
    이름이 여러 번 나오면 **처음 하나에만** 붙인다 — 같은 줄에서 같은 코드를
    두 번 읽을 이유가 없다. 이미 괄호가 따라오는 자리는 건드리지 않는다.
    표제어 3글자 미만은 사전이 있어도 쓰지 않는다(3-3 4번과 같은 문턱).

    '한 줄에 한 번' 은 **코드 단위**다. `하이닉스` 와 `SK하이닉스` 처럼 한
    코드에 표제어가 둘 있으면(영문명·약칭을 표제어로도 적은 손입력) 표제어마다
    붙이면 같은 괄호가 한 줄에 두 번 나온다.
    """
    used = set()
    for name in sorted(names or {}, key=len, reverse=True):
        code = (names or {}).get(name)
        if not code or len(name) < 3 or code in used:
            continue
        i = text.find(name)
        if i < 0:
            continue
        used.add(code)
        end = i + len(name)
        if text[end:end + 1] == '(':
            continue
        text = text[:end] + f'({code})' + text[end:]
    return text


# ─────────────────────────── 게시물 ───────────────────────────
def index(posts):
    """`{id: 게시물}`. id 는 AT-URI 문자열이다(공통 계약)."""
    out = {}
    for p in (posts or {}).get('posts') or []:
        if p.get('id'):
            out[str(p['id'])] = p
    return out


def _seen(post_ids, idx):
    """그 줄이 근거로 댄 게시물 중 `posts.json` 에 실제로 있는 것."""
    return [idx[str(i)] for i in (post_ids or []) if str(i) in idx]


def accounts(post_ids, idx):
    """출처 계정 — 게시물 수 내림차순 → 계정명 오름차순(대소문자 무시)."""
    n = {}
    for p in _seen(post_ids, idx):
        h = (p.get('account') or '').strip()
        if h:
            n[h] = n.get(h, 0) + 1
    return [h for h, _ in sorted(n.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def _early(post_ids, idx, start):
    """창 시작보다 이른 게시물이면 `(게시 9/18)`. 날짜가 여럿이면 전부 적는다."""
    if not start:
        return ''
    days = sorted({d.date() for d in (_dt(p.get('posted_at')) for p in _seen(post_ids, idx))
                   if d and d < start})
    return f"(게시 {'·'.join(_md(d) for d in days)})" if days else ''


def _newest(post_ids, idx):
    """그 줄의 게시물 중 가장 최근 시각. 모르면 None — 뒤로 보낸다."""
    ts = [d for d in (_dt(p.get('posted_at')) for p in _seen(post_ids, idx)) if d]
    return max(ts) if ts else None


# ─────────────────────────── 머리 세 줄 ───────────────────────────
def _missing_line(items):
    """결손 줄. 제목보다 위에 둔다 — 무엇이 빠졌는지 모르고 읽으면 안 된다."""
    items = [str(x).strip().replace('\n', ' ') for x in items if str(x).strip()]
    return f"빠진 것 {len(items)}건 — {' · '.join(items)}" if items else None


def _span(posts, idx=None):
    """`커버 구간:` — 창 안 게시물의 `posted_at` 최소~최대(1장의 정의).

    `coverage.first/last` 가 없거나 `fromisoformat` 이 못 읽는 표기면 **렌더가
    직접 센다.** 남이 빼먹은 값 때문에 '창 안 게시물 없음' 이라고 단정하면 바로
    위 2행의 'N건 수집' 과 모순되고, 그 '없음' 은 사실이 아니다(2장 1번).
    게시물은 있는데 시각을 하나도 못 읽으면 모른다고 적는다.
    """
    cov = (posts or {}).get('coverage') or {}
    first, last = _dt(cov.get('first')), _dt(cov.get('last'))
    if not (first and last):
        inside = [p for p in (index(posts) if idx is None else idx).values()
                  if p.get('in_window') is not False]
        ts = [d for d in (_dt(p.get('posted_at')) for p in inside) if d]
        if ts:
            first, last = min(ts), max(ts)
        elif inside:
            return f'커버 구간: 게시 시각을 읽지 못함 {len(inside)}건'
        else:
            return '커버 구간: 창 안 게시물 없음'
    return f'커버 구간: {_stamp(first)} ~ {_stamp(last)} KST'


# 수집기가 남기는 질의 결손 한 줄 — `질의 "HBM" 막힘 — <호스트별 사유>` (bsky.collect).
_QFAIL = re.compile(r'^질의 "(.+?)" (막힘|실패)')
# 파싱에서 뺀 응답 — `응답 32건은 uri·핸들·createdAt·text 중 하나가 없어 …`
_QDROP = re.compile(r'^응답 (\d+)건은 .*파싱에서 뺐다')


def _short(w, n=80):
    """사유 한 조각을 3행에 싣는 꼴로 — HTML 을 벗기고 한 줄로, 길면 자른다."""
    w = re.sub(r'<[^>]+>', ' ', str(w))
    w = re.sub(r'\s+', ' ', w).strip()
    return w if len(w) <= n else w[:n].rstrip() + '…'


def _coverage_line(posts, idx=None):
    """3행. 커버 구간과 **코드가 아는** 결손만.

    모르는 것을 '없음' 이라 쓰지 않는다(2장 1번). 다만 **발송문은 수집 로그가
    아니다.** 2026-09-23 미리보기에서 막힌 질의 7개의 호스트별 403 HTML 이
    gaps 와 errors 로 두 번씩 3행에 실려 1통이 6,000자를 넘었다 — 사용자가
    '검증·산출 과정 내용이 섞여 들어가지 않게' 하라고 한 바로 그것이다.
    그래서 질의 결손은 **이름만 모아** 한 조각으로 적고(무엇이 빠졌는지는 그대로
    드러난다), 원문 사유는 posts.json 의 errors·gaps 와 실행 로그에 남긴다.
    폴백 호스트로 받은 줄은 결손이 아니라 싣지 않는다.

    `gaps`·`errors` 만 보면 안 된다. 수집기는 429/403 으로 막힌 질의를 건수
    (`blocked`)와 한 줄 사유(`cut`)로만 적기도 한다(공통 posts.json 계약). 그 둘을
    빼면 막힌 날에도 3행이 '미수집 구간 없음' 이 된다 — 막힌 것과 없는 것을 섞는
    것이다. '없음' 을 쓸 자격은 소스가 `blocked: 0` · `cut: null` 을 실제로 적었을
    때만 있다.
    """
    cov = (posts or {}).get('coverage') or {}
    src = (posts or {}).get('sources') or {}
    why, blocked_q, failed_q = [], [], []

    def take(text, prefix=''):
        text = str(text or '').strip()
        if not text:
            return
        m = _QFAIL.match(text)
        if m:
            bucket = blocked_q if m.group(2) == '막힘' else failed_q
            if m.group(1) not in bucket:
                bucket.append(m.group(1))
            return
        if '폴백으로 받음' in text:
            return                              # 받았다 — 결손이 아니다
        d = _QDROP.match(text)
        if d:
            why.append(f'형식이 맞지 않는 응답 {d.group(1)}건 제외')
            return
        why.append(prefix + _short(text))

    for g in cov.get('gaps') or []:
        take((g or {}).get('why'))
    for name, s in sorted(src.items()):
        s = s or {}
        for e in s.get('errors') or []:
            take(e, f'{name} ')
        blocked = s.get('blocked')
        if (isinstance(blocked, int) and not isinstance(blocked, bool) and blocked > 0
                and not blocked_q):
            why.append(f'{name} 막힌 질의 {blocked}건')
        if s.get('cut'):
            why.append(f"{name} {_short(_one_line(s['cut']))}")
    if blocked_q:
        why.append(f'접속 제한으로 못 받은 질의 {len(blocked_q)}개: {", ".join(blocked_q)}')
    if failed_q:
        why.append(f'오류로 못 받은 질의 {len(failed_q)}개: {", ".join(failed_q)}')
    why = list(dict.fromkeys(w for w in why if w))
    if not why:
        clean = bool(src) and all((s or {}).get('blocked') == 0 and (s or {}).get('cut') is None
                                  for s in src.values())
        why = ['미수집 구간 없음' if clean else '미수집 구간을 알 수 없음']
    return f"({_span(posts, idx)}. {' · '.join(why)})"


def head(facts, posts, cfg=None, missing=()):
    """결손 줄 + 머리 세 줄. 건수는 여기가 센다(1장)."""
    idx = index(posts)
    asof = (facts or {}).get('asof') or (posts or {}).get('asof') or ''
    got = sum(1 for p in idx.values() if p.get('in_window') is not False)
    counts = (facts or {}).get('counts') or {}
    cited = len({str(i) for i in _cited(facts) if str(i) in idx})
    analyzed = counts.get('analyzed')
    items = list((facts or {}).get('missing') or []) + list(missing)
    if analyzed is None:
        analyzed = cited
    else:
        try:
            analyzed = int(analyzed)
        except (TypeError, ValueError):
            # 상류가 숫자가 아닌 것을 넣었으면 우리가 센 값을 쓴다(2장 3번).
            items.append('분석 건수를 읽지 못함 — themes.json 확인 필요')
            analyzed = cited
    if analyzed > got:
        # 1장은 머리 건수를 '코드가 센다' 고 못박았다. 수집 건수보다 큰 분석 건수는
        # 상류(analyze)가 모델 출력을 그대로 흘린 값이고, 그것을 그대로 실으면
        # 게시물에 없는 수치가 다이제스트에 나간다(2장 3번). 상한으로 묶고 적는다.
        items.append(f'분석 건수({analyzed})가 수집 건수({got})를 넘음 — themes.json 확인 필요')
        analyzed = got
    # 기본 구획의 말. `scope` 가 없으면 설정의 `topics_label`(xdigest.yaml '표시')
    # 이다 — 전에는 그 키를 읽는 코드가 없어 설정을 고쳐도 2행이 안 바뀌었다.
    label = opt(cfg, 'scope', None) or opt(cfg, 'topics_label', SCOPE)
    secs = _secs(facts)
    if secs:
        # 별도 구획이 있는 날은 구획마다 건수를 적는다(D-NEXT-Q). `관련` 은 뺀다 —
        # 셋을 한 줄에 적으면 말이 길어지고, 뜻은 예전 2행과 같다.
        count = [f'{label} {analyzed}건'] + [
            f"{_one_line(sec.get('label') or sec.get('title'))} "
            f"{_section_count(sec, idx, items)}건" for sec in secs]
        line2 = f"게시물 {got}건 수집 / {' · '.join(count)} 분석"
    else:
        line2 = f"게시물 {got}건 수집 / {label} 관련 {analyzed}건 분석"
    lines = []
    m = _missing_line(items)
    if m:
        lines.append(m)
    lines += [f"{opt(cfg, 'title', TITLE)} · {asof} {opt(cfg, 'send_at', SEND_AT)} KST",
              line2,
              _coverage_line(posts, idx)]
    return lines


def _secs(facts):
    """facts.json 의 별도 구획 목록. 없으면 빈 목록 — 구획이 생기기 전의 파일이다."""
    return [sec for sec in ((facts or {}).get('sections') or []) if isinstance(sec, dict)]


def _section_count(sec, idx, items):
    """머리 2행의 구획 건수. analyze 가 센 `analyzed` 를 쓰되 코드가 다시 묶는다.

    그 구획 `topic` 으로 창 안에 들어온 게시물보다 크면 상류가 모델 출력을 흘린
    것이다 — 기본 구획의 '분석 건수가 수집 건수를 넘음' 과 같은 처리다(2장 3번).
    숫자가 아니면 0 으로 적고 결손에 사유를 남긴다. 지어내지 않는다.
    """
    name = _one_line(sec.get('label') or sec.get('title'))
    try:
        n = int(sec.get('analyzed') or 0)
    except (TypeError, ValueError):
        items.append(f'{name} 분석 건수를 읽지 못함 — themes.json 확인 필요')
        return 0
    have = sum(1 for p in idx.values()
               if p.get('in_window') is not False and p.get('topic') == sec.get('key'))
    if n > have:
        items.append(f'{name} 분석 건수({n})가 수집 건수({have})를 넘음 — themes.json 확인 필요')
        n = have
    return n


def _cited(facts):
    out = []
    for t in (facts or {}).get('themes') or []:
        for f in (t or {}).get('facts') or []:
            out += list((f or {}).get('post_ids') or [])
    for s in (facts or {}).get('standalone') or []:
        out += list((s or {}).get('post_ids') or [])
    return out


# ─────────────────────────── 구획 ───────────────────────────
def _theme_block(t, idx, names, start, cap):
    """`▸` 소주제 하나 — 제목 · `•` 사실 · `└` 출처 계정.

    본문이 빈 사실은 **cap 을 재기 전에** 버린다. 그래서 그 건수는 `cut`(상한
    초과)과 섞이지 않게 따로 돌려준다 — 빈 것을 조용히 버리면 소주제가 통째로
    사라지는데 2행은 그 게시물들을 '분석 N건' 으로 계속 센다(2장 6번).
    """
    raw = list((t or {}).get('facts') or [])
    facts = [f for f in raw if _one_line((f or {}).get('text'))]
    kept, ids, lost = facts[:cap], [], 0
    lines = [f"▸ {_one_line((t or {}).get('title'))}"]
    for f in kept:
        pid = list(f.get('post_ids') or [])
        ids += pid
        if not _seen(pid, idx):
            lost += 1
        tail = _early(pid, idx, start)
        lines.append(f"• {with_codes(_item(f['text']), names)}"
                     + (f' {tail}' if tail else ''))
    acc = accounts(ids, idx)
    if acc:
        lines.append('└ ' + ' '.join('@' + a for a in acc))
    return lines, ids, len(facts) - len(kept), lost, len(raw) - len(facts)


def _themes(facts, idx, names, start, lim, missing):
    """`■ 공통 테마`. 소주제 정렬은 출처 계정 수 내림차순 → 게시물 수 내림차순."""
    blocks, dropped = [], []
    for t in (facts or {}).get('themes') or []:
        lines, ids, cut, lost, blank = _theme_block(t, idx, names, start, lim['facts'])
        if len(lines) < 2:
            # `•` 가 하나도 없는 소주제는 적을 것이 없다. 다만 통째로 사라지면
            # 읽는 사람이 '분석 N건' 이 어디로 갔는지 알 수 없어 제목을 남긴다
            # (3-6 '검증 실패로 뺀 소주제 1건(제목)' 과 같은 규약).
            dropped.append(_one_line((t or {}).get('title')) or '제목 없음')
            continue
        blocks.append((len(accounts(ids, idx)), len({str(i) for i in ids}),
                       lines, cut, lost, blank))
    blocks.sort(key=lambda b: (-b[0], -b[1]))  # 안정 정렬 — 같으면 모델 출력 순서
    kept = blocks[:lim['themes']]
    if len(blocks) > len(kept):
        missing.append(f'소주제 {len(blocks) - len(kept)}건 생략')
    cut = sum(b[3] for b in kept)
    if cut:
        missing.append(f'소주제 안 사실 {cut}건 생략')
    lost = sum(b[4] for b in kept)
    if lost:
        missing.append(f'출처 게시물을 찾지 못한 줄 {lost}건')
    blank = sum(b[5] for b in kept)
    if blank:
        missing.append(f'본문이 빈 사실 {blank}건 제외')
    if dropped:
        missing.append(f"'•' 가 없어 뺀 소주제 {len(dropped)}건({' · '.join(dropped)})")
    out = []
    for b in kept:
        out += b[2]
    return out


def _standalone(facts, idx, names, start, lim, missing):
    """`■ 주목할 단독 소식`. 게시물 시각 내림차순. 줄 끝에 `(@계정)`."""
    raw = list((facts or {}).get('standalone') or [])
    items = [s for s in raw if _one_line((s or {}).get('text'))]
    if len(raw) > len(items):
        missing.append(f'본문이 빈 단독 소식 {len(raw) - len(items)}건 제외')
    items.sort(key=lambda s: (_newest(s.get('post_ids'), idx)
                              or datetime.min.replace(tzinfo=KST)), reverse=True)
    kept = items[:lim['standalone']]
    if len(items) > len(kept):
        missing.append(f'단독 소식 {len(items) - len(kept)}건 생략')
    out, lost = [], 0
    for s in kept:
        pid = list(s.get('post_ids') or [])
        acc = accounts(pid, idx)
        if not acc:
            lost += 1
        # `(게시 M/D)` 는 `(@계정)` **앞**이다(1장 표).
        tail = [_early(pid, idx, start)]
        if acc:
            tail.append('(' + ' '.join('@' + a for a in acc) + ')')
        out.append(' '.join([f"• {with_codes(_item(s['text']), names)}"]
                            + [x for x in tail if x]))
    if lost:
        missing.append(f'출처 게시물을 찾지 못한 단독 소식 {lost}건')
    return out


def _views(facts, names, lim, missing):
    """`■ 투자 인사이트`. 전부 `»` 다 — 이 구획은 모델의 종합이고 기호가 그것을 말한다."""
    raw = list((facts or {}).get('views') or [])
    items = [v for v in raw if _one_line((v or {}).get('text'))]
    if len(raw) > len(items):
        missing.append(f'본문이 빈 투자 인사이트 {len(raw) - len(items)}건 제외')
    kept = items[:lim['views']]
    if len(items) > len(kept):
        missing.append(f'투자 인사이트 {len(items) - len(kept)}건 생략')
    return [f"» {with_codes(_one_line(v['text']), names)}" for v in kept]


def _section(sec, idx, names, start, lim, missing):
    """별도 구획 하나(D-NEXT-Q) — `• 사실 (@계정)` 줄들, 없으면 사유 한 줄.

    줄 모양은 단독 소식과 같다(`(@계정)` 은 코드가 `post_ids` 에서 뽑는다). 순서는
    모델 출력 순서(중요도) 그대로다 — 소주제 안 `•` 와 같은 규약(1장 표).
    """
    title = _one_line(sec.get('title')) or '별도 구획'
    raw = list(sec.get('items') or [])
    items = [s for s in raw if _one_line((s or {}).get('text'))]
    if len(raw) > len(items):
        missing.append(f'본문이 빈 {title} 줄 {len(raw) - len(items)}건 제외')
    kept = items[:lim['section']]
    if len(items) > len(kept):
        missing.append(f'{title} {len(items) - len(kept)}건 생략')
    out, lost = [], 0
    for s in kept:
        pid = list(s.get('post_ids') or [])
        acc = accounts(pid, idx)
        if not acc:
            lost += 1
        tail = [_early(pid, idx, start)]
        if acc:
            tail.append('(' + ' '.join('@' + a for a in acc) + ')')
        out.append(' '.join([f"• {with_codes(_item(s['text']), names)}"]
                            + [x for x in tail if x]))
    if lost:
        missing.append(f'출처 게시물을 찾지 못한 {title} 줄 {lost}건')
    if out:
        return out
    try:
        pool, n = int(sec.get('pool') or 0), int(sec.get('analyzed') or 0)
    except (TypeError, ValueError):
        pool, n = 0, 0
    if sec.get('error'):
        # 막힌 것과 없는 것은 다르다(2장 6번). 사유는 결손 줄에, 구획 아래는 한 줄.
        missing.append(f"{title} 분석 실패 — {_short_why(sec['error'])}")
        return [SECTION_EMPTY['failed'].format(n=pool)]
    if not pool:
        return [SECTION_EMPTY['none']]
    if not n:
        return [SECTION_EMPTY['irrelevant'].format(n=pool)]
    return [SECTION_EMPTY['no_lines'].format(n=n)]


# ─────────────────────────── 조립 ───────────────────────────
def _short_why(why):
    """결손 줄에 실을 한 줄. 전문은 state 의 themes·verify.json 에 남는다.

    2026-09-22 첫 실발송에서 텔레그램 첫 줄이 이랬다:

        빠진 것 1건 — 분석 실패 — BadRequestError: Error code: 400 -
        {'type': 'error', 'error': {'type': 'invalid_request_error', 'message':
        'This model does not support the effort parameter.'}, 'request_id':
        'req_011CfHqvwCaBQmdNioFkvm1v'}

    읽는 사람에게 `request_id` 와 JSON 은 뜻이 없고, 정작 무슨 일이 났는지는
    가운데 `message` 한 조각에 있다(7장 문체). 그것을 꺼내 쓰고, 못 꺼내면
    앞부분을 자른다. **지우지는 않는다** — 전문은 파일에 그대로 있고 `--check`
    와 로그가 그것을 본다(2장 6번).
    """
    why = ' '.join(str(why or '').split()) or '사유 미기록'
    kind = (re.match(r'^([A-Za-z_]+(?:Error|Exception)):', why) or [None, None])[1]
    m = re.search(r"'message':\s*'([^']+)'", why) or re.search(r'"message":\s*"([^"]+)"', why)
    if m:
        why = f'{kind}: {m.group(1)}' if kind else m.group(1)
    return why if len(why) <= WHY_MAX else why[:WHY_MAX - 1].rstrip() + '…'


def _fallback(facts, posts, cfg, missing):
    """분석이 실패한 날(3-6). 머리 세 줄 + 계정별 건수 + 창 안 URL 목록.

    1장의 `■` 셋을 내지 않는다 — 넣을 사실이 없어 제목 셋과 사유 셋만 남고,
    그러면 읽는 사람이 직접 가 볼 URL 이 밀린다. 아무것도 안 오는 것보다
    낫고 지어낸 것은 없다.
    """
    why = _short_why(str((facts or {}).get('error') or '사유 미기록'))
    missing = [f'분석 실패 — {why}'] + list(missing)
    lines = head({'asof': (posts or {}).get('asof'), 'counts': {'analyzed': 0}},
                 posts, cfg, missing=missing)
    inside = [p for p in index(posts).values() if p.get('in_window') is not False]
    n = {}
    for p in inside:
        h = _one_line(p.get('account')) or '계정 미확인'
        n[h] = n.get(h, 0) + 1
    lines.append(f'■ {FALLBACK_COUNTS}')
    order = sorted(n.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    for h, c in order[:FALLBACK_ACCT_MAX]:
        lines.append(f'• @{h} {c}건')
    if len(order) > FALLBACK_ACCT_MAX:
        rest = order[FALLBACK_ACCT_MAX:]
        lines.append(f'• 외 {len(rest)}개 계정 {sum(c for _, c in rest)}건')
    # 최근 것부터 적는다. 40건에서 잘리므로 어느 쪽을 남기느냐가 곧 순서다.
    inside.sort(key=lambda p: (_dt(p.get('posted_at')) or datetime.min.replace(tzinfo=KST)),
                reverse=True)
    urls = [_one_line(p['url']) for p in inside if p.get('url')]
    lines.append(f'■ {FALLBACK_URLS}')
    lines += [f'• {u}' for u in urls[:FALLBACK_URL_MAX]]
    if len(urls) > FALLBACK_URL_MAX:
        lines.append(f'• 외 {len(urls) - FALLBACK_URL_MAX}건 생략')
    return '\n'.join(lines)


def compose(facts, posts, cfg=None, names=None):
    """`digest.txt` 본문 전체(분할 전). 1행의 `(실제 발송 …)` 부기는 발송이 붙인다(3-5)."""
    names = load_names() if names is None else names
    idx = index(posts)
    start = _dt(((posts or {}).get('window') or {}).get('start'))
    lim = _limits(cfg)
    missing = []
    if not facts or (facts or {}).get('error'):
        return _fallback(facts, posts, cfg, missing)
    body = {SEC_THEMES: _themes(facts, idx, names, start, lim, missing),
            SEC_ALONE: _standalone(facts, idx, names, start, lim, missing),
            SEC_VIEWS: _views(facts, names, lim, missing)}
    extra = [(_one_line(sec.get('title')) or '별도 구획',
              _section(sec, idx, names, start, lim, missing)) for sec in _secs(facts)]
    lines = head(facts, posts, cfg, missing=missing)
    for name in SECTIONS:
        lines.append(f'■ {name}')
        lines += body[name] or [EMPTY_WHY[name]]
    # 별도 구획은 투자 인사이트 뒤다(D-NEXT-Q). 분할(`split`)은 1통 뒤의 `■` 를
    # 한 통에 합치고 넘치면 `■` 마다 나누므로 새 구획도 같은 규칙으로 나간다.
    for name, body_lines in extra:
        lines.append(f'■ {name}')
        lines += body_lines
    return '\n'.join(lines)


# ─────────────────────────── 분할 ───────────────────────────
def _blocks(text):
    """본문 → (머리 줄, [(구획명, 구획 줄)]). 입력은 `digest.txt` 하나다."""
    head_lines, blocks = [], []
    for ln in (text or '').split('\n'):
        if ln.startswith('■ '):
            blocks.append((ln[2:].strip(), []))
        elif blocks:
            blocks[-1][1].append(ln)
        else:
            head_lines.append(ln)
    return head_lines, blocks


def _units(name, body):
    """자르면 안 되는 묶음. 공통 테마는 `▸` 묶음 하나, 나머지는 줄 하나.

    `▸` 제목과 첫 `•` 사이, `•` 와 `└` 사이가 이 묶음 안에 들어가 있어 거기서
    잘릴 수 없다(1장 3번).
    """
    if name != SEC_THEMES:
        return [[ln] for ln in body]
    units, cur = [], []
    for ln in body:
        if ln.startswith('▸') and cur:
            units.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        units.append(cur)
    return units


def _chunks(name, body, limit, lead=None):
    """구획 하나를 여러 통으로. 이어지는 통은 첫 줄에 `(이어서)` — 통 번호는 없다."""
    first, cont = (lead or []) + [f'■ {name}'], [f'{CONT} · ■ {name}']
    out, cur, prefix = [], [], first
    for u in _units(name, body):
        if cur and len('\n'.join(prefix + cur + u)) > limit:
            out.append('\n'.join(prefix + cur))
            cur, prefix = list(u), cont
        else:
            cur += u
    out.append('\n'.join(prefix + cur))
    return out


def split(text, limit=TG_LIMIT):
    """1장 규칙대로 통을 나눈다. 1통 = 머리 + `■ 공통 테마`, 2통 = 나머지 `■`.

    합쳐서 한도를 넘으면 `■` 마다 한 통이고, 그래도 넘치면 `▸`(공통 테마) 또는
    `•` 경계에서 나눈다. 한 묶음이 혼자 한도를 넘는 병적인 경우는 그대로 두고
    `send()` 의 `_split` 이 줄 경계에서 받는다 — 마지막 안전망이지 설계가 아니다.
    """
    head_lines, blocks = _blocks(text)
    if not blocks:
        return [text] if (text or '').strip() else []
    name, body = blocks[0]
    one = '\n'.join(head_lines + [f'■ {name}'] + body)
    parts = [one] if len(one) <= limit else _chunks(name, body, limit, lead=head_lines)
    rest = blocks[1:]
    if rest:
        joined = '\n'.join('\n'.join([f'■ {n}'] + b) for n, b in rest)
        if len(joined) <= limit:
            parts.append(joined)
        else:
            for n, b in rest:
                block = '\n'.join([f'■ {n}'] + b)
                parts += [block] if len(block) <= limit else _chunks(n, b, limit)
    return parts


def render(facts, posts, cfg=None, now=None, names=None):
    """조립 → 분할. 반환은 통 목록(문자열 리스트)이다.

    `now` 는 받아 두지만 본문에는 쓰지 않는다. 1행 시각은 **예정 발송 시각**이고
    실제 발송 시각은 발송 단계가 붙인다(1장 머리 세 줄 표 · 3-5) — 렌더 시점에는
    모르는 값이라 여기서 붙일 수 없다.
    """
    return split(compose(facts, posts, cfg, names=names))


# ─────────────────────────── 파일 ───────────────────────────
def today():
    """기준일은 **KST 달력일**이다. 보드의 `--only-fresh`(최근 거래일)와 다르다 —
    X 는 휴장일에도 돈다(4장 '중복 방지')."""
    return datetime.now(KST).date().isoformat()


def day_dir(asof, make=False):
    d = os.path.join(STATE, str(asof).replace('-', ''))
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def read(asof, name):
    """단계 산출 읽기. 없으면 None — 빈 것으로 꾸미지 않는다."""
    p = os.path.join(day_dir(asof), name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f) if name.endswith('.json') else f.read()


def write_digest(asof, text):
    p = os.path.join(day_dir(asof, make=True), 'digest.txt')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


def cfg_load(path=None):
    """`config/xdigest.yaml`. 없으면 빈 것 — 기본값으로 돈다."""
    path = path or os.path.join(ROOT, 'config', 'xdigest.yaml')
    if not os.path.exists(path):
        return {}
    import yaml
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


# 통 하나가 4,096자를 넘었는지 확인하는 자리(시험·요약이 쓴다).
def over(parts, limit=TG_LIMIT):
    return [i for i, p in enumerate(parts, 1) if len(p) > limit]


def stray(text):
    """본문에서 기호로 시작하지 않는 줄. 있으면 가짜 구획이 섞인 것이다.

    모델 문자열의 줄바꿈은 `_one_line` 이 눌러 없앤다. 그래도 다른 경로로 한 줄이
    새면 `split` 이 그것을 구획 경계로 읽어 조용히 통 순서가 바뀌므로, 발송·렌더가
    한 줄 점검으로 본다(2장 6번). 빈 구획의 사유 줄은 기호 없는 정상 줄이라 뺀다.
    """
    allowed, out, body = set(EMPTY_WHY.values()), [], False
    for ln in (text or '').split('\n'):
        if ln.startswith('■ '):
            body = True
            continue
        if (body and ln.strip() and ln not in allowed and not ln.startswith(MARKS)
                and not _SECTION_EMPTY_RE.match(ln)):
            out.append(ln)
    return out
