#!/usr/bin/env python3
"""
종목별 트리거(재료) 수집 — 52주 이상 신고가 종목마다 **왜 올랐는지**를 붙인다.

2026-09-21 초안은 테마마다 "트리거는 확인되지 않음" 을 찍었고 신고가 11종목 줄에는
재료가 한 줄도 없었다. 테마 뉴스(news.py)는 테마명·대표 종목명으로 묻는 것이라
신고가를 낸 **그 종목**의 기사·공시는 안 걸린다. 여기는 종목 단위다 (D-085).

소스 넷, 우선순위 순.
  dart          그 종목의 당일 공시 (corp_code 로. 코스닥도 든다)
  telegram_x    X 게시물을 사용자가 텔레그램 봇에 포워딩한 인박스 — X 를 자동으로
                검색하는 무료·합법 경로는 없다. 인박스는 다른 스트림(tg_inbox.py)이
                만들고 여기서는 **읽기만** 한다. 파일이 없으면 그렇게 적는다
  naver_news    네이버 검색 API. '{이름} {코드}'·'{이름} 신고가' 로 묻는다 —
                '{이름} 주가' 는 자동 생성 시세 기사가 맨 위로 온다
  google_news   Google News RSS. 키 없는 비공식 경로라 2차 소스다. 실패는 결손으로
                적되 단계를 막지 않는다

채택 규칙은 전부 **제목**에서 본다 — 그날의 트리거라면 제목에 이름이 있다.
  - 이름 다음 글자가 한글·영문·숫자면 불일치 (케이씨 ≠ 케이씨텍·케이씨씨).
    조사(가·는·은·이…)는 붙어도 되되 그 뒤가 다시 어절 글자면 불일치.
  - 유니버스에 그 이름을 접두로 갖는 더 긴 이름이 제목에 있으면 제외.
  - 3자 이하·일반명사 이름은 제목에 이름이 있고 (요약에 주식어 or 매체가 금융지)
    일 때만 채택. '대상' 하나로는 기사가 종목 얘기인지 알 수 없다.
  - 자동 생성 시세 기사(제목 패턴·도메인)는 채택 전에 뺀다.
  - 당일(asof) 것만. 인박스는 최근 24시간.
  - 우선주(engine/kinds 판정 — 코드 끝자리와 이름 접미 둘 다)는 '우/우B/2우B' 를 뗀
    보통주로 묻고 `matched_by: pref_common` 을 단다. 제목이 우선주 자체를 부르면
    ('S-Oil우, 사상 최고가') `pref_self` 다.

시간 예산. 전체 데드라인(240초) + 소스별 상한 + 호출 timeout 8초·재시도 1회.
소스가 접속 단계에서 실패하거나 401·403·429 면 그 소스는 접는다(D-083 의 패턴).
DART 가 키·한도·IP 를 거부하는 status(010·011·012·020·021·800·901)와 Google News 가
XML 대신 consent 페이지를 주는 것도 다음 종목에서 똑같이 나므로 접는다.
데드라인을 넘긴 종목은 `skipped: 시간 예산 초과` 로 남긴다 — 조용히 빼지 않는다.

**items 가 빈 종목은 결손이 아니다.** 그날 기사·공시가 없었다는 사실이다. 다만
어디까지 봤는지('질의당 상위 20건')는 sources 에 남긴다. 결손(missing)은 소스를
접은 것·**종목 단위 조회 실패(소스별 N/M 종목 + 첫 사유)**·예산 초과·인박스 없음·
공시 접수 시각 한계다 (CLAUDE.md 2장 6번). 종목 단위 실패를 카운트에만 두면 전
종목이 실패한 날도 결손이 비어 '그날 공시 없음' 으로 읽힌다.

텔레그램 줄에는 링크를 넣지 않는다(4,096자·레거시 Markdown). 링크는 대시보드
payload 로 간다.
"""
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from ..engine import kinds as K
from ..engine.config import ROOT
from . import creds, dart, gnews, kis, news, stockflows
from .http import scrub

SOURCE = 'triggers'
KST = timezone(timedelta(hours=9))
# 인박스 계약 — 스트림 I(tg_inbox.py)가 만든다. 여기서는 읽기만 한다.
#   {"source":"telegram","updated_at":KST ISO,"items":[{"update_id","chat_id","date",
#    "text","urls":[…],"x_ids":[…],"author":str|null,"kind":"x"|"other","text_via"}]}
INBOX_PATH = os.path.join(ROOT, 'state', 'inbox.json')
# 소스 우선순위. 공시가 제일 확실하고, 포워딩은 사람이 고른 것이고, 뉴스는 잡음이 섞인다.
ORDER = ('dart', 'telegram_x', 'naver_news', 'google_news')
SOURCE_KO = {'dart': 'DART 종목 공시', 'telegram_x': 'X 포워딩 인박스',
             'naver_news': '네이버 종목 뉴스', 'google_news': 'Google News RSS'}
# 소비자들이 파일이 없을 때 결손에 적는 한 줄. 세 곳(사실 팩·텔레그램·payload)이
# 같은 문장을 써야 하므로 여기 둔다.
ABSENT_LINE = '재료: 수집되지 않음(단계 실패)'
INBOX_ABSENT_LINE = 'X 포워딩 인박스 없음 — 봇에 게시물을 공유하면 붙는다'
REASON_MAX = 160

