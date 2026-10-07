"""텔레그램 Bot API 클라이언트(kbj.services.notifier.telegram_api).

승격(ET `board/tests/test_telegram.py`·`test_send_files.py` — 단언 내용 그대로, unittest → pytest):
- `TestSend` 5개: 자격증명 없을 때·빈 본문의 반환값. ET `send(text, token=, chat_id=)` →
  `TelegramApi(token).send_message(chat_id, text)`, `check(token=, chat_id=)` →
  `TelegramApi(token).check(chat_id)`. 반환은 `(ok, 사유)` 튜플 대신 `SendResult(ok, reason)`
  (check 는 튜플 그대로). 사유 문구의 이름은 KBJ 환경변수(`KBJ_TELEGRAM_BOT_TOKEN`) —
  'TELEGRAM_BOT_TOKEN' 을 담는다는 단언은 그대로 성립한다. ET 의 `T._cred` 바꿔치기(환경에
  자격이 있어도 같은 결과)는 필요 없다 — 토큰은 생성자로만 받는다.
- `TestCheckDestination` 3개: `requests.get` 바꿔치기 → httpx MockTransport(FakeTelegram).
- `TestPlainTextMode` 2개: ET 보드 발송 함수(기본 parse_mode='Markdown')가 shim 을 거쳐 notifier 가
  보내는 길 전체로 본다(묶음 H 가 ET `send` 를 `legacy_send(..., parse_mode=parse_mode)` 로 바꾼다 —
  `et_send` 가 그 모양). 평문(None)이 기본값(HTML)으로 바뀌지 않는 것이 핵심이다.
- `SendDocumentTest` 2개(test_send_files.py): 없는 파일·자격증명 없음.

새로: 429 retry_after 재시도·초과, 오류 문구에 토큰 없음, 사진 묶음·캡션, 이어 보내기, 토픽 thread.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from kbj.data.limits import load_limits
from kbj.data.ratelimit import LocalRateLimiter
from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig
from kbj.services.notifier.service import NotifierService
from kbj.services.notifier.store import MemoryNotifyLogStore
from kbj.services.notifier.telegram_api import Attachment, SendResult, TelegramApi
from tests.fakes.clock import FakeClock
from tests.unit.notifier.conftest import (
    CHAT,
    FAKE_TOKEN,
    T0,
    FakeTelegram,
    make_settings,
    refused,
)


def api_of(tg: FakeTelegram, token: str | None = "t", **kw: object) -> TelegramApi:  # noqa: S107
    return TelegramApi(
        SecretStr(token) if token is not None else None,
        transport=tg.transport,
        **kw,  # pyright: ignore[reportArgumentType]
    )


# ── 승격: ET TestSend — 발송 실패가 파이프라인을 멈추면 안 된다. 예외 대신 (False, 사유) ──────────


def test_send_without_token(tg: FakeTelegram) -> None:
    r = api_of(tg, token=None).send_message(None, "본문")
    assert not r.ok
    assert "TELEGRAM_BOT_TOKEN" in r.reason


def test_send_without_chat_id(tg: FakeTelegram) -> None:
    r = api_of(tg, token="1234:abcd").send_message(None, "본문")
    assert not r.ok
    assert "TELEGRAM_CHAT_ID" in r.reason


def test_send_with_empty_body(tg: FakeTelegram) -> None:
    r = api_of(tg, token="1234:abcd").send_message("-100", "   ")
    assert not r.ok
    assert "본문" in r.reason


def test_check_without_token(tg: FakeTelegram) -> None:
    ok, why = api_of(tg, token=None).check(None)
    assert not ok
    assert "TELEGRAM_BOT_TOKEN" in why


def test_check_reports_missing_chat_id_as_failure(tg: FakeTelegram) -> None:
    ok, why = api_of(tg, token="").check(None)
    assert not ok
    assert isinstance(why, str)


# ── 승격: ET TestCheckDestination — 봇이 살아 있는 것과 그 방에 보낼 수 있는 것은 다르다 ──────────
# 봇이 방에서 쫓겨났거나 chat_id 가 틀리면 getMe 는 통과하고 발송만 실패한다. 18시에 조용히
# 실패하지 않으려면 점검에서 목적지까지 봐야 한다.


def _serve(me: int = 200, chat: int = 200) -> FakeTelegram:
    tg = FakeTelegram(me="board_bot", chat_title="리서치방")
    if me != 200:
        tg.script["getMe"] = [refused(me, "Unauthorized")]
    if chat != 200:
        tg.script["getChat"] = [refused(chat, "Bad Request: chat not found")]
    return tg


def test_봇과_방을_모두_확인한다() -> None:
    tg = _serve()
    ok, why = api_of(tg).check("1")
    assert ok
    assert "board_bot" in why
    assert "리서치방" in why
    assert tg.methods() == ["getMe", "getChat"]


def test_방이_없으면_실패다() -> None:
    tg = _serve(chat=400)
    ok, why = api_of(tg).check("1")
    assert not ok
    assert "대화방" in why


def test_메시지를_보내지_않는다() -> None:
    """점검이 발송으로 새면 안 된다."""
    tg = _serve()
    api_of(tg).check("1")
    assert "sendMessage" not in tg.methods()


# ── 승격: ET TestPlainTextMode — 미국장 브리프는 평문으로 보낸다 ──────────────────────────────
# 브리프 본문에는 `**유니버스를 …**` 와 `—` 가 섞여 있다. Markdown 으로 보내면 텔레그램이 '엔티티가
# 안 닫혔다' 며 400 으로 거절한다. 국장 리포트는 우리가 서식을 붙여 만들므로 기본값은 Markdown
# 그대로여야 한다.


def _capture(
    config: NotifyConfig, redis: object, clock: FakeClock, **kw: object
) -> tuple[bool, dict[str, object]]:
    """ET 보드 `send` 가 shim 으로 바뀐 모양(묶음 H) → notifier 가 보낸 sendMessage 본문."""
    tg = FakeTelegram()
    settings = make_settings(notify_enabled=True)
    outbox = Outbox(redis, now=clock)  # pyright: ignore[reportArgumentType]
    client = NotifyClient(config, True, outbox=outbox, now=clock)

    def et_send(
        text: str,
        token: str | None = None,
        chat_id: str | None = None,
        silent: bool = False,
        parse_mode: str | None = "Markdown",
    ) -> tuple[bool, str]:
        return client.legacy_send(
            text, source="et.board", parse_mode=parse_mode, kind="board.rankings", silent=silent
        )

    ok, _why = et_send("본문 **굵게** — 대시", token="t", chat_id="c", **kw)  # pyright: ignore[reportArgumentType]
    svc = NotifierService(
        outbox,
        TelegramApi(settings.telegram_bot_token, transport=tg.transport),
        config,
        MemoryNotifyLogStore(),
        settings,
        now=clock,
    )
    svc.drain_once()
    sent = tg.sent()
    assert len(sent) == 1
    return ok, sent[0]


def test_plain_mode_sends_no_parse_mode(
    config: NotifyConfig, redis: object, clock: FakeClock
) -> None:
    ok, sent = _capture(config, redis, clock, parse_mode=None)
    assert ok
    assert "parse_mode" not in sent


def test_default_is_still_markdown(config: NotifyConfig, redis: object, clock: FakeClock) -> None:
    ok, sent = _capture(config, redis, clock)
    assert ok
    assert sent.get("parse_mode") == "Markdown"


# ── 승격: ET test_send_files.SendDocumentTest ─────────────────────────────────────────────


def test_missing_file_is_an_error_not_silence(tg: FakeTelegram) -> None:
    r = api_of(tg, token="t").send_document("c", "/없는/파일.html")
    assert not r.ok
    assert "없다" in r.reason


def test_missing_creds_named(tg: FakeTelegram) -> None:
    r = api_of(tg, token=None).send_document(None, __file__)
    # 자격증명이 환경에 있으면 이 시험은 성립하지 않는다 — 건너뛴다.
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        pytest.skip("실자격증명이 있는 환경")
    assert not r.ok
    assert "TELEGRAM_BOT_TOKEN" in r.reason


# ── 새로 ─────────────────────────────────────────────────────────────────────────────────


def test_send_message_form_and_thread(tg: FakeTelegram) -> None:
    r = api_of(tg).send_message(CHAT, "<b>안녕</b>", thread_id=42, silent=True)
    assert r.ok and r.message_ids == (1001,) and r.parts_sent == 1
    sent = tg.sent()[0]
    assert sent["chat_id"] == CHAT
    assert sent["message_thread_id"] == "42"
    assert sent["parse_mode"] == "HTML"  # 기본 HTML(SD·bok·ETF)
    assert sent["disable_notification"] == "true"
    assert sent["disable_web_page_preview"] == "true"


def test_429_waits_retry_after_then_succeeds(tg: FakeTelegram) -> None:
    """bok send-telegram.js:34 — 429 는 retry_after + 1초 쉬고 다시."""
    tg.script["sendMessage"] = [refused(429, "Too Many Requests: retry after 3", retry_after=3)]
    slept: list[float] = []
    cfg = load_limits(settings=make_settings()).source("telegram").rate_config()
    lim = LocalRateLimiter(cfg, FakeClock(T0))
    r = api_of(tg, sleep=slept.append, limiter=lim).send_message(CHAT, "x")
    assert r.ok
    assert slept == [4.0]
    assert tg.methods() == ["sendMessage", "sendMessage"]
    assert lim.current_rate() < 25.0  # 한도초과 신호를 리미터에 알렸다


def test_429_gives_up_after_four_retries_as_transient(tg: FakeTelegram) -> None:
    tg.script["sendMessage"] = [refused(429, "Too Many Requests", retry_after=1)] * 5
    slept: list[float] = []
    r = api_of(tg, sleep=slept.append).send_message(CHAT, "x")
    assert not r.ok and r.transient and r.status == 429
    assert len(slept) == 4 and len(tg.calls) == 5
    assert "429" in r.reason


def test_error_reason_never_carries_the_token(tg: FakeTelegram) -> None:
    """연결 예외 문구에는 URL 이 통째로 실린다 — 텔레그램은 토큰을 URL 경로에 담는다."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"연결 실패: {request.url}", request=request)

    tg.script["sendMessage"] = [boom]
    r = api_of(tg, token=FAKE_TOKEN).send_message(CHAT, "x")
    assert not r.ok and r.transient
    assert FAKE_TOKEN not in r.reason
    assert FAKE_TOKEN.split(":")[1] not in r.reason
    assert "sendMessage" in r.reason


