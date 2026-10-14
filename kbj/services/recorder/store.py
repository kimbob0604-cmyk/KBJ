"""녹화·체결 쓰기 싱크 — `PgGexRepo` 표 정의 + 디스크 스풀(P7 ING, docs/p7_design.md §1.4·D-P7-8).

GX `data/store.py:PostgresSink` 의 '쓰기 + 스풀' 부분을 KBJ 저장소 정의 위에 다시 세웠다(GX 싱크는
원천·서비스 모듈을 import 해서 계약 ⑦ 을 깬다 — 통째로 옮길 수 없다). `kbj.store.repos.gex` 는
스풀 없이 실패를 올리고(FND-B), 서비스(ws-gateway·recorder)가 이 싱크로 감싼다.

- 메서드 이름은 GX 싱크 그대로(D-P7-8): `write_raw`·`write_fut_ticks`·`write_opt_ticks`·
  `write_session_log`·`write_health(events, *, tagger)`·`flush_spool()`·`close()` — ws-gateway
  `GatewayStore`·recorder `RawStore` 프로토콜을 만족한다. legacy `PostgresSink` 도 같은 모양이다.
- 행·SQL 은 `kbj.store.repos.gex` 의 표 정의(`RAW_MESSAGES`·`FUT_TICKS`·`OPT_TICKS`·
  `SESSION_LOG`·`HEALTH_EVENTS`)와 행 함수를 그대로 쓴다(키·덮어쓰기·digest 가 같다 — 같은 원문은
  같은 행). health 는 서비스 이벤트(`kbj.services.runtime.HealthEvent`)라 `kbj.store.db.health_row`
  로 만든다(detail 은 빈 객체 JSON).
- 연결 오류(접속 실패 `StoreError`·`OperationalError`·`InterfaceError`)면 스풀에 넣고 백오프 동안
  DB 를 부르지 않는다. 스풀이 밀려 있으면 새 묶음도 스풀 뒤에 붙인다(표마다 순서 유지).
  `flush_spool()` 이 백오프가 지난 뒤 오래된 것부터 재적재한다(한 번에 `replay_budget_s` 까지).
  데이터 오류(제약 위반 등)는 `StoreError` 로 올린다 — 호출자가 한 건씩 다시 쓴다. 재적재 중 데이터
  오류 묶음은 한 행씩 다시 보내고 거부된 행만 데드레터로(`RejectedRows`).
- 스풀이 없으면 연결 오류도 `StoreError` 로 올린다(에러를 삼키지 않는다).
- 스풀 상태 알림(스풀 시작·복구·버림·데드레터)은 로그 + `on_health`(잠금 밖에서 부른다). 접속
  정보는 어디에도 싣지 않는다(`kbj.store.db` 가 이미 가린 `StoreError` 문구만).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import psycopg

from kbj.config.settings import Settings
from kbj.core.gex.records import (
    FuturesTickRecord,
    OptionTickRecord,
    RawEnvelope,
    SessionLogRecord,
)
from kbj.services.runtime import HealthEvent, Severity, Tagger, log_event, utcnow
from kbj.store.db import StoreError, connect
from kbj.store.db import health_row as _service_health_row
from kbj.store.repos._common import ConnFactory
from kbj.store.repos.gex import (
    FUT_TICKS,
    HEALTH_EVENTS,
    OPT_TICKS,
    RAW_MESSAGES,
    SESSION_LOG,
    GxTable,
    attr_row,
    raw_row,
    session_log_row,
)
from kbj.store.spool import (
    DiskSpool,
    DropNotice,
    RejectedRows,
    ReplayResult,
    SpooledBatch,
    SpoolError,
    TransientWrite,
)

Row = tuple[Any, ...]
Tag = tuple[date | None, str | None]
HealthHook = Callable[[HealthEvent], None]
_PENDING_HOOKS = 100  # health 싱크를 잇기 전 붙잡아 두는 알림 수

TABLES: Final[tuple[GxTable, ...]] = (
    RAW_MESSAGES,
    FUT_TICKS,
    OPT_TICKS,
    SESSION_LOG,
    HEALTH_EVENTS,
)
_BY_NAME: Final = {t.name: t for t in TABLES}
_SESSIONS: Final = frozenset({"day", "night"})
_TRANSIENT = (psycopg.OperationalError, psycopg.InterfaceError)

log = logging.getLogger("kbj.services.recorder.store")


class _Unavailable(Exception):
    """연결 오류 — 스풀이 있으면 스풀로, 없으면 StoreError."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _tag(trade_date: date | None, session: str | None) -> Tag:
    if trade_date is None or session not in _SESSIONS:
        return None, None
    return trade_date, session