DEFAULTS = dict(
    kind='w52', per_stock_items=3,
    naver_queries=['{name} {code}', '{name} 신고가'], naver_display=20,
    naver_skip_when_past=True,
    gnews_queries=['{name}'],
    budget_sec=240,
    per_source_sec=dict(naver_news=90, google_news=60, dart=60, telegram_x=20),
    timeout_sec=8, retries=1, inbox_hours=24, inbox_enabled=False,
    title_chars_tg=60,
    name_particles=['가', '는', '은', '이', '도', '의', '를', '을', '와', '과', '로', '에'],
    generic_names=[], stock_words=[], autogen_title_patterns=[],
    domain_blacklist=[], finance_outlets={}, outlets={})

_WORD = re.compile(r'[가-힣A-Za-z0-9]')
_X_URL = re.compile(r'https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/\S+')
_ANY_URL = re.compile(r'https?://\S+')
_CODE6 = re.compile(r'(?<!\d)(\d{6})(?!\d)')
_NONWORD = re.compile(r'[^가-힣a-z0-9]+')


def settings(cfg):
    """설정 블록 + 기본값. 코드에 숫자를 박지 않되 설정이 비어도 돈다."""
    c = dict(DEFAULTS)
    c.update((cfg or {}).get('triggers') or {})
    c['per_source_sec'] = dict(DEFAULTS['per_source_sec'],
                               **((cfg or {}).get('triggers') or {}).get('per_source_sec') or {})
    c['_autogen'] = [re.compile(p) for p in c.get('autogen_title_patterns') or []]
    return c


def short(e, limit=REASON_MAX):
    """예외를 한 줄로. URL·스택·인증키를 싣지 않는다."""
    return stockflows.short(scrub(str(e)), limit)


# ─────────────────────────── 매칭 ───────────────────────────
def norm_title(t):
    """중복 제거용. 표기 흔들림(공백·기호·대소문자)을 지운다."""
    return _NONWORD.sub('', unicodedata.normalize('NFKC', str(t or '')).lower())


def name_in(name, text, particles=()):
    """제목에 종목명이 **어절 경계**로 있는가.

    이름 다음 글자가 한글·영문·숫자면 다른 이름의 앞부분이다(케이씨 ≠ 케이씨텍).
    조사는 붙어도 된다 — 단 조사 뒤가 다시 어절 글자면 그것도 다른 이름이다
    (케이씨 ≠ 케이씨에스). 앞 글자도 같은 기준으로 본다(삼성케이씨).
    대소문자는 가리지 않는다(S-Oil / S-OIL).
    """
    if not name or not text:
        return False
    lo, key = str(text).lower(), str(name).lower()
    i = 0
    while True:
        i = lo.find(key, i)
        if i < 0:
            return False
        j = i + len(key)
        if i > 0 and _WORD.match(lo[i - 1]):
            i = j
            continue
        nxt = lo[j:j + 1]
        if not nxt or not _WORD.match(nxt):
            return True
        for p in particles or ():
            if lo.startswith(p, j):
                after = lo[j + len(p):j + len(p) + 1]
                if not after or not _WORD.match(after):
                    return True
        i = j


def is_generic(name, S):
    """일반명사·짧은 이름 — 제목에 있어도 종목 얘기인지 따로 확인해야 한다."""
    return len(name) <= 3 or name in set(S.get('generic_names') or [])


def longer_names(name, universe_names):
    """유니버스에서 이 이름을 접두로 갖는 더 긴 이름들."""
    return [n for n in universe_names if n != name and n.startswith(name)]


def _is_finance(outlet, S):
    fo = S.get('finance_outlets') or {}
    return bool(outlet) and (outlet in fo or outlet in set(fo.values()))


def adopt(name, art, S, generic, longer, alias=None):
    """기사를 이 종목의 재료로 채택할지. 반환은 matched_by 또는 None(사유는 두 번째).

    `alias` 는 우선주 자체의 이름이다(name 은 질의에 쓴 보통주 이름). 제목이 우선주를
    직접 부르면('S-Oil우, 사상 최고가') 보통주 이름으로는 다음 글자 '우' 가 어절
    글자라 걸리지 않고, longer 에 자기 이름이 들어 있으면 접두 제외에도 걸린다 —
    우선주가 신고가를 낸 날 정작 그 종목 기사만 빠졌다. 그래서 자기 이름부터 본다.
    """
    title = art.get('title') or ''
    outlet = art.get('outlet') or ''
    if outlet in set(S.get('domain_blacklist') or []):
        return None, 'autogen_domain'
    if any(p.search(title) for p in S.get('_autogen') or []):
        return None, 'autogen_title'
    parts = S.get('name_particles') or ()
    if alias and name_in(alias, title, parts):
        return 'pref_self', None
    if not name_in(name, title, parts):
        return None, 'name_not_in_title'
    if any(name_in(ln, title, parts) for ln in longer):
        return None, 'longer_name_in_title'
    if generic:
        text = f'{title} {art.get("summary") or ""}'
        if any(w in text for w in S.get('stock_words') or []):
            return 'title+stockword', None
        if _is_finance(outlet, S):
            return 'title+finance_outlet', None
        return None, 'generic_without_stock_context'
    return 'title', None


