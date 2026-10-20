"""운영 점검의 읽기 원천 — `ops.*` 표·등록부 기대값(docs/p9_design.md §6.2, ADR 0037).

- **읽기만** 한다. 단 하나의 쓰기는 하트비트 끊김 기록(`record_heartbeat_gaps` — `ops.health_events`
  kind `heartbeat_gap`)이고, 그것도 부르는 쪽(워치독)이 정한다. 점검 CLI·API 는 쓰지 않는다.
- 이 모듈은 로그인 API(`kbj.services.api`)가 import 한다 — 계약 ⑩ 때문에 작업 등록부
  (`kbj.services.scheduler.registry` → `kbj.data.catalog` → 로그인 어댑터)를 import 하지 않는다.
  기대값(켜진 상시 서비스·켜진 작업·마감·브리핑 마감 시각)은 `config/jobs.yaml` 에서 필요한 칸만
  직접 읽는다(`load_expect`). 등록부와 같은 값을 읽는지는 시험이 대조한다(tests/unit/ops).
- 값·키·토큰은 다루지 않는다. 행에는 작업 이름·상태·시각·개수만 싣는다. 발송 사유(reason)는 가린다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any, Final, Literal, Protocol

import psycopg
import yaml
from psycopg import sql
from psycopg.types.json import Jsonb

from kbj.core.masking import mask_text
from kbj.store.db import StoreError, event_digest

__all__ = [
    "BRIEF_KINDS",
    "GAP_KIND",
    "Expect",
    "ExpectError",
    "GapEvent",
    "IssueCounts",
    "JobRunRow",
    "MemoryOpsSource",
    "NotifyRow",
    "OpsSource",
    "PgOpsSource",
    "ServiceExpect",
    "TableSize",
    "load_expect",
    "record_heartbeat_gaps",
]

GAP_KIND: Final = "heartbeat_gap"  # ops.health_events.kind — 하트비트 끊김 한 번 관측
BRIEF_KINDS: Final[tuple[str, ...]] = ("brief.morning", "brief.closing")
_DEFAULT_CATCH_UP: Final[dict[str, time]] = {
    "brief.morning": time(9, 0),
    "brief.closing": time(20, 30),
}

RunStatus = Literal["queued", "running", "ok", "failed", "skipped", "timeout"]


class ExpectError(ValueError):
    """`config/jobs.yaml` 을 읽지 못했다(형식 오류)."""


# ── 기대값(등록부의 필요한 칸만) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceExpect:
    """켜진 상시 서비스 — 등록부 이름·하트비트 이름(Redis 키)·기준 초."""

    name: str
    beat: str
    heartbeat_s: int


@dataclass(frozen=True)
class Expect:
    services: tuple[ServiceExpect, ...]
    jobs: frozenset[str]  # 켜진 작업 이름
    deadline_min: Mapping[str, int] = field(default_factory=dict[str, int])
    brief_until: Mapping[str, time] = field(default_factory=lambda: dict(_DEFAULT_CATCH_UP))


def _hhmm(v: object, where: str) -> time:
    if not isinstance(v, str) or len(v) != 5 or v[2] != ":":
        raise ExpectError(f"{where}: HH:MM 형식이 아니다")
    try:
        return time(int(v[:2]), int(v[3:]))
    except ValueError:
        raise ExpectError(f"{where}: HH:MM 형식이 아니다") from None


def load_expect(path: Path) -> Expect:
    """등록부 파일 → 기대값. 서비스는 enabled·heartbeat_s 가 있는 것, 작업은 enabled 인 것."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ExpectError(f"{path.name}: 읽지 못했다({type(e).__name__})") from None
    if not isinstance(raw, Mapping):
        raise ExpectError(f"{path.name}: 최상위가 mapping 이 아니다")
    data: Mapping[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    defaults: Mapping[str, Any] = data.get("defaults") or {}
    services: list[ServiceExpect] = []
    for s in data.get("services") or []:
        if not isinstance(s, Mapping):
            raise ExpectError("services 항목이 mapping 이 아니다")
        svc: Mapping[str, Any] = s  # pyright: ignore[reportUnknownVariableType]
        hb = svc.get("heartbeat_s")
        if not svc.get("enabled", False) or hb is None:
            continue
        name = str(svc["name"])
        services.append(ServiceExpect(name, str(svc.get("heartbeat_key") or name), int(hb)))
    jobs: set[str] = set()
    deadlines: dict[str, int] = {}
    until = dict(_DEFAULT_CATCH_UP)
    for j in data.get("jobs") or []:
        if not isinstance(j, Mapping):
            raise ExpectError("jobs 항목이 mapping 이 아니다")
        job: Mapping[str, Any] = j  # pyright: ignore[reportUnknownVariableType]
        name = str(job["name"])
        if not job.get("enabled", defaults.get("enabled", False)):
            continue
        jobs.add(name)
        deadlines[name] = int(job.get("deadline_min", defaults.get("deadline_min", 120)))
        sched = job.get("schedule")
        if name in until and isinstance(sched, Mapping) and sched.get("catch_up_until"):
            until[name] = _hhmm(sched.get("catch_up_until"), f"{name}.catch_up_until")  # pyright: ignore[reportUnknownMemberType]
    return Expect(tuple(services), frozenset(jobs), deadlines, until)


# ── 행 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JobRunRow:
    """`ops.job_run` 한 줄(상태·시각 + 짧은 사유 — 값·키 없음, 실행기가 가려 적은 detail.reason)."""

    job: str
    as_of: str
    attempt: int
    status: RunStatus
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    source: str = "scheduler"
    reason: str | None = None

    @property
    def at(self) -> datetime:
        """이 실행을 어느 날로 셀지 정하는 시각(시작 시각, 없으면 기록 시각)."""
        return self.started_at or self.created_at


@dataclass(frozen=True)
class NotifyRow:
    """`ops.notify_log` 한 줄(본문 없음). reason 은 가린 문장."""

    kind: str
    subject: str | None
    status: str
    requested_at: datetime
    sent_at: datetime | None = None
    as_of: Any = None  # date | None — 브리핑의 기준일
    parts: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class GapEvent:
    """하트비트 끊김 관측 한 건 — `ts` 에 본 나이가 `age_s` 초(끊긴 시작 = ts − age_s)."""

    service: str
    ts: datetime
    age_s: float | None  # None = 하트비트가 아예 없었다(시작 시각 모름)


@dataclass(frozen=True)
class IssueCounts:
    collection_gaps: int
    quarantine: int


@dataclass(frozen=True)
class TableSize:
    schema: str
    table: str
    bytes: int


class OpsSource(Protocol):
    """점검이 읽는 것. 시각 구간은 [start, end)(aware)."""

    def job_runs(self, start: datetime, end: datetime) -> list[JobRunRow]: ...
    def notifies(self, start: datetime, end: datetime) -> list[NotifyRow]: ...
    def gap_events(self, start: datetime, end: datetime) -> list[GapEvent]: ...
    def issues(self, start: datetime, end: datetime) -> IssueCounts: ...
    def table_sizes(self, limit: int) -> list[TableSize]: ...
    def database_bytes(self) -> int | None: ...


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return ts


def _in(ts: datetime, start: datetime, end: datetime) -> bool:
    return start <= ts < end


@dataclass
class MemoryOpsSource:
    """시험·데모용 메모리 원천(같은 구간 규칙)."""

    runs: list[JobRunRow] = field(default_factory=list[JobRunRow])
    notify: list[NotifyRow] = field(default_factory=list[NotifyRow])
    gaps: list[GapEvent] = field(default_factory=list[GapEvent])
    collection_gap_ts: list[datetime] = field(default_factory=list[datetime])
    quarantine_ts: list[datetime] = field(default_factory=list[datetime])
    sizes: list[TableSize] = field(default_factory=list[TableSize])
    db_bytes: int | None = None

    def job_runs(self, start: datetime, end: datetime) -> list[JobRunRow]:
        return [r for r in self.runs if _in(r.at, start, end)]

    def notifies(self, start: datetime, end: datetime) -> list[NotifyRow]:
        return [n for n in self.notify if _in(n.requested_at, start, end)]

    def gap_events(self, start: datetime, end: datetime) -> list[GapEvent]:
        return [g for g in self.gaps if _in(g.ts, start, end)]

    def issues(self, start: datetime, end: datetime) -> IssueCounts:
        return IssueCounts(
            sum(1 for t in self.collection_gap_ts if _in(t, start, end)),
            sum(1 for t in self.quarantine_ts if _in(t, start, end)),
        )

    def table_sizes(self, limit: int) -> list[TableSize]:
        return sorted(self.sizes, key=lambda t: (-t.bytes, t.schema, t.table))[:limit]

    def database_bytes(self) -> int | None:
        return self.db_bytes

    def write_gaps(self, events: Sequence[GapEvent]) -> int:
        self.gaps.extend(events)
        return len(events)


# ── Postgres ─────────────────────────────────────────────────────────────────────────────

_Q_RUNS: Final = sql.SQL(
    "SELECT job, as_of, attempt, status, started_at, finished_at, created_at, source, "
    "left(detail->>'reason', 300) "
    "FROM ops.job_run WHERE COALESCE(started_at, created_at) >= %s "
    "AND COALESCE(started_at, created_at) < %s"
)
_Q_NOTIFY: Final = sql.SQL(
    "SELECT kind, subject, status, requested_at, sent_at, as_of, parts, reason "
    "FROM ops.notify_log WHERE requested_at >= %s AND requested_at < %s ORDER BY requested_at"
)
_Q_GAPS: Final = sql.SQL(
    "SELECT service, ts, detail FROM ops.health_events "
    "WHERE kind = %s AND ts >= %s AND ts < %s ORDER BY ts"
)
_Q_ISSUES: Final = sql.SQL(
    "SELECT (SELECT count(*) FROM ops.collection_gaps WHERE start_ts >= %s AND start_ts < %s), "
    "(SELECT count(*) FROM ops.quarantine WHERE ts >= %s AND ts < %s)"
)
# 일반 표(청크·시스템 스키마 제외) — 하이퍼테이블 부모는 청크 크기를 따로 더한다
_Q_TABLES: Final = sql.SQL(
    "SELECT n.nspname, c.relname, pg_total_relation_size(c.oid) "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'm') "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
    "AND position('timescaledb' in n.nspname) = 0"
)
_Q_HYPER: Final = sql.SQL(
    "SELECT hypertable_schema, hypertable_name, "
    "hypertable_size("
    "(quote_ident(hypertable_schema) || '.' || quote_ident(hypertable_name))::regclass) "
    "FROM timescaledb_information.hypertables"
)
_Q_DB: Final = sql.SQL("SELECT pg_database_size(current_database())")
_INSERT_GAP: Final = sql.SQL(
    "INSERT INTO ops.health_events "
    "(ts, trade_date, session, service, kind, level, message, detail, digest) "
    "VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s, %s) ON CONFLICT (ts, digest) DO NOTHING"
)

