"""데이터 키 선점(`ops.data_claim`)과 작업 실행 기록(`ops.job_run`) 저장소(설계 §6.1·§6.5).

선점 규칙(데이터 키 = `(source, dataset, as_of, venue)` — `kbj.data.spec.DataKey`):

- `claim(key, job, run_id, now)`: 살아 있는 줄(`claimed`·`done`)이 없으면 `claimed` 로 넣고 True.
  이미 `done` 이면 False(받지 않는다 → `skipped(duplicate)`). 다른 작업이 `claimed` 로 쥐고 있으면
  False. **같은 작업**이 쥐고 있으면 True — 재시도(아직 공표 전 등)·재기동이 같은 선점을 이어 쓴다
  (run_id 만 바꾼다).
- `complete(key, run_id, rows, now)`: `claimed` → `done`(done_at 필수 — 0002 CHECK).
- `fail(key, run_id, reason, now)`: `claimed` → `failed`. 실패 줄은 여러 개 남아도 되고,
  뒤의 claim 이
  다시 잡는다(부분 유일 인덱스가 `claimed`·`done` 만 본다).

실행 기록: `run_id = "<job>:<as_of>:<attempt>"`, 상태는 0002 CHECK(`queued`·`running`·`ok`·`failed`·
`skipped`·`timeout`). 처리기가 없는 작업(P2 의 대부분)은 `skipped` + detail `{"reason": "planned"}`
('계획됨' — §6.9 경계).

DB 오류는 `StoreError`(접속 정보 없음)로 올린다 — 실행기가 그 작업만 실패로 처리한다(격리).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from kbj.core.masking import mask_text
from kbj.data.spec import DataKey
from kbj.store.db import StoreError

__all__ = [
    "ClaimRow",
    "ClaimStore",
    "MemoryClaimStore",
    "MemoryRunLog",
    "PgClaimStore",
    "PgRunLog",
    "RunLog",
    "RunRecord",
    "RunStatus",
    "make_run_id",
]

RunStatus = Literal["queued", "running", "ok", "failed", "skipped", "timeout"]
RunSource = Literal["scheduler", "manual", "legacy_import"]
ClaimStatus = Literal["claimed", "done", "failed"]
REASON_MAX: Final = 300


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return ts.astimezone(UTC)


def _reason(text: str) -> str:
    return mask_text(text)[:REASON_MAX]


def make_run_id(job: str, as_of: str, attempt: int) -> str:
    if attempt < 1:
        raise ValueError("attempt 는 1 이상")
    return f"{job}:{as_of}:{attempt}"


# ── 선점 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClaimRow:
    key: DataKey
    job: str
    run_id: str | None
    status: ClaimStatus
    claimed_at: datetime
    done_at: datetime | None = None
    rows: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


class ClaimStore(Protocol):
    def claim(self, key: DataKey, job: str, run_id: str, now: datetime) -> bool: ...

    def complete(self, key: DataKey, run_id: str, rows: int | None, now: datetime) -> None: ...

    def fail(self, key: DataKey, run_id: str, reason: str, now: datetime) -> None: ...

    def live(self, key: DataKey) -> ClaimRow | None: ...


class MemoryClaimStore:
    """메모리 선점 — 시험·시뮬레이션용. 규칙은 Pg 와 같다(스레드 안전)."""

    def __init__(self) -> None:
        self.rows: list[ClaimRow] = []
        self.refused: list[tuple[DataKey, str]] = []  # (키, 거절당한 작업) — 중복 0 단언용
        self._lock = threading.Lock()

    def _live_index(self, key: DataKey) -> int | None:
        for i, r in enumerate(self.rows):
            if r.key == key and r.status in ("claimed", "done"):
                return i
        return None

    def claim(self, key: DataKey, job: str, run_id: str, now: datetime) -> bool:
        with self._lock:
            i = self._live_index(key)
            if i is None:
                self.rows.append(ClaimRow(key, job, run_id, "claimed", _utc(now)))
                return True
            cur = self.rows[i]
            if cur.status == "claimed" and cur.job == job:
                self.rows[i] = replace(cur, run_id=run_id)
                return True
            self.refused.append((key, job))
            return False

    def complete(self, key: DataKey, run_id: str, rows: int | None, now: datetime) -> None:
        with self._lock:
            i = self._live_index(key)
            if i is None or self.rows[i].status != "claimed":
                raise StoreError(f"선점 없이 완료할 수 없다: {key.label()}")
            self.rows[i] = replace(
                self.rows[i], status="done", run_id=run_id, done_at=_utc(now), rows=rows
            )

    def fail(self, key: DataKey, run_id: str, reason: str, now: datetime) -> None:
        with self._lock:
            i = self._live_index(key)
            if i is None or self.rows[i].status != "claimed":
                return  # 이미 끝났거나 없음 — 실패로 덮지 않는다
            self.rows[i] = replace(
                self.rows[i],
                status="failed",
                run_id=run_id,
                done_at=_utc(now),
                detail={"reason": _reason(reason)},
            )

    def live(self, key: DataKey) -> ClaimRow | None:
        with self._lock:
            i = self._live_index(key)
            return None if i is None else self.rows[i]

    def done_keys(self) -> list[DataKey]:
        return [r.key for r in self.rows if r.status == "done"]


ConnectFn = Callable[[], psycopg.Connection[Any]]

_KEY_WHERE = sql.SQL("source = %s AND dataset = %s AND as_of = %s AND venue = %s")
_SELECT_LIVE = sql.SQL(
    "SELECT id, job, run_id, status, claimed_at, done_at, rows, detail FROM ops.data_claim "
    "WHERE {} AND status IN ('claimed', 'done') FOR UPDATE"
).format(_KEY_WHERE)
_INSERT = sql.SQL(
    "INSERT INTO ops.data_claim (source, dataset, as_of, venue, job, run_id, status, claimed_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, 'claimed', %s) "
    "ON CONFLICT (source, dataset, as_of, venue) WHERE status IN ('claimed', 'done') DO NOTHING"
)


def _db_error(what: str, e: psycopg.Error) -> StoreError:
    kind = type(e).__name__ + (f"({e.sqlstate})" if e.sqlstate else "")
    return StoreError(f"{what} 실패: {kind}", cause=kind)


class PgClaimStore:
    """`ops.data_claim`(0002). 연결 공장을 받아 호출마다 짧은 트랜잭션으로 쓴다."""

    def __init__(self, connect_fn: ConnectFn) -> None:
        self._connect = connect_fn

    def __repr__(self) -> str:
        return "PgClaimStore()"

    def claim(self, key: DataKey, job: str, run_id: str, now: datetime) -> bool:
        k = tuple(key)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_SELECT_LIVE, k)
                row = cur.fetchone()
                if row is None:
                    cur.execute(_INSERT, (*k, job, run_id, _utc(now)))
                    return cur.rowcount == 1
                if row[3] == "claimed" and row[1] == job:
                    cur.execute(
                        "UPDATE ops.data_claim SET run_id = %s WHERE id = %s", (run_id, row[0])
                    )
                    return True
                return False
        except psycopg.Error as e:
            raise _db_error("선점", e) from None

    def complete(self, key: DataKey, run_id: str, rows: int | None, now: datetime) -> None:
        q = sql.SQL(
            "UPDATE ops.data_claim SET status = 'done', run_id = %s, done_at = %s, rows = %s "
            "WHERE {} AND status = 'claimed'"
        ).format(_KEY_WHERE)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(q, (run_id, _utc(now), rows, *tuple(key)))
                if cur.rowcount != 1:
                    raise StoreError(f"선점 없이 완료할 수 없다: {key.label()}")
        except psycopg.Error as e:
            raise _db_error("선점 완료", e) from None

    def fail(self, key: DataKey, run_id: str, reason: str, now: datetime) -> None:
        q = sql.SQL(
            "UPDATE ops.data_claim SET status = 'failed', run_id = %s, done_at = %s, detail = %s "
            "WHERE {} AND status = 'claimed'"
        ).format(_KEY_WHERE)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(q, (run_id, _utc(now), Jsonb({"reason": _reason(reason)}), *tuple(key)))
        except psycopg.Error as e:
            raise _db_error("선점 실패 기록", e) from None

    def live(self, key: DataKey) -> ClaimRow | None:
        q = sql.SQL(
            "SELECT job, run_id, status, claimed_at, done_at, rows, detail FROM ops.data_claim "
            "WHERE {} AND status IN ('claimed', 'done')"
        ).format(_KEY_WHERE)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(q, tuple(key))
                r = cur.fetchone()
        except psycopg.Error as e:
            raise _db_error("선점 조회", e) from None
        if r is None:
            return None
        return ClaimRow(key, r[0], r[1], r[2], r[3], r[4], r[5], r[6] or {})


# ── 실행 기록 ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunRecord:
    """`ops.job_run` 한 줄. detail 에는 값·키를 싣지 않는다(쓰는 쪽이 가린 짧은 사유·수)."""

    run_id: str
    job: str
    as_of: str
    attempt: int
    status: RunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])
    source: RunSource = "scheduler"


class RunLog(Protocol):
    def record(self, rec: RunRecord) -> None: ...

    def latest(self, job: str, as_of: str) -> RunRecord | None: ...


class MemoryRunLog:
    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}
        self.history: list[RunRecord] = []
        self._lock = threading.Lock()

    def record(self, rec: RunRecord) -> None:
        with self._lock:
            self.records[rec.run_id] = rec
            self.history.append(rec)

    def latest(self, job: str, as_of: str) -> RunRecord | None:
        with self._lock:
            got = [r for r in self.records.values() if r.job == job and r.as_of == as_of]
        return max(got, key=lambda r: r.attempt) if got else None

    def of(self, job: str) -> list[RunRecord]:
        with self._lock:
            return sorted(
                (r for r in self.records.values() if r.job == job),
                key=lambda r: (r.as_of, r.attempt),
            )


_UPSERT_RUN = sql.SQL(
    "INSERT INTO ops.job_run (run_id, job, as_of, attempt, status, started_at, finished_at, "
    "detail, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (run_id) DO UPDATE SET status = EXCLUDED.status, "
    "started_at = COALESCE(EXCLUDED.started_at, ops.job_run.started_at), "
    "finished_at = EXCLUDED.finished_at, detail = EXCLUDED.detail"
)


def _clean_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    # 문자열은 가린다(사유 문구에 키·토큰이 섞이지 않게 — 절대 규칙 5)
    raw = json.loads(json.dumps(dict(detail), ensure_ascii=False, default=str))
    return {k: (_reason(v) if isinstance(v, str) else v) for k, v in raw.items()}


class PgRunLog:
    """`ops.job_run`(0002)."""

    def __init__(self, connect_fn: ConnectFn) -> None:
        self._connect = connect_fn

    def __repr__(self) -> str:
        return "PgRunLog()"

    def record(self, rec: RunRecord) -> None:
        row = (
            rec.run_id,
            rec.job,
            rec.as_of,
            rec.attempt,
            rec.status,
            None if rec.started_at is None else _utc(rec.started_at),
            None if rec.finished_at is None else _utc(rec.finished_at),
            Jsonb(_clean_detail(rec.detail)),
            rec.source,
        )
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_UPSERT_RUN, row)
        except psycopg.Error as e:
            raise _db_error("실행 기록", e) from None

    def latest(self, job: str, as_of: str) -> RunRecord | None:
        q = (
            "SELECT run_id, attempt, status, started_at, finished_at, detail, source "
            "FROM ops.job_run WHERE job = %s AND as_of = %s ORDER BY attempt DESC LIMIT 1"
        )
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(q, (job, as_of))
                r = cur.fetchone()
        except psycopg.Error as e:
            raise _db_error("실행 기록 조회", e) from None
        if r is None:
            return None
        return RunRecord(r[0], job, as_of, r[1], r[2], r[3], r[4], r[5] or {}, r[6])