def publisher_of(outlet, S):
    """도메인 → 표시명. 표에 없으면 도메인 그대로 — 지어내지 않는다."""
    if not outlet:
        return None
    return ((S.get('finance_outlets') or {}).get(outlet)
            or (S.get('outlets') or {}).get(outlet) or outlet)


def _item(art, S, source, matched_by, query=None):
    d = dict(title=art.get('title'), link=art.get('url') or art.get('link'),
             publisher=art.get('publisher') or publisher_of(art.get('outlet'), S),
             published_at=art.get('published_at') or art.get('date'),
             source=source, matched_by=matched_by)
    if art.get('kind'):
        d['kind'] = art['kind']
    if query:
        d['query'] = query
    return d


# ─────────────────────────── 인박스 ───────────────────────────
def _parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=KST)


def load_inbox(inbox, now, hours, path=INBOX_PATH):
    """인박스 항목 중 최근 `hours` 시간 것. 반환 (items, 못 읽은 사유 또는 None)."""
    if inbox is None:
        if not os.path.exists(path):
            return [], INBOX_ABSENT_LINE
        try:
            with open(path, encoding='utf-8') as f:
                inbox = json.load(f)
        except (OSError, ValueError) as e:
            return [], f'인박스 파일을 읽지 못했다 — {short(e)}'
    lo = now - timedelta(hours=hours)
    keep = []
    for it in (inbox or {}).get('items') or []:
        d = _parse_dt(it.get('date'))
        if d is None or d < lo:
            continue
        keep.append(it)
    return keep, None


def inbox_matches(items, name, code, S, codes=()):
    """인박스에서 이 종목 것. 본문에 6자리 코드가 있거나 이름이 어절 경계로 있다.

    일반명사 이름은 코드가 있거나 주식어가 함께 있어야 한다 — 포워딩된 다른
    종목 글에 '대상' 이 스치면 그 종목 재료가 된다. `codes` 는 함께 볼 코드
    (우선주면 제 코드와 보통주 코드 둘 다).
    """
    out = []
    parts = S.get('name_particles') or ()
    generic = is_generic(name, S)
    want = {code, *codes}
    for it in items:
        text = it.get('text') or ''
        if want & set(_CODE6.findall(text)):
            by = 'code'
        elif name_in(name, text, parts):
            if generic and not any(w in text for w in S.get('stock_words') or []):
                continue
            by = 'name'
        else:
            continue
        urls = [u for u in (it.get('urls') or []) if _X_URL.match(u)]
        if not urls:
            m = _X_URL.search(text)
            urls = [m.group(0)] if m else []
        author = (it.get('author') or '').strip().lstrip('@')
        # 제목에서 URL 을 지운다 — 링크는 link 로 따로 가고, 텔레그램 줄에는 링크를
        # 넣지 않는다. 본문에 남겨 두면 그 줄이 링크를 실어 나가는 셈이 된다.
        body = ' '.join(_ANY_URL.sub('', text).split())
        out.append(dict(title=body[:120], link=urls[0] if urls else None,
                        publisher=f'@{author}' if author else 'X(포워딩)',
                        published_at=it.get('date'), source='telegram_x', matched_by=by))
    return out


# ─────────────────────────── 시간 예산 ───────────────────────────
class Budget:
    def __init__(self, total, per_source, clock=None):
        self.clock = clock or time.monotonic
        self.t0 = self.clock()
        self.total = total
        self.per = per_source or {}
        self.spent = {}

    def elapsed(self):
        return self.clock() - self.t0

    def over(self):
        return self.elapsed() > self.total

    def charge(self, source, sec):
        self.spent[source] = self.spent.get(source, 0.0) + sec

    def source_over(self, source):
        lim = self.per.get(source)
        return lim is not None and self.spent.get(source, 0.0) > lim


# DART 가 XML/JSON 의 status 로 주는 거부. 종목이 아니라 키·한도·IP·점검의 문제라
# 다음 종목을 불러도 같다. 013(조회 없음)·100(필드 값)·900(정의되지 않음)은 뺀다 —
# 그 종목만의 일이거나 무엇인지 모르는 것이라 접지 않는다.
_DART_CUT = re.compile(r'DART status=(010|011|012|020|021|800|901)\b')
# Google News 가 200 으로 주는 consent 페이지·차단 응답(gnews.parse 의 세 사유).
# 질의를 바꿔 다시 불러도 같은 페이지가 온다.
_GNEWS_CUT = re.compile(r'RSS 가 아니다|XML 이 아니다|XML 파싱 실패')


def cut_reason(e):
    """이 실패로 소스를 접어야 하는가. 접속 불가·401·403·429·DART 키/한도 status·
    Google News consent 페이지면 사유, 아니면 None.

    종목 하나의 실패(013 조회 없음·파싱·HTTP 5xx)는 그 종목만의 일이다. 서버가 안
    받거나 키를 거부하거나 한도를 넘긴 것은 다음 종목을 불러도 같으므로 접는다
    (D-083). 접지 않는 실패도 사라지지 않는다 — `_finish` 가 소스별 N/M 종목으로
    결손에 적는다.
    """
    msg = str(e)
    if kis.connect_failed(e):
        return f'접속 불가 — {short(e, 100)}'
    m = re.search(r'HTTP (401|403|429)\b', msg)
    if m:
        what = {'401': '인증 거부', '403': '접근 거부', '429': '호출 한도'}[m.group(1)]
        return f'HTTP {m.group(1)} {what} — {short(e, 100)}'
    m = _DART_CUT.search(msg)
    if m:
        return f'DART status={m.group(1)} {dart.STATUS_KO.get(m.group(1), "")} — {short(e, 100)}'
    if _GNEWS_CUT.search(msg):
        return f'RSS 가 아니다(consent 페이지·차단 응답) — {short(e, 100)}'
    return None


