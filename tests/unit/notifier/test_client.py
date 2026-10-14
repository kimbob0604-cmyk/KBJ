"""알림 넣기(kbj.services.notifier.client) — 설계 §1.6·§5.5.

- 발송 꺼짐(기본): `notify()` → `NotifyTicket(ok=True, reason="suppressed")` — 넣기는 그대로 한다
  (notifier 가 기록)
- Redis 가 없으면 `ok=False` + 사유 — 조용히 버리지 않는다

P9(LEGACY): legacy 발송 shim(`legacy_send*`·`legacy_kinds`)은 ET·SD 를 지우며 같이 지웠다
(`test_client_shim.py` → 이 파일). shim 전용 시험 6개는 폐지, shim 으로 공통 경로를 보던 시험은
같은 단언을 `notify*` 로 옮겼다 — 목록·사유는 `docs/p9_design.md` 'LEGACY 구현 기록'.
"""

from __future__ import annotations

from pathlib import Path

import fakeredis
import pytest

from kbj.services.notifier import client as client_mod
from kbj.services.notifier.client import (
    NotifyClient,
    notify,
    set_default_client,
    webhook_status_from,
)
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig
from kbj.services.runtime.heartbeat import Heartbeat
from kbj.store.redis_keys import NOTIFY_WEBHOOK_INFO, heartbeat_key
from tests.fakes.clock import FakeClock


def _client(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, *, enabled: bool
) -> NotifyClient:
    return NotifyClient(config, enabled, outbox=Outbox(redis, now=clock), now=clock)


def _queued(redis: fakeredis.FakeRedis, clock: FakeClock) -> list[str]:
    return [e.message.kind for e in Outbox(redis, now=clock).read("t", 50) if e.message]


def test_notify_disabled_is_ok_suppressed(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=False)
    t = c.notify("<b>아침</b>", kind="brief.morning", source="job.brief.morning")
    assert t.ok and t.reason == "suppressed" and not t.duplicate and t.id


def test_notify_duplicate_same_day(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    first = c.notify("하나", kind="brief.morning", source="job")
    second = c.notify("둘", kind="brief.morning", source="job")  # U2 — 하루 1회
    assert first.ok and not first.duplicate
    assert second.ok and second.duplicate and second.reason == "duplicate"


def test_no_redis_is_a_reason_not_silence(config: NotifyConfig, clock: FakeClock) -> None:
    c = NotifyClient(config, True, now=clock)  # 대기열 없음(KBJ_REDIS_URL 없음)
    t = c.notify("본문", kind="brief.morning", source="job")
    assert not t.ok and "보내지 않았다" in t.reason and "REDIS" in t.reason.upper()


def test_redis_down_is_a_reason(config: NotifyConfig, clock: FakeClock) -> None:
    server = fakeredis.FakeServer()
    c = NotifyClient(config, True, outbox=Outbox(fakeredis.FakeRedis(server=server), now=clock))
    server.connected = False
    t = c.notify("본문", kind="brief.morning", source="job")
    assert not t.ok and "Redis" in t.reason


def test_unknown_explicit_kind_is_refused(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    t = c.notify("x", kind="board.nope", source="s")
    assert not t.ok and "board.nope" in t.reason


def test_empty_body_is_refused(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    assert not c.notify("  ", kind="brief.morning", source="s").ok
    assert not c.notify("", kind="brief.morning", source="s").ok


def test_missing_document_is_named(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    c = _client(config, redis, clock, enabled=True)
    t = c.notify_document(tmp_path / "없는.xlsx", kind="board.files", source="job")
    assert not t.ok and "없다" in t.reason and "없는.xlsx" in t.reason


def test_document_and_media_are_queued(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    c = _client(config, redis, clock, enabled=True)
    doc = tmp_path / "rankings.xlsx"
    doc.write_bytes(b"xlsx")
    pngs = [tmp_path / "a.png", tmp_path / "b.png"]
    for p in pngs:
        p.write_bytes(b"png")
    assert c.notify_document(doc, "엑셀", kind="board.files", source="job").ok
    assert c.notify_media(pngs, "차트", kind="flows.report", source="job").ok
    assert _queued(redis, clock) == ["board.files", "flows.report"]


def test_attachment_cap(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    small = config.model_copy(
        update={"delivery": config.delivery.model_copy(update={"max_attachment_bytes": 3})}
    )
    c = _client(small, redis, clock, enabled=True)
    p = tmp_path / "big.html"
    p.write_bytes(b"1234")
    t = c.notify_document(p, kind="board.files", source="s")
    assert not t.ok and "상한" in t.reason


def test_parse_mode_none_stays_plain(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    c.notify("평문", kind="board.us", source="job", parse_mode=None)
    c.notify("정책", kind="board.rankings", source="s")  # parse_mode 생략 → 정책(Markdown)
    msgs = [e.message for e in Outbox(redis, now=clock).read("t", 5) if e.message]
    assert [m.parse_mode for m in msgs] == [None, "Markdown"]


def test_module_level_notify_uses_the_default_client(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    set_default_client(_client(config, redis, clock, enabled=True))
    try:
        assert notify("마감", kind="brief.closing", source="job").ok
        assert notify("x", kind="ops.universe", source="job").ok
        assert _queued(redis, clock) == ["brief.closing", "ops.universe"]
    finally:
        set_default_client(None)


def test_module_level_notify_reports_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> NotifyClient:
        raise FileNotFoundError("config/notify.yaml")

    set_default_client(None)
    monkeypatch.setattr(client_mod, "_build_default", broken)
    t = notify("x", kind="brief.morning", source="s")
    assert not t.ok and "설정" in t.reason


# ── webhook_status ───────────────────────────────────────────────────────────────────────


def test_webhook_status_reads_notifier_summary(
    redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    st = webhook_status_from(redis, clock.now())
    assert not st["ok"] and "점검" in st["reason"]
    hb = Heartbeat(service="notifier", at=clock.now(), pid=1, status={"enabled": False})
    redis.set(heartbeat_key("notifier"), hb.model_dump_json())
    redis.set(NOTIFY_WEBHOOK_INFO, '{"url_set": true, "pending_update_count": 2, "error": null}')
    st = webhook_status_from(redis, clock.now())
    assert st["ok"] and st["pending_update_count"] == 2 and st["notify_enabled"] is False
    redis.set(NOTIFY_WEBHOOK_INFO, '{"url_set": false, "error": null}')
    assert "setup-webhook" in webhook_status_from(redis, clock.now())["reason"]
    redis.set(
        NOTIFY_WEBHOOK_INFO,
        '{"checked": false, "url_set": null, "error": null, "note": "발송 꺼짐 — 점검 생략"}',
    )
    st = webhook_status_from(redis, clock.now())
    assert not st["ok"] and "점검 안 함" in st["reason"] and "setup-webhook" not in st["reason"]
