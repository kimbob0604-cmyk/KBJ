"""DB 쓰기 실패 로컬 디스크 큐 (docs/phase1_design.md §2 `db` 장애 시 — data/spool.py·store.py).

- 값 인코딩(Decimal·aware datetime·date·bytes)이 모든 표의 실제 행에서 왕복한다
- 오래된 것부터·표마다 순서대로 재적재, 연결 오류면 그 묶음에서 멈췄다 이어 간다, 데이터 오류는
  데드레터, 깨진 줄은 따로, 상한은 오래된 세그먼트부터 버리고 알린다, 다시 기동해도 이어 간다
- `PostgresSink(spool=...)`: 연결 오류 묶음을 스풀에 넣고 예외 없이 돌아온다, 백오프 동안 DB 를
  부르지 않는다, 복구 뒤 스풀 → 새 묶음 순서, health(시작·버림·데드레터·복구), 비밀 없음

실제 DB 는 쓰지 않는다(가짜 연결). 컨테이너를 멈췄다 켜는 시험은 tests/integration/test_spool_db.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

from core.calendar import SessionInfo, State
from data import store
from data.kis.master import parse_master_line
from data.spool import (
    DEAD,
    DiskSpool,
    SpoolError,
    decode_batch,
    encode_batch,
)
from services.auth.health import HealthEvent as AuthHealthEvent
from services.poller.records import ChainRecord, HealthEvent
from services.recorder.envelope import RawEnvelope
from tests.fakes.pg import FakeDb

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


# KBJ P2: test_values_round_trip_with_their_type 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_kst_datetime_is_stored_as_the_same_instant_in_utc 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_naive_datetime_and_unknown_types_are_refused 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


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


# KBJ P2: test_malformed_lines_are_value_errors 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# ── 디스크 스풀 ──


def _rows(n: int, start: int = 0) -> list[store.Row]:
    return [(i, f"v{i}") for i in range(start, start + n)]


COLS = ("a", "b")


def _collect(sp: DiskSpool) -> list[tuple[str, list[store.Row]]]:
    got: list[tuple[str, list[store.Row]]] = []
    res = sp.replay(lambda b: got.append((b.table, b.rows)))
    assert res.complete
    return got


# KBJ P2: test_append_then_replay_in_order_and_files_are_removed 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_segments_rotate_and_replay_follows_creation_order 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_transient_error_stops_at_that_batch_and_resumes_there 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_deadline_stops_between_batches 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_data_errors_go_to_dead_letters_and_replay_continues 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_rejected_rows_alone_go_to_dead_letters_with_the_batch_tag 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_truncated_line_from_a_crash_is_set_aside_and_the_rest_replays 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_restart_keeps_order_old_segments_before_new 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_one_process_per_spool_directory 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_files_are_private 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_cap_drops_oldest_segments_and_reports_what_was_lost 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_a_batch_bigger_than_the_cap_is_dropped_and_reported 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_bad_table_names_are_refused 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# ── PostgresSink + 스풀 (가짜 DB) ──


@dataclass
class Mono:
    t: float = 0.0

    def __call__(self) -> float:
        return self.t


@dataclass
class Rig:
    db: FakeDb
    sink: store.PostgresSink
    spool: DiskSpool
    mono: Mono
    health: list[AuthHealthEvent]


def _rig(tmp_path: Path, **kw: Any) -> Rig:
    db = FakeDb()
    mono = Mono()
    health: list[AuthHealthEvent] = []
    sp = kw.pop("spool", None) or DiskSpool(tmp_path / "spool", now=lambda: T0)
    sink = store.PostgresSink(
        connect=db,
        service="recorder",
        spool=sp,
        on_health=health.append,
        monotonic=mono,
        now=lambda: T0,
        **kw,
    )
    return Rig(db, sink, sp, mono, health)


def test_connection_failure_goes_to_the_spool_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    r = _rig(tmp_path)
    r.db.down = True
    with caplog.at_level(logging.INFO, logger="data.store"):
        r.sink.write_chain([_chain()])
    assert r.spool.pending and r.sink.degraded
    s = r.sink.stats
    # 묶음 + 스풀 시작 알림(health_events 행 — DB 가 죽어 있어 그것도 스풀로)
    assert (s.spooled_batches, s.spooled_rows, s.failures, s.batches) == (2, 2, 1, 0)
    assert [e.kind for e in r.health] == ["db_spooling"]
    assert r.health[0].service == "recorder" and r.health[0].severity == "warning"
    events = [json.loads(x.getMessage())["event"] for x in caplog.records]
    assert "db_write_failed" in events and "db_spooling" in events and "db_spooled" in events
    logs = [json.loads(x.getMessage()) for x in caplog.records]
    spooled = next(x for x in logs if x["event"] == "db_spooled")
    assert (spooled["trade_date"], spooled["session"]) == ("2026-09-28", "day")


def test_backoff_skips_the_database_then_replays_in_order(tmp_path: Path) -> None:
    r = _rig(tmp_path, retries=0)
    r.db.down = True
    r.sink.write_chain([_chain("1100.00")])
    tries = r.db.connects
    r.sink.write_chain([_chain("1102.50")])  # 백오프 1초 안 — DB 를 부르지 않는다
    assert r.db.connects == tries
    r.db.down = False
    r.sink.write_chain([_chain("1105.00")])  # 아직 백오프 안 — 스풀 뒤에 붙는다
    assert r.db.rows == [] and r.db.connects == tries
    r.mono.t = 1.5
    r.sink.write_chain([_chain("1107.50")])  # 백오프가 지났다: 스풀 → 이 묶음
    strikes = [row[5] for t, row in r.db.rows if t == "chain_snapshots"]
    assert strikes == [Decimal(x) for x in ("1100.00", "1102.50", "1105.00", "1107.50")]
    assert not r.spool.pending and not r.sink.degraded
    kinds = [row[4] for t, row in r.db.rows if t == "health_events"]
    assert kinds == ["db_spooling", "db_spool_replayed"]  # 시작 알림은 스풀로 갔다가 들어간다
    assert [e.kind for e in r.health] == ["db_spooling", "db_spool_replayed"]
    s = r.sink.stats
    assert (s.spooled_batches, s.replayed_batches) == (4, 4)  # 알림 행 포함


def test_backoff_doubles_up_to_the_maximum(tmp_path: Path) -> None:
    r = _rig(tmp_path, retries=0, backoff_s=(1.0, 4.0))
    r.db.down = True
    waits: list[float] = []
    for _ in range(5):
        r.sink.write_raw([_env({"i": len(waits)})])
        assert r.sink._down_until is not None
        waits.append(r.sink._down_until - r.mono.t)
        r.mono.t = r.sink._down_until
    assert waits == [1.0, 2.0, 4.0, 4.0, 4.0]


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


def test_dead_letters_and_drops_are_health_events(tmp_path: Path) -> None:
    r = _rig(tmp_path)
    r.db.down = True
    r.sink.write_chain([_chain()])
    r.sink.write_raw([_env({"a": 1})])
    r.db.down = False
    r.db.reject = {"chain_snapshots"}
    r.mono.t = 2.0
    assert r.sink.flush_spool()
    assert r.db.tables().count("raw_messages") == 1 and "chain_snapshots" not in r.db.tables()
    assert r.sink.stats.dead_rows == 1
    assert (tmp_path / "spool" / DEAD / "chain_snapshots.jsonl").exists()
    kinds = [e.kind for e in r.health]
    assert "spool_dead_letter" in kinds
    ev = next(e for e in r.health if e.kind == "spool_dead_letter")
    assert ev.severity == "critical" and "chain_snapshots" in ev.detail


def test_cap_overflow_is_reported_and_counted(tmp_path: Path) -> None:
    row = store.raw_row(_env({"a": "x" * 400}))
    big = len(encode_batch("raw_messages", store.RAW_MESSAGES.columns, [row], TAG, T0))
    sp = DiskSpool(tmp_path / "spool", max_bytes=big * 3, segment_bytes=1, now=lambda: T0)
    r = _rig(tmp_path, spool=sp)
    r.db.down = True
    for _ in range(4):
        r.sink.write_raw([_env({"a": "x" * 400})])
    drops = [e for e in r.health if e.kind == "spool_dropped"]
    assert drops and all(e.severity == "critical" for e in drops)
    assert "버렸다" in drops[0].detail and r.sink.stats.dropped_rows >= 1


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


def test_spool_never_holds_the_database_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pw = "s3cr@t/pw-spool"
    dsn = f"postgresql://gex:{quote(pw, safe='')}@db.invalid:5432/gexlab"
    db = FakeDb(down=True, error=f"connection to {dsn} failed for password {pw}")
    monkeypatch.setattr(store, "_connect_dsn", lambda *_: db())
    health: list[AuthHealthEvent] = []
    sp = DiskSpool(tmp_path / "spool")
    s = store.PostgresSink(dsn, service="poller", spool=sp, on_health=health.append)
    with caplog.at_level(logging.DEBUG, logger="data.store"):
        s.write_raw([_env({"a": 1})])
    disk = b"".join(p.read_bytes() for p in (tmp_path / "spool").rglob("*.jsonl"))
    text = disk.decode() + "".join(x.getMessage() for x in caplog.records)
    text += "".join(e.detail for e in health)
    assert disk and pw not in text and quote(pw, safe="") not in text and "db.invalid" not in text


def test_from_settings_puts_the_spool_under_the_service_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://gex:pw@127.0.0.1:1/gexlab")
    monkeypatch.setenv("SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("SPOOL_MAX_MB", "3")

    def tagger(_: datetime) -> tuple[date | None, str | None]:
        return None, None

    s = store.PostgresSink.from_settings(service="poller", spool=True, tagger=tagger)
    try:
        assert s.spool is not None and s.spool.directory == tmp_path / "spool" / "poller"
        assert s.spool.max_bytes == 3 << 20
        assert os.path.isdir(tmp_path / "spool" / "poller")
        assert s._tagger is tagger
    finally:
        s.close()


# KBJ P2: auth(토큰 발급)는 KBJ 서비스로 옮겼다(ADR 0004) — services/auth/service.py 에 진입점이
# 없어 "auth" 경우를 뺐다(KBJ auth 의 health 싱크는 kbj 쪽 시험이 본다)
@pytest.mark.parametrize("service", ["poller", "recorder", "scheduler", "ws_gateway"])
def test_every_service_gives_its_sink_the_calendar_tagger(service: str) -> None:
    """서비스 진입점(main — compose 에서만 돈다)이 싱크에 tagger 를 넘긴다 (소스로 확인)."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "services" / service / "service.py").read_text(encoding="utf-8")
    calls = re.findall(r"PostgresSink\.from_settings\(([^)]*)\)", src)
    assert calls and all("tagger=" in c for c in calls), service