# ─────────────────────────── 소스 호출 ───────────────────────────
def _naver(name, code, S, s, display=None):
    out = []
    for q in S.get('naver_queries') or []:
        qq = q.format(name=name, code=code)
        rows = news.search(qq, display=display or S['naver_display'], s=s,
                           timeout=S['timeout_sec'], retries=S['retries'] + 1)
        out.extend(dict(r, query=qq) for r in rows)
    return out


def _gnews(name, code, S, s):
    out = []
    for q in S.get('gnews_queries') or []:
        qq = q.format(name=name, code=code)
        rows = gnews.search(qq, s=s, timeout=S['timeout_sec'], retries=S['retries'] + 1)
        out.extend(dict(r, query=qq) for r in rows)
    return out


def _dart(code, asof, S, s):
    return dart.disclosures_for(code, asof, s=s, timeout=S['timeout_sec'],
                                retries=S['retries'] + 1)


# 버린 사유 → 사람이 읽는 말. 결손 줄과 로그에 그대로 나간다.
DROP_KO = {
    'not_asof': '다른 날 기사',
    'autogen_title': '자동 생성 시세 기사',
    'autogen_domain': '자동 생성 매체',
    'name_not_in_title': '제목에 종목명 없음',
    'longer_name_in_title': '이름이 겹치는 다른 종목',
    'generic_without_stock_context': '일반명사 이름인데 주식 맥락 없음',
}


def _drop(src, source, why):
    """받아 놓고 버린 기사 한 건. 사유별로 센다 (CLAUDE.md 2장 6번).

    버리는 것 자체는 옳다 — 다른 날 기사나 이름이 안 든 기사를 재료라고 적으면
    안 된다. 다만 **조용히** 버리면 '그날 기사가 없었다' 와 구분되지 않는다.
    """
    src[source]['dropped'][why] = src[source]['dropped'].get(why, 0) + 1


def _base(asof, S, now):
    return dict(source=SOURCE, as_of=asof,
                collected_at=now.isoformat(timespec='seconds'),
                kind=S['kind'], n_target=0, budget_sec=S['budget_sec'],
                title_chars_tg=S['title_chars_tg'],
                # 인박스 창. 텔레그램 머리가 'X 포워딩은 최근 N시간' 이라 적는 근거다 —
                # 당일 것만 싣는 기사·공시와 달리 전일 오후 포워딩도 든다.
                inbox_hours=S['inbox_hours'], by_code={},
                # error 는 접지 않은 종목 단위 실패의 **첫** 사유. failed 가 몇인지와
                # 함께 결손 한 줄이 된다.
                # dropped 는 받아 놓고 **버린** 기사의 사유별 수다. 소스가 ok 인데
                # 재료가 0 건인 날, 못 찾은 것인지 걸러낸 것인지 이것으로 갈린다
                # (2026-09-22 실측: 네이버가 20건을 줬는데 전부 이튿날 기사였다).
                sources={k: dict(ok=0, failed=0, skipped=0, cut=None, error=None,
                                 dropped={})
                         for k in ORDER},
                missing=[])


