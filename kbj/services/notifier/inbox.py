"""텔레그램 인박스 — 사용자가 봇에 공유한 X 게시물·링크를 `prv_alerts.tg_inbox` 로(설계 §5.8).

ET `board/ingest/tg_inbox.py` 승격(파싱 계약 그대로 — D-086). 바뀐 것은 받는 길 하나다:
getUpdates 폴링·offset 파일 대신 **웹훅**(webhook.py)이 받은 업데이트를 `InboxProcessor.process` 가
저장소에 넣는다.
getUpdates·offset·파일 저장 코드(ET `drain`·`_call`·`webhook_url`·`read_offset`·`write_offset`·
`read_inbox`)는 옮기지 않는다(§5.8).

계약(ET 머리말 그대로)
  항목 {"update_id":int,"chat_id":int,"date":"<KST ISO>","text":str,"urls":[str],"x_ids":[str],
        "author":str|null,"kind":"x"|"other","text_via":"message"|"oembed"|null}
  · update_id 로 중복 제거(먼저 있던 것을 둔다). 보존 기간(기본 14일)이 지난 항목은 버린다.
  · `kind:'x'` = x.com / twitter.com / t.co 링크가 있는 메시지. `x_ids` 는 `/status/<id>` 의 id —
    x.com·twitter.com 링크에서만 뽑는다. **t.co 는 풀지 않는다**(외부 호출 최소).
  · `author` = 링크의 계정 → 없으면 oEmbed 의 작성자 → 그것도 없으면 forward_origin 의 이름.
  · `text` 는 메시지 본문(캡션 포함). 본문이 URL 뿐이면 oEmbed(`publish.twitter.com/oembed`,
    공식·인증 없음)로 채우고 `text_via:'oembed'`. 실패하면 URL 그대로 두고 `text_via:null` —
    **지어내지 않는다**.
  · 허용 대화(숫자 id 또는 `@username` — chat.username 과 대조) 밖의 업데이트는 버리고 건수만 센다.
  · 편집(edited_message)은 인박스에 넣지 않는다(ET `ALLOWED`).

legacy ET `ingest/triggers.py` 는 `state/inbox.json` 대신 `read_items(chat_ids, since)` 를 읽는다 —
돌려주는 dict 는 ET inbox.json 과 같은 모양이다.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from kbj.core.masking import mask_text
from kbj.core.time import KST
from kbj.services.notifier.store import InboxItem, InboxStore, inbox_cutoff

log = logging.getLogger(__name__)

TIMEOUT: Final = 20  # oEmbed 호출 1건(초)
KEEP_DAYS: Final = 14  # 보존 기간 기본값. notify.yaml inbox.keep_days 가 우선
OEMBED: Final = "https://publish.twitter.com/oembed"

X_HOSTS: Final = ("x.com", "twitter.com")
X_ALL_HOSTS: Final = (*X_HOSTS, "t.co")

_URL = re.compile(r"https?://[^\s<>\"'　]+", re.I)
# x.com/<계정>/status/<id>. 계정 자리에 `i`(x.com/i/status/…)가 오면 계정이 아니고,
# `i/web`(알림 메일·웹 '링크 복사' 가 주는 x.com/i/web/status/…)도 같다.
_STATUS = re.compile(
    r"^(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/(?:#!/)?(?:i/web/|([A-Za-z0-9_]{1,15})/)"
    r"status(?:es)?/(\d+)",
    re.I,
)
_HOST = re.compile(r"^https?://([^/?#]+)", re.I)
_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_HANDLE = re.compile(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/?$", re.I)
_TRAIL = ".,;:!?)]}>'\"»"  # 문장 끝에 딸려 붙는 글자


class OEmbedError(RuntimeError):
    """oEmbed 를 못 받았다(사유). 항목은 그대로 두고 사유만 남긴다."""


class _Response(Protocol):
    status_code: int

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


class HttpGetter(Protocol):
    """`.get(url, params=, timeout=)` — httpx.Client·requests 세션·시험용 가짜."""

    def get(self, url: str, *, params: Any = ..., timeout: Any = ...) -> Any: ...


# ─────────────────────────── 대화 허용 목록 ───────────────────────────
def norm_ids(ids: object) -> list[int | str]:
    """'1, 2' 나 [' -100123 ', ''] → [1, 2] / [-100123]. 숫자가 아니면(`@channel`) 문자열로 둔다."""
    seq: Iterable[Any]
    if isinstance(ids, str | int):
        seq = str(ids).split(",")
    elif isinstance(ids, Iterable):
        seq = ids  # pyright: ignore[reportUnknownVariableType]
    else:
        seq = []
    out: list[int | str] = []
    for x in seq:  # pyright: ignore[reportUnknownVariableType]
        s = str(x).strip()  # pyright: ignore[reportUnknownArgumentType]
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            out.append(s)  # `@channel` 같은 값 — chat.username 과 맞춘다
    return out


def allow_sets(chat_ids: Iterable[int | str]) -> tuple[set[str], set[str]]:
    """허용 목록 → (숫자 id 문자열 집합, 소문자 username 집합).

    업데이트의 chat.id 는 늘 숫자다. `@channel` 을 그 자리와 비교하면 절대 안 맞아 모든 업데이트를
    '허용 밖' 으로 버리게 된다 — 그래서 username 은 따로 대조한다.
    """
    ids: set[str] = set()
    names: set[str] = set()
    for x in chat_ids:
        if isinstance(x, int):
            ids.add(str(x))
        else:
            names.add(str(x).lstrip("@").lower())
    return ids, names


def chat_allowed(chat: Mapping[str, Any] | None, ids: set[str], names: set[str]) -> bool:
    chat = chat or {}
    if str(chat.get("id")) in ids:
        return True
    uname = str(chat.get("username") or "").lower()
    return bool(uname) and uname in names


@dataclass(frozen=True)
class AllowList:
    """대화 허용 목록(숫자 id·@username)."""

    ids: frozenset[str]
    names: frozenset[str]

    @classmethod
    def of(cls, chat_ids: Iterable[int | str]) -> AllowList:
        ids, names = allow_sets(chat_ids)
        return cls(frozenset(ids), frozenset(names))

    def __bool__(self) -> bool:
        return bool(self.ids or self.names)

    def allows(self, chat: Mapping[str, Any] | None) -> bool:
        return chat_allowed(chat, set(self.ids), set(self.names))


# ─────────────────────────── 파싱 ───────────────────────────
def _utf16_slice(text: str, offset: int, length: int) -> str:
    """텔레그램 entity 의 offset·length 는 UTF-16 코드 유닛이다. 파이썬 인덱스가 아니다."""
    b = text.encode("utf-16-le")
    return b[offset * 2 : (offset + length) * 2].decode("utf-16-le", "ignore")


def _clean_url(u: str | None) -> str:
    s = (u or "").strip()
    while s and s[-1] in _TRAIL:
        s = s[:-1]
    return s


def extract_urls(
    text: str | None, entities: Sequence[Mapping[str, Any]] | None = None
) -> list[str]:
    """entities(url·text_link) + 본문 정규식. 순서를 지키고 중복은 없앤다."""
    body = text or ""
    found: list[str] = []
    for e in entities or []:
        t = e.get("type")
        if t == "text_link" and e.get("url"):
            found.append(str(e["url"]))
        elif t == "url":
            try:
                found.append(_utf16_slice(body, int(e["offset"]), int(e["length"])))
            except (KeyError, TypeError, ValueError):
                continue
    found.extend(_URL.findall(body))
    out: list[str] = []
    for raw in found:
        u = _clean_url(raw)
        if not u:
            continue
        if not u.lower().startswith(("http://", "https://")):
            u = "https://" + u  # `url` entity 는 스킴 없이 올 수 있다
        if u not in out:
            out.append(u)
    return out


def _host(u: str | None) -> str:
    m = _HOST.match(u or "")
    return (m.group(1) if m else "").lower()


def is_x_url(u: str) -> bool:
    h = _host(u)
    return any(h == x or h.endswith("." + x) for x in X_ALL_HOSTS)


def status_of(u: str | None) -> tuple[str | None, str] | None:
    """x.com·twitter.com 링크 → (계정 또는 None, status id). 아니면 None(ET `_status`)."""
    m = _STATUS.match(re.sub(r"^https?://", "", u or "", flags=re.I))
    if not m:
        return None
    acct = m.group(1)  # i/web/ 이면 None
    return (None if not acct or acct.lower() == "i" else acct), m.group(2)


def x_ids_and_author(urls: Iterable[str]) -> tuple[list[str], str | None]:
    ids: list[str] = []
    author: str | None = None
    for u in urls:
        st = status_of(u)
        if not st:
            continue
        acct, sid = st
        if sid not in ids:
            ids.append(sid)
        if author is None and acct:
            author = acct
    return ids, author


def forward_name(msg: Mapping[str, Any]) -> str | None:
    """전달된 메시지의 원 작성자 이름. Bot API 7.0 의 forward_origin 을 먼저, 그 전
    형식(forward_from·forward_sender_name·forward_from_chat)을 다음에 본다."""
    fo: Mapping[str, Any] = msg.get("forward_origin") or {}
    for key in ("sender_user", "sender_chat", "chat"):
        who: Mapping[str, Any] = fo.get(key) or {}
        name = (
            who.get("username")
            or " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x)
            or who.get("title")
        )
        if name:
            return str(name)
    if fo.get("sender_user_name"):
        return str(fo["sender_user_name"])
    for key in ("forward_from", "forward_from_chat"):
        who2: Mapping[str, Any] = msg.get(key) or {}
        name2 = who2.get("username") or who2.get("first_name") or who2.get("title")
        if name2:
            return str(name2)
    sender = msg.get("forward_sender_name")
    return str(sender) if sender else None


def only_urls(text: str | None, urls: Iterable[str]) -> bool:
    """본문이 URL 뿐인가. URL 을 지우고 남는 글자가 없으면 그렇다.

    `url` entity 는 스킴 없이 올 수 있고(본문 'x.com/a/status/1') extract_urls 가 'https://' 를 붙여
    돌려주므로, 스킴을 뗀 꼴도 함께 지워야 본문과 맞는다.
    """
    rest = text or ""
    for u in urls:
        rest = rest.replace(u, " ")
        rest = rest.replace(re.sub(r"^https?://", "", u, flags=re.I), " ")
    rest = _URL.sub(" ", rest)
    return not rest.strip()


def update_message(up: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """인박스 대상 메시지(`message`·`channel_post`). 편집 등은 None."""
    msg = up.get("message") or up.get("channel_post")
    return msg if isinstance(msg, Mapping) else None  # pyright: ignore[reportUnknownVariableType]


def parse_update(up: Mapping[str, Any]) -> InboxItem | None:
    """업데이트 하나 → 항목 dict. 메시지가 아니면(예: 편집) None.

    oEmbed 는 여기서 부르지 않는다 — 네트워크 없이 파싱만 검증할 수 있어야 한다.
    """
    msg = update_message(up)
    if msg is None:
        return None
    chat: Mapping[str, Any] = msg.get("chat") or {}
    text = str(msg.get("text") or msg.get("caption") or "")
    ents = msg.get("entities") or msg.get("caption_entities") or []
    urls = extract_urls(text, ents)
    x_urls = [u for u in urls if is_x_url(u)]
    ids, author = x_ids_and_author(x_urls)
    ts = msg.get("date")
    date = (
        datetime.fromtimestamp(int(ts), KST).isoformat(timespec="seconds")
        if ts is not None
        else None
    )
    return {
        "update_id": up.get("update_id"),
        "chat_id": chat.get("id"),
        "date": date,
        "text": text,
        "urls": urls,
        "x_ids": ids,
        "author": author or forward_name(msg),
        "kind": "x" if x_urls else "other",
        # 본문이 URL 뿐이면 아직 채워진 것이 없다. oEmbed 가 채우면 'oembed' 로 바뀐다.
        "text_via": None if only_urls(text, urls) else "message",
    }


# ─────────────────────────── oEmbed ───────────────────────────
def _oembed_text(h: str | None) -> str:
    """oEmbed html 의 <p> 안쪽이 본문이다. 태그를 걷고 엔티티를 되돌린다."""
    m = _P.search(h or "")
    if not m:
        return ""
    t = _BR.sub("\n", m.group(1))
    t = _TAG.sub("", t)
    return html.unescape(t).strip()


def oembed(s: HttpGetter, url: str, *, timeout: float = TIMEOUT) -> tuple[str, str | None]:
    """공식 oEmbed 로 본문·작성자를 받는다. 반환 (본문, 계정 또는 표시명) — 실패는 OEmbedError.

    쿼리(`?s=20&t=…`)는 떼고 보낸다. 공유 링크에 딸려 오는 추적 파라미터라 게시물과 무관하고,
    없어야 같은 게시물이 같은 요청이 된다.
    """
    clean = url.split("?", 1)[0].split("#", 1)[0]
    try:
        r: _Response = s.get(OEMBED, params={"url": clean, "omit_script": 1}, timeout=timeout)
    except Exception as ex:  # 사유를 문자열로 보존(어떤 세션이 올지 모른다)
        raise OEmbedError(mask_text(f"oEmbed 실패: {type(ex).__name__}: {ex}")) from None
    if r.status_code != 200:
        raise OEmbedError(f"oEmbed HTTP {r.status_code} · {(r.text or '')[:120]}")
    try:
        js: Any = r.json()
    except ValueError:
        raise OEmbedError("oEmbed 응답이 JSON 이 아니다") from None
    body: Mapping[str, Any] = js if isinstance(js, Mapping) else {}
    text = _oembed_text(body.get("html"))
    if not text:
        raise OEmbedError("oEmbed 응답에 본문이 없다")
    hm = _HANDLE.search(str(body.get("author_url") or ""))
    author = (hm.group(1) if hm else None) or body.get("author_name") or None
    return text, (str(author) if author else None)


def fill_oembed(
    item: InboxItem,
    s: HttpGetter | None,
    log_fn: Callable[[str], None] | None = None,
    *,
    timeout: float = TIMEOUT,
) -> InboxItem:
    """본문이 URL 뿐인 X 항목을 oEmbed 로 채운다. 못 채우면 그대로 두고 사유만 적는다.

    s 가 None 이면(발송 꺼짐 모드 — 외부 HTTP 0) 부르지 않는다 — 항목은 '못 채움'(text_via null).
    """
    if item.get("text_via") is not None or item.get("kind") != "x" or s is None:
        return item
    target = next((u for u in item.get("urls") or [] if status_of(u)), None)
    if not target:
        return item  # t.co 뿐이면 풀지 않는다. 지어내지도 않는다
    try:
        text, author = oembed(s, target, timeout=timeout)
    except OEmbedError as ex:
        (log_fn or log.info)(f"oEmbed 못 받음 (update {item.get('update_id')}): {ex}")
        return item
    item["text"] = text
    item["text_via"] = "oembed"
    # 우선순위는 링크 계정 → oEmbed 작성자 → 전달자. parse_update 는 링크 계정이 없을 때
    # forward_origin 의 이름을 넣어 두는데, 그건 텔레그램에서 전달한 채널·사람이지 게시물을 쓴
    # 사람이 아니다. oEmbed 가 실제 작성자를 줬으면 그쪽이 맞다.
    _, link_author = x_ids_and_author(item.get("urls") or [])
    if author and not link_author:
        item["author"] = author
    return item


# ─────────────────────────── 저장 ───────────────────────────
@dataclass(frozen=True)
class InboxReport:
    """한 번 처리한 결과(ET `drain` 반환의 getUpdates 무관한 부분)."""

    n_new: int
    n_x: int
    n_dropped_other_chat: int
    n_kept: int
    n_expired: int


class InboxProcessor:
    """받은 업데이트 → 허용 대화만 파싱·oEmbed → 저장소(update_id 중복 제거·보존 기간)."""

    def __init__(
        self,
        store: InboxStore,
        chat_ids: Iterable[int | str],
        *,
        now: Callable[[], datetime],
        oembed_client: HttpGetter | None = None,
        keep_days: int = KEEP_DAYS,
        oembed_timeout: float = TIMEOUT,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.chat_ids = norm_ids(list(chat_ids))
        self.allow = AllowList.of(self.chat_ids)
        if not self.allow:
            raise ValueError(
                "읽을 대화방이 없다 — KBJ_TELEGRAM_INBOX_CHAT_IDS 나 KBJ_TELEGRAM_CHAT_ID"
            )
        self._now = now
        self._oembed = oembed_client
        self._keep_days = keep_days
        self._oembed_timeout = oembed_timeout
        self._log = log_fn

    def allows(self, up: Mapping[str, Any]) -> bool:
        msg = update_message(up)
        return msg is not None and self.allow.allows(msg.get("chat"))

    def process(self, updates: Sequence[Mapping[str, Any]]) -> InboxReport:
        now = self._now()
        items: list[InboxItem] = []
        dropped: list[object] = []
        for up in updates:
            it = parse_update(up)
            if it is None:
                continue
            msg = update_message(up) or {}
            if not self.allow.allows(msg.get("chat")):
                dropped.append(it.get("chat_id"))
                continue
            items.append(fill_oembed(it, self._oembed, self._log, timeout=self._oembed_timeout))
        new_ids = set(self.store.upsert(items, now)) if items else set()
        n_x = sum(1 for it in items if it.get("kind") == "x" and it.get("update_id") in new_ids)
        n_expired = self.store.expire(inbox_cutoff(now, self._keep_days))
        if dropped:  # chat id 는 남기지 않는다(비밀 취급) — 건수만
            (self._log or log.info)(f"허용 밖 대화 {len(dropped)}건 버림")
        return InboxReport(
            n_new=len(new_ids),
            n_x=n_x,
            n_dropped_other_chat=len(dropped),
            n_kept=self.store.count(),
            n_expired=n_expired,
        )


def read_items(
    chat_ids: Iterable[int | str] | None = None,
    since: datetime | None = None,
    *,
    store: InboxStore,
) -> dict[str, Any]:
    """ET `state/inbox.json` 과 같은 모양의 dict(legacy ET `triggers` 가 읽는다).

    `chat_ids` 가 모두 숫자 id 면 그 대화의 항목만 돌려준다. `@username` 이 하나라도 섞이면
    거르지 않는다 — 항목에는 숫자 chat id 만 있어 username 과 맞출 수 없고, 저장소에는 받을 때
    허용 목록으로 이미 거른 것만 있다(ET `inbox.json` 도 읽는 쪽에서 거르지 않았다). 숫자 id 로만
    거르면 `@channel` 의 항목이 조용히 빠진다.
    `updated_at` 은 마지막으로 받은 시각(KST ISO) — 없으면 None.
    """
    ids = norm_ids(list(chat_ids) if chat_ids is not None else [])
    numeric = {i for i in ids if isinstance(i, int)}
    by_id = bool(ids) and len(numeric) == len(ids)
    rows = store.items(since)
    items = [it for it, _ in rows if not by_id or it.get("chat_id") in numeric]
    last = max((rec for _, rec in rows), default=None)
    return {
        "source": "telegram",
        "updated_at": last.astimezone(KST).isoformat(timespec="seconds") if last else None,
        "chat_ids": ids,
        "items": items,
    }
