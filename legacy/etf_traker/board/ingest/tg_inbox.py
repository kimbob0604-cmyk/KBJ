#!/usr/bin/env python3
"""
텔레그램 인박스 — 사용자가 봇에 공유한 X 게시물을 읽어 `state/inbox.json` 으로 남긴다.

X 를 무료·합법·자동으로 검색하는 길이 없다(공식 API 유료, 스크래핑은 규약 위반).
유일한 길은 **사용자가 X 앱에서 게시물 → 공유 → Telegram → 보드 봇** 으로 넘기고,
봇이 `getUpdates` 로 그 대화를 읽는 것이다. 여기서는 **수집기와 파일**만 만든다 —
신고가 종목 줄에 붙이는 쪽(`↳ X: …`)은 `ingest/triggers.py` 가 이 파일을 읽어서 한다(D-085).

계약 (D-086. 바꿔야 하면 거기에 근거를 적는다)
  `state/inbox.json` — 날짜 디렉터리가 아니라 state 루트. Actions 캐시 `board/state`
  에 실려 실행 사이에 남는다.
  {"source":"telegram","updated_at":"<KST ISO>","chat_ids":[…],
   "items":[{"update_id":int,"chat_id":int,"date":"<KST ISO>","text":str,
             "urls":[str],"x_ids":[str],"author":str|null,
             "kind":"x"|"other","text_via":"message"|"oembed"|null}]}
  · update_id 로 중복 제거. 보존 기간(settings.yaml `inbox.keep_days`)이 지난 항목은 버린다.
  · `kind:'x'` = x.com / twitter.com / t.co 링크가 있는 메시지. `x_ids` 는 `/status/<id>`
    의 id — x.com·twitter.com 링크에서만 뽑는다. **t.co 는 풀지 않는다**(외부 호출 최소).
  · `author` = 링크의 계정 → 없으면 oEmbed 의 작성자 → 그것도 없으면 forward_origin 의
    이름. 전달자(텔레그램 채널·사람)는 게시물을 쓴 사람이 아니라 맨 뒤다.
  · `text` 는 메시지 본문(공유 시 딸려 온 캡션 포함). 본문이 URL 뿐이면 oEmbed
    (`publish.twitter.com/oembed`, 공식·인증 없음)로 본문·작성자를 채우고
    `text_via:'oembed'`. 실패하면 `text` 는 URL 그대로 두고 `text_via:null` —
    **지어내지 않는다** (CLAUDE.md 2장 1번).

텔레그램이 갱신을 서버에 두는 시간은 **24시간**이고, 더 큰 offset 으로 부르는 순간 앞
갱신을 지운다. 그래서 페이지를 받을 때마다 다음 호출 전에 inbox.json 에 먼저 쓰고,
offset 파일은 그 뒤에 갱신한다. 둘 사이에서 죽으면 같은 페이지를 한 번 더 받지만
update_id 중복 제거가 흡수한다. 반대 순서면 받았다고 확인한 갱신이 디스크에 없다.

누구나 봇에 말을 걸 수 있다. 화이트리스트(chat_ids) 밖 대화의 갱신은 버리되 건수와
chat_id 는 로그에 남긴다 — 단체방을 쓰려면 그 id 를 `TRIGGER_INBOX_CHAT_IDS` 에 넣으면 된다.
값은 숫자 id 가 기본이고 `@username`(공개 채널·그룹) 도 받는다 — 갱신의 chat.id 는 늘
숫자라 문자열로는 절대 안 맞고, chat.username 과 대조해야 한다.

메시지는 **보내지 않는다.** 발송은 report/telegram.py 가 따로 한다.
"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

from ..engine.build import STATE
from . import creds
from .http import Fetch, session

# ─────────────────────────── 상수 ───────────────────────────
API = 'https://api.telegram.org/bot{token}/{method}'     # report/telegram.py 와 같다
TIMEOUT = 20                                             # 호출 1건 타임아웃(초)
PAGE = 100                                               # getUpdates limit 상한 (API 규정)
ALLOWED = ('message', 'channel_post')                    # 편집·반응 갱신은 받지 않는다
KEEP_DAYS = 14                                           # 보존 기간 기본값. settings.yaml 이 우선
OEMBED = 'https://publish.twitter.com/oembed'
KST = timezone(timedelta(hours=9))

INBOX_PATH = os.path.join(STATE, 'inbox.json')
# offset 은 파일 하나다. inbox.json 안에 두면 소비자가 그 파일을 고쳐 쓸 때 잃는다.
OFFSET_PATH = os.path.join(STATE, '.inbox_offset.json')

X_HOSTS = ('x.com', 'twitter.com')
X_ALL_HOSTS = X_HOSTS + ('t.co',)

_URL = re.compile(r'https?://[^\s<>"\'　]+', re.I)
# x.com/<계정>/status/<id>. 계정 자리에 `i`(x.com/i/status/…)가 오면 계정이 아니고,
# `i/web`(알림 메일·웹 '링크 복사' 가 주는 x.com/i/web/status/…)도 같다.
_STATUS = re.compile(
    r'^(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/(?:#!/)?(?:i/web/|([A-Za-z0-9_]{1,15})/)'
    r'status(?:es)?/(\d+)', re.I)
_HOST = re.compile(r'^https?://([^/?#]+)', re.I)
_P = re.compile(r'<p[^>]*>(.*?)</p>', re.S | re.I)
_BR = re.compile(r'<br\s*/?>', re.I)
_TAG = re.compile(r'<[^>]+>')
_HANDLE = re.compile(r'(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/?$', re.I)
_TRAIL = '.,;:!?)]}>\'"»'                                # 문장 끝에 딸려 붙는 글자


# ─────────────────────────── 자격증명 ───────────────────────────
def inbox_chat_ids():
    """읽을 대화방. `TRIGGER_INBOX_CHAT_IDS`(쉼표 구분)가 있으면 그것, 없으면
    `TELEGRAM_CHAT_ID` 하나 — 보통은 사용자와 봇의 1:1 대화라 추가 설정 없이 바로 쓴다."""
    raw = creds.get('TRIGGER_INBOX_CHAT_IDS') or creds.get('TELEGRAM_CHAT_ID') or ''
    return _norm_ids(raw.split(','))


def _norm_ids(ids):
    if isinstance(ids, (str, int)):
        ids = str(ids).split(',')
    out = []
    for x in ids or []:
        s = str(x).strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            out.append(s)                # `@channel` 같은 값. drain 이 chat.username 과 맞춘다
    return out


def _allow_sets(chat_ids):
    """화이트리스트를 (숫자 id 문자열 집합, 소문자 username 집합) 으로 나눈다.

    갱신의 chat.id 는 항상 숫자다. `@channel` 을 그 자리와 비교하면 절대 안 맞아 모든
    갱신을 '화이트리스트 밖' 으로 버리게 된다 — 그래서 username 은 따로 대조한다.
    """
    ids, names = set(), set()
    for x in chat_ids:
        if isinstance(x, int):
            ids.add(str(x))
        else:
            names.add(str(x).lstrip('@').lower())
    return ids, names


def _chat_allowed(chat, ids, names):
    chat = chat or {}
    if str(chat.get('id')) in ids:
        return True
    uname = str(chat.get('username') or '').lower()
    return bool(uname) and uname in names


# ─────────────────────────── HTTP ───────────────────────────
def _scrub(msg, token):
    """예외·URL 에 든 봇 토큰을 지운다. 텔레그램은 토큰을 URL 경로에 담는다."""
    return str(msg).replace(str(token), '<TOKEN>') if token else str(msg)


def _call(s, token, method, params=None):
    """Bot API 한 번. `ok:false` 는 텔레그램이 준 사유 그대로 Fetch 로 올린다.

    재시도하지 않는다 — 실패한 페이지는 다음 실행이 같은 offset 으로 다시 받는다.
    """
    url = API.format(token=token, method=method)
    try:
        r = s.get(url, params=params or {}, timeout=TIMEOUT)
    except Exception as ex:                                 # noqa: BLE001 - 사유를 문자열로 보존
        raise Fetch(_scrub(f'{method} 실패: {type(ex).__name__}: {ex}', token)) from None
    try:
        js = r.json()
    except ValueError:
        raise Fetch(_scrub(f'{method} HTTP {r.status_code} · JSON 아님: '
                           f'{(r.text or "")[:200]}', token)) from None
    if not isinstance(js, dict) or not js.get('ok'):
        why = (js or {}).get('description') if isinstance(js, dict) else str(js)
        raise Fetch(_scrub(f'{method} 거부: HTTP {r.status_code} · '
                           f'{why or "사유 없음"}', token))
    return js.get('result')


def webhook_url(s, token):
    """걸려 있는 웹훅 주소. 없으면 ''. 웹훅이 있으면 getUpdates 는 409 로 거부된다."""
    info = _call(s, token, 'getWebhookInfo') or {}
    return str(info.get('url') or '')


# ─────────────────────────── 파싱 ───────────────────────────
def _utf16_slice(text, offset, length):
    """텔레그램 entity 의 offset·length 는 UTF-16 코드 유닛이다. 파이썬 인덱스가 아니다."""
    b = text.encode('utf-16-le')
    return b[offset * 2:(offset + length) * 2].decode('utf-16-le', 'ignore')


def _clean_url(u):
    u = (u or '').strip()
    while u and u[-1] in _TRAIL:
        u = u[:-1]
    return u


def extract_urls(text, entities=None):
    """entities(url·text_link) + 본문 정규식. 순서를 지키고 중복은 없앤다."""
    text = text or ''
    found = []
    for e in entities or []:
        t = e.get('type')
        if t == 'text_link' and e.get('url'):
            found.append(e['url'])
        elif t == 'url':
            try:
                found.append(_utf16_slice(text, int(e['offset']), int(e['length'])))
            except (KeyError, TypeError, ValueError):
                continue
    found.extend(_URL.findall(text))
    out = []
    for u in found:
        u = _clean_url(u)
        if not u:
            continue
        if not u.lower().startswith(('http://', 'https://')):
            u = 'https://' + u                    # `url` entity 는 스킴 없이 올 수 있다
        if u not in out:
            out.append(u)
    return out


def _host(u):
    m = _HOST.match(u or '')
    return (m.group(1) if m else '').lower()


def is_x_url(u):
    h = _host(u)
    return any(h == x or h.endswith('.' + x) for x in X_ALL_HOSTS)


def _status(u):
    """x.com·twitter.com 링크 → (계정 또는 None, status id). 아니면 None."""
    m = _STATUS.match(re.sub(r'^https?://', '', u or '', flags=re.I))
    if not m:
        return None
    acct = m.group(1)                     # i/web/ 이면 None
    return (None if not acct or acct.lower() == 'i' else acct), m.group(2)


def x_ids_and_author(urls):
    ids, author = [], None
    for u in urls:
        st = _status(u)
        if not st:
            continue
        acct, sid = st
        if sid not in ids:
            ids.append(sid)
        if author is None and acct:
            author = acct
    return ids, author


def _forward_name(msg):
    """전달된 메시지의 원 작성자 이름. Bot API 7.0 의 forward_origin 을 먼저,
    그 전 형식(forward_from·forward_sender_name·forward_from_chat)을 다음에 본다."""
    fo = msg.get('forward_origin') or {}
    for key in ('sender_user', 'sender_chat', 'chat'):
        who = fo.get(key) or {}
        name = who.get('username') or ' '.join(
            x for x in (who.get('first_name'), who.get('last_name')) if x) or who.get('title')
        if name:
            return name
    if fo.get('sender_user_name'):
        return fo['sender_user_name']
    for key in ('forward_from', 'forward_from_chat'):
        who = msg.get(key) or {}
        name = who.get('username') or who.get('first_name') or who.get('title')
        if name:
            return name
    return msg.get('forward_sender_name') or None


def _only_urls(text, urls):
    """본문이 URL 뿐인가. URL 을 지우고 남는 글자가 없으면 그렇다.

    `url` entity 는 스킴 없이 올 수 있고(본문 'x.com/a/status/1') extract_urls 가
    'https://' 를 붙여 돌려주므로, 스킴을 뗀 꼴도 함께 지워야 본문과 맞는다.
    """
    rest = text or ''
    for u in urls:
        rest = rest.replace(u, ' ')
        rest = rest.replace(re.sub(r'^https?://', '', u, flags=re.I), ' ')
    rest = _URL.sub(' ', rest)
    return not rest.strip()


def parse_update(up):
    """갱신 하나 → 항목 dict. 메시지가 아니면(예: 편집) None.

    oEmbed 는 여기서 부르지 않는다 — 네트워크 없이 파싱만 검증할 수 있어야 한다.
    """
    msg = up.get('message') or up.get('channel_post')
    if not isinstance(msg, dict):
        return None
    chat = msg.get('chat') or {}
    text = msg.get('text') or msg.get('caption') or ''
    ents = msg.get('entities') or msg.get('caption_entities') or []
    urls = extract_urls(text, ents)
    x_urls = [u for u in urls if is_x_url(u)]
    ids, author = x_ids_and_author(x_urls)
    ts = msg.get('date')
    date = (datetime.fromtimestamp(int(ts), KST).isoformat(timespec='seconds')
            if ts is not None else None)
    return dict(
        update_id=up.get('update_id'),
        chat_id=chat.get('id'),
        date=date,
        text=text,
        urls=urls,
        x_ids=ids,
        author=author or _forward_name(msg),
        kind='x' if x_urls else 'other',
        # 본문이 URL 뿐이면 아직 채워진 것이 없다. oEmbed 가 채우면 'oembed' 로 바뀐다.
        text_via=None if _only_urls(text, urls) else 'message',
    )


# ─────────────────────────── oEmbed ───────────────────────────
def _oembed_text(h):
    """oEmbed html 의 <p> 안쪽이 본문이다. 태그를 걷고 엔티티를 되돌린다."""
    m = _P.search(h or '')
    if not m:
        return ''
    t = _BR.sub('\n', m.group(1))
    t = _TAG.sub('', t)
    return html.unescape(t).strip()


def oembed(s, url):
    """공식 oEmbed 로 본문·작성자를 받는다. 반환 (본문, 계정 또는 표시명) — 실패는 Fetch.

    쿼리(`?s=20&t=…`)는 떼고 보낸다. 공유 링크에 딸려 오는 추적 파라미터라 게시물과
    무관하고, 없어야 같은 게시물이 같은 요청이 된다.
    """
    clean = url.split('?', 1)[0].split('#', 1)[0]
    try:
        r = s.get(OEMBED, params=dict(url=clean, omit_script=1), timeout=TIMEOUT)
    except Exception as ex:                                 # noqa: BLE001
        raise Fetch(f'oEmbed 실패: {type(ex).__name__}: {ex}') from None
    if r.status_code != 200:
        raise Fetch(f'oEmbed HTTP {r.status_code} · {(r.text or "")[:120]}')
    try:
        js = r.json()
    except ValueError:
        raise Fetch('oEmbed 응답이 JSON 이 아니다') from None
    text = _oembed_text((js or {}).get('html'))
    if not text:
        raise Fetch('oEmbed 응답에 본문이 없다')
    hm = _HANDLE.search(str((js or {}).get('author_url') or ''))
    author = (hm.group(1) if hm else None) or (js or {}).get('author_name') or None
    return text, author


def fill_oembed(item, s, log=print):
    """본문이 URL 뿐인 X 항목을 oEmbed 로 채운다. 못 채우면 그대로 두고 사유만 적는다."""
    if item.get('text_via') is not None or item.get('kind') != 'x':
        return item
    target = next((u for u in item.get('urls') or [] if _status(u)), None)
    if not target:
        return item                       # t.co 뿐이면 풀지 않는다. 지어내지도 않는다
    try:
        text, author = oembed(s, target)
    except Fetch as ex:
        log(f'   oEmbed 못 받음 (update {item.get("update_id")}): {ex}')
        return item
    item['text'] = text
    item['text_via'] = 'oembed'
    # 우선순위는 링크 계정 → oEmbed 작성자 → 전달자. parse_update 는 링크 계정이 없을
    # 때 forward_origin 의 이름을 넣어 두는데, 그건 텔레그램에서 전달한 채널·사람이지
    # 게시물을 쓴 사람이 아니다. oEmbed 가 실제 작성자를 줬으면 그쪽이 맞다.
    _, link_author = x_ids_and_author(item.get('urls') or [])
    if author and not link_author:
        item['author'] = author
    return item


# ─────────────────────────── 파일 ───────────────────────────
def _read_json(path, default):
    """없으면 default. 손상(JSON 아님)은 ValueError 로 올린다 — 삼키면 drain 이 빈
    기본값 위에 새 파일을 덮어써 14일치가 로그 한 줄 없이 사라진다 (CLAUDE.md 2장 6번)."""
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _write_json(path, payload):
    """임시 파일에 쓰고 바꿔 끼운다. 쓰다 죽어도 반쪽짜리 파일이 남지 않는다."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_offset(path, log=print):
    """저장된 offset. 파일이 손상됐으면 None — 같은 페이지를 한 번 더 받지만 update_id
    중복 제거가 흡수한다. 사유는 남긴다."""
    try:
        d = _read_json(path, {})
    except ValueError as ex:
        log(f'   offset 파일이 손상됐다 ({ex}) — 처음부터 받는다: {path}')
        return None
    v = (d or {}).get('offset') if isinstance(d, dict) else None
    return int(v) if isinstance(v, int) else None