# ─────────────────────────── 수집 ───────────────────────────
def collect(newhigh, universe, asof, cfg=None, log=print, inbox=None, now=None,
            clock=None):
    """대상 종목마다 당일 재료를 붙인다. 반환은 triggers.json 에 그대로 쓸 dict.

    inbox  인박스 dict 를 직접 주면 파일을 읽지 않는다(시험용)
    now    KST 기준 현재 시각(인박스 창·collected_at). 없으면 지금
    clock  단조 시계(시간 예산). 없으면 time.monotonic
    """
    S = settings(cfg)
    now = now or datetime.now(KST)
    rows = stockflows.targets(newhigh or {}, S['kind'])
    out = _base(asof, S, now)
    out['n_target'] = len(rows)
    src = out['sources']
    if not rows:
        log(f'  트리거 — {S["kind"]} 이상 신고가가 없어 받을 대상이 없다')
        return out

    stocks = (universe or {}).get('stocks') or []
    by_name = {x['name']: x for x in stocks if x.get('name') and x.get('code')}
    uni_names = list(by_name)

    # ── 소스 준비. 여기서 접힌 소스는 종목을 돌며 부르지 않는다 ──
    ns = None
    if not creds.has('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET'):
        src['naver_news']['cut'] = 'NAVER_CLIENT_ID/SECRET 없음'
    else:
        try:
            ns = news._s()
        except Exception as e:                       # noqa: BLE001
            src['naver_news']['cut'] = f'세션 준비 실패 — {short(e)}'
    if not creds.has('DART_API_KEY'):
        src['dart']['cut'] = 'DART_API_KEY 없음'
    else:
        try:
            dart.corp_codes()
        except Exception as e:                       # noqa: BLE001
            src['dart']['cut'] = f'corp_code 매핑 실패 — {short(e)}'
    # 인박스는 사용자가 X 게시물을 봇에 손으로 공유해야 채워진다. 그러지 않기로
    # 정했으므로(2026-09-22, XDIGEST.md 8장 11번에서 블루스카이를 골랐다) 기본은
    # 꺼 둔다. 켜져 있으면 배너에 '인박스 없음' 이 **매일** 한 줄 붙는데, 고칠
    # 수도 없고 고칠 생각도 없는 줄이 매일 붙으면 배너 전체를 안 읽게 된다.
    # 수집기(ingest/tg_inbox.py)와 이 경로는 그대로 두고 설정으로만 끈다 —
    # 나중에 공유를 쓰기로 하면 한 줄로 되돌아온다.
    if not S['inbox_enabled']:
        src['telegram_x']['cut'] = None
        src['telegram_x']['skipped'] = 1
        src['telegram_x']['scope'] = '꺼 둠 (settings.yaml triggers.inbox_enabled)'
        inbox_items = []
    else:
        inbox_items, why = load_inbox(inbox, now, S['inbox_hours'])
        if why:
            src['telegram_x']['cut'] = why
    gs = gnews.session()
    ds = dart.session() if not src['dart']['cut'] else None

    # 네이버 검색은 기간 필터가 없고 **최신순**이다. 기준일이 오늘이 아니면(재시도
    # 크론·수동 재실행) 받는 것이 전부 그 뒤 날짜다. 창을 100건으로 넓혀도 그랬다 —
    # 2026-09-22 09:28 KST 실측: 11종목 × 2질의로 2,124건을 받아 **한 건도** 기준일
    # 기사가 아니었다. 큰 응답을 스물두 번 받아 0건을 얻는 것이라 아예 건너뛰고
    # 사유를 남긴다. 정시(16:07 KST) 실행은 기준일이 오늘이라 영향이 없다.
    if S['naver_skip_when_past'] and str(asof) != now.strftime('%Y-%m-%d') \
            and not src['naver_news']['cut']:
        src['naver_news']['cut'] = (f'기준일({asof})이 오늘이 아니다 — 검색이 최신순이라 '
                                    '그 날짜에 닿지 못한다(2026-09-22 실측 2,124건 전부 다른 날)')
        ns = None
    n_disp = S['naver_display']
    nq = len(S.get('naver_queries') or [])
    src['naver_news']['scope'] = (f'종목당 질의 {nq}개 × 상위 {n_disp}건 · '
                                  f'{asof} 자 기사만 · 제목에 이름이 있는 것만')
    src['google_news']['scope'] = f'종목당 질의 {len(S.get("gnews_queries") or [])}개 · when:1d · {asof} 자만'
    src['dart']['scope'] = f'{asof} 접수분 · 접수 시각은 응답에 없다'
    if S['inbox_enabled']:
        src['telegram_x']['scope'] = (
            f'최근 {S["inbox_hours"]}시간 포워딩 {len(inbox_items)}건 · '
            '본문에 종목명(어절 경계) 또는 6자리 코드')

    budget = Budget(S['budget_sec'], S['per_source_sec'], clock)
    cap = S['per_stock_items']
    n_over = []
    for r in rows:
        code, name = r['code'], r.get('name') or r['code']
        entry = dict(code=code, name=name, items=[])
        out['by_code'][code] = entry
        qname, qcode, pref = name, code, False
        # 우선주 판정은 kinds.of — 코드 끝자리(보통주는 0)와 이름 접미 둘 다 본다.
        # 이름만 보면 끝 글자가 '우' 인 보통주를 우선주로 오판하고, 접미를 '우B' 까지만
        # 알면 '현대차2우B' 의 보통주를 '현대차2' 로 찾아 못 찾는다.
        if K.of(code, name) == K.PREF:
            base = K.common_name(name)
            common = by_name.get(base)
            if not common:
                entry['skipped'] = '우선주 — 보통주를 유니버스에서 못 찾음'
                for k in ORDER:
                    src[k]['skipped'] += 1
                continue
            qname, qcode, pref = base, common['code'], True
            entry['query_as'] = dict(name=qname, code=qcode)
        if budget.over():
            entry['skipped'] = '시간 예산 초과'
            n_over.append(name)
            for k in ORDER:
                src[k]['skipped'] += 1
            continue
        generic = is_generic(qname, S)
        # 우선주면 자기 이름은 접두 제외 목록에서 뺀다 — 제목이 자기를 부른 기사가
        # '더 긴 이름' 에 걸려 빠지면 안 된다(adopt 의 alias).
        longer = [n for n in longer_names(qname, uni_names) if n != name]
        seen = set()
        for source in ORDER:
            if len(entry['items']) >= cap:
                break
            if src[source]['cut']:
                continue
            if budget.over():
                entry['note'] = f'시간 예산 초과 — {SOURCE_KO[source]} 부터는 검색하지 않음'
                break
            if budget.source_over(source):
                src[source]['cut'] = (f'소스별 시간 상한 {S["per_source_sec"].get(source)}초 '
                                      f'초과 ({budget.spent.get(source, 0):.0f}초)')
                log(f'  {SOURCE_KO[source]} — {src[source]["cut"]}')
                continue
            t0 = budget.clock()
            try:
                if source == 'dart':
                    arts = _dart(qcode, asof, S, ds)
                elif source == 'telegram_x':
                    arts = inbox_matches(inbox_items, qname, qcode, S, codes=(code,))
                elif source == 'naver_news':
                    arts = _naver(qname, qcode, S, ns, display=n_disp)
                else:
                    arts = _gnews(qname, qcode, S, gs)
            except Exception as e:                   # noqa: BLE001
                budget.charge(source, budget.clock() - t0)
                why = cut_reason(e)
                if why:
                    src[source]['cut'] = why
                    log(f'  {SOURCE_KO[source]} — {why} · 나머지 종목은 부르지 않는다')
                else:
                    src[source]['failed'] += 1
                    entry.setdefault('errors', {})[source] = short(e)
                    if not src[source]['error']:
                        src[source]['error'] = short(e)
                continue
            budget.charge(source, budget.clock() - t0)
            src[source]['ok'] += 1
            for a in arts:
                if source == 'telegram_x':
                    by, key = a['matched_by'], norm_title(a['title'])
                else:
                    if source == 'dart':
                        by = 'corp_code'
                        if (a.get('date') or asof) != asof:
                            _drop(src, source, 'not_asof')
                            continue
                    else:
                        if a.get('date') != asof:
                            _drop(src, source, 'not_asof')
                            continue
                        by, why_drop = adopt(qname, a, S, generic, longer,
                                             alias=name if pref else None)
                        if not by:
                            _drop(src, source, why_drop or 'not_adopted')
                            continue
                    key = norm_title(a.get('title'))
                if not key or key in seen:
                    continue
                seen.add(key)
                if pref and by != 'pref_self':
                    by = 'pref_common'
                entry['items'].append(_item(a, S, source, by, a.get('query')))
                if len(entry['items']) >= cap:
                    break
    _finish(out, S, budget, n_over, now, log)
    return out


