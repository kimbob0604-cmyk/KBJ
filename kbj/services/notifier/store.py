"""발송 기록·인박스 저장소(설계 §5.4·§5.8). 표는 묶음 G 의 0002_ops_core·0005_alerts_inbox.

- `ops.notify_log`(운영) — 발송 메타, 본문 없음: 종류·토픽·기준일·대상·중복 키(UNIQUE)·본문
  해시·글자 수·조각 수·상태·사유·메시지 id·부른 곳·시각.
- `prv_alerts.notify_message`(로그인·개인) — 본문(`dedup_key` PK → notify_log). 시세·포트폴리오가
  섞인다.
- `prv_alerts.tg_inbox`(로그인·개인) — 인박스 항목. ET `state/inbox.json` 항목 모양 + `received_at`.

- 상태: `queued`(보내는 중·재시도 대기)·`sent`·`failed`·`suppressed`(발송 꺼짐)·`duplicate`·
  `cooldown`.
- 2차 중복 방지: `begin` 은 같은 `dedup_key` 의 행이 없거나 `queued`·`failed` 일 때만 받는다 —
  이미 `sent`·`suppressed` 면 False(Redis 문지기 키가 만료된 뒤 다시 들어온 같은 메시지).
- 문지기에 걸린 요청(duplicate·cooldown)도 한 줄씩 남긴다. `dedup_key` 열이 `NOT NULL UNIQUE`
  (0002) 라 `<원래 키>#<상태>#<대기열 항목 id>` 로 적는다 — 같은 거절을 두 번 처리해도(재기동)
  한 줄,
  `LIKE '<원래 키>#%'` 로 원래 키의 거절을 모아 볼 수 있다(`rejected_key`).
- 메모리 구현(시험·시뮬레이션)과 Postgres 구현이 같은 규칙을 따른다. Postgres 오류는 그대로 올라간다
  (서비스가 그 항목을 보내지 않고 나중에 다시 — service.py).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

from kbj.core.time import KST

NotifyStatus = Literal["queued", "sent", "failed", "suppressed", "duplicate", "cooldown"]
NOTIFY_STATUSES: tuple[str, ...] = (
    "queued",
    "sent",
    "failed",
    "suppressed",
    "duplicate",
    "cooldown",
)
_REOPENABLE = ("queued", "failed")


@dataclass(frozen=True)
class NotifyLogEntry:
    """`ops.notify_log` 한 행(본문 없음)."""

    kind: str
    topic: str | None
    as_of: date
    subject: str | None
    dedup_key: str | None
    body_sha256: str
    body_chars: int
    parts: int
    status: NotifyStatus
    reason: str
    source: str
    requested_at: datetime
    message_ids: tuple[int, ...] = ()
    sent_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in NOTIFY_STATUSES:
            raise ValueError(f"모르는 상태 {self.status!r}")
        for ts in (self.requested_at, self.sent_at):
            if ts is not None and (ts.tzinfo is None or ts.utcoffset() is None):
                raise ValueError("naive datetime 금지")


@dataclass(frozen=True)
class StoredBody:
    """`prv_alerts.notify_message` 한 행."""

    dedup_key: str
    body: str
    parse_mode: str | None
    created_at: datetime


def rejected_key(key: str, status: str, entry_id: str) -> str:
    """거절 기록의 유일 키 — `<원래 키>#<상태>#<대기열 항목 id>`."""
    return f"{key}#{status}#{entry_id}"


class NotifyLogStore(Protocol):
    def begin(self, entry: NotifyLogEntry) -> bool: ...

    def finish(self, entry: NotifyLogEntry, body: StoredBody | None = None) -> None: ...

    def record_rejected(self, entry: NotifyLogEntry) -> None: ...


@dataclass
class MemoryNotifyLogStore:
    """메모리 발송 기록(시험·시뮬레이션). `rows` 는 넣은 순서."""

    rows: list[NotifyLogEntry] = field(default_factory=list[NotifyLogEntry])
    bodies: dict[str, StoredBody] = field(default_factory=dict[str, StoredBody])

    def _index(self, key: str) -> int | None:
        for i, r in enumerate(self.rows):
            if r.dedup_key == key:
                return i
        return None

    def begin(self, entry: NotifyLogEntry) -> bool:
        if entry.dedup_key is None:
            raise ValueError("begin 에는 dedup_key 가 있어야 한다")
        i = self._index(entry.dedup_key)
        if i is None:
            self.rows.append(replace(entry, status="queued"))
            return True
        if self.rows[i].status not in _REOPENABLE:
            return False
        self.rows[i] = replace(
            self.rows[i], status="queued", reason=entry.reason, requested_at=entry.requested_at
        )
        return True

    def finish(self, entry: NotifyLogEntry, body: StoredBody | None = None) -> None:
        if entry.dedup_key is None:
            raise ValueError("finish 에는 dedup_key 가 있어야 한다")
        i = self._index(entry.dedup_key)
        if i is None:
            self.rows.append(entry)
        else:
            self.rows[i] = entry
        if body is not None:
            self.bodies[body.dedup_key] = body

    def record_rejected(self, entry: NotifyLogEntry) -> None:
        if entry.dedup_key is None:
            raise ValueError("거절 기록에도 유일 키(rejected_key)가 있어야 한다")
        if self._index(entry.dedup_key) is None:  # 같은 거절을 두 번 처리해도 한 줄
            self.rows.append(entry)

    # 시험 편의
    def of(self, kind: str, status: str | None = None) -> list[NotifyLogEntry]:
        return [r for r in self.rows if r.kind == kind and (status is None or r.status == status)]


_LOG_COLS = (
    "kind",
    "topic",
    "as_of",
    "subject",
    "dedup_key",
    "body_sha256",
    "body_chars",
    "parts",
    "status",
    "reason",
    "message_ids",
    "source",
    "requested_at",
    "sent_at",
)
_LOG_INSERT = (  # 열 이름은 위 상수뿐 — 값은 모두 자리표시자(%(이름)s)로 넘긴다
    f"INSERT INTO ops.notify_log AS l ({', '.join(_LOG_COLS)}) "  # noqa: S608
    f"VALUES ({', '.join('%(' + c + ')s' for c in _LOG_COLS)})"
)
SQL_BEGIN = (
    _LOG_INSERT + " ON CONFLICT (dedup_key) DO UPDATE SET status = 'queued', "
    "reason = EXCLUDED.reason, requested_at = EXCLUDED.requested_at "
    "WHERE l.status IN ('queued', 'failed') RETURNING l.id"
)
SQL_FINISH = (
    _LOG_INSERT + " ON CONFLICT (dedup_key) DO UPDATE SET topic = EXCLUDED.topic, "
    "parts = EXCLUDED.parts, status = EXCLUDED.status, reason = EXCLUDED.reason, "
    "message_ids = EXCLUDED.message_ids, sent_at = EXCLUDED.sent_at"
)
SQL_REJECTED = _LOG_INSERT + " ON CONFLICT (dedup_key) DO NOTHING"
SQL_BODY = (
    "INSERT INTO prv_alerts.notify_message (dedup_key, body, parse_mode, created_at) "
    "VALUES (%(dedup_key)s, %(body)s, %(parse_mode)s, %(created_at)s) "
    "ON CONFLICT (dedup_key) DO UPDATE SET body = EXCLUDED.body, "
    "parse_mode = EXCLUDED.parse_mode, created_at = EXCLUDED.created_at"
)


def _log_params(e: NotifyLogEntry, *, dedup_key: str | None) -> dict[str, Any]:
    return {
        "kind": e.kind,
        "topic": e.topic,
        "as_of": e.as_of,
        "subject": e.subject,
        "dedup_key": dedup_key,
        "body_sha256": e.body_sha256,
        "body_chars": e.body_chars,
        "parts": e.parts,
        "status": e.status,
        "reason": e.reason,
        "message_ids": list(e.message_ids),
        "source": e.source,
        "requested_at": e.requested_at,
        "sent_at": e.sent_at,
    }


class _Cursor(Protocol):
    def execute(self, query: Any, params: Any = ..., /) -> Any: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...

    @property
    def rowcount(self) -> int: ...

    def __enter__(self) -> _Cursor: ...

    def __exit__(self, *a: object) -> None: ...


class _Conn(Protocol):
    """psycopg.Connection 의 쓰는 부분만(시험은 가짜 연결을 넣는다)."""

    def cursor(self) -> Any: ...

    def transaction(self) -> Any: ...


class PgNotifyLogStore:
    """Postgres 발송 기록. 연결은 autocommit 이어야 한다(행마다 `transaction()` 으로 묶는다)."""

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def begin(self, entry: NotifyLogEntry) -> bool:
        if entry.dedup_key is None:
            raise ValueError("begin 에는 dedup_key 가 있어야 한다")
        params = _log_params(replace(entry, status="queued"), dedup_key=entry.dedup_key)
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(SQL_BEGIN, params)
            return cur.fetchone() is not None

    def finish(self, entry: NotifyLogEntry, body: StoredBody | None = None) -> None:
        if entry.dedup_key is None:
            raise ValueError("finish 에는 dedup_key 가 있어야 한다")
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(SQL_FINISH, _log_params(entry, dedup_key=entry.dedup_key))
            if body is not None:
                cur.execute(
                    SQL_BODY,
                    {
                        "dedup_key": body.dedup_key,
                        "body": body.body,
                        "parse_mode": body.parse_mode,
                        "created_at": body.created_at,
                    },
                )

    def record_rejected(self, entry: NotifyLogEntry) -> None:
        if entry.dedup_key is None:
            raise ValueError("거절 기록에도 유일 키(rejected_key)가 있어야 한다")
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(SQL_REJECTED, _log_params(entry, dedup_key=entry.dedup_key))


# ── 인박스 ──────────────────────────────────────────────────────────────────────────────────

InboxItem = dict[
    str, Any
]  # ET 항목 dict(update_id·chat_id·date·text·urls·x_ids·author·kind·text_via)


class InboxStore(Protocol):
    def upsert(self, items: Sequence[InboxItem], received_at: datetime) -> list[int]:
        """새로 들어간 항목의 update_id(이미 있던 update_id 는 먼저 것을 둔다 — ET `merge`)."""
        ...

    def expire(self, cutoff: datetime) -> int:
        """date 가 cutoff 보다 이른 항목을 지운다. 지운 수."""
        ...

    def items(self, since: datetime | None = None) -> list[tuple[InboxItem, datetime]]:
        """(항목, 받은 시각) — update_id 순."""
        ...

    def count(self) -> int: ...


def _item_dt(item: InboxItem) -> datetime | None:
    d = item.get("date")
    if not d:
        return None
    ts = datetime.fromisoformat(str(d))
    return ts if ts.tzinfo is not None else None


@dataclass
class MemoryInboxStore:
    data: dict[int, tuple[InboxItem, datetime]] = field(
        default_factory=dict[int, tuple[InboxItem, datetime]]
    )

    def upsert(self, items: Sequence[InboxItem], received_at: datetime) -> list[int]:
        new: list[int] = []
        for it in items:
            uid = int(it["update_id"])
            if uid in self.data:
                continue
            self.data[uid] = (dict(it), received_at)
            new.append(uid)
        return new

    def expire(self, cutoff: datetime) -> int:
        gone = [u for u, (it, _) in self.data.items() if (d := _item_dt(it)) and d < cutoff]
        for u in gone:
            del self.data[u]
        return len(gone)

    def count(self) -> int:
        return len(self.data)

    def items(self, since: datetime | None = None) -> list[tuple[InboxItem, datetime]]:
        out: list[tuple[InboxItem, datetime]] = []
        for u in sorted(self.data):
            it, rec = self.data[u]
            d = _item_dt(it)
            if since is not None and (d is None or d < since):
                continue
            out.append((dict(it), rec))
        return out


SQL_INBOX_UPSERT = (
    "INSERT INTO prv_alerts.tg_inbox (update_id, chat_id, date, text, urls, x_ids, author, kind, "
    "text_via, received_at) VALUES (%(update_id)s, %(chat_id)s, %(date)s, %(text)s, %(urls)s, "
    "%(x_ids)s, %(author)s, %(kind)s, %(text_via)s, %(received_at)s) "
    "ON CONFLICT (update_id) DO NOTHING RETURNING update_id"
)
SQL_INBOX_EXPIRE = "DELETE FROM prv_alerts.tg_inbox WHERE date < %(cutoff)s"
SQL_INBOX_COUNT = "SELECT count(*) FROM prv_alerts.tg_inbox"
SQL_INBOX_ITEMS = (
    "SELECT update_id, chat_id, date, text, urls, x_ids, author, kind, text_via, received_at "
    "FROM prv_alerts.tg_inbox WHERE (%(since)s::timestamptz IS NULL OR date >= %(since)s) "
    "ORDER BY update_id"
)


class PgInboxStore:
    """Postgres 인박스(`prv_alerts.tg_inbox`). 시각은 timestamptz — 읽을 때 KST ISO 문자열로."""

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def upsert(self, items: Sequence[InboxItem], received_at: datetime) -> list[int]:
        new: list[int] = []
        with self._conn.transaction(), self._conn.cursor() as cur:
            for it in items:
                cur.execute(
                    SQL_INBOX_UPSERT,
                    {
                        "update_id": it["update_id"],
                        "chat_id": it.get("chat_id"),
                        "date": _item_dt(it),
                        "text": it.get("text") or "",
                        "urls": list(it.get("urls") or []),
                        "x_ids": list(it.get("x_ids") or []),
                        "author": it.get("author"),
                        "kind": it.get("kind"),
                        "text_via": it.get("text_via"),
                        "received_at": received_at,
                    },
                )
                row = cur.fetchone()
                if row is not None:
                    new.append(int(row[0]))
        return new

    def expire(self, cutoff: datetime) -> int:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(SQL_INBOX_EXPIRE, {"cutoff": cutoff})
            return int(cur.rowcount)

    def count(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute(SQL_INBOX_COUNT)
            row = cur.fetchone()
        return int(row[0]) if row is not None else 0

    def items(self, since: datetime | None = None) -> list[tuple[InboxItem, datetime]]:
        with self._conn.cursor() as cur:
            cur.execute(SQL_INBOX_ITEMS, {"since": since})
            rows: Iterable[Any] = cur.fetchall()
        out: list[tuple[InboxItem, datetime]] = []
        for r in rows:
            uid, chat, d, text, urls, xids, author, kind, via, rec = r
            item: InboxItem = {
                "update_id": int(uid),
                "chat_id": int(chat) if chat is not None else None,
                "date": d.astimezone(KST).isoformat(timespec="seconds") if d is not None else None,
                "text": text,
                "urls": list(urls or []),
                "x_ids": list(xids or []),
                "author": author,
                "kind": kind,
                "text_via": via,
            }
            out.append((item, rec if rec.tzinfo is not None else rec.replace(tzinfo=UTC)))
        return out


def inbox_cutoff(now: datetime, keep_days: int) -> datetime:
    """보존 기간이 지난 경계(ET `merge` 의 cutoff)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return now - timedelta(days=keep_days)