def write_offset(path, offset, now):
    _write_json(path, dict(offset=int(offset), updated_at=now.isoformat(timespec='seconds')))


def read_inbox(path, log=print):
    """저장된 인박스. 손상된 파일은 `<path>.bad` 로 옮겨 두고 빈 것에서 시작한다 —
    옛 항목은 그 파일에 남아 손으로 되살릴 수 있고, 새 갱신은 24시간 안에 받아야
    하므로 여기서 멈추지 않는다. 조용히는 아니다: 사유를 로그에 적는다."""
    try:
        d = _read_json(path, None)
    except ValueError as ex:
        bad = path + '.bad'
        os.replace(path, bad)
        log(f'   inbox.json 이 손상됐다 ({ex}) — {bad} 로 옮기고 빈 인박스에서 '
            '시작한다. 옛 항목은 그 파일에 있다')
        d = None
    if not isinstance(d, dict):
        d = {}
    items = [x for x in d.get('items') or [] if isinstance(x, dict)]
    return dict(source='telegram', updated_at=d.get('updated_at'),
                chat_ids=list(d.get('chat_ids') or []), items=items)


def merge(inbox, new_items, chat_ids, now, keep_days=KEEP_DAYS):
    """항목을 합친다. update_id 중복은 먼저 있던 것을 두고, 보존 기간이 지난 것은 버린다.

    반환 (새 inbox, 실제로 들어간 새 항목 수, 만료로 뺀 수)
    """
    cutoff = (now - timedelta(days=keep_days)).isoformat(timespec='seconds')
    have = {x.get('update_id'): x for x in inbox.get('items') or []}
    n_new = 0
    for it in new_items:
        if it.get('update_id') in have:
            continue
        have[it['update_id']] = it
        n_new += 1
    kept, n_expired = [], 0
    for it in have.values():
        d = it.get('date')
        if d and d < cutoff:              # KST ISO 는 문자열 비교로 시간순이 맞는다
            n_expired += 1
            continue
        kept.append(it)
    kept.sort(key=lambda x: (x.get('update_id') is None, x.get('update_id') or 0))
    return (dict(source='telegram', updated_at=now.isoformat(timespec='seconds'),
                 chat_ids=list(chat_ids), items=kept), n_new, n_expired)