def _finish(out, S, budget, n_over, now, log):
    src = out['sources']
    for k in ORDER:
        cut = src[k]['cut']
        if not cut:
            continue
        out['missing'].append(cut if cut == INBOX_ABSENT_LINE else f'{SOURCE_KO[k]} — {cut}')
    # 접지 않은 종목 단위 실패(HTTP 5xx·파싱·013 아닌 status). 카운트에만 두면 세
    # 소비자(사실 팩·텔레그램·payload)는 missing 만 읽으므로 전 종목이 실패한 날도
    # '그날 공시 없음' 으로 읽힌다 (CLAUDE.md 2장 6번). 분모는 실제로 부른 종목 수다.
    for k in ORDER:
        n_fail = src[k]['failed']
        if not n_fail:
            continue
        tried = src[k]['ok'] + n_fail
        why = src[k].get('error')
        out['missing'].append(f'{SOURCE_KO[k]} — {n_fail}/{tried}종목 조회 실패'
                              + (f' (첫 사유: {why})' if why else ''))
    if n_over:
        shown = ', '.join(n_over[:3]) + (' 외' if len(n_over) > 3 else '')
        out['missing'].append(f'트리거 — 시간 예산 {S["budget_sec"]}초 초과로 '
                              f'{len(n_over)}종목({shown})은 검색하지 않았다')
    # 부르기는 했는데 한 건도 못 남긴 소스. 받은 게 있었으면 **왜 걸러졌는지**를
    # 적는다 — 없으면 '그날 기사가 없었다' 로 읽히는데, 실제로는 이튿날 기사만
    # 받았거나 자동 생성 시세 기사만 온 것일 수 있다 (2026-09-22 실측).
    used = {k: 0 for k in ORDER}
    for v in out['by_code'].values():
        for it in v['items']:
            used[it['source']] = used.get(it['source'], 0) + 1
    for k in ORDER:
        dropped = src[k]['dropped']
        if used[k] or not src[k]['ok'] or not dropped:
            continue
        top = sorted(dropped.items(), key=lambda kv: -kv[1])[:3]
        out['missing'].append(
            f'{SOURCE_KO[k]} — 받은 기사가 전부 걸러져 재료가 0건이다 ('
            + ', '.join(f'{DROP_KO.get(w, w)} {n}건' for w, n in top) + ')')
    if src['dart']['ok']:
        out['missing'].append(f'공시는 {now.strftime("%H:%M")} 접수분까지 — '
                              '그 뒤 접수분은 이 실행에 없다')
    out['elapsed_sec'] = round(budget.elapsed(), 1)
    n_items = sum(len(v['items']) for v in out['by_code'].values())
    n_with = sum(1 for v in out['by_code'].values() if v['items'])
    log(f'  트리거 — {n_with}/{out["n_target"]}종목에 재료 {n_items}건 · '
        f'{out["elapsed_sec"]}초'
        + ''.join(f' · {SOURCE_KO[k]} 접음' for k in ORDER if src[k]['cut']))


