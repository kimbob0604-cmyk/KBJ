"""발송 금지 모드(`KBJ_NOTIFY_ENABLED=false`, 기본) — 외부 HTTP 호출 0 을 고정한다(설계 §5.5, D4).

조립은 진입점과 같은 `build_service` 로 한다. 텔레그램·oEmbed 자리에 세는 transport 를 끼우고, 그
밖의 실제 HTTP 전송은 `no_real_http` 가 막는다(시도하면 시험이 깨진다). 넣은 것: 본문·첨부·사진
묶음 알림, legacy shim 발송, 웹훅으로 받은 명령·X 링크(oEmbed 대상)·일반 메시지, 웹훅 점검 주기.
기대: 텔레그램·oEmbed 요청 0, 발송 기록은 모두 `suppressed`, 인박스는 '못 채움'(text_via null —
지어내지 않음), 명령 응답도 `suppressed`.
"""

from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import httpx
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.services.notifier.policy import NotifyConfig
from kbj.services.notifier.service import build_service
from kbj.services.notifier.store import MemoryInboxStore, MemoryNotifyLogStore
from kbj.services.notifier.webhook import SECRET_HEADER, WEBHOOK_PATH, WebhookHandler
from kbj.services.runtime import MemoryHealthSink
from tests.fakes.clock import FakeClock
from tests.unit.notifier.conftest import CHAT, SECRET, make_settings


def test_notify_is_off_by_default() -> None:
    assert Settings(_env_file=None).notify_enabled is False  # pyright: ignore[reportCallIssue]


def test_disabled_mode_makes_zero_http_calls(
    config: NotifyConfig,
    clock: FakeClock,
    tmp_path: Path,
    no_real_http: list[str],
) -> None:
    requests: list[str] = []

    def count(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    counting = httpx.MockTransport(count)
    redis = fakeredis.FakeRedis()
    log = MemoryNotifyLogStore()
    inbox = MemoryInboxStore()
    settings = make_settings(telegram_inbox_chat_ids=SecretStr(CHAT))
    assert settings.notify_enabled is False
    svc = build_service(
        settings,
        config=config,
        redis=redis,
        log_store=log,
        inbox_store=inbox,
        now=clock,
        health=MemoryHealthSink(),
        telegram_transport=counting,
        oembed_transport=counting,
        limiter_clock=clock,
        sleep=clock.sleep,
    )
    assert svc.router is not None
    client = svc.router.client

    # 보낼 것: 본문·첨부·사진, legacy shim
    doc = tmp_path / "rankings.xlsx"
    doc.write_bytes(b"xlsx")
    png = tmp_path / "c.png"
    png.write_bytes(b"png")
    assert client.notify(
        "<b>아침</b>", kind="brief.morning", source="job.brief.morning"
    ).reason == ("suppressed")
    client.notify_document(doc, "엑셀", kind="board.files", source="et.board.files")
    client.notify_media([png], "차트", kind="flows.report", source="et.flow")
    assert client.legacy_send("SD 알림", source="sd.send_telegram")[0] is False

    # 받은 것: 명령·X 링크(URL 뿐 → 켜짐이면 oEmbed 대상)·일반 메시지
    hook = WebhookHandler(SecretStr(SECRET), redis, now=clock)
    chat = int(CHAT)
    for uid, text in ((1, "/도움"), (2, "https://x.com/i/status/5"), (3, "메모")):
        msg = {"date": int(clock.now().timestamp()), "chat": {"id": chat}, "text": text}
        up = {"update_id": uid, "message": msg}
        res = hook.handle(
            {
                "method": "POST",
                "path": WEBHOOK_PATH,
                "headers": {SECRET_HEADER: SECRET},
                "body": json.dumps(up),
            }
        )
        assert res.status == 200

    for _ in range(3):  # 웹훅 점검 주기를 넘기며 여러 바퀴
        svc.step()
        clock.advance(700)

    assert requests == [] and no_real_http == []  # ← 핵심: 외부 HTTP 0
    statuses = {(r.kind, r.status) for r in log.rows}
    assert statuses == {
        ("brief.morning", "suppressed"),
        ("board.files", "suppressed"),
        ("flows.report", "suppressed"),
        ("legacy.other", "suppressed"),
        ("cmd.reply", "suppressed"),
    }
    items = [it for it, _ in inbox.items()]
    assert [it["update_id"] for it in items] == [2, 3]
    assert items[0]["text_via"] is None  # oEmbed 를 부르지 않았다 — 지어내지 않는다
    info = json.loads(redis.get("notify:webhook_info") or b"{}")
    assert info["checked"] is False


def test_same_assembly_with_the_switch_on_does_call_out(
    config: NotifyConfig, clock: FakeClock, no_real_http: list[str]
) -> None:
    """대조군 — 같은 조립에 스위치만 켜면 텔레그램·oEmbed 를 부른다(0 이 배선 누락 탓이 아니다)."""
    calls: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host or "")
        if request.url.host == "publish.twitter.com":
            return httpx.Response(200, json={"html": "<p>본문</p>", "author_name": "x"})
        if request.url.path.endswith("getWebhookInfo"):
            return httpx.Response(200, json={"ok": True, "result": {"url": ""}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    t = httpx.MockTransport(answer)
    redis = fakeredis.FakeRedis()
    settings = make_settings(notify_enabled=True, telegram_inbox_chat_ids=SecretStr(CHAT))
    svc = build_service(
        settings,
        config=config,
        redis=redis,
        log_store=MemoryNotifyLogStore(),
        inbox_store=MemoryInboxStore(),
        now=clock,
        telegram_transport=t,
        oembed_transport=t,
        limiter_clock=clock,
        sleep=clock.sleep,
    )
    assert svc.router is not None
    svc.router.client.notify("x", kind="ops.universe", source="s")
    hook = WebhookHandler(SecretStr(SECRET), redis, now=clock)
    msg = {
        "date": int(clock.now().timestamp()),
        "chat": {"id": int(CHAT)},
        "text": "https://x.com/i/status/5",
    }
    hook.handle_update({"update_id": 1, "message": msg}, SECRET)
    svc.step()
    assert calls.count("api.telegram.org") == 2  # sendMessage + getWebhookInfo
    assert calls.count("publish.twitter.com") == 1
    assert no_real_http == []