# ─────────────────────────── 수집 ───────────────────────────
def drain(token, chat_ids, offset_path, inbox_path, s=None, log=print, now=None,
          keep_days=KEEP_DAYS):
    """봇에 쌓인 갱신을 전부 읽어 inbox.json 에 합친다.

    ① getWebhookInfo — 웹훅이 걸려 있으면 getUpdates 가 거부되므로 Fetch.
    ② getUpdates 를 빌 때까지 반복. **페이지마다 inbox.json 에 즉시 저장**하고
       offset 파일은 그 뒤에 갱신한다 — 다음 호출이 앞 갱신을 서버에서 지운다.
    ③ chat_ids 에 없는 대화의 갱신은 버리고 건수만 남긴다.
    반환 dict(n_new, n_x, n_dropped_other_chat, n_pages, n_kept, n_expired, webhook).
    """
    s = s or session()
    now = now or datetime.now(KST)
    chat_ids = _norm_ids(chat_ids)
    allow_ids, allow_names = _allow_sets(chat_ids)
    if not (allow_ids or allow_names):
        raise Fetch('읽을 대화방이 없다 — TRIGGER_INBOX_CHAT_IDS 나 TELEGRAM_CHAT_ID 가 필요하다')

    wh = webhook_url(s, token)
    if wh:
        raise Fetch(f'웹훅이 걸려 있어 getUpdates 가 거부된다: {wh} — '
                    'deleteWebhook 을 부르거나 웹훅 쪽을 끄고 다시 돌려라')

    offset = read_offset(offset_path, log=log)
    inbox = read_inbox(inbox_path, log=log)
    n_new = n_x = n_drop = n_pages = n_expired = 0
    dropped_chats = set()
    while True:
        params = dict(limit=PAGE, timeout=0, allowed_updates=json.dumps(list(ALLOWED)))
        if offset is not None:
            params['offset'] = offset
        ups = _call(s, token, 'getUpdates', params) or []
        if not ups:
            break
        n_pages += 1
        items = []
        for up in ups:
            it = parse_update(up)
            if it is None:
                continue
            chat = (up.get('message') or up.get('channel_post') or {}).get('chat')
            if not _chat_allowed(chat, allow_ids, allow_names):
                n_drop += 1
                dropped_chats.add(it.get('chat_id'))
                continue
            items.append(fill_oembed(it, s, log=log))
        seen = {x.get('update_id') for x in inbox.get('items') or []}
        inbox, added, expired = merge(inbox, items, chat_ids, now, keep_days)
        n_new += added
        n_expired += expired
        n_x += sum(1 for it in items if it.get('kind') == 'x' and it['update_id'] not in seen)
        # **디스크가 먼저다.** 다음 getUpdates 가 이 페이지를 서버에서 지운다.
        _write_json(inbox_path, inbox)
        offset = max(int(u.get('update_id') or 0) for u in ups) + 1
        write_offset(offset_path, offset, now)
        log(f'   페이지 {n_pages}: {len(ups)}건 받음 → 새 {added}건 · '
            f'다음 offset {offset}')
    if n_pages == 0:
        # 받은 것이 없어도 만료는 적용한다. 파일이 없으면 만들어 소비자가 빈 파일을 읽게 한다.
        inbox, _, n_expired = merge(inbox, [], chat_ids, now, keep_days)
        _write_json(inbox_path, inbox)
    if n_drop:
        ids = ', '.join(str(c) for c in sorted(dropped_chats, key=str))
        log(f'   화이트리스트 밖 대화 {n_drop}건 버림 (chat_id {ids}) — '
            '받으려면 그 숫자 id 를 TRIGGER_INBOX_CHAT_IDS 에 넣어라')
    return dict(n_new=n_new, n_x=n_x, n_dropped_other_chat=n_drop, n_pages=n_pages,
                n_kept=len(inbox['items']), n_expired=n_expired, webhook=wh)