def failed(asof, cfg, why, now=None):
    """단계가 예외로 죽어도 남길 파일. sources 전부 failed + missing 한 줄.

    파일이 없으면 소비자가 '수집되지 않음' 을 결손에 넣지만, 있으면 **왜** 인지까지
    남는다. 조용히 빠지는 것과 사유가 있는 것은 다르다 (CLAUDE.md 2장 6번).
    """
    S = settings(cfg)
    now = now or datetime.now(KST)
    out = _base(asof, S, now)
    reason = short(why)
    for k in ORDER:
        out['sources'][k]['cut'] = f'단계 실패 — {reason}'
        out['sources'][k]['failed'] = 1
    out['missing'].append(f'{ABSENT_LINE.rstrip(")")} — {reason})')
    out['failed'] = reason
    return out


def absent(asof=None):
    """파일이 없을 때 소비자가 쓰는 대역. 결손 한 줄만 든다."""
    return dict(source=SOURCE, as_of=asof, absent=True, by_code={}, sources={},
                missing=[ABSENT_LINE])


def load(asof):
    """state/<asof>/triggers.json. 없으면 absent() — 소비자는 둘을 같은 모양으로 읽는다."""
    from ..engine.build import read
    return read(asof, 'triggers.json') or absent(asof)


# ─────────────────────────── 점검 ───────────────────────────
PROBE_NAME, PROBE_CODE = '케이씨', '029460'    # 일반명사 종목. 경계 규칙이 여기서 갈린다
PROBE_KOSDAQ = '252990'                       # 코스닥. disclosures() 의 corp_cls='Y' 가 놓치는 쪽


def _title_hit_ratio(rows, name, S):
    if not rows:
        return '0건'
    hit = sum(1 for r in rows if name_in(name, r.get('title'), S.get('name_particles')))
    return f'{len(rows)}건 중 제목에 이름 {hit}건 ({hit * 100 // len(rows)}%)'


def probe(cfg=None):
    """--check 용. 네이버 종목 질의 1건·gnews 1건·DART 1건·인박스 파일 유무."""
    from ..engine.config import load as _load
    S = settings(cfg or _load())
    out = []
    if not creds.has('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET'):
        out.append(('네이버 종목 질의', False, 'NAVER_CLIENT_ID/SECRET 없음'))
    else:
        try:
            rows = news.search(f'{PROBE_NAME} {PROBE_CODE}', display=10,
                               timeout=S['timeout_sec'], retries=S['retries'] + 1)
            out.append(('네이버 종목 질의', True,
                        f"'{PROBE_NAME} {PROBE_CODE}' → {_title_hit_ratio(rows, PROBE_NAME, S)}"))
        except Exception as e:                       # noqa: BLE001
            out.append(('네이버 종목 질의', False, short(e)))
    try:
        rows = gnews.search(PROBE_NAME, timeout=S['timeout_sec'], retries=S['retries'] + 1)
        out.append(('Google News RSS', True,
                    f"'{PROBE_NAME}' → {_title_hit_ratio(rows, PROBE_NAME, S)}"))
    except Exception as e:                           # noqa: BLE001
        out.append(('Google News RSS', False, short(e)))
    if not creds.has('DART_API_KEY'):
        out.append(('DART 종목 공시', False, 'DART_API_KEY 없음'))
    else:
        try:
            today = datetime.now(KST).date().isoformat()
            rows = dart.disclosures_for(PROBE_CODE, today, timeout=S['timeout_sec'],
                                        retries=S['retries'] + 1)
            out.append(('DART 종목 공시', True,
                        f'{PROBE_CODE} corp_code 조회 · {today} 공시 {len(rows)}건'
                        + (f' · 예: {rows[0]["title"][:40]}' if rows else '')))
        except Exception as e:                       # noqa: BLE001
            out.append(('DART 종목 공시', False, short(e)))
    if os.path.exists(INBOX_PATH):
        items, why = load_inbox(None, datetime.now(KST), S['inbox_hours'])
        out.append(('X 인박스', not why,
                    why or f'{len(items)}건 (최근 {S["inbox_hours"]}시간) · {INBOX_PATH}'))
    else:
        # 파일이 없는 것은 소스 고장이 아니라 아직 공유된 게시물이 없다는 사실이다.
        # FAIL 로 찍으면 봇에 뭔가 보낼 때까지 --check 가 매일 빨간 줄 하나를 달고 산다.
        out.append(('X 인박스', True, f'아직 공유된 게시물 없음 — 봇에 X 게시물을 공유하면 '
                                   f'tg_inbox 가 {INBOX_PATH} 를 만든다'))
    return out


