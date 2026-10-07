"""발송 기록·인박스 저장소(kbj.services.notifier.store) — 메모리 구현 규칙, Postgres 쪽 SQL·인자.

Postgres 구현은 가짜 연결로 '어떤 SQL 을 어떤 인자로 부르는지'만 본다. 실제 표(0002·0005)에 대한
왕복은 Docker 통합 시험 몫이다(묶음 G·I — 요청에 적었다).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from kbj.services.notifier.store import (
    SQL_BEGIN,
    SQL_BODY,
    SQL_FINISH,
    SQL_INBOX_UPSERT,
    SQL_REJECTED,
    MemoryInboxStore,
    MemoryNotifyLogStore,
    NotifyLogEntry,
    PgInboxStore,
    PgNotifyLogStore,
    StoredBody,
    inbox_cutoff,
    rejected_key,
)
from tests.unit.notifier.conftest import KST

AT = datetime(2026, 10, 6, 7, 40, tzinfo=UTC)


def _entry(**kw: Any) -> NotifyLogEntry:
    base: dict[str, Any] = {
        "kind": "brief.closing",
        "topic": "market",
        "as_of": date(2026, 10, 6),
        "subject": None,
        "dedup_key": "brief.closing:20261006",
        "body_sha256": "a" * 64,
        "body_chars": 10,
        "parts": 1,
        "status": "queued",
        "reason": "",
        "source": "job.brief.closing",
        "requested_at": AT,
    }
    base.update(kw)
    return NotifyLogEntry(**base)


# ── 메모리 발송 기록 ───────────────────────────────────────────────────────────────────


def test_begin_refuses_a_key_already_sent_or_suppressed() -> None:
    s = MemoryNotifyLogStore()
    assert s.begin(_entry())
    assert s.begin(_entry())  # queued 는 다시 열 수 있다(재기동 뒤 같은 항목)
    s.finish(_entry(status="sent", sent_at=AT, message_ids=(5,)))
    assert not s.begin(_entry())
    s2 = MemoryNotifyLogStore()
    s2.finish(_entry(status="suppressed"))
    assert not s2.begin(_entry())


def test_failed_key_can_be_reopened() -> None:
    s = MemoryNotifyLogStore()
    s.finish(_entry(status="failed", reason="403"))
    assert s.begin(_entry(reason="다시"))
    assert s.rows[0].status == "queued" and len(s.rows) == 1


def test_rejections_need_their_own_unique_key_and_are_idempotent() -> None:
    s = MemoryNotifyLogStore()
    key = rejected_key("brief.closing:20261006", "duplicate", "1700000000000-0")
    assert key == "brief.closing:20261006#duplicate#1700000000000-0"
    s.record_rejected(_entry(status="duplicate", dedup_key=key))
    s.record_rejected(_entry(status="duplicate", dedup_key=key))  # 같은 거절을 두 번 처리
    assert len(s.rows) == 1
    with pytest.raises(ValueError):
        s.record_rejected(_entry(status="duplicate", dedup_key=None))


def test_entry_validation() -> None:
    with pytest.raises(ValueError):
        _entry(status="weird")
    with pytest.raises(ValueError):
        _entry(requested_at=datetime(2026, 10, 6))  # noqa: DTZ001


def test_bodies_are_kept_apart_from_the_log() -> None:
    s = MemoryNotifyLogStore()
    s.finish(_entry(status="sent"), StoredBody("brief.closing:20261006", "<b>본문</b>", "HTML", AT))
    assert "본문" not in repr(s.rows[0])
    assert s.bodies["brief.closing:20261006"].body == "<b>본문</b>"


# ── Postgres 발송 기록(가짜 연결) ─────────────────────────────────────────────────────


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.rowcount = 0

    def execute(self, query: str, params: Any = None) -> None:
        self.conn.executed.append((query, params))
        self.rowcount = self.conn.rowcount

    def fetchone(self) -> Any:
        return self.conn.fetch.pop(0) if self.conn.fetch else None

    def fetchall(self) -> list[Any]:
        return self.conn.rows

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *a: object) -> None:
        pass


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.fetch: list[Any] = []
        self.rows: list[Any] = []
        self.rowcount = 0
        self.transactions = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.transactions += 1
        yield


def test_pg_begin_uses_the_reopen_rule() -> None:
    conn = FakeConn()
    store = PgNotifyLogStore(conn)
    conn.fetch = [(1,)]
    assert store.begin(_entry(status="sent"))  # begin 은 늘 queued 로 넣는다
    q, p = conn.executed[0]
    assert q == SQL_BEGIN and p["status"] == "queued" and p["dedup_key"] == "brief.closing:20261006"
    assert "WHERE l.status IN ('queued', 'failed')" in q and "RETURNING" in q
    assert not store.begin(_entry())  # RETURNING 이 비면(이미 sent) 거절


def test_pg_finish_writes_log_and_body_in_one_transaction() -> None:
    conn = FakeConn()
    PgNotifyLogStore(conn).finish(
        _entry(status="sent", message_ids=(1, 2), sent_at=AT),
        StoredBody("brief.closing:20261006", "본문", "HTML", AT),
    )
    assert conn.transactions == 1
    (q1, p1), (q2, p2) = conn.executed
    assert q1 == SQL_FINISH and p1["message_ids"] == [1, 2] and p1["status"] == "sent"
    assert q2 == SQL_BODY and p2["body"] == "본문" and "prv_alerts.notify_message" in q2
    assert "ops.notify_log" in q1


def test_pg_rejected_insert_is_idempotent() -> None:
    conn = FakeConn()
    PgNotifyLogStore(conn).record_rejected(_entry(status="cooldown", dedup_key="k#cooldown#1-0"))
    q, p = conn.executed[0]
    assert q == SQL_REJECTED and q.endswith("ON CONFLICT (dedup_key) DO NOTHING")
    assert p["dedup_key"] == "k#cooldown#1-0"


def test_sql_takes_values_only_as_parameters() -> None:
    for q in (SQL_BEGIN, SQL_FINISH, SQL_REJECTED, SQL_BODY, SQL_INBOX_UPSERT):
        assert "%(" in q and "'" not in q.replace("'queued'", "").replace("'failed'", "")


# ── 인박스 ────────────────────────────────────────────────────────────────────────────


def _item(uid: int, when: datetime | None, chat: int = 1) -> dict[str, Any]:
    return {
        "update_id": uid,
        "chat_id": chat,
        "date": when.astimezone(KST).isoformat(timespec="seconds") if when else None,
        "text": "t",
        "urls": [],
        "x_ids": [],
        "author": None,
        "kind": "other",
        "text_via": "message",
    }


def test_memory_inbox_upsert_keeps_the_first_and_expires() -> None:
    s = MemoryInboxStore()
    assert s.upsert([_item(1, AT), _item(2, None)], AT) == [1, 2]
    assert s.upsert([_item(1, AT), _item(3, AT)], AT) == [3]
    assert s.expire(inbox_cutoff(AT + timedelta(days=15), 14)) == 2  # 날짜 없는 2 는 둔다
    assert [it["update_id"] for it, _ in s.items()] == [2] and s.count() == 1


def test_pg_inbox_upsert_and_items_round_trip() -> None:
    conn = FakeConn()
    store = PgInboxStore(conn)
    conn.fetch = [(1,), None]
    assert store.upsert([_item(1, AT), _item(1, AT)], AT) == [1]
    q, p = conn.executed[0]
    assert q == SQL_INBOX_UPSERT and p["date"] == AT and p["received_at"] == AT
    conn.rows = [(1, 7, AT, "t", ["u"], ["9"], "a", "x", None, AT)]
    ((item, rec),) = store.items(since=AT - timedelta(days=1))
    assert item["date"] == "2026-10-06T16:40:00+09:00" and item["chat_id"] == 7
    assert item["urls"] == ["u"] and item["text_via"] is None and rec == AT
    conn.rowcount = 3
    assert store.expire(AT) == 3


def test_inbox_cutoff_refuses_naive() -> None:
    with pytest.raises(ValueError):
        inbox_cutoff(datetime(2026, 10, 6), 14)  # noqa: DTZ001