def test_on_health_may_write_back_into_the_same_sink(tmp_path: Path) -> None:
    """on_health 는 잠금 밖에서 불린다 — 서비스 health 싱크가 같은 싱크에 써도 멈추지 않는다."""
    db = FakeDb(down=True)
    mono = Mono()
    seen: list[str] = []
    sink: store.PostgresSink

    def back(ev: AuthHealthEvent) -> None:
        seen.append(ev.kind)
        sink.write_health([ev])

    sink = store.PostgresSink(
        connect=db,
        service="scheduler",
        spool=DiskSpool(tmp_path / "spool"),
        on_health=back,
        monotonic=mono,
    )
    sink.write_chain([_chain()])
    assert seen == ["db_spooling"] and sink.spool is not None and sink.spool.pending
    db.down = False
    mono.t = 5.0
    assert sink.flush_spool()
    assert seen == ["db_spooling", "db_spool_replayed"]
    assert db.of("chain_snapshots") and len(db.of("health_events")) >= 3


# KBJ P2: test_a_failed_append_leaves_no_half_line_and_the_next_batch_is_intact 는 kbj tests/unit/store/test_spool.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# ── 재적재 중 한 행의 데이터 오류 ──


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


def test_one_bad_row_in_a_spooled_batch_does_not_take_the_good_rows_with_it(
    tmp_path: Path,
) -> None:
    """DB 가 정상일 때는 호출자(recorder·ws-gateway)가 한 건씩 다시 써서 살린다 — 스풀을 거쳐도
    같아야 한다: 거부된 행만 데드레터, 나머지는 DB 로."""
    r = _rig(tmp_path)
    r.db.reject_if = _is_bad
    r.db.down = True
    r.sink.write_raw([_raw_env(i) for i in range(499)] + [_raw_env(999, "BAD")])
    assert r.spool.pending
    r.db.down = False
    r.mono.t = 5.0
    assert r.sink.flush_spool()
    rows = r.db.of("raw_messages")
    assert len(rows) == 499 and all(row[4] == "H0IFCNT0" for row in rows)
    dead = decode_batch((tmp_path / "spool" / DEAD / "raw_messages.jsonl").read_bytes())
    assert [row[4] for row in dead.rows] == ["BAD"] and dead.tag == TAG
    s = r.sink.stats
    assert (s.dead_rows, s.replayed_rows) == (1, 499 + 1)  # 스풀 시작 알림 행 포함
    ev = next(e for e in r.health if e.kind == "spool_dead_letter")
    assert "raw_messages: 1/500행" in ev.detail and "CheckViolation" in ev.detail
    assert not r.sink.degraded