# ─────────────────────────── 점검 ───────────────────────────
def probe():
    """--check 용. 웹훅이 비어 있는지, 대기 중인 갱신이 있는지만 본다.

    getUpdates 는 **offset 없이** 부른다 — 확인 처리가 되지 않아 다음 drain 이 그대로
    받는다. 메시지는 보내지 않는다.
    """
    token = creds.get('TELEGRAM_BOT_TOKEN')
    if not token:
        return [('인박스', False, 'TELEGRAM_BOT_TOKEN 없음')]
    rows = []
    ids = inbox_chat_ids()
    if ids:
        src = 'TRIGGER_INBOX_CHAT_IDS' if creds.get('TRIGGER_INBOX_CHAT_IDS') else 'TELEGRAM_CHAT_ID'
        rows.append(('대화방', True, f'{len(ids)}개 ({src})'))
    else:
        rows.append(('대화방', False, 'TRIGGER_INBOX_CHAT_IDS 도 TELEGRAM_CHAT_ID 도 없다'))
    s = session()
    try:
        wh = webhook_url(s, token)
    except Fetch as ex:
        return rows + [('웹훅', False, str(ex)[:160])]
    if wh:
        return rows + [('웹훅', False, f'걸려 있다: {wh} — getUpdates 가 거부된다. '
                                       'deleteWebhook 이 필요하다')]
    rows.append(('웹훅', True, '없음 — getUpdates 가능'))
    try:
        ups = _call(s, token, 'getUpdates', dict(limit=1, timeout=0)) or []
    except Fetch as ex:
        return rows + [('대기 갱신', False, str(ex)[:160])]
    if ups:
        it = parse_update(ups[0]) or {}
        rows.append(('대기 갱신', True,
                     f'있음 — 첫 건 update {it.get("update_id")} · chat {it.get("chat_id")} '
                     f'· {it.get("kind")} (확인 처리하지 않았다)'))
    else:
        rows.append(('대기 갱신', True, '없음 — 24시간 안에 새로 공유된 것이 없거나 이미 읽었다'))
    return rows