def test_error_reason_never_carries_the_chat_id(tg: FakeTelegram) -> None:
    """getChat 은 GET 이라 chat id 가 주소에 실린다 — 연결 예외 문구로 새지 않게 가린다."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"연결 실패: {request.url}", request=request)

    tg.script["getChat"] = [boom]
    ok, why = api_of(tg, token=FAKE_TOKEN).check(CHAT)
    assert not ok and "getChat" in why
    assert CHAT not in why and FAKE_TOKEN not in why


def test_limiter_failure_on_429_is_a_transient_result(tg: FakeTelegram) -> None:
    class BrokenOn429:
        def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
            pass

        def on_rate_limited(self) -> float:
            raise ConnectionError("redis down")

    tg.script["sendMessage"] = [refused(429, "Too Many Requests", retry_after=1)]
    r = api_of(tg, limiter=BrokenOn429()).send_message(CHAT, "x")
    assert not r.ok and r.transient and r.status == 429 and "레이트리미터" in r.reason


def test_refusal_reason_is_telegrams_own_words(tg: FakeTelegram) -> None:
    tg.script["sendMessage"] = [refused(403, "Forbidden: bot was kicked from the supergroup chat")]
    r = api_of(tg).send_message(CHAT, "x")
    assert not r.ok and not r.transient and r.status == 403
    assert "bot was kicked" in r.reason


def test_server_error_is_transient(tg: FakeTelegram) -> None:
    tg.script["sendMessage"] = [httpx.Response(502, text="Bad Gateway")]
    r = api_of(tg).send_message(CHAT, "x")
    assert not r.ok and r.transient and r.status == 502


def test_overlong_message_is_refused_before_sending(tg: FakeTelegram) -> None:
    r = api_of(tg).send_message(CHAT, "가" * 4097)
    assert not r.ok and "4096" in r.reason
    assert tg.calls == []


def test_send_text_stops_at_failure_and_resumes(tg: FakeTelegram) -> None:
    api = api_of(tg)
    tg.script["sendMessage"] = [
        httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
        httpx.Response(502, text="Bad Gateway"),
    ]
    r = api.send_text(CHAT, ["하나", "둘", "셋"])
    assert not r.ok and r.parts_sent == 1 and r.transient
    assert "2/3" in r.reason
    r2 = api.send_text(CHAT, ["하나", "둘", "셋"], start=r.parts_sent)
    assert r2.ok and r2.parts_sent == 3
    assert [d["text"] for d in tg.sent()] == ["하나", "둘", "둘", "셋"]


def test_send_document_multipart_and_caption_cap(tg: FakeTelegram, tmp_path: Path) -> None:
    p = tmp_path / "신고가보드-2026-10-06.html"
    p.write_bytes(b"<title>board</title>")
    r = api_of(tg).send_document(CHAT, p, "캡션" * 600, thread_id=7)
    assert r.ok and r.reason == p.name
    d = tg.sent("sendDocument")[0]
    assert d["document"] == (p.name, b"<title>board</title>")
    assert len(d["caption"]) == 1024 and d["caption"].endswith("…")
    assert d["message_thread_id"] == "7"


def test_send_media_group_ten_per_group_caption_on_first(tg: FakeTelegram) -> None:
    photos = [Attachment(f"c{i}.png", bytes([i])) for i in range(12)]
    r = api_of(tg).send_media_group(CHAT, photos, "수급 차트")
    assert r.ok and r.parts_sent == 2 and len(r.message_ids) == 12
    groups = tg.sent("sendMediaGroup")
    medias = [json.loads(g["media"]) for g in groups]
    assert [len(m) for m in medias] == [10, 2]
    assert medias[0][0]["caption"] == "수급 차트"
    assert all("caption" not in it for it in medias[0][1:] + medias[1])
    assert groups[0]["photo0"] == ("c0.png", b"\x00")


def test_send_media_group_missing_photo_is_named(tg: FakeTelegram, tmp_path: Path) -> None:
    there = tmp_path / "a.png"
    there.write_bytes(b"x")
    r = api_of(tg).send_media_group(CHAT, [there, tmp_path / "없는.png"])
    assert not r.ok and "없는.png" in r.reason
    assert tg.calls == []


def test_set_webhook_sends_secret_and_allowed_updates(tg: FakeTelegram) -> None:
    ok = api_of(tg).set_webhook(
        "https://kbj.example/telegram/webhook",
        SecretStr("s3cret-value"),
        ["message", "edited_message", "channel_post"],
    )
    assert ok
    d = tg.sent("setWebhook")[0]
    assert d["secret_token"] == "s3cret-value"
    assert json.loads(d["allowed_updates"]) == ["message", "edited_message", "channel_post"]
    assert d["drop_pending_updates"] == "false"


def test_set_webhook_error_hides_the_secret(tg: FakeTelegram) -> None:
    from kbj.services.notifier.telegram_api import TelegramApiError

    tg.script["setWebhook"] = [refused(400, "Bad Request: secret s3cret-value is invalid")]
    with pytest.raises(TelegramApiError) as ei:
        api_of(tg).set_webhook("https://x/telegram/webhook", SecretStr("s3cret-value"), ["message"])
    assert "s3cret-value" not in str(ei.value)


def test_limiter_failure_is_a_transient_result_not_an_exception(tg: FakeTelegram) -> None:
    class Broken:
        def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
            raise TimeoutError("리미터 대기 초과")

        def on_rate_limited(self) -> float:
            return 0.0

    r = api_of(tg, limiter=Broken()).send_message(CHAT, "x")
    assert not r.ok and r.transient and "레이트리미터" in r.reason
    assert tg.calls == []


def test_per_chat_limiter_is_asked_with_the_chat(tg: FakeTelegram) -> None:
    asked: list[str] = []

    class Rec:
        def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
            pass

        def on_rate_limited(self) -> float:
            return 0.0

    def per_chat(chat: str) -> Rec:
        asked.append(chat)
        return Rec()

    factory: Callable[[str], Rec] = per_chat
    api_of(tg, chat_limiter=factory).send_message(CHAT, "x")
    assert asked == [CHAT]


def test_send_result_is_a_value() -> None:
    r = SendResult(True, "1건 발송", (1,))
    assert r.ok and r.message_ids == (1,) and not r.transient
