"""notifier 서비스(kbj.services.notifier.service) — 소비·발송·기록·재시도(설계 §1.6·§5.4·§5.5).

꺼짐 → `suppressed`(텔레그램 0회), 켜짐 → 가짜 텔레그램 1회, 실패 → 재시도·`failed`, 2차 중복,
캐치업 시한, 토픽 thread, 조각 상한, 서식 오류 평문 재발송, 기록 저장소 장애(보내지 않고 미룸 /
보낸 뒤엔 다시 보내지 않음), 웹훅 점검.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest

from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig, Topic
from kbj.services.notifier.service import NotifierService, run
from kbj.services.notifier.store import (
    MemoryNotifyLogStore,
    NotifyLogEntry,
    StoredBody,
)
from kbj.services.notifier.telegram_api import TelegramApi
from kbj.services.runtime import MemoryHealthSink
from kbj.store.redis_keys import NOTIFY_WEBHOOK_INFO, notify_dedup_key
from tests.fakes.clock import FakeClock
from tests.unit.notifier.conftest import CHAT, KST, FakeTelegram, make_settings, ok, refused


class Rig:
    def __init__(
        self,
        config: NotifyConfig,
        *,
        enabled: bool,
        tg: FakeTelegram | None = None,
        store: Any = None,
        clock: FakeClock | None = None,
        **settings_kw: Any,
    ) -> None:
        self.clock = clock or FakeClock(datetime(2026, 10, 6, 16, 0, tzinfo=KST))
        self.redis = fakeredis.FakeRedis()
        self.tg = tg or FakeTelegram()
        self.store = store if store is not None else MemoryNotifyLogStore()
        self.health = MemoryHealthSink()
        self.settings = make_settings(notify_enabled=enabled, **settings_kw)
        self.outbox = Outbox(self.redis, now=self.clock)
        self.client = NotifyClient(config, enabled, outbox=self.outbox, now=self.clock)
        self.slept: list[float] = []
        self.svc = NotifierService(
            self.outbox,
            TelegramApi(
                self.settings.telegram_bot_token,
                transport=self.tg.transport,
                sleep=self.slept.append,
            ),
            config,
            self.store,
            self.settings,
            now=self.clock,
            health=self.health,
            redis=self.redis,
        )


@pytest.fixture
def on(config: NotifyConfig) -> Rig:
    return Rig(config, enabled=True)


@pytest.fixture
def off(config: NotifyConfig) -> Rig:
    return Rig(config, enabled=False)


# ── 꺼짐(§5.5) ─────────────────────────────────────────────────────────────────────────


def test_disabled_records_suppressed_and_calls_nothing(off: Rig) -> None:
    off.client.notify("<b>마감</b>", kind="brief.closing", source="job.brief.closing")
    assert off.svc.drain_once() == 1
    (row,) = off.store.rows
    assert row.status == "suppressed" and row.reason == "KBJ_NOTIFY_ENABLED=false"
    assert row.kind == "brief.closing" and row.topic == "market" and row.parts == 1
    assert row.dedup_key == "brief.closing:20261006" and row.as_of == date(2026, 10, 6)
    assert off.store.bodies[row.dedup_key].body == "<b>마감</b>"  # 본문은 prv_alerts 쪽
    assert off.tg.calls == []
    assert off.outbox.backlog() == 0


# ── 켜짐 ──────────────────────────────────────────────────────────────────────────────


def test_enabled_sends_once_and_records(on: Rig) -> None:
    on.client.notify("<b>마감</b>", kind="brief.closing", source="job.brief.closing")
    on.client.notify("<b>또</b>", kind="brief.closing", source="job.brief.closing")  # U2
    on.svc.drain_once()
    assert on.tg.methods() == ["sendMessage"]
    sent = on.tg.sent()[0]
    assert sent["chat_id"] == CHAT and sent["parse_mode"] == "HTML"
    assert "message_thread_id" not in sent  # 토픽 id 미설정 → 일반 대화 + 경고
    assert on.health.kinds() == ["notify_topic_missing"]
    rows = on.store.rows
    assert [r.status for r in rows] == ["sent", "duplicate"]
    assert rows[0].message_ids == (1001,) and rows[0].sent_at is not None
    assert rows[1].dedup_key is not None and rows[1].dedup_key.startswith("brief.closing:20261006#")


def test_topic_thread_from_config(config: NotifyConfig) -> None:
    cfg = config.model_copy(update={"topics": {**config.topics, Topic.ALERT: 77}})
    rig = Rig(cfg, enabled=True)
    rig.client.notify("규칙", kind="alert.rule", source="s", subject="005930")
    rig.svc.drain_once()
    assert rig.tg.sent()[0]["message_thread_id"] == "77"
    assert rig.health.kinds() == []


def test_long_body_is_split_and_numbered(on: Rig) -> None:
    body = "\n".join("종목 줄 " + "가" * 60 for _ in range(200))
    on.client.legacy_send(body, source="sd.send_telegram_long", numbered=True, kind="ops.universe")
    on.svc.drain_once()
    texts = [d["text"] for d in on.tg.sent()]
    n = len(texts)
    assert n > 1 and all(len(t) <= 4096 for t in texts)
    assert texts[0].startswith(f"<i>(1/{n})</i>")
    assert on.store.rows[0].parts == n and len(on.store.rows[0].message_ids) == n


def test_too_many_parts_fails_without_sending(on: Rig) -> None:
    body = "\n".join("가" * 100 for _ in range(500))  # 약 12조각 > 상한 10
    on.client.notify(body, kind="ops.universe", source="s")
    on.svc.drain_once()
    assert on.tg.calls == []
    (row,) = on.store.rows
    assert row.status == "failed" and "max_parts" in row.reason


def test_document_and_media_are_sent(on: Rig, tmp_path: Path) -> None:
    doc = tmp_path / "rankings-2026-10-06.xlsx"
    doc.write_bytes(b"xlsx")
    pngs = []
    for i in range(3):
        p = tmp_path / f"c{i}.png"
        p.write_bytes(b"png")
        pngs.append(p)
    on.client.notify_document(doc, "랭킹 엑셀", kind="board.files", source="et.board.files")
    on.client.notify_media(pngs, "수급", kind="flows.report", source="et.flow")
    on.svc.drain_once()
    assert on.tg.methods() == ["sendDocument", "sendMediaGroup"]
    assert on.tg.sent("sendDocument")[0]["document"] == (doc.name, b"xlsx")
    assert [r.status for r in on.store.rows] == ["sent", "sent"]
    assert on.store.rows[1].message_ids == (1002, 1003, 1004)


def test_transient_failure_retries_then_succeeds_without_resending_sent_parts(on: Rig) -> None:
    body = "\n".join("가" * 100 for _ in range(60))  # 2조각
    on.tg.script["sendMessage"] = [ok({"message_id": 1}), httpx.Response(502, text="bad")]
    on.client.notify(body, kind="ops.universe", source="s")
    on.svc.drain_once()
    row = on.store.rows[0]
    assert row.status == "queued" and "재시도 1/3" in row.reason
    assert on.svc.drain_once() == 0  # 아직 때가 아니다
    on.clock.advance(10)
    on.svc.drain_once()
    texts = [d["text"] for d in on.tg.sent()]
    assert len(texts) == 3 and texts[1] == texts[2]  # 실패한 둘째만 다시
    assert on.store.rows[0].status == "sent" and on.outbox.backlog() == 0


def test_resume_point_is_absolute_across_two_partial_failures(on: Rig) -> None:
    """두 번 연달아 중간에서 끊겨도 이어 보낼 자리를 잃지 않는다 — `parts_sent` 는 앞에서부터 센
    수(절대 위치)라 더하면 셋째 조각을 건너뛰고 '보냄' 으로 적는다(검증에서 찾은 회귀)."""
    body = "\n".join(f"{i:03d} " + "가" * 100 for i in range(100))  # 3조각(조각마다 다른 본문)
    on.tg.script["sendMessage"] = [
        ok({"message_id": 1}),
        httpx.Response(502, text="bad"),  # 1회차: 둘째에서 끊김
        ok({"message_id": 2}),
        httpx.Response(502, text="bad"),  # 2회차: 셋째에서 끊김
    ]
    on.client.notify(body, kind="ops.universe", source="s")
    on.svc.drain_once()
    on.clock.advance(10)
    on.svc.drain_once()
    on.clock.advance(20)
    on.svc.drain_once()
    texts = [d["text"] for d in on.tg.sent()]
    assert len(texts) == 5
    assert texts[1] == texts[2] and texts[3] == texts[4]  # 실패한 조각만 다시
    assert len(set(texts)) == 3  # 세 조각 모두 갔다
    (row,) = on.store.rows
    assert row.status == "sent" and row.parts == 3 and len(row.message_ids) == 3


def test_ack_failure_after_sending_does_not_resend(on: Rig, tmp_path: Path) -> None:
    """보낸 뒤 대기열 확인(XACK)이 Redis 장애로 실패해도, 다시 읽은 항목을 또 보내지 않는다
    (첨부는 이어 보낼 위치가 없어 특히 — 검증에서 찾은 회귀)."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    doc = tmp_path / "board.html"
    doc.write_bytes(b"<b>board</b>")
    on.client.notify_document(doc, "보드", kind="board.files", source="et.board.files")
    real_ack = on.outbox.ack
    calls = {"n": 0}

    def flaky_ack(entry_id: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RedisConnectionError("redis blip")
        real_ack(entry_id)

    on.outbox.ack = flaky_ack  # type: ignore[method-assign]
    on.svc.drain_once()
    assert on.tg.methods() == ["sendDocument"] and on.outbox.backlog() == 1
    on.clock.advance(11)
    on.svc.drain_once()
    assert on.tg.methods() == ["sendDocument"]  # 다시 보내지 않았다
    assert on.outbox.backlog() == 0
    (row,) = on.store.rows
    assert row.status == "sent" and on.svc.health()["sent"] == 1


def test_transient_failures_exhaust_then_failed_and_gate_released(on: Rig) -> None:
    on.tg.script["sendMessage"] = [httpx.Response(503, text="x")] * 3
    t = on.client.notify("x", kind="brief.morning", source="job")
    for _ in range(3):
        on.svc.drain_once()
        on.clock.advance(400)
    (row,) = on.store.rows
    assert row.status == "failed" and "503" in row.reason
    assert not on.redis.exists(notify_dedup_key(t.dedup_key or ""))  # 다시 넣을 수 있다
    assert "notify_failed" in on.health.kinds()
    assert not on.client.notify("x", kind="brief.morning", source="job").duplicate


def test_permanent_failure_is_not_retried(on: Rig) -> None:
    on.tg.script["sendMessage"] = [refused(403, "Forbidden: bot was kicked")]
    on.client.notify("x", kind="ops.universe", source="s")
    on.svc.drain_once()
    assert len(on.tg.calls) == 1 and on.store.rows[0].status == "failed"
    assert on.outbox.backlog() == 0


def test_parse_error_is_resent_as_plain_text(on: Rig) -> None:
    on.tg.script["sendMessage"] = [refused(400, "Bad Request: can't parse entities: bad tag")]
    on.client.notify("<b>열림", kind="ops.universe", source="s")
    on.svc.drain_once()
    first, second = on.tg.sent()
    assert first["parse_mode"] == "HTML" and "parse_mode" not in second
    assert on.store.rows[0].status == "sent" and "평문" in on.store.rows[0].reason


def test_catch_up_deadline_blocks_a_late_closing(config: NotifyConfig) -> None:
    rig = Rig(config, enabled=True, clock=FakeClock(datetime(2026, 10, 6, 20, 31, tzinfo=KST)))
    rig.client.notify("늦은 마감", kind="brief.closing", source="job", as_of=date(2026, 10, 6))
    rig.svc.drain_once()
    assert rig.tg.calls == []
    assert rig.store.rows[0].status == "failed" and "캐치업" in rig.store.rows[0].reason


def test_second_layer_duplicate_after_gate_expired(on: Rig) -> None:
    on.client.notify("하나", kind="brief.morning", source="job")
    on.svc.drain_once()
    on.redis.delete(notify_dedup_key("brief.morning:20261006"))  # 문지기 키 만료(36시간 뒤 등)
    on.client.notify("하나 다시", kind="brief.morning", source="job")
    on.svc.drain_once()
    assert on.tg.methods() == ["sendMessage"]
    assert [r.status for r in on.store.rows] == ["sent", "duplicate"]
    assert "2차" in on.store.rows[1].reason


def test_reply_goes_to_the_asking_chat(on: Rig) -> None:
    on.client.notify(
        "답", kind="cmd.reply", source="notifier.commands", chat="555", thread_id=3, subject="u1"
    )
    on.svc.drain_once()
    sent = on.tg.sent()[0]
    assert sent["chat_id"] == "555" and sent["message_thread_id"] == "3"
    assert on.health.kinds() == []  # 응답은 토픽 경고 대상이 아니다


def test_missing_chat_id_fails_with_reason(config: NotifyConfig) -> None:
    rig = Rig(config, enabled=True, telegram_chat_id=None)
    rig.client.notify("x", kind="ops.universe", source="s")
    rig.svc.drain_once()
    assert rig.tg.calls == [] and "KBJ_TELEGRAM_CHAT_ID" in rig.store.rows[0].reason


def test_missing_token_is_critical(config: NotifyConfig) -> None:
    rig = Rig(config, enabled=True, telegram_bot_token=None)
    rig.client.notify("x", kind="ops.universe", source="s")
    rig.svc.drain_once()
    assert rig.store.rows[0].status == "failed"
    assert any(
        e.kind == "notify_token_missing" and e.severity == "critical" for e in rig.health.events
    )


# ── 기록 저장소 장애 ───────────────────────────────────────────────────────────────────


class FlakyStore(MemoryNotifyLogStore):
    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def begin(self, entry: NotifyLogEntry) -> bool:
        if self.down:
            raise ConnectionError("db down")
        return super().begin(entry)

    def finish(self, entry: NotifyLogEntry, body: StoredBody | None = None) -> None:
        if self.down:
            raise ConnectionError("db down")
        super().finish(entry, body)


def test_store_down_before_send_defers_without_sending(config: NotifyConfig) -> None:
    store = FlakyStore()
    rig = Rig(config, enabled=True, store=store)
    store.down = True
    rig.client.notify("x", kind="ops.universe", source="s")
    rig.svc.drain_once()
    assert rig.tg.calls == [] and rig.outbox.backlog() == 1
    assert "notify_store_failed" in rig.health.kinds()
    store.down = False
    rig.clock.advance(11)
    rig.svc.drain_once()
    assert rig.tg.methods() == ["sendMessage"] and store.rows[0].status == "sent"


def test_store_down_after_send_never_resends(config: NotifyConfig) -> None:
    store = FlakyStore()
    rig = Rig(config, enabled=True, store=store)
    rig.client.notify("x", kind="ops.universe", source="s")

    real_finish = MemoryNotifyLogStore.finish

    def finish_fails_once(entry: NotifyLogEntry, body: StoredBody | None = None) -> None:
        store.finish = lambda e, b=None: real_finish(store, e, b)  # type: ignore[method-assign]
        raise ConnectionError("db down")

    store.finish = finish_fails_once  # type: ignore[method-assign]
    rig.svc.drain_once()
    assert rig.tg.methods() == ["sendMessage"] and rig.outbox.backlog() == 0
    assert store.rows[0].status == "queued"  # begin 만 남았다
    rig.svc.drain_once()  # 미뤄 둔 기록을 쓴다
    assert rig.tg.methods() == ["sendMessage"]
    assert store.rows[0].status == "sent"


# ── 거절 기록·형식 오류 ────────────────────────────────────────────────────────────────


def test_cooldown_rejection_is_recorded(on: Rig) -> None:
    for _ in range(2):
        on.client.notify("같은 알림", kind="alert.rule", source="s", subject="005930")
    on.svc.drain_once()
    assert [r.status for r in on.store.rows] == ["sent", "cooldown"]
    on.clock.advance(3601)
    on.client.notify("같은 알림", kind="alert.rule", source="s", subject="005930")
    on.svc.drain_once()
    assert [r.status for r in on.store.rows] == ["sent", "cooldown", "sent"]
    assert on.store.rows[0].dedup_key != on.store.rows[2].dedup_key


def test_broken_outbox_entry_is_dropped_with_health(on: Rig) -> None:
    on.redis.xadd("notify:outbox", {"v": "1", "type": "message", "meta": "{깨짐"})
    assert on.svc.drain_once() == 1
    assert "notify_bad_entry" in on.health.kinds() and on.outbox.backlog() == 0


# ── 웹훅 점검·한 바퀴 ─────────────────────────────────────────────────────────────────


def test_webhook_check_writes_summary_without_the_url_path(on: Rig) -> None:
    on.tg.webhook = {
        "url": "https://kbj.example/telegram/webhook",
        "pending_update_count": 150,
        "last_error_message": "Wrong response",
    }
    info = on.svc.check_webhook()
    stored = json.loads(on.redis.get(NOTIFY_WEBHOOK_INFO) or b"{}")
    assert info["url_set"] and stored["host"] == "kbj.example"
    assert "telegram/webhook" not in json.dumps(stored)
    assert "webhook_pending" in on.health.kinds()


def test_webhook_missing_is_a_warning(on: Rig) -> None:
    on.svc.check_webhook()
    assert "webhook_missing" in on.health.kinds()


def test_step_runs_everything_and_rechecks_webhook_on_schedule(on: Rig) -> None:
    on.client.notify("x", kind="ops.universe", source="s")
    st = on.svc.step()
    assert st["outbox"] == 1 and st["backlog"] == 0
    assert on.tg.methods() == ["sendMessage", "getWebhookInfo"]
    on.svc.step()
    assert on.tg.methods().count("getWebhookInfo") == 1
    on.clock.advance(601)
    on.svc.step()
    assert on.tg.methods().count("getWebhookInfo") == 2
    assert on.svc.health()["sent"] == 1


def test_run_loop_stops(on: Rig) -> None:
    import threading

    stop = threading.Event()
    seen: list[dict[str, Any]] = []

    def on_step(st: dict[str, Any]) -> None:
        seen.append(st)
        stop.set()

    assert run(on.svc, stop, step_s=0.0, on_step=on_step) == 1 and len(seen) == 1


def test_naive_retry_clock_is_not_used(on: Rig) -> None:
    """재시도 시각은 주입한 시계로만 잰다(벽시계 아님)."""
    on.tg.script["sendMessage"] = [httpx.Response(502, text="x")]
    on.client.notify("x", kind="ops.universe", source="s")
    on.svc.drain_once()
    on.clock.advance(9)
    on.svc.drain_once()
    assert len(on.tg.calls) == 1
    on.clock.advance(timedelta(seconds=2).total_seconds())
    on.svc.drain_once()
    assert len(on.tg.calls) == 2
