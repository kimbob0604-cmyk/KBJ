"""녹화·체결 쓰기 싱크(`SpooledGexStore`) — DB 장애 → 디스크 스풀 → 재적재(P7 ING 새 시험).

GX `tests/unit/test_recorder_service.py::test_db_outage_goes_to_the_disk_spool_and_replays`(GX
`PostgresSink`) 의 KBJ 판이다 — 같은 시나리오를 KBJ 싱크로 돌린다(GX 판은 legacy
`tests/unit/test_recorder_postgres_spool.py` 에 남았다). 가짜 연결 공장만 쓴다 — 실제 DB 없음.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from kbj.core.gex.records import RawEnvelope
from kbj.services.recorder.envelope import Batcher
from kbj.services.recorder.service import Recorder
from kbj.services.recorder.store import SpooledGexStore, service_health_row
from kbj.services.runtime import HealthEvent, MemoryHealthSink
from kbj.services.runtime.bus import encode_envelope
from kbj.store.db import StoreError
from kbj.store.spool import DiskSpool

T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
_TABLE = re.compile(r'INSERT INTO "(\w+)"\."(\w+)"')


class FakeDb:
    """연결 공장. down 이면 접속이 `StoreError`(kbj.store.db.connect 와 같은 꼴), reject 표는
    데이터 오류(`psycopg.errors.CheckViolation`)."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.reject: set[str] = set()
        self.rows: list[tuple[str, tuple[Any, ...]]] = []
        self.connects = 0

    def __call__(self) -> Any:
        self.connects += 1
        if self.down:
            raise StoreError("DB 접속 실패: OperationalError", cause="OperationalError")
        return _Conn(self)

    def of(self, table: str) -> list[tuple[Any, ...]]:
        return [r for t, r in self.rows if t == table]


class _Conn:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self.db)


class _Cursor:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, query: sql.Composed, rows: Sequence[tuple[Any, ...]]) -> None:
        m = _TABLE.search(query.as_string())
        assert m is not None
        table = m.group(2)
        if table in self.db.reject:
            raise psycopg.errors.CheckViolation("check")
        self.db.rows += [(table, tuple(r)) for r in rows]


def _env(i: int) -> RawEnvelope:
    return RawEnvelope.model_validate(
        {
            "received_at": T0 + timedelta(milliseconds=i),
            "source": "kis_ws",
            "tr_id": "H0IFCNT0",
            "key": "A01612",
            "payload": f"0|H0IFCNT0|001|A01612^{i:06d}",
            "trade_date": date(2026, 9, 28),
            "session": "day",
        }
    )


def test_db_outage_goes_to_the_disk_spool_and_replays(tmp_path: Path) -> None:
    db = FakeDb(down=True)
    mono = [0.0]
    events: list[HealthEvent] = []
    sink = SpooledGexStore(
        db,
        service="recorder",
        spool=DiskSpool(tmp_path / "spool"),
        on_health=events.append,
        monotonic=lambda: mono[0],
        now=lambda: T0,
    )
    health = MemoryHealthSink()
    rec = Recorder(sink, health, batcher=Batcher(max_items=2), now=lambda: T0)
    for i in range(4):
        rec.accept(encode_envelope(_env(i)))
    assert rec.stats.written == 4 and rec.stats.failed == 0 and not health.events
    assert sink.degraded and db.of("raw_messages") == []
    assert [e.kind for e in events] == ["db_spooling"]
    assert not sink.flush_spool()  # 백오프(1초) 안 — DB 를 부르지 않는다
    db.down = False
    mono[0] = 60.0
    assert sink.flush_spool()
    assert len(db.of("raw_messages")) == 4 and not sink.degraded
    assert [e.kind for e in events] == ["db_spooling", "db_spool_replayed"]


def test_while_the_spool_is_pending_new_batches_queue_behind_it(tmp_path: Path) -> None:
    db = FakeDb(down=True)
    mono = [0.0]
    sink = SpooledGexStore(
        db, service="recorder", spool=DiskSpool(tmp_path / "spool"), monotonic=lambda: mono[0]
    )
    sink.write_raw([_env(0)])
    db.down = False
    sink.write_raw([_env(1)])  # 백오프 안 — 스풀 뒤에 붙는다(순서 유지)
    assert db.of("raw_messages") == []
    mono[0] = 10.0
    sink.write_raw([_env(2)])  # 백오프가 지났다 — 스풀부터 비우고 쓴다
    texts = [r[7] for r in db.of("raw_messages")]
    assert texts == [_env(i).payload for i in range(3)]


def test_without_a_spool_a_connection_error_is_raised() -> None:
    sink = SpooledGexStore(FakeDb(down=True), service="recorder")
    with pytest.raises(StoreError, match="raw_messages: DB 연결 실패"):
        sink.write_raw([_env(0)])
    assert sink.flush_spool()


def test_data_errors_are_raised_not_spooled(tmp_path: Path) -> None:
    db = FakeDb()
    db.reject.add("raw_messages")
    spool = DiskSpool(tmp_path / "spool")
    sink = SpooledGexStore(db, service="recorder", spool=spool)
    with pytest.raises(StoreError, match="CheckViolation"):
        sink.write_raw([_env(0)])
    assert not spool.pending and not sink.degraded


def test_service_health_rows_use_the_tagger_and_an_empty_detail() -> None:
    ev = HealthEvent("ws_key_missing", "접속키 없음", T0, "warning", service="ws-gateway")
    row = service_health_row(ev, lambda _t: (date(2026, 9, 28), "day"))
    assert row[:7] == (
        T0,
        date(2026, 9, 28),
        "day",
        "ws-gateway",
        "ws_key_missing",
        "warning",
        "접속키 없음",
    )
    assert row[7] == "{}" and isinstance(row[8], bytes)
    db = FakeDb()
    SpooledGexStore(db, service="ws-gateway").write_health([ev], tagger=lambda _t: (None, None))
    (written,) = db.of("health_events")
    assert written[1:3] == (None, None)