def test_connection_loss_while_salvaging_keeps_the_whole_batch_for_later(
    tmp_path: Path,
) -> None:
    """한 행씩 다시 보내다 연결이 끊기면 묶음째 스풀에 남긴다(데드레터 아님) — 다시 보내도
    유니크 키로 멱등이라 먼저 들어간 행이 겹치지 않는다(가짜 DB 는 겹쳐 남기니 집합으로 본다)."""
    r = _rig(tmp_path)
    r.db.reject_if = _is_bad
    r.db.down = True
    r.sink.write_raw([_raw_env(i) for i in range(4)] + [_raw_env(999, "BAD")])
    r.db.down = False
    r.mono.t = 5.0
    orig = r.sink._send
    calls = [0]

    def flaky(table: store.Table, rows: Any, tag: store.Tag) -> None:
        if table is store.RAW_MESSAGES:
            calls[0] += 1
            if calls[0] == 3:  # 묶음 → 1행 → (여기서 끊김)
                r.db.down = True
        orig(table, rows, tag)

    r.sink._send = flaky  # type: ignore[method-assign]
    assert r.sink.flush_spool() is False and r.spool.pending
    assert not (tmp_path / "spool" / DEAD).exists() and r.sink.stats.dead_rows == 0
    r.db.down = False
    r.mono.t = 60.0
    assert r.sink.flush_spool()
    got = {row[9] for row in r.db.of("raw_messages")}  # digest
    assert len(got) == 4 and r.sink.stats.dead_rows == 1