ConnectFn = Callable[[], psycopg.Connection[Any]]


def _age_of(detail: object) -> float | None:
    if isinstance(detail, Mapping):
        v = detail.get("age_s")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(v, int | float) and not isinstance(v, bool):
            return float(v)
    return None


class PgOpsSource:
    """`ops.*` 읽기(+ 하트비트 끊김 쓰기). 실패는 `StoreError`(종류만 — 접속 정보 없음)."""

    def __init__(self, connect_fn: ConnectFn) -> None:
        self._connect = connect_fn

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgOpsSource()"

    def _fetch(self, q: sql.SQL, params: Sequence[object] | None = None) -> list[tuple[Any, ...]]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(q, params)
                return list(cur.fetchall())
        except psycopg.Error as e:
            kind = type(e).__name__
            raise StoreError(f"운영 기록 조회 실패: {kind}", cause=kind) from None

    def job_runs(self, start: datetime, end: datetime) -> list[JobRunRow]:
        rows = self._fetch(_Q_RUNS, (_aware(start), _aware(end)))
        return [JobRunRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows]

    def notifies(self, start: datetime, end: datetime) -> list[NotifyRow]:
        rows = self._fetch(_Q_NOTIFY, (_aware(start), _aware(end)))
        return [
            NotifyRow(*r[:7], reason=None if r[7] is None else mask_text(r[7]))  # pyright: ignore[reportArgumentType]
            for r in rows
        ]

    def gap_events(self, start: datetime, end: datetime) -> list[GapEvent]:
        rows = self._fetch(_Q_GAPS, (GAP_KIND, _aware(start), _aware(end)))
        return [GapEvent(r[0], r[1], _age_of(r[2])) for r in rows]

    def issues(self, start: datetime, end: datetime) -> IssueCounts:
        s, e = _aware(start), _aware(end)
        rows = self._fetch(_Q_ISSUES, (s, e, s, e))
        return IssueCounts(int(rows[0][0]), int(rows[0][1]))

    def table_sizes(self, limit: int) -> list[TableSize]:
        sizes = {(r[0], r[1]): int(r[2] or 0) for r in self._fetch(_Q_TABLES)}
        for r in self._fetch(_Q_HYPER):
            sizes[(r[0], r[1])] = int(r[2] or 0)
        out = [TableSize(s, t, b) for (s, t), b in sizes.items()]
        return sorted(out, key=lambda t: (-t.bytes, t.schema, t.table))[:limit]

    def database_bytes(self) -> int | None:
        rows = self._fetch(_Q_DB)
        return None if not rows or rows[0][0] is None else int(rows[0][0])

    def write_gaps(self, events: Sequence[GapEvent]) -> int:
        rows = [_gap_row(ev) for ev in events]
        if not rows:
            return 0
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany(_INSERT_GAP, rows)
                return max(cur.rowcount, 0)
        except psycopg.Error as e:
            kind = type(e).__name__
            raise StoreError(f"하트비트 끊김 기록 실패: {kind}", cause=kind) from None