def service_health_row(ev: HealthEvent, tagger: Tagger | None = None) -> Row:
    """서비스 health 이벤트 → `ops.health_events` 행(`HEALTH_EVENTS` 열 순서, detail 은 JSON)."""
    r = _service_health_row(ev, tagger)
    return (*r[:7], "{}", r[8])


class SpooledGexStore:
    """`prv_gex.raw_messages`·`fut_ticks`·`opt_ticks`·`ops.session_log`·`ops.health_events` 쓰기 +
    디스크 스풀. 묶음 단위로 잠근다(작업 스레드 여럿이 써도 차례로)."""

    def __init__(
        self,
        conn_factory: ConnFactory,
        *,
        service: str,
        spool: DiskSpool | None = None,
        on_health: HealthHook | None = None,
        backoff_s: tuple[float, float] = (1.0, 30.0),
        replay_budget_s: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
        tagger: Tagger | None = None,
    ) -> None:
        if not 0 < backoff_s[0] <= backoff_s[1] or replay_budget_s <= 0:
            raise ValueError("0 < 백오프 처음 <= 최대, replay_budget_s > 0")
        self.service = service
        self._connect = conn_factory
        self._spool = spool
        self._on_health = on_health
        self._backoff = backoff_s
        self._budget = replay_budget_s
        self._mono = monotonic
        self._now = now
        self._tagger = tagger
        self._lock = threading.Lock()
        self._delay = 0.0
        self._down_until: float | None = None
        self._spooling = False
        self._hooks: list[HealthEvent] = []
        self.rows_written = 0
        self.rows_spooled = 0
        self.rows_dropped = 0
        if spool is not None and spool.pending:
            self._spooling = True
            st = spool.status()
            self._notice(
                "db_spool_pending_at_start",
                f"이전 실행이 남긴 스풀 {st.segments}세그먼트 {st.bytes}B({','.join(st.tables)}) — "
                "DB 로 재적재한다",
                "warning",
            )

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return f"SpooledGexStore(service={self.service!r})"

    @property
    def degraded(self) -> bool:
        """DB 대신 스풀에 쓰는 중인가."""
        return self._spooling

    def close(self) -> None:
        with self._lock:
            if self._spool is not None:
                self._spool.close()

    # ── 쓰기(GX 싱크 이름) ──

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        if envelopes:
            first = envelopes[0]
            self._write(
                RAW_MESSAGES, [raw_row(e) for e in envelopes], _tag(first.trade_date, first.session)
            )

    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None:
        if rows:
            tag = _tag(rows[0].trade_date, rows[0].session)
            self._write(FUT_TICKS, [attr_row(FUT_TICKS, r) for r in rows], tag)

    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None:
        if rows:
            tag = _tag(rows[0].trade_date, rows[0].session)
            self._write(OPT_TICKS, [attr_row(OPT_TICKS, r) for r in rows], tag)

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        if rows:
            out = [session_log_row(r) for r in rows]
            self._write(SESSION_LOG, out, (out[0][1], out[0][2]))

    def write_health(self, events: Sequence[HealthEvent], *, tagger: Tagger | None = None) -> None:
        if events:
            out = [
                service_health_row(e, tagger if tagger is not None else self._tagger)
                for e in events
            ]
            self._write(HEALTH_EVENTS, out, (out[0][1], out[0][2]))

    def flush_spool(self) -> bool:
        """백오프가 지났으면 스풀을 재적재한다(한 번에 `replay_budget_s` 까지). 비었으면 True."""
        if self._spool is None:
            return True
        try:
            with self._lock:
                if self._spool.pending and not self._down():
                    self._drain()
                return not self._spool.pending
        finally:
            self._dispatch_hooks()

    # ── 내부 ──

    def _write(self, table: GxTable, rows: Sequence[Row], tag: Tag) -> None:
        try:
            with self._lock:
                sp = self._spool
                if sp is not None and (sp.pending or self._down()):
                    if not self._down():
                        self._drain()
                    if sp.pending or self._down():
                        self._to_spool(table, rows, tag)
                        return
                try:
                    self._send(table, rows)
                except _Unavailable as u:
                    if sp is None:
                        raise StoreError(
                            f"{table.name}: DB 연결 실패 — {u.kind}", cause=u.kind
                        ) from None
                    self._went_down(table, u)
                    self._to_spool(table, rows, tag)
                    return
                self._up()
                self.rows_written += len(rows)
                self._recovered()
        finally:
            self._dispatch_hooks()

    def _send(self, table: GxTable, rows: Sequence[Row]) -> None:
        """트랜잭션 하나로 보낸다. 연결 오류는 `_Unavailable`, 데이터 오류는 `StoreError`."""
        try:
            conn = self._connect()
        except StoreError as e:  # kbj.store.db.connect — 접속 실패(가린 문구)
            raise _Unavailable(e.cause or "StoreError") from None
        except _TRANSIENT as e:
            raise _Unavailable(type(e).__name__) from None
        try:
            with conn, conn.cursor() as cur:
                cur.executemany(table.insert_sql(), rows)
        except _TRANSIENT as e:
            raise _Unavailable(type(e).__name__) from None
        except psycopg.Error as e:
            kind = f"{type(e).__name__}({e.sqlstate})" if e.sqlstate else type(e).__name__
            raise StoreError(f"{table.name}: {kind}", cause=kind) from None

    def _down(self) -> bool:
        return self._down_until is not None and self._mono() < self._down_until

    def _up(self) -> None:
        self._delay = 0.0
        self._down_until = None

    def _bump_backoff(self) -> None:
        first, most = self._backoff
        self._delay = first if self._delay <= 0 else min(self._delay * 2, most)
        self._down_until = self._mono() + self._delay

    def _went_down(self, table: GxTable, u: _Unavailable) -> None:
        self._bump_backoff()
        if not self._spooling:
            self._spooling = True
            self._notice(
                "db_spooling",
                f"DB 쓰기 실패({u.kind}) — {table.name} 부터 로컬 스풀에 쌓는다, "
                f"{self._delay:g}초 뒤 재적재 시도",
                "warning",
            )

    def _need_spool(self) -> DiskSpool:
        if self._spool is None:
            raise RuntimeError("스풀 없이 부르지 않는다")
        return self._spool

    def _to_spool(self, table: GxTable, rows: Sequence[Row], tag: Tag) -> None:
        sp = self._need_spool()
        try:
            drops = sp.append(table.name, table.columns, rows, tag)
        except SpoolError as e:
            self._notice(
                "spool_write_failed", f"{table.name} {len(rows)}행 스풀 실패: {e}", "critical"
            )
            raise StoreError(f"{table.name}: DB·스풀 모두 실패 — {e}") from None
        if not any(d.reason == "too_large" for d in drops):
            self.rows_spooled += len(rows)
        for d in drops:
            self._dropped(d)

    def _dropped(self, d: DropNotice) -> None:
        self.rows_dropped += d.rows
        what = {
            "oldest": "스풀 상한 — 가장 오래된 세그먼트를 버렸다",
            "too_large": "묶음 하나가 스풀 상한보다 커서 버렸다",
            "dead_full": "데드레터 상한 — 들어가지 않는 묶음을 버렸다",
        }[d.reason]
        self._notice(
            "spool_dropped",
            f"{what}: {','.join(d.tables)} {d.batches}묶음 {d.rows}행 {d.bytes}B",
            "critical",
        )

    def _drain(self) -> bool:
        """스풀을 오래된 것부터 DB 로(잠금 안). 다 비우면 True."""
        sp = self._need_spool()

        def write(b: SpooledBatch) -> None:
            table = _BY_NAME.get(b.table)
            if table is None or table.columns != b.columns:
                raise StoreError(f"{b.table}: 표·열 정의가 스풀과 다르다")
            try:
                self._send(table, b.rows)
            except _Unavailable:
                raise TransientWrite from None
            except StoreError as e:
                if len(b.rows) <= 1:
                    raise
                self._salvage(table, b, e.cause or type(e).__name__)
                return
            self.rows_written += len(b.rows)

        res = sp.replay(write, deadline=self._mono() + self._budget, clock=self._mono)
        self._replayed(res)
        if res.stopped == "transient":
            self._bump_backoff()
            return False
        if sp.pending:
            return False
        self._up()
        self._recovered()
        return True

    def _salvage(self, table: GxTable, b: SpooledBatch, cause: str) -> None:
        bad: list[Row] = []
        for row in b.rows:
            try:
                self._send(table, [row])
            except _Unavailable:
                raise TransientWrite from None
            except StoreError:
                bad.append(row)
        self.rows_written += len(b.rows) - len(bad)
        if bad:
            raise RejectedRows(bad, cause)

    def _replayed(self, res: ReplayResult) -> None:
        if res.batches or res.dead_batches or res.corrupt_lines:
            log_event(
                log, logging.INFO, self.service, "db_spool_replay",
                batches=res.batches, rows=res.rows, dead_batches=res.dead_batches,
                corrupt_lines=res.corrupt_lines, stopped=res.stopped,
            )  # fmt: skip
        if res.dead_batches or res.corrupt_lines:
            self._notice(
                "spool_dead_letter",
                f"재적재 안 되는 묶음 {res.dead_batches}개({res.dead_rows}행)·깨진 줄 "
                f"{res.corrupt_lines}개를 데드레터로 옮겼다: {'; '.join(res.errors)}",
                "critical",
            )
        for d in res.drops:
            self._dropped(d)

    def _recovered(self) -> None:
        if not self._spooling or (self._spool is not None and self._spool.pending):
            return
        self._spooling = False
        self._notice("db_spool_replayed", "DB 복구 — 스풀을 모두 재적재했다", "info")

    def set_health_hook(self, hook: HealthHook | None) -> None:
        """조립 뒤 health 싱크를 잇는다 — 싱크(`ServiceHealthSink`)가 이 저장소에 쓰므로 저장소를
        먼저 만들고 나중에 잇는다. 잇기 전에 쌓인 알림(기동 때 남은 스풀 등)은 다음 쓰기 때
        넘어간다."""
        self._on_health = hook

    def _notice(self, kind: str, detail: str, severity: Severity) -> None:
        level = {"info": logging.INFO, "warning": logging.WARNING}.get(severity, logging.CRITICAL)
        log_event(log, level, self.service, kind, detail=detail)
        self._hooks.append(HealthEvent(kind, detail, self._now(), severity, service=self.service))
        if self._on_health is None:  # 아직 안 이었으면 최근 것만 붙잡아 둔다(로그에는 이미 남았다)
            del self._hooks[:-_PENDING_HOOKS]

    def _dispatch_hooks(self) -> None:
        """잠금 밖에서 on_health 로 넘긴다(그쪽이 다시 이 싱크에 써도 막히지 않게)."""
        if self._on_health is None:
            return
        with self._lock:
            hooks, self._hooks = self._hooks, []
        for ev in hooks:
            try:
                self._on_health(ev)
            except Exception as e:  # health 실패가 쓰기를 막지 않는다
                log_event(log, logging.ERROR, self.service, "health_failed", error=type(e).__name__)


def open_store(
    settings: Settings, *, service: str, tagger: Tagger | None = None, spool: bool = True
) -> SpooledGexStore:
    """서비스 진입점용: `KBJ_DATABASE_URL` 연결 공장 + `spool_dir/<서비스>` 디스크 큐(GX
    `PostgresSink.from_settings(service=…, spool=True)` 자리). 접속은 쓸 때마다 짧게 연다."""
    if settings.database_url is None:
        raise StoreError("KBJ_DATABASE_URL 이 없다")
    name = service.replace("-", "_")

    def conn() -> psycopg.Connection[Any]:
        return connect(settings, service=name)

    disk = (
        DiskSpool(Path(settings.spool_dir) / service, max_bytes=settings.spool_max_mb << 20)
        if spool
        else None
    )
    return SpooledGexStore(conn, service=service, spool=disk, tagger=tagger)


__all__ = ["TABLES", "SpooledGexStore", "open_store", "service_health_row"]