# ── 다시 기동: 이전 실행이 남긴 스풀 ──


def test_a_backlog_left_by_the_previous_run_is_degraded_and_its_drain_is_reported(
    tmp_path: Path,
) -> None:
    first = _rig(tmp_path)
    first.db.down = True
    first.sink.write_raw([_env({"a": 1})])
    first.sink.close()  # 스풀을 남기고 끝난 프로세스

    db = FakeDb()
    health: list[AuthHealthEvent] = []
    sink = store.PostgresSink(
        connect=db,
        service="recorder",
        spool=DiskSpool(tmp_path / "spool", now=lambda: T0),
        on_health=health.append,
        monotonic=Mono(),
        now=lambda: T0,
    )
    assert sink.degraded and db.connects == 0  # 만들 때 DB 를 부르지 않는다
    assert sink.flush_spool()
    assert not sink.degraded
    assert [e.kind for e in health] == ["db_spool_pending_at_start", "db_spool_replayed"]
    assert "이전 실행이 남긴 스풀" in health[0].detail and health[0].severity == "warning"
    kinds = [row[4] for t, row in db.rows if t == "health_events"]
    assert kinds == ["db_spooling", "db_spool_pending_at_start", "db_spool_replayed"]
    assert len(db.of("raw_messages")) == 1


# ── 싱크 자신의 health 행도 거래일·세션 ──


def test_the_sinks_own_health_rows_are_tagged_by_the_tagger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """재적재 끝·데드레터·이전 실행 스풀 알림은 묶음 태그가 없다 — tagger(시각)로 붙인다."""
    seen: list[datetime] = []

    def tagger(t: datetime) -> tuple[date | None, str | None]:
        seen.append(t)
        return DAY, "day"

    first = _rig(tmp_path)
    first.db.down = True
    first.sink.write_raw([_env({"a": 1})])
    first.sink.close()
    r = _rig(tmp_path, spool=DiskSpool(tmp_path / "spool", now=lambda: T0), tagger=tagger)
    r.db.reject = {"raw_messages"}
    with caplog.at_level(logging.INFO, logger="data.store"):
        assert r.sink.flush_spool()
    tags = {row[4]: (row[1], row[2]) for t, row in r.db.rows if t == "health_events"}
    for kind in ("db_spool_pending_at_start", "spool_dead_letter", "db_spool_replayed"):
        assert tags[kind] == (DAY, "day"), kind
    logs = {x["event"]: x for x in map(json.loads, (m.getMessage() for m in caplog.records))}
    for event in ("spool_dead_letter", "db_spool_replay", "db_spool_replayed"):
        assert (logs[event]["trade_date"], logs[event]["session"]) == ("2026-09-28", "day")
    assert seen and all(t == T0 for t in seen)


def test_a_failing_tagger_leaves_the_health_row_untagged_but_written(tmp_path: Path) -> None:
    def broken(_: datetime) -> tuple[date | None, str | None]:
        raise RuntimeError("calendar")

    r = _rig(tmp_path, tagger=broken)
    r.db.down = True
    r.sink.write_raw([_env({"a": 1})])
    r.db.down = False
    r.mono.t = 5.0
    assert r.sink.flush_spool()
    tags = {row[4]: (row[1], row[2]) for t, row in r.db.rows if t == "health_events"}
    assert tags["db_spooling"] == (DAY, "day")  # 묶음 태그는 그대로
    assert tags["db_spool_replayed"] == (None, None)