def _gap_row(ev: GapEvent) -> tuple[Any, ...]:
    ts = _aware(ev.ts).astimezone(UTC)
    detail: dict[str, Any] = {} if ev.age_s is None else {"age_s": round(ev.age_s)}
    seen = "없음" if ev.age_s is None else f"{ev.age_s:.0f}초"
    message = f"하트비트 끊김(마지막 {seen})"
    detail_json = json.dumps(detail, separators=(",", ":"), sort_keys=True)
    digest = event_digest(ev.service, GAP_KIND, "warning", message, detail_json)
    return (ts, ev.service, GAP_KIND, "warning", message, Jsonb(detail), digest)


class GapWriter(Protocol):
    def write_gaps(self, events: Sequence[GapEvent]) -> int: ...


def record_heartbeat_gaps(
    services: Iterable[ServiceExpect],
    heartbeat_age: Callable[[str, datetime], float | None],
    now: datetime,
    writer: GapWriter,
) -> list[GapEvent]:
    """지금 하트비트가 기준(heartbeat_s)보다 오래된 서비스를 `heartbeat_gap` 으로 남긴다.

    워치독처럼 주기적으로 부르는 쪽이 쓴다(ADR 0037). 같은 끊김을 여러 번 보면 여러 줄이 남고,
    판정(`check.outages`)이 끊긴 시작 시각(ts − age)으로 하나로 묶는다.
    """
    events = []
    for s in services:
        age = heartbeat_age(s.beat, now)
        if age is None or age > s.heartbeat_s:
            events.append(GapEvent(s.name, _aware(now), age))
    writer.write_gaps(events)
    return events