def probe_table(asof, cfg=None, log=print):
    """--trigger-probe 용. 조사에서 정한 프로브를 순서대로 찍는다.

    판정은 셋이다 — PASS / FAIL / 기록. '기록' 은 판정하지 않고 값만 남기는 줄이다
    (질의별 제목 포함 비율·비공식 URL 의 상태·웹훅 상태). 라이브 호출은 러너에서만
    돈다. 반환 [(status, label, note)].
    """
    import requests
    from ..engine.config import load as _load
    S = settings(cfg or _load())
    rows = []
    tmo = S['timeout_sec']

    def put(status, label, note):
        rows.append((status, label, note))
        log(f'  {status:<4}  {label:<34} {note}')

    # 1. 네이버 질의 셋 — 제목에 이름이 든 비율
    if creds.has('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET'):
        for q, nm in ((f'{PROBE_NAME} 주가', PROBE_NAME), (f'{PROBE_NAME} {PROBE_CODE}', PROBE_NAME),
                      ('"비엠티"', '비엠티')):
            try:
                got = news.search(q, display=S['naver_display'], timeout=tmo,
                                  retries=S['retries'] + 1)
            except Exception as e:                   # noqa: BLE001
                put('FAIL', f'네이버 {q}', short(e))
                continue
            auto = sum(1 for r in got if any(p.search(r.get('title') or '')
                                              for p in S['_autogen'])
                       or (r.get('outlet') in set(S.get('domain_blacklist') or [])))
            put('기록', f'네이버 {q}', f'{_title_hit_ratio(got, nm, S)} · 자동생성 의심 {auto}건'
                + (f' · 1위: {got[0]["title"][:50]}' if got else ''))
    else:
        put('FAIL', '네이버 질의', 'NAVER_CLIENT_ID/SECRET 없음')

    # 2. Google News RSS — 200/XML/한국어 + 5회 연속 내성
    try:
        got = gnews.search(PROBE_NAME, timeout=tmo, retries=1)
        ko = sum(1 for r in got if re.search(r'[가-힣]', r.get('title') or ''))
        put('PASS', 'Google News RSS 200·XML·한국어',
            f'{len(got)}건 · 한국어 제목 {ko}건 · {_title_hit_ratio(got, PROBE_NAME, S)}')
    except Exception as e:                           # noqa: BLE001
        put('FAIL', 'Google News RSS 200·XML·한국어', short(e))
    okn, last = 0, ''
    for i in range(5):
        try:
            gnews.search(f'{PROBE_NAME} {i}', timeout=tmo, retries=1)
            okn += 1
        except Exception as e:                       # noqa: BLE001
            last = short(e, 80)
    put('PASS' if okn == 5 else 'FAIL', 'Google News RSS 5회 연속',
        f'{okn}/5 성공' + (f' · 마지막 실패: {last}' if last else ''))

    # 3. DART corp 별 1건 (코스피·코스닥) + 날짜 전체 total
    if creds.has('DART_API_KEY'):
        for code in (PROBE_CODE, PROBE_KOSDAQ):
            try:
                got = dart.disclosures_for(code, asof, timeout=tmo, retries=S['retries'] + 1)
                put('PASS', f'DART corp {code}', f'{asof} 공시 {len(got)}건'
                    + (f' · {got[0]["title"][:40]} [{got[0]["kind"]}]' if got else ''))
            except Exception as e:                   # noqa: BLE001
                put('FAIL', f'DART corp {code}', short(e))
        try:
            d = dart.disclosures(asof, asof)
            total = d.get('total') or 0
            put('기록', 'DART 날짜 전체 (corp_cls=Y, 1쪽)',
                f'total {total}건 → 100건씩 {-(-int(total) // 100) if total else 0}쪽 · '
                f'1쪽에서 종목 {len(d.get("by_stock") or {})}')
        except Exception as e:                       # noqa: BLE001
            put('FAIL', 'DART 날짜 전체', short(e))
    else:
        put('FAIL', 'DART', 'DART_API_KEY 없음')

    # 4. 네이버 금융 종목 뉴스 비공식 URL 둘 — 상태·키만 기록. 판정 없음
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'})
    for label, url in (
            ('네이버 m.stock 종목 뉴스(비공식)',
             f'https://m.stock.naver.com/api/news/stock/{PROBE_CODE}?pageSize=10&page=1'),
            ('네이버 finance 종목 뉴스(비공식)',
             f'https://finance.naver.com/item/news_news.naver?code={PROBE_CODE}&page=1')):
        try:
            r = s.get(url, timeout=tmo)
            ct = (r.headers.get('content-type') or '')[:40]
            keys = ''
            try:
                js = r.json()
                top = js[0] if isinstance(js, list) and js else js
                keys = ' · 키: ' + ','.join(list(top.keys())[:6]) if isinstance(top, dict) else ' · 리스트'
            except Exception:                        # noqa: BLE001
                pass
            put('기록', label, f'HTTP {r.status_code} · {ct} · {len(r.content):,}B{keys}')
        except Exception as e:                       # noqa: BLE001
            put('기록', label, f'요청 실패 — {short(e, 100)}')

    # 5. 텔레그램 getWebhookInfo — 폴링(getUpdates)과 웹훅은 함께 못 쓴다
    token = creds.get('TELEGRAM_BOT_TOKEN')
    if not token:
        put('기록', '텔레그램 getWebhookInfo', 'TELEGRAM_BOT_TOKEN 없음')
    else:
        try:
            r = s.get(f'https://api.telegram.org/bot{token}/getWebhookInfo', timeout=tmo)
            js = r.json() if r.status_code == 200 else {}
            res = js.get('result') or {}
            put('기록', '텔레그램 getWebhookInfo',
                f'HTTP {r.status_code} · url={"(없음 → getUpdates 폴링 가능)" if not res.get("url") else "설정됨"} '
                f'· pending {res.get("pending_update_count", "?")}')
        except Exception as e:                       # noqa: BLE001
            put('기록', '텔레그램 getWebhookInfo',
                f'요청 실패 — {str(e).replace(token, "<TOKEN>")[:100]}')
    return rows
