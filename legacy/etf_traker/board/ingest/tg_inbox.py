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

## KBJ P2 (설계 §5.8·§5.10 #33)

수신은 KBJ notifier 의 웹훅 하나다(U1 — getUpdates 폐지). notifier 가 허용 대화의 갱신을
`parse_update`·oEmbed 로 정리해 `prv_alerts.tg_inbox` 에 쌓고, 여기 `drain` 은 그것을
`kbj.services.notifier.inbox.read_items` 로 읽어 위 계약 그대로 `state/inbox.json` 을 만든다
(triggers.py 는 그대로 이 파일을 읽는다). getUpdates·getWebhookInfo 호출, offset 파일, 봇 토큰
읽기는 지웠다. 링크·X 판정·갱신 파싱은 KBJ 정본(`kbj.services.notifier.inbox`)을 다시 내보낸다.
위 '24시간·offset' 문단은 옛 동작 기록이다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from kbj.config.settings import Settings
from kbj.services.notifier.client import webhook_status
from kbj.services.notifier.inbox import (  # KBJ 정본 다시 내보내기(두 벌 금지)
    OEMBED,
    X_ALL_HOSTS,
    X_HOSTS,
    allow_sets as _allow_sets,
    chat_allowed as _chat_allowed,
    extract_urls,
    fill_oembed,
    is_x_url,
    norm_ids as _norm_ids,
    oembed,
    parse_update,
    read_items,
    x_ids_and_author,
)

from ..engine.build import STATE
from .http import Fetch

# ─────────────────────────── 상수 ───────────────────────────
KEEP_DAYS = 14                                           # 보존 기간 기본값. settings.yaml 이 우선
KST = timezone(timedelta(hours=9))

INBOX_PATH = os.path.join(STATE, 'inbox.json')

__all__ = ['INBOX_PATH', 'KEEP_DAYS', 'OEMBED', 'X_ALL_HOSTS', 'X_HOSTS', 'drain',
           'extract_urls', 'fill_oembed', 'inbox_chat_ids', 'is_x_url', 'merge', 'oembed',
           'parse_update', 'probe', 'read_inbox', 'x_ids_and_author', '_allow_sets',
           '_chat_allowed', '_norm_ids']


# ─────────────────────────── 자격증명 ───────────────────────────
def inbox_chat_ids():
    """읽을 대화방. `KBJ_TELEGRAM_INBOX_CHAT_IDS`(쉼표 구분)가 있으면 그것, 없으면
    `KBJ_TELEGRAM_CHAT_ID` 하나(KBJ 설정 — 옛 TRIGGER_INBOX_CHAT_IDS·TELEGRAM_CHAT_ID 는 읽지 않는다)."""
    st = Settings()
    for v in (st.telegram_inbox_chat_ids, st.telegram_chat_id):
        raw = v.get_secret_value() if v is not None else ''
        if raw.strip():
            return _norm_ids(raw.split(','))
    return []


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
def _default_store():
    """KBJ notifier 의 인박스 저장소(`prv_alerts.tg_inbox`). DB 주소가 없으면 Fetch."""
    st = Settings()
    if st.database_url is None:
        raise Fetch('KBJ_DATABASE_URL 이 없다 — 인박스는 KBJ notifier DB(prv_alerts.tg_inbox)에 있다')
    from kbj.services.notifier.store import PgInboxStore   # 지연 import — DB 가 있을 때만
    from kbj.store.db import connect
    return PgInboxStore(connect(st, autocommit=True, service='legacy-et-inbox'))


def drain(chat_ids, inbox_path=INBOX_PATH, *, store=None, log=print, now=None,
          keep_days=KEEP_DAYS):
    """KBJ 인박스에서 최근 `keep_days` 일 항목을 읽어 inbox.json 에 합친다(계약 그대로).

    텔레그램을 부르지 않는다 — 갱신은 notifier 웹훅이 받아 저장소에 넣어 둔다. `store` 는
    시험·스크립트용(없으면 KBJ DB). chat_ids 가 모두 숫자면 그 대화 항목만 남긴다.
    반환 dict(n_new, n_x, n_dropped_other_chat, n_pages, n_kept, n_expired, webhook).
    """
    now = now or datetime.now(KST)
    chat_ids = _norm_ids(chat_ids)
    if not chat_ids:
        raise Fetch('읽을 대화방이 없다 — KBJ_TELEGRAM_INBOX_CHAT_IDS 나 KBJ_TELEGRAM_CHAT_ID 가 필요하다')
    got = read_items(chat_ids, now - timedelta(days=keep_days),
                     store=store if store is not None else _default_store())
    inbox = read_inbox(inbox_path, log=log)
    seen = {x.get('update_id') for x in inbox.get('items') or []}
    items = got.get('items') or []
    inbox, n_new, n_expired = merge(inbox, items, chat_ids, now, keep_days)
    n_x = sum(1 for it in items if it.get('kind') == 'x' and it.get('update_id') not in seen)
    _write_json(inbox_path, inbox)
    return dict(n_new=n_new, n_x=n_x, n_dropped_other_chat=0, n_pages=0,
                n_kept=len(inbox['items']), n_expired=n_expired, webhook=None)


# ─────────────────────────── 점검 ───────────────────────────
def probe():
    """--check 용. KBJ notifier 의 웹훅 상태와 읽을 대화방만 본다(메시지는 보내지 않는다)."""
    rows = []
    ids = inbox_chat_ids()
    if ids:
        rows.append(('대화방', True, f'{len(ids)}개 (KBJ 설정)'))
    else:
        rows.append(('대화방', False, 'KBJ_TELEGRAM_INBOX_CHAT_IDS 도 KBJ_TELEGRAM_CHAT_ID 도 없다'))
    st = webhook_status()
    rows.append(('웹훅(KBJ notifier)', bool(st.get('ok')), str(st.get('reason') or 'ok')[:160]))
    return rows
