"""텔레그램 인박스(kbj.services.notifier.inbox) — ET `board/tests/test_tg_inbox.py` 39개 중 27개
승격.

ET 시험은 가짜 getUpdates 로 `drain` 을 돌렸다. KBJ 는 getUpdates 를 없애고(설계 §5.8) 웹훅으로
받으므로, 같은 업데이트를 **받는 길 전체**로 흘린다: 웹훅 처리기(시크릿·update_id 중복) →
`notify:inbound` → 갈래(InboundRouter) → `InboxProcessor` → 저장소 → `read_items`(ET
`state/inbox.json` 과 같은 dict).

지키려는 것(ET 머리말 그대로)
1. **지어내지 않는다.** oEmbed 를 못 받으면 본문은 URL 그대로, text_via 는 null.
2. **버리지 않는다.** 종목명이 없는 일반 메시지도 kind:'other' 로 남는다.
3. (ET 1번 '디스크가 다음 호출보다 먼저다' 는 getUpdates offset 규칙이라 웹훅에서는 '같은 update_id
   는 한 번만' 으로 바뀐다 — offset 단언은 `tg:update:<id>` 표시로 옮겼다.)

승격 27 = Links 9 · Whitelist 4 · DedupExpiry 4 · OEmbed 7 · Other 3(단언 그대로, 이름 바뀐 곳만):
`TI._status`→`status_of`, `TI._only_urls`→`only_urls`, `TI._forward_name`→`forward_name`,
`Fetch`(대화방 없음)→`ValueError`, ET `r['n_dropped_other_chat']`→갈래의 '허용 밖' 건수,
`os.remove(offset)`(같은 페이지를 다시 받음)→`tg:update:<id>` 를 지우고 다시 보냄,
`offset_value()`(버린·무시한 업데이트도 지나감)→그 update_id 가 처리됨으로 표시돼 다시 와도
duplicate.
버림 12(Paging 4·Webhook 3·CmdInboxExit 2·ChatIdsFromCreds 1·파일 손상 2) — getUpdates·offset·파일
저장·ET CLI 가 없어졌다(설계 §1.6).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import fakeredis
import pytest
from pydantic import SecretStr

from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.commands import default_registry
from kbj.services.notifier.inbox import (
    KEEP_DAYS,
    OEMBED,
    AllowList,
    InboxProcessor,
    extract_urls,
    forward_name,
    only_urls,
    read_items,
    status_of,
)
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig
from kbj.services.notifier.store import MemoryInboxStore
from kbj.services.notifier.webhook import (
    SECRET_HEADER,
    WEBHOOK_PATH,
    InboundQueue,
    InboundRouter,
    WebhookHandler,
)
from kbj.store.redis_keys import tg_update_key
from tests.unit.notifier.conftest import KST

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=KST)
CHAT = 777
SECRET = "inbox-test-secret"


class Resp:
    def __init__(self, js: Any, status: int = 200, text: str | None = None) -> None:
        self._js, self.status_code = js, status
        self.text = text if text is not None else json.dumps(js)

    def json(self) -> Any:
        if self._js is None:
            raise ValueError("not json")
        return self._js


class FakeAPI:
    """oEmbed 응답(ET FakeAPI 의 oEmbed 부분). getUpdates 는 없다 — 업데이트는 웹훅으로 들어온다."""

    def __init__(self, oembed: Any = None) -> None:
        self.oembed = oembed  # 함수(url) → Resp, 또는 예외 인스턴스
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: Any = None, timeout: Any = None) -> Resp:
        p = dict(params or {})
        if url.startswith(OEMBED):
            self.calls.append(("oembed", p))
            if isinstance(self.oembed, Exception):
                raise self.oembed
            if self.oembed is None:
                return Resp({}, status=404, text="Not Found")
            return self.oembed(p.get("url"))
        raise AssertionError(f"예상 밖 호출 {url}")


def upd(
    uid: int,
    text: str,
    chat: int = CHAT,
    when: datetime = NOW,
    entities: list[dict[str, Any]] | None = None,
    caption: bool = False,
    forward: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = (
        {"caption": text, "caption_entities": entities or []}
        if caption
        else {"text": text, "entities": entities or []}
    )
    msg: dict[str, Any] = dict(
        message_id=uid * 10, date=int(when.timestamp()), chat=dict(id=chat, type="private"), **body
    )
    if forward:
        msg["forward_origin"] = forward
    return dict(update_id=uid, message=msg)


def oembed_ok(url: str) -> Resp:
    return Resp(
        dict(
            url=url,
            author_name="Some One",
            author_url="https://twitter.com/someone",
            html='<blockquote class="twitter-tweet"><p lang="ko" dir="ltr">삼성전자 HBM4 '
            "양산 &amp; 공급<br>2분기부터</p>&mdash; Some One (@someone) "
            '<a href="https://twitter.com/someone/status/1">Sep 20, 2026</a></blockquote>',
        )
    )


class Harness:
    """웹훅 → 스트림 → 갈래 → 인박스. ET `Base.drain`/`read` 자리."""

    def __init__(self, config: NotifyConfig) -> None:
        self.config = config
        self.redis = fakeredis.FakeRedis()
        self.store = MemoryInboxStore()
        self.chat_ids: Sequence[int | str] = (CHAT,)
        self.logs: list[str] = []

    def post(self, up: dict[str, Any], now: datetime = NOW) -> str:
        h = WebhookHandler(SecretStr(SECRET), self.redis, now=lambda: now)
        req = {
            "method": "POST",
            "path": WEBHOOK_PATH,
            "headers": {SECRET_HEADER: SECRET},
            "body": json.dumps(up).encode(),
        }
        return h.handle(req).outcome

    def drain(
        self,
        updates: Sequence[dict[str, Any]],
        chat_ids: Sequence[int | str] = (CHAT,),
        now: datetime = NOW,
        keep_days: int = KEEP_DAYS,
        api: FakeAPI | None = None,
    ) -> dict[str, int]:
        self.chat_ids = chat_ids
        proc = InboxProcessor(
            self.store,
            chat_ids,
            now=lambda: now,
            oembed_client=api if api is not None else FakeAPI(),
            keep_days=keep_days,
            log_fn=self.logs.append,
        )
        router = InboundRouter(
            commands=default_registry(),
            client=NotifyClient(self.config, False, outbox=Outbox(self.redis, now=lambda: now)),
            command_chats=AllowList.of(chat_ids),
            inbox=proc,
        )
        for up in updates:
            self.post(up, now)
        q = InboundQueue(self.redis)
        out = {"n_new": 0, "n_x": 0, "n_expired": 0}
        for e in q.read("t", 1000):
            assert e.update is not None
            rr = router.route(e.update)
            q.ack(e.id)
            if rr.inbox is not None:
                for k in out:
                    out[k] += getattr(rr.inbox, k)
        out["n_expired"] += proc.process([]).n_expired  # 받은 것이 없어도 만료는 적용(ET)
        out["n_dropped_other_chat"] = router.counts.ignored
        out["n_kept"] = self.store.count()
        return out

    def read(self) -> dict[str, Any]:
        return read_items(self.chat_ids, store=self.store)

    def handled(self, uid: int) -> bool:
        return bool(self.redis.exists(tg_update_key(uid)))


@pytest.fixture
def h(config: NotifyConfig) -> Harness:
    return Harness(config)


# ── (b) 화이트리스트 ─────────────────────────────────────────────────────────────────────


def test_other_chats_are_dropped_and_counted(h: Harness) -> None:
    ups = [upd(1, "내 것"), upd(2, "남의 것", chat=999), upd(3, "또 남", chat=998)]
    r = h.drain(ups)
    assert r["n_dropped_other_chat"] == 2
    assert r["n_new"] == 1
    assert [x["chat_id"] for x in h.read()["items"]] == [CHAT]
    # 버린 업데이트도 처리한 것으로 표시된다. 안 그러면 텔레그램이 다시 보낼 때마다 다시 센다.
    assert all(h.handled(u) for u in (1, 2, 3))
    assert [h.post(u) for u in ups] == ["duplicate"] * 3


def test_chat_ids_accept_strings(h: Harness) -> None:
    r = h.drain([upd(1, "x", chat=-100123)], chat_ids=[" -100123 ", ""])
    assert r["n_new"] == 1
    assert h.read()["chat_ids"] == [-100123]


def test_no_chat_ids_is_an_error(h: Harness) -> None:
    with pytest.raises(ValueError):
        h.drain([], chat_ids=[])


def test_at_username_matches_chat_username_not_id(h: Harness) -> None:
    """업데이트의 chat.id 는 늘 숫자다. `@channel` 은 chat.username 과 맞춰야 한다 — 문자열 그대로
    id 와 비교하면 모든 업데이트를 '허용 밖' 으로 버린다."""
    post = dict(
        update_id=1,
        channel_post=dict(
            message_id=10,
            date=int(NOW.timestamp()),
            chat=dict(id=-100999, type="channel", username="MyNews", title="뉴스"),
            text="https://x.com/a/status/1 메모",
            entities=[],
        ),
    )
    other = dict(
        update_id=2,
        channel_post=dict(
            message_id=20,
            date=int(NOW.timestamp()),
            chat=dict(id=-100998, type="channel", username="other", title="딴 방"),
            text="남의 것",
            entities=[],
        ),
    )
    r = h.drain([post, other], chat_ids=["@mynews"])
    assert r["n_new"] == 1
    assert r["n_dropped_other_chat"] == 1
    assert [x["chat_id"] for x in h.read()["items"]] == [-100999]


# ── (c) 링크 추출 ──────────────────────────────────────────────────────────────────────


def test_x_com_link_gives_id_and_author(h: Harness) -> None:
    url = "https://x.com/semianalysis/status/1839000000000000001?s=46&t=zz"
    r = h.drain([upd(1, f"이거 봐 {url}")])
    it = h.read()["items"][0]
    assert r["n_x"] == 1
    assert it["kind"] == "x"
    assert it["x_ids"] == ["1839000000000000001"]
    assert it["author"] == "semianalysis"
    assert it["urls"] == [url]
    assert it["text_via"] == "message"


def test_twitter_com_and_entities(h: Harness) -> None:
    # 본문에는 안 보이고 entity 에만 있는 text_link, 그리고 UTF-16 오프셋의 url entity.
    # 앞에 이모지(서로게이트 쌍, UTF-16 으로 2유닛)를 둔다 — '한글' 같은 BMP 문자만 쓰면 UTF-16
    # 유닛 수와 파이썬 인덱스가 같아 `text[offset:]` 구현도 통과해 버린다.
    head = "🚀🚀 한글 "
    text = head + "https://twitter.com/foo/status/42 끝"
    off = len(head.encode("utf-16-le")) // 2
    assert off == len(head) + 2, "이모지 둘이 파이썬 인덱스보다 2유닛 더 나가야 한다"
    ents = [
        dict(type="url", offset=off, length=len("https://twitter.com/foo/status/42")),
        dict(type="text_link", offset=0, length=2, url="https://x.com/bar/status/43"),
    ]
    h.drain([upd(1, text, entities=ents)])
    it = h.read()["items"][0]
    assert it["x_ids"] == ["42", "43"], "entity 목록 순서대로"
    assert it["author"] == "foo", "첫 링크의 계정"
    assert len(it["urls"]) == 2


def test_t_co_is_x_but_not_resolved(h: Harness) -> None:
    api = FakeAPI()
    h.drain([upd(1, "https://t.co/AbCdEf")], api=api)
    it = h.read()["items"][0]
    assert it["kind"] == "x"
    assert it["x_ids"] == []
    assert it["author"] is None
    assert it["text_via"] is None, "URL 뿐인데 풀 수 없으면 채운 것이 없다"
    assert it["text"] == "https://t.co/AbCdEf"
    assert not [c for c in api.calls if c[0] == "oembed"], "t.co 로는 oEmbed 도 안 부른다"


def test_i_status_has_no_author_but_forward_origin_does(h: Harness) -> None:
    fwd = dict(type="hidden_user", sender_user_name="전달한 사람")
    h.drain([upd(1, "https://x.com/i/status/77 메모", forward=fwd)])
    it = h.read()["items"][0]
    assert it["x_ids"] == ["77"]
    assert it["author"] == "전달한 사람"


def test_i_web_status_gives_id_without_author(h: Harness) -> None:
    """알림 메일·웹 '링크 복사' 는 x.com/i/web/status/<id> 를 준다. id 는 뽑히고 계정은 없다."""
    assert status_of("https://x.com/i/web/status/123?s=46") == (None, "123")
    assert status_of("https://twitter.com/i/web/status/9") == (None, "9")
    h.drain([upd(1, "https://x.com/i/web/status/123?s=46")], api=FakeAPI(oembed=oembed_ok))
    it = h.read()["items"][0]
    assert it["x_ids"] == ["123"]
    assert it["text_via"] == "oembed", "URL 뿐이면 oEmbed 대상이 된다"
    assert it["author"] == "someone"


def test_schemeless_url_entity_still_counts_as_url_only(h: Harness) -> None:
    """`url` entity 는 스킴 없이 온다. extract_urls 가 붙인 https:// 때문에 본문과 안 맞으면 URL
    뿐인 메시지가 '본문 있음' 으로 남고 oEmbed 도 안 부른다."""
    text = "x.com/a/status/1"
    ents = [dict(type="url", offset=0, length=len(text))]
    api = FakeAPI(oembed=oembed_ok)
    h.drain([upd(1, text, entities=ents)], api=api)
    it = h.read()["items"][0]
    assert it["urls"] == ["https://x.com/a/status/1"]
    assert it["text_via"] == "oembed"
    assert [c for c in api.calls if c[0] == "oembed"]
    assert only_urls("x.com/a/status/1", ["https://x.com/a/status/1"])
    assert not only_urls("x.com/a/status/1 메모", ["https://x.com/a/status/1"])


def test_forward_origin_user_and_channel() -> None:
    assert (
        forward_name(
            dict(
                forward_origin=dict(
                    type="user", sender_user=dict(first_name="길동", last_name="홍")
                )
            )
        )
        == "길동 홍"
    )
    assert (
        forward_name(
            dict(
                forward_origin=dict(type="channel", chat=dict(title="뉴스방", username="newsroom"))
            )
        )
        == "newsroom"
    )
    assert forward_name(dict(forward_from=dict(username="old"))) == "old"
    assert forward_name({}) is None


def test_trailing_punctuation_is_not_part_of_the_url() -> None:
    urls = extract_urls("봐라 (https://x.com/a/status/1). 그리고 https://x.com/a/status/1")
    assert urls == ["https://x.com/a/status/1"]


def test_caption_of_a_photo_counts_as_text(h: Harness) -> None:
    h.drain([upd(1, "캡처 https://x.com/z/status/9", caption=True)])
    it = h.read()["items"][0]
    assert it["x_ids"] == ["9"]
    assert it["text"] == "캡처 https://x.com/z/status/9"


# ── (d) 중복 제거·만료 ─────────────────────────────────────────────────────────────────


def test_same_update_id_is_not_added_twice(h: Harness) -> None:
    h.drain([upd(1, "한 번")])
    # 중복 표시(48시간)가 지나 텔레그램이 같은 업데이트를 또 보내는 상황(ET: offset 파일이 사라짐).
    h.redis.delete(tg_update_key(1))
    r = h.drain([upd(1, "한 번"), upd(2, "새로")])
    assert r["n_new"] == 1
    assert [x["update_id"] for x in h.read()["items"]] == [1, 2]


def test_items_older_than_keep_days_are_dropped(h: Harness) -> None:
    old = NOW - timedelta(days=15)
    edge = NOW - timedelta(days=13)
    r = h.drain([upd(1, "옛날", when=old), upd(2, "아직", when=edge)])
    assert r["n_expired"] == 1
    assert [x["update_id"] for x in h.read()["items"]] == [2]


def test_expiry_applies_to_stored_items_on_a_later_run(h: Harness) -> None:
    h.drain([upd(1, "그때는 신선")])
    r = h.drain([], now=NOW + timedelta(days=15))
    assert r["n_expired"] == 1
    assert h.read()["items"] == []


def test_keep_days_is_configurable(h: Harness) -> None:
    r = h.drain([upd(1, "x", when=NOW - timedelta(days=3))], keep_days=2)
    assert r["n_expired"] == 1


# ── (f) oEmbed ─────────────────────────────────────────────────────────────────────────


def test_url_only_message_is_filled_from_oembed(h: Harness) -> None:
    api = FakeAPI(oembed=oembed_ok)
    h.drain([upd(1, "https://x.com/i/status/1?s=20")], api=api)
    it = h.read()["items"][0]
    assert it["text_via"] == "oembed"
    assert it["text"] == "삼성전자 HBM4 양산 & 공급\n2분기부터"
    assert it["author"] == "someone", "author_url 의 계정"
    assert it["x_ids"] == ["1"]
    oe = [p for m, p in api.calls if m == "oembed"]
    assert oe[0]["url"] == "https://x.com/i/status/1", "추적 파라미터는 뗀다"
    assert str(oe[0]["omit_script"]) == "1"


def test_oembed_failure_keeps_the_url_and_marks_null(h: Harness) -> None:
    h.drain([upd(1, "https://x.com/gone/status/2")], api=FakeAPI(oembed=None))  # 404
    it = h.read()["items"][0]
    assert it["text"] == "https://x.com/gone/status/2"
    assert it["text_via"] is None
    assert it["author"] == "gone", "링크의 계정은 oEmbed 와 무관하게 있다"


def test_oembed_exception_does_not_stop_the_page(h: Harness) -> None:
    api = FakeAPI(oembed=ConnectionError("막힘"))
    r = h.drain([upd(1, "https://x.com/a/status/3"), upd(2, "다음 것")], api=api)
    assert r["n_new"] == 2
    assert h.read()["items"][0]["text_via"] is None


def test_message_with_caption_does_not_call_oembed(h: Harness) -> None:
    api = FakeAPI(oembed=oembed_ok)
    h.drain([upd(1, "이건 내 메모 https://x.com/a/status/4")], api=api)
    assert h.read()["items"][0]["text_via"] == "message"
    assert not [c for c in api.calls if c[0] == "oembed"]


def test_link_author_wins_over_oembed_author(h: Harness) -> None:
    h.drain([upd(1, "https://x.com/linkacct/status/5")], api=FakeAPI(oembed=oembed_ok))
    assert h.read()["items"][0]["author"] == "linkacct"


def test_oembed_author_beats_forward_origin(h: Harness) -> None:
    """채널 '뉴스방' 이 전달한 i/status 링크. 게시물을 쓴 건 oEmbed 가 준 계정이지 전달한 채널이
    아니다 — 전달자를 앞에 두면 X 게시물을 채널이 쓴 것처럼 보인다."""
    fwd = dict(type="channel", chat=dict(title="뉴스방", username="newsroom"))
    h.drain([upd(1, "https://x.com/i/status/5", forward=fwd)], api=FakeAPI(oembed=oembed_ok))
    it = h.read()["items"][0]
    assert it["text_via"] == "oembed"
    assert it["author"] == "someone"


def test_forward_origin_stays_when_oembed_fails(h: Harness) -> None:
    fwd = dict(type="channel", chat=dict(title="뉴스방", username="newsroom"))
    h.drain([upd(1, "https://x.com/i/status/6", forward=fwd)], api=FakeAPI(oembed=None))
    it = h.read()["items"][0]
    assert it["text_via"] is None
    assert it["author"] == "newsroom", "채운 것이 없으면 전달자라도 남긴다"


# ── (g) 일반 메시지 ────────────────────────────────────────────────────────────────────


def test_plain_message_is_kept_as_other(h: Harness) -> None:
    r = h.drain([upd(1, "오늘 장 어땠어"), upd(2, "https://n.news.naver.com/a/1")])
    assert r["n_new"] == 2
    assert r["n_x"] == 0
    items = h.read()["items"]
    assert [x["kind"] for x in items] == ["other", "other"]
    assert items[0]["text"] == "오늘 장 어땠어"
    assert items[0]["text_via"] == "message"
    assert items[1]["urls"] == ["https://n.news.naver.com/a/1"]
    assert items[1]["x_ids"] == []


def test_item_shape_matches_the_contract(h: Harness) -> None:
    h.drain([upd(1, "x https://x.com/a/status/1")])
    d = h.read()
    assert set(d) == {"source", "updated_at", "chat_ids", "items"}
    assert d["updated_at"] == NOW.isoformat(timespec="seconds")
    it = d["items"][0]
    assert set(it) == {
        "update_id",
        "chat_id",
        "date",
        "text",
        "urls",
        "x_ids",
        "author",
        "kind",
        "text_via",
    }
    assert it["date"] == NOW.isoformat(timespec="seconds")
    assert it["date"].endswith("+09:00")


def test_non_message_updates_are_ignored(h: Harness) -> None:
    edited = dict(update_id=9, edited_message=dict(chat=dict(id=CHAT), text="편집"))
    r = h.drain([edited])
    assert r["n_new"] == 0
    assert h.handled(9), "무시한 업데이트도 처리한 것으로 표시된다"


# ── 새로: read_items 계약·since ───────────────────────────────────────────────────────


def test_read_items_filters_by_since_and_numeric_chat(h: Harness) -> None:
    h.drain(
        [upd(1, "a", when=NOW - timedelta(hours=30)), upd(2, "b", chat=555)], chat_ids=[CHAT, 555]
    )
    recent = read_items([CHAT, 555], since=NOW - timedelta(hours=24), store=h.store)
    assert [x["update_id"] for x in recent["items"]] == [2]
    only = read_items([CHAT], store=h.store)
    assert [x["update_id"] for x in only["items"]] == [1]
    assert read_items(store=MemoryInboxStore())["updated_at"] is None


def test_read_items_with_a_username_does_not_drop_that_channel(h: Harness) -> None:
    """`@channel` 이 섞인 목록으로 읽어도 그 채널 항목이 빠지지 않는다 — 항목에는 숫자 chat id 만
    있어 username 과 맞출 수 없으므로 거르지 않는다(ET 도 읽는 쪽에서 거르지 않았다)."""
    post = dict(
        update_id=1,
        channel_post=dict(
            message_id=10,
            date=int(NOW.timestamp()),
            chat=dict(id=-100999, type="channel", username="MyNews", title="뉴스"),
            text="메모",
            entities=[],
        ),
    )
    h.drain([post, upd(2, "내 것")], chat_ids=[CHAT, "@mynews"])
    got = read_items([CHAT, "@mynews"], store=h.store)
    assert [x["update_id"] for x in got["items"]] == [1, 2]
    assert got["chat_ids"] == [CHAT, "@mynews"]
