"""웹훅 수신(kbj.services.notifier.webhook) — 순수 처리 함수(요청 dict → 응답), 설계 §5.6·D4.

시크릿(`hmac.compare_digest`)·경로·메서드·update_id 중복·Redis 장애(503 — 텔레그램이 다시 보낸다)·
갈래(명령/인박스/버림). HTTP 서버는 없다(FastAPI 는 P3).
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis
import pytest
from pydantic import SecretStr

from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.commands import default_registry
from kbj.services.notifier.inbox import AllowList, InboxProcessor
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig
from kbj.services.notifier.store import MemoryInboxStore
from kbj.services.notifier.webhook import (
    ALLOWED_UPDATES,
    SECRET_HEADER,
    WEBHOOK_PATH,
    InboundQueue,
    InboundRouter,
    WebhookHandler,
    handle_update,
    secret_ok,
)
from kbj.store.redis_keys import NOTIFY_INBOUND, tg_update_key
from tests.fakes.clock import FakeClock

SECRET = "webhook-secret-xyz"
OWNER = -100500
INBOX = 4242


def _req(body: Any, secret: str | None = SECRET, **kw: Any) -> dict[str, Any]:
    headers = {} if secret is None else {SECRET_HEADER.lower(): secret}
    req: dict[str, Any] = {
        "method": "POST",
        "path": WEBHOOK_PATH,
        "headers": headers,
        "body": body if isinstance(body, bytes | str) else json.dumps(body).encode(),
    }
    req.update(kw)
    return req


def _msg_update(uid: int, text: str, chat: int = OWNER, **extra: Any) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {"message_id": uid, "date": 1, "chat": {"id": chat}, "text": text, **extra},
    }


@pytest.fixture
def handler(redis: fakeredis.FakeRedis, clock: FakeClock) -> WebhookHandler:
    return WebhookHandler(SecretStr(SECRET), redis, now=clock)


# ── 처리기 ─────────────────────────────────────────────────────────────────────────────


def test_valid_update_is_acknowledged_and_queued(
    handler: WebhookHandler, redis: fakeredis.FakeRedis
) -> None:
    res = handler.handle(_req(_msg_update(10, "안녕")))
    assert (res.status, res.outcome, res.update_id) == (200, "queued", 10)
    assert redis.exists(tg_update_key(10)) and redis.xlen(NOTIFY_INBOUND) == 1
    ttl = redis.ttl(tg_update_key(10))
    assert isinstance(ttl, int) and 0 < ttl <= 48 * 3600
    assert res.as_response() == {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "body": '{"ok": true}',
    }


def test_same_update_twice_is_processed_once(
    handler: WebhookHandler, redis: fakeredis.FakeRedis
) -> None:
    assert handler.handle(_req(_msg_update(10, "a"))).outcome == "queued"
    again = handler.handle(_req(_msg_update(10, "a")))
    assert (again.status, again.outcome) == (200, "duplicate")  # 200 — 텔레그램이 멈추게
    assert redis.xlen(NOTIFY_INBOUND) == 1


@pytest.mark.parametrize("secret", [None, "", "webhook-secret-xy", "WEBHOOK-SECRET-XYZ"])
def test_wrong_or_missing_secret_is_forbidden(
    handler: WebhookHandler, redis: fakeredis.FakeRedis, secret: str | None
) -> None:
    res = handler.handle(_req(_msg_update(10, "a"), secret=secret))
    assert (res.status, res.outcome) == (403, "forbidden")
    assert redis.xlen(NOTIFY_INBOUND) == 0 and not redis.exists(tg_update_key(10))
    assert "secret" not in res.as_response()["body"]


def test_unconfigured_secret_refuses_everything(
    redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    """비밀이 없으면 인증 없는 수신을 하지 않는다(SD 의 봇 토큰 파생 기본값 같은 것 없음)."""
    for secret in (None, SecretStr("")):
        h = WebhookHandler(secret, redis, now=clock)
        assert h.handle(_req(_msg_update(1, "a"), secret="")).status == 503
    assert not secret_ok(None, "x") and not secret_ok(SecretStr("a"), None)


def test_header_name_is_case_insensitive(handler: WebhookHandler) -> None:
    req = _req(_msg_update(3, "a"))
    req["headers"] = {"X-TELEGRAM-BOT-API-SECRET-TOKEN": SECRET}
    assert handler.handle(req).outcome == "queued"


@pytest.mark.parametrize(
    ("over", "status"),
    [({"path": "/api/telegram/webhook"}, 404), ({"method": "GET"}, 405)],
)
def test_wrong_route(handler: WebhookHandler, over: dict[str, str], status: int) -> None:
    assert handler.handle(_req(_msg_update(1, "a"), **over)).status == status


@pytest.mark.parametrize("body", [b"{not json", b"[1,2]", {"no_update_id": 1}, {"update_id": -1}])
def test_bad_body_is_400(handler: WebhookHandler, body: Any) -> None:
    res = handler.handle(_req(body))
    assert (res.status, res.outcome) == (400, "bad_request")


def test_body_may_already_be_a_dict(handler: WebhookHandler) -> None:
    req = _req(b"")
    req["body"] = _msg_update(5, "x")
    assert handler.handle(req).outcome == "queued"
    assert handle_update(handler, _msg_update(6, "y"), SECRET).outcome == "queued"


def test_redis_down_is_503_and_leaves_no_mark(clock: FakeClock) -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    h = WebhookHandler(SecretStr(SECRET), r, now=clock)
    server.connected = False
    res = h.handle(_req(_msg_update(10, "a")))
    assert (res.status, res.outcome) == (503, "unavailable")
    server.connected = True
    assert not r.exists(tg_update_key(10))  # 다시 오면 받는다


# ── 갈래 ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def router(config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock) -> InboundRouter:
    store = MemoryInboxStore()
    inbox = InboxProcessor(store, [INBOX], now=clock, oembed_client=None)
    client = NotifyClient(config, True, outbox=Outbox(redis, now=clock), now=clock)
    return InboundRouter(
        commands=default_registry(),
        client=client,
        command_chats=AllowList.of([OWNER, INBOX]),
        inbox=inbox,
    )


def _replies(redis: fakeredis.FakeRedis, clock: FakeClock) -> list[Any]:
    return [e.message for e in Outbox(redis, now=clock).read("t", 10) if e.message]


def test_command_from_owner_is_answered_in_the_same_chat_and_thread(
    router: InboundRouter, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    r = router.route(_msg_update(31, "/도움", message_thread_id=9))
    assert r.route == "command" and r.ticket is not None and r.ticket.ok
    (m,) = _replies(redis, clock)
    assert m.kind == "cmd.reply" and m.chat == str(OWNER) and m.thread_id == 9
    assert m.subject == "u31" and "명령어" in (m.text or "")
    assert str(OWNER) not in m.dedup_key  # chat id 는 키에 넣지 않는다


def test_edited_command_is_answered_too(router: InboundRouter) -> None:
    up = {"update_id": 32, "edited_message": {"chat": {"id": OWNER}, "text": "/help"}}
    assert router.route(up).route == "command"


def test_command_from_a_stranger_is_ignored(
    router: InboundRouter, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    r = router.route(_msg_update(33, "/도움", chat=999))
    assert r.route == "ignored" and _replies(redis, clock) == []
    assert router.counts.ignored == 1


def test_link_in_inbox_chat_goes_to_the_inbox(router: InboundRouter) -> None:
    r = router.route(_msg_update(34, "https://x.com/a/status/1 메모", chat=INBOX))
    assert r.route == "inbox" and r.inbox is not None and r.inbox.n_new == 1


def test_plain_message_from_owner_who_is_not_an_inbox_chat_is_ignored(
    router: InboundRouter,
) -> None:
    assert router.route(_msg_update(35, "그냥 말")).route == "ignored"


def test_non_message_update_is_counted_separately(router: InboundRouter) -> None:
    r = router.route({"update_id": 36, "my_chat_member": {"chat": {"id": OWNER}}})
    assert r.route == "not_message" and router.counts.not_message == 1


def test_inbound_queue_round_trip(handler: WebhookHandler, redis: fakeredis.FakeRedis) -> None:
    handler.handle(_req(_msg_update(40, "a")))
    redis.xadd(NOTIFY_INBOUND, {"v": "1", "update": "{깨짐"})
    q = InboundQueue(redis)
    got = q.read("c", 10)
    assert got[0].update is not None and got[0].update["update_id"] == 40
    assert got[1].update is None and got[1].error is not None
    for e in got:
        q.ack(e.id)
    assert q.backlog() == 0


def test_allowed_updates_match_the_design() -> None:
    assert ALLOWED_UPDATES == ("message", "edited_message", "channel_post")
