"""DB 쓰기 실패 로컬 디스크 큐 (docs/phase1_design.md §2 `db` 장애 시 — data/spool.py·store.py).

- 값 인코딩(Decimal·aware datetime·date·bytes)이 모든 표의 실제 행에서 왕복한다
- 오래된 것부터·표마다 순서대로 재적재, 연결 오류면 그 묶음에서 멈췄다 이어 간다, 데이터 오류는
  데드레터, 깨진 줄은 따로, 상한은 오래된 세그먼트부터 버리고 알린다, 다시 기동해도 이어 간다
- `PostgresSink(spool=...)`: 연결 오류 묶음을 스풀에 넣고 예외 없이 돌아온다, 백오프 동안 DB 를
  부르지 않는다, 복구 뒤 스풀 → 새 묶음 순서, health(시작·버림·데드레터·복구), 비밀 없음

실제 DB 는 쓰지 않는다(가짜 연결). 컨테이너를 멈췄다 켜는 시험은 tests/integration/test_spool_db.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from kbj.core.calendar import SessionInfo, State
from kbj.core.gex.records import ChainRecord, HealthEvent
from kbj.data.private.kis.master import parse_master_line
from kbj.services.recorder.envelope import RawEnvelope
from kbj.services.runtime.health import HealthEvent as AuthHealthEvent
from kbj.store.spool import (
    DiskSpool,
    SpoolError,
    decode_batch,
    encode_batch,
)
from tests.unit.gex_ingest import store_compat as store
from tests.unit.gex_ingest.fakes.pg import FakeDb

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 28, 10, 0, 2, 250000, tzinfo=KST)
DAY = date(2026, 9, 28)
TAG: store.Tag = (DAY, "day")


def _chain(strike: str = "1100.00", **kw: object) -> ChainRecord:
    base: dict[str, object] = {
        "ts": T0,
        "trade_date": DAY,
        "session": "day",
        "mrkt_cls": "WKM",
        "expiry": "260904",
        "strike": Decimal(strike),
        "cp": "C",
        "source": "board",
        "code": "BAFBZW001",
        "last": Decimal("12.35"),
        "oi": 6875,
        "iv_kis": Decimal("25.1234"),
        "gamma": Decimal("0.0081"),
    }
    base.update(kw)
    return ChainRecord.model_validate(base)


def _env(payload: str | dict[str, Any]) -> RawEnvelope:
    return RawEnvelope(
        received_at=T0,
        source="kis_rest",
        tr_id="FHPIF05030100",
        key="k",
        payload=payload,
        trade_date=DAY,
        session="day",
    )


def _bar(close: str) -> store.MinuteBarRecord:
    return store.MinuteBarRecord(
        ts=T0.replace(second=0, microsecond=0),
        trade_date=DAY,
        session="day",
        received_at=T0,
        code="A01612",
        market="F",
        open=Decimal("1096.00"),
        high=Decimal("1097.00"),
        low=Decimal("1095.50"),
        close=Decimal(close),
        volume=10,
        cum_value=1234,
    )


# ── 값 인코딩 ──


# KBJ P2: test_values_round_trip_with_their_type 는 kbj tests/unit/store/test_spool.py 로
# 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_kst_datetime_is_stored_as_the_same_instant_in_utc 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_naive_datetime_and_unknown_types_are_refused 는 kbj tests/unit/store/test_spool.py
# 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


def _all_table_rows() -> list[tuple[store.Table, store.Row]]:
    line = "5|B01610A51|KR4B016AA511|C 202610 1,125.0|1|01125.00| |2001|KOSPI200"
    master = parse_master_line(line)
    assert master is not None
    info = SessionInfo(State.DAY, DAY, "day")
    return [
        (store.CHAIN_SNAPSHOTS, store.attr_row(store.CHAIN_SNAPSHOTS, _chain())),
        (store.RAW_MESSAGES, store.raw_row(_env({"a": [1, 2.5, None], "b": "x"}))),
        (store.RAW_MESSAGES, store.raw_row(_env("0|H0IFCNT0|001|A01612^093000"))),
        (store.MINUTE_BARS, store.attr_row(store.MINUTE_BARS, _bar("1096.40"))),
        (store.MASTER_SNAPSHOTS, store.master_row(master, ts=T0, trade_date=DAY, session="day")),
        (
            store.SESSION_LOG,
            store.session_log_row(store.SessionLogRecord.transition(T0, info, State.PRE_DAY)),
        ),
        (
            store.HEALTH_EVENTS,
            store.health_row(
                HealthEvent(
                    ts=T0, trade_date=DAY, session="day", kind="k", level="info", message="m"
                )
            ),
        ),
    ]


def test_every_kind_of_table_row_round_trips_through_a_line() -> None:
    for table, row in _all_table_rows():
        line = encode_batch(table.name, table.columns, [row], TAG, T0)
        b = decode_batch(line)
        assert (b.table, b.columns, b.tag, b.at) == (table.name, table.columns, TAG, T0)
        assert b.rows == [row], table.name
        assert [type(v) for v in b.rows[0]] == [type(v) for v in row], table.name


# KBJ P2: test_malformed_lines_are_value_errors 는 kbj tests/unit/store/test_spool.py 로
# 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# ── 디스크 스풀 ──


def _rows(n: int, start: int = 0) -> list[store.Row]:
    return [(i, f"v{i}") for i in range(start, start + n)]


COLS = ("a", "b")


def _collect(sp: DiskSpool) -> list[tuple[str, list[store.Row]]]:
    got: list[tuple[str, list[store.Row]]] = []
    res = sp.replay(lambda b: got.append((b.table, b.rows)))
    assert res.complete
    return got


# KBJ P2: test_append_then_replay_in_order_and_files_are_removed 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_segments_rotate_and_replay_follows_creation_order 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_transient_error_stops_at_that_batch_and_resumes_there 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_deadline_stops_between_batches 는 kbj tests/unit/store/test_spool.py 로
# 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_data_errors_go_to_dead_letters_and_replay_continues 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_rejected_rows_alone_go_to_dead_letters_with_the_batch_tag 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_truncated_line_from_a_crash_is_set_aside_and_the_rest_replays 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_restart_keeps_order_old_segments_before_new 는 kbj tests/unit/store/test_spool.py 로
# 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_one_process_per_spool_directory 는 kbj tests/unit/store/test_spool.py 로
# 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_files_are_private 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj
# 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_cap_drops_oldest_segments_and_reports_what_was_lost 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_a_batch_bigger_than_the_cap_is_dropped_and_reported 는 kbj
# tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_bad_table_names_are_refused 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은
# 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# ── PostgresSink + 스풀 (가짜 DB) ──


@dataclass
class Mono:
    t: float = 0.0

    def __call__(self) -> float:
        return self.t


@dataclass
class Rig:
    db: FakeDb
    sink: store.GexIngestStore
    spool: DiskSpool
    mono: Mono
    health: list[AuthHealthEvent]


def _rig(tmp_path: Path, **kw: Any) -> Rig:
    db = FakeDb()
    mono = Mono()
    health: list[AuthHealthEvent] = []
    sp = kw.pop("spool", None) or DiskSpool(tmp_path / "spool", now=lambda: T0)
    sink = store.sink(
        connect=db,
        service="recorder",
        spool=sp,
        on_health=health.append,
        monotonic=mono,
        now=lambda: T0,
        **kw,
    )
    return Rig(db, sink, sp, mono, health)


def test_flush_spool_drains_without_new_writes(tmp_path: Path) -> None:
    r = _rig(tmp_path)
    r.db.down = True
    r.sink.write_raw([_env({"a": 1})])
    assert r.sink.flush_spool() is False  # 백오프 안
    r.db.down = False
    r.mono.t = 5.0
    assert r.sink.flush_spool() is True
    assert r.db.tables().count("raw_messages") == 1


def test_upserted_bars_keep_the_newest_value_after_replay(tmp_path: Path) -> None:
    """DO UPDATE 표: 스풀에 옛 값이 남은 동안 새 값도 스풀 뒤에 — 재적재가 새 값을 덮지 않는다."""
    r = _rig(tmp_path)
    r.db.down = True
    r.sink.write_minute_bars([_bar("1096.40")])
    r.db.down = False
    r.sink.write_minute_bars([_bar("1096.90")])  # 백오프 안 — 스풀 뒤로
    r.mono.t = 2.0
    assert r.sink.flush_spool()
    closes = [row[9] for t, row in r.db.rows if t == "minute_bars"]
    assert closes == [Decimal("1096.40"), Decimal("1096.90")]  # 마지막이 최신 → DB 에 최신


def test_replay_budget_bounds_one_call(tmp_path: Path) -> None:
    r = _rig(tmp_path, replay_budget_s=0.5)
    r.db.down = True
    for i in range(3):
        r.sink.write_raw([_env({"i": i})])
    r.db.down = False
    r.mono.t = 10.0
    orig = r.sink._send

    def slow_send(*a: Any, **k: Any) -> None:
        r.mono.t += 0.3
        orig(*a, **k)

    r.sink._send = slow_send  # type: ignore[method-assign]
    assert r.sink.flush_spool() is False and r.spool.pending  # 0.5초 안에 다 못 한다
    assert r.sink.flush_spool() is True


def test_spool_failure_is_a_store_error_and_a_health_event(tmp_path: Path) -> None:
    r = _rig(tmp_path)
    r.db.down = True

    def broken(*_: object) -> Any:
        raise SpoolError("스풀 쓰기 실패: OSError")

    r.spool.append = broken  # type: ignore[method-assign]
    with pytest.raises(store.StoreError, match="DB·스풀 모두 실패"):
        r.sink.write_chain([_chain()])
    assert "spool_write_failed" in [e.kind for e in r.health]


def test_data_errors_still_raise_and_are_not_spooled(tmp_path: Path) -> None:
    r = _rig(tmp_path)
    r.db.reject = {"chain_snapshots"}
    with pytest.raises(store.StoreError, match="CheckViolation"):
        r.sink.write_chain([_chain()])
    assert not r.spool.pending and not r.sink.degraded


def _raw_env(i: int, tr_id: str = "H0IFCNT0") -> RawEnvelope:
    return RawEnvelope(
        received_at=T0 + timedelta(seconds=i),
        source="kis_ws",
        tr_id=tr_id,
        key="A01612",
        payload=f"0|{tr_id}|001|A01612^{i:06d}",
        trade_date=DAY,
        session="day",
    )


def _is_bad(table: str, row: tuple[Any, ...]) -> bool:
    return table == "raw_messages" and row[4] == "BAD"
