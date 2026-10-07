"""저장 스키마·SQL — DB 없이 확인할 수 있는 것 (PLAN §4.5, docs/phase1_design.md §8).

- `db/migrations/*.sql` 의 표 정의(공통 열·hypertable·유니크 키)
- `db.migrate` 의 파일 찾기·적용 대상 고르기(체크섬)
- `data.store` 표 상수 ↔ 마이그레이션, 레코드 → 행 튜플, SQL 조립, 싱크의 트랜잭션·재연결·가림
  (가짜 연결 — 실제 DB 는 tests/integration/test_store.py)
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import psycopg
import psycopg.errors
import pytest
from psycopg import sql
from pydantic import BaseModel, ValidationError

from config.settings import Settings
from core.calendar import State, TradingCalendar, state_at
from data import store
from data.kis.master import parse_master
from data.kis.models import MinuteBar
from data.kis.ws import FuturesTick, OptionTick, WsData, load_fields, parse_frame, ticks_from
from data.krx.models import KrxOptionDaily, parse_futures_rows, parse_option_rows
from data.spool import DiskSpool
from db import migrate as db_migrate
from db.migrate import MIGRATIONS_DIR, Migration, MigrationError, discover, pending
from services.auth.health import HealthEvent as AuthHealthEvent
from services.poller.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
)
from services.poller.sink import Sink
from services.recorder.envelope import RawEnvelope

INIT_SQL = (MIGRATIONS_DIR / "001_init.sql").read_text(encoding="utf-8")
SCHEMA_SQL = "\n".join(m.sql for m in discover())  # 001 + 뒤 번호 파일 (002_collection_reports …)

_CREATE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.S)
# 뒤 번호 파일이 적용된 표에 더한 열 (004_flags — levels·oi_changes.flag)
_ADD_COLUMN = re.compile(r"^ALTER TABLE (\w+) ADD COLUMN (\w+) (.*?);", re.S | re.M)
_CONSTRAINT = ("CHECK", "UNIQUE", "PRIMARY", "CONSTRAINT", "FOREIGN")


def _no_comments(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def schema_tables(text: str = SCHEMA_SQL) -> dict[str, dict[str, str]]:
    """CREATE TABLE 블록 + ALTER TABLE … ADD COLUMN → {표: {열: 정의}} (제약 줄은 '#제약N' 키로,
    더한 열의 정의는 공백을 한 칸으로)."""
    out: dict[str, dict[str, str]] = {}
    for name, body in _CREATE.findall(text):
        cols: dict[str, str] = {}
        for raw in body.splitlines():
            line = raw.split("--", 1)[0].strip().rstrip(",")
            if not line:
                continue
            if line.startswith(_CONSTRAINT):
                cols[f"#제약{len(cols)}"] = line
            else:
                col, _, rest = line.partition(" ")
                cols[col] = rest.strip()
        out[name] = cols
    for name, col, rest in _ADD_COLUMN.findall(_no_comments(text)):
        out[name][col] = " ".join(rest.split())
    return out


def unique_keys(cols: dict[str, str]) -> list[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for k, v in cols.items():
        m = re.fullmatch(r"UNIQUE \(([^)]*)\)", v) if k.startswith("#") else None
        if m:
            keys.append(tuple(c.strip() for c in m.group(1).split(",")))
    return keys


def hypertables(text: str = SCHEMA_SQL) -> dict[str, tuple[str, str]]:
    found = re.findall(
        r"create_hypertable\('(\w+)', by_range\('(\w+)', INTERVAL '([^']+)'\)\)", text
    )
    return {t: (col, iv) for t, col, iv in found}


TABLES = schema_tables()
MARKET = (
    "raw_messages",
    "fut_ticks",
    "opt_ticks",
    "chain_snapshots",
    "fut_board",
    "investor_flow",
    "series_expiries",
    "minute_bars",
    "krx_fut_daily",
    "krx_opt_daily",
    "master_snapshots",
)
ENGINE = ("levels", "metrics", "strike_gex", "option_iv", "oi_changes")


def test_design_tables_exist() -> None:
    # 설계 §8 + PLAN §4.5 (series_expiries 는 poller Sink.write_expiries 몫)
    assert set(TABLES) == {
        *MARKET,
        "session_log",
        "health_events",
        "collection_gaps",
        "quarantine",
        "collection_reports",  # 002 — 무결측 판정 리포트 (설계 §9)
        *ENGINE,  # 003 — engine 산출 (Phase 3 설계 §2)
    }


# 일 단위 자료(하루 한 번 적재)는 청크를 1년으로 — 1일 청크면 표마다 청크가 거래일 수만큼 생긴다
# (2026-09-29 사용자 결정). 나머지는 1일
YEARLY_CHUNK = ("krx_fut_daily", "krx_opt_daily", "master_snapshots")


def test_every_table_is_a_hypertable_on_a_key_column_with_its_chunk() -> None:
    ht = hypertables()
    assert set(ht) == set(TABLES)
    for table, (col, interval) in ht.items():
        assert interval == ("365 days" if table in YEARLY_CHUNK else "1 day"), table
        # 유니크 키마다 파티션 열이 들어 있어야 hypertable 이 받는다
        keys = unique_keys(TABLES[table])
        assert keys, f"{table}: 재적재 멱등용 유니크 키가 없다"
        assert all(col in k for k in keys), table


@pytest.mark.parametrize("table", [*MARKET, *ENGINE])
def test_market_tables_have_utc_ts_trade_date_and_session(table: str) -> None:
    cols = TABLES[table]
    assert cols["ts"].startswith("timestamptz NOT NULL")
    assert cols["trade_date"].startswith("date")
    assert "CHECK (session IN ('day', 'night'))" in cols["session"]
    assert not any("timestamp " in v or v == "timestamp" for v in cols.values())  # naive 금지


def test_ticks_and_chain_columns_per_design() -> None:
    need_tick = {"code", "seq", "price", "qty", "cum_vol", "cum_buy_qty", "cum_sell_qty", "oi"}
    assert need_tick | {"oi_chg"} <= set(TABLES["fut_ticks"])
    greeks = {"delta", "gamma", "vega", "theta", "iv", "expiry", "strike", "cp"}
    assert need_tick | greeks <= set(TABLES["opt_ticks"])
    assert unique_keys(TABLES["fut_ticks"]) == [("ts", "code", "seq")]
    assert unique_keys(TABLES["opt_ticks"]) == [("ts", "code", "seq")]
    chain = TABLES["chain_snapshots"]
    assert "CHECK (source IN ('board', 'fill'))" in chain["source"]
    assert "CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid'))" in chain["quality"]
    # 설계 키 (ts, expiry, strike, cp, source) + mrkt_cls: WKM·WKI 261001 이 같은 6자리 만기다
    assert unique_keys(chain) == [("ts", "mrkt_cls", "expiry", "strike", "cp", "source")]


def test_prices_are_numeric_and_strikes_numeric_8_2() -> None:
    for table, cols in TABLES.items():
        if "strike" in cols:
            assert cols["strike"].startswith("numeric(8, 2)"), table
        for c in ("price", "last", "bid", "ask", "open", "high", "low", "close", "setl_prc"):
            if c in cols:
                assert cols[c].startswith("numeric"), (table, c)


def test_krx_keys_and_night_iv_nullable() -> None:
    for t in ("krx_fut_daily", "krx_opt_daily"):
        assert unique_keys(TABLES[t]) == [("trade_date", "isu_cd", "session")]
    assert "NOT NULL" not in TABLES["krx_opt_daily"]["imp_volt"]  # 야간 '0.00' → NULL


def test_minute_bars_key_and_market() -> None:
    cols = TABLES["minute_bars"]
    assert unique_keys(cols) == [("code", "market", "ts")]
    assert "CHECK (market IN ('F', 'CM'))" in cols["market"]


def test_raw_messages_compressed_after_three_days_with_digest_in_orderby() -> None:
    assert "add_compression_policy('raw_messages', INTERVAL '3 days')" in INIT_SQL
    # 압축 청크에서도 ON CONFLICT 가 중복을 찾으려면 유니크 키 열이 orderby 에 있어야 한다
    assert "timescaledb.compress_orderby = 'ts, digest'" in INIT_SQL
    assert unique_keys(TABLES["raw_messages"]) == [("ts", "digest")]
    assert "TODO" in INIT_SQL and "Parquet" in INIT_SQL  # 30일 이동은 범위 밖


# ── db.migrate ──


def _m(version: str, body: str = "SELECT 1;") -> Migration:
    return Migration(version, f"{version}_x.sql", body)


def test_discover_orders_by_number(tmp_path: Path) -> None:
    for name in ("010_later.sql", "002_second.sql", "001_first.sql"):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    assert [m.name for m in discover(tmp_path)] == [
        "001_first.sql",
        "002_second.sql",
        "010_later.sql",
    ]


@pytest.mark.parametrize("names", [["001_a.sql", "1_b.sql"], ["001_a.sql", "001_b.sql"]])
def test_discover_rejects_bad_names_and_duplicate_numbers(tmp_path: Path, names: list[str]) -> None:
    for name in names:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover(tmp_path)


def test_repo_migrations_start_with_001_init() -> None:
    ms = discover()
    assert ms[0].name == "001_init.sql" and ms[0].version == "001"
    assert "CREATE EXTENSION IF NOT EXISTS timescaledb" in ms[0].sql


def test_pending_skips_applied_and_keeps_order() -> None:
    ms = [_m("001"), _m("002"), _m("003")]
    applied = {"001": ("001_x.sql", ms[0].checksum)}
    assert [m.version for m in pending(ms, applied)] == ["002", "003"]
    assert pending(ms, {m.version: (m.name, m.checksum) for m in ms}) == []


def test_pending_refuses_changed_or_missing_applied_files() -> None:
    ms = [_m("001"), _m("002")]
    with pytest.raises(MigrationError, match="바뀐"):
        pending(ms, {"001": ("001_x.sql", _m("001", "SELECT 2;").checksum)})
    with pytest.raises(MigrationError, match="파일이 없다"):
        pending(ms[1:], {"001": ("001_x.sql", ms[0].checksum)})


def test_collection_reports_one_row_per_session_stream() -> None:
    cols = TABLES["collection_reports"]
    assert unique_keys(cols) == [("trade_date", "session", "stream")]
    assert "CHECK (status IN ('ok', 'gaps', 'unverified'))" in cols["status"]
    assert cols["required"].startswith("boolean NOT NULL")
    assert "001_init" not in (MIGRATIONS_DIR / "002_collection_reports.sql").name


# ── data.store: 표 상수 ↔ 마이그레이션 ──

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 28, 10, 0, 2, 250000, tzinfo=KST)
DAY = date(2026, 9, 28)


def _schema_columns(table: str) -> set[str]:
    return {c for c in TABLES[table] if not c.startswith("#")}


@pytest.mark.parametrize("table", store.TABLES, ids=lambda t: t.name)
def test_table_constants_match_the_migration(table: store.Table) -> None:
    cols = TABLES[table.name]
    assert set(table.columns) == _schema_columns(table.name)
    assert table.key in unique_keys(cols)
    in_sql = {c for c in _schema_columns(table.name) if cols[c].startswith("jsonb")}
    assert in_sql == set(table.jsonb)  # jsonb 열은 %s::jsonb 로 보낸다


def test_insert_sql_is_built_from_constants_with_placeholders_only() -> None:
    q = store.INVESTOR_FLOW.insert_sql().as_string()
    assert q == (
        'INSERT INTO "investor_flow" ("ts", "trade_date", "session", "market_code", '
        '"sector_code", "investor", "sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", '
        '"net_value", "quality") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
        'ON CONFLICT ("ts", "market_code", "sector_code", "investor") DO NOTHING'
    )
    raw = store.RAW_MESSAGES.insert_sql().as_string()
    assert "%s::jsonb" in raw and raw.count("%s") == len(store.RAW_MESSAGES.columns)
    assert raw.endswith('ON CONFLICT ("ts", "digest") DO NOTHING')


def test_upsert_updates_every_non_key_column() -> None:
    t = store.Table("x_y", ("a", "b", "c"), key=("a",), upsert=True)
    assert t.update_columns == ("b", "c")
    assert (
        t.insert_sql()
        .as_string()
        .endswith('ON CONFLICT ("a") DO UPDATE SET "b" = EXCLUDED."b", "c" = EXCLUDED."c"')
    )


def test_upsert_can_keep_the_stored_row_when_the_new_row_lacks_a_column() -> None:
    """keep_if_null: 그 열이 NULL 인 새 행은 이미 있는 행을 고치지 않는다(정보가 적은 행)."""
    t = store.Table("x_y", ("a", "b", "c"), key=("a",), upsert=True, keep_if_null="b")
    assert (
        t.insert_sql()
        .as_string()
        .endswith(
            'ON CONFLICT ("a") DO UPDATE SET "b" = EXCLUDED."b", "c" = EXCLUDED."c" '
            'WHERE EXCLUDED."b" IS NOT NULL'
        )
    )
    for keep, upsert in (
        ("a", True),  # 키 열
        ("z", True),  # 열 밖
        ("b", False),  # DO NOTHING 표엔 뜻이 없다
    ):
        with pytest.raises(ValueError, match="keep_if_null"):
            store.Table("x_y", ("a", "b", "c"), key=("a",), upsert=upsert, keep_if_null=keep)


@pytest.mark.parametrize(
    ("name", "cols", "key"),
    [
        ("Bad", ("a",), ("a",)),  # 대문자
        ("t", ("a; drop",), ("a; drop",)),  # 식별자 아님
        ("t", ("a", "a"), ("a",)),  # 열 중복
        ("t", ("a",), ("b",)),  # 키가 열 밖
        ("t", ("a",), ()),  # 키 없음
    ],
)
def test_table_rejects_non_constant_identifiers(
    name: str, cols: tuple[str, ...], key: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        store.Table(name, cols, key=key)


@pytest.mark.parametrize(
    ("model", "table"),
    [
        (ChainRecord, store.CHAIN_SNAPSHOTS),
        (FuturesRecord, store.FUT_BOARD),
        (InvestorRecord, store.INVESTOR_FLOW),
        (ExpiryRecord, store.SERIES_EXPIRIES),
    ],
)
def test_poller_record_fields_are_exactly_the_table_columns(
    model: type[BaseModel], table: store.Table
) -> None:
    # 레코드에 필드가 늘면 표에도 열을 더해야 한다(조용히 버리지 않는다)
    assert set(model.model_fields) == set(table.columns)


# ── data.store: 레코드 → 행 ──


def _chain(**kw: object) -> ChainRecord:
    base: dict[str, object] = {
        "ts": T0,
        "trade_date": DAY,
        "session": "day",
        "mrkt_cls": "WKM",
        "expiry": "260904",
        "strike": Decimal("1100.00"),
        "cp": "C",
        "source": "board",
        "code": "BAFBZW...",
        "last": Decimal("12.35"),
        "bid": Decimal("12.30"),
        "ask": Decimal("12.40"),
        "oi": 6875,
        "oi_chg": 341,
        "volume": 2707,
        "iv_kis": Decimal("25.1234"),
        "delta": Decimal("0.5123"),
        "gamma": Decimal("0.0081"),
        "theta": Decimal("-1.2"),
        "vega": Decimal("0.9"),
        "rho": Decimal("0.01"),
    }
    base.update(kw)
    return ChainRecord.model_validate(base)


def test_chain_row_is_utc_and_in_column_order() -> None:
    row = store.attr_row(store.CHAIN_SNAPSHOTS, _chain())
    d = dict(zip(store.CHAIN_SNAPSHOTS.columns, row, strict=True))
    assert d["ts"] == T0 and d["ts"].utcoffset() == timedelta(0)  # 10:00 KST → 01:00 UTC
    assert d["ts"].hour == 1
    assert (d["trade_date"], d["session"], d["mrkt_cls"], d["expiry"]) == (
        DAY,
        "day",
        "WKM",
        "260904",
    )
    assert d["strike"] == Decimal("1100.00") and d["gamma"] == Decimal("0.0081")
    assert d["quality"] == "ok" and d["source"] == "board"


def test_fill_row_keeps_missing_quotes_as_null() -> None:
    row = store.attr_row(store.CHAIN_SNAPSHOTS, _chain(source="fill", bid=None, ask=None))
    d = dict(zip(store.CHAIN_SNAPSHOTS.columns, row, strict=True))
    assert d["bid"] is None and d["ask"] is None and d["source"] == "fill"


def _env(payload: str | dict[str, Any], **kw: Any) -> RawEnvelope:
    base: dict[str, Any] = {
        "received_at": T0,
        "source": "kis_rest",
        "tr_id": "FHPIF05030100",
        "key": "board:WKM:260904",
        "payload": payload,
        "trade_date": DAY,
        "session": "day",
    }
    base.update(kw)
    return RawEnvelope.model_validate(base)


def test_raw_row_dict_payload_is_canonical_json_with_stable_digest() -> None:
    a = store.raw_row(_env({"rt_cd": "0", "output1": [{"b": "1", "a": 2}]}))
    b = store.raw_row(_env({"output1": [{"a": 2, "b": "1"}], "rt_cd": "0"}))
    d = dict(zip(store.RAW_MESSAGES.columns, a, strict=True))
    assert d["payload"] == '{"output1":[{"a":2,"b":"1"}],"rt_cd":"0"}'
    assert d["payload_text"] is None and d["lossy"] is False
    assert d["ts"] == T0 and (d["trade_date"], d["session"]) == (DAY, "day")
    assert a == b  # 키 순서가 달라도 같은 행·같은 digest
    assert len(d["digest"]) == 32


def test_raw_row_text_payload_goes_to_payload_text() -> None:
    frame = "0|H0IFCNT0|001|A01612^142701^..."
    d = dict(
        zip(store.RAW_MESSAGES.columns, store.raw_row(_env(frame, source="kis_ws")), strict=True)
    )
    assert d["payload"] is None and d["payload_text"] == frame and d["source"] == "kis_ws"
    # 같은 글자라도 문자열 원문과 JSON 원문은 digest 가 다르다
    as_json = store.raw_row(_env({"x": frame}))[-1]
    assert d["digest"] != as_json


@pytest.mark.parametrize("field", ["tr_id", "key", "source"])
def test_raw_digest_depends_on_identity_fields(field: str) -> None:
    other = {"tr_id": "X", "key": "other", "source": "krx"}[field]
    assert store.raw_row(_env({"a": 1}))[-1] != store.raw_row(_env({"a": 1}, **{field: other}))[-1]


def test_raw_row_replaces_what_postgres_cannot_store() -> None:
    d = dict(
        zip(store.RAW_MESSAGES.columns, store.raw_row(_env("a\x00b", source="kis_ws")), strict=True)
    )
    assert d["payload_text"] == "a\ufffdb" and d["lossy"] is True
    nan = store.raw_row(_env({"v": float("nan"), "k\x00": [float("inf")]}))
    d = dict(zip(store.RAW_MESSAGES.columns, nan, strict=True))
    assert d["payload"] == '{"k\ufffd":["inf"],"v":"nan"}' and d["lossy"] is True


DIRTY = "H0IF\x00CNT0\ud800"  # NUL(text 가 못 받음) + 짝 없는 서로게이트(UTF-8 로 못 씀)
CLEANED = "H0IF\ufffdCNT0?"


@pytest.mark.parametrize("field", ["tr_id", "key"])
def test_raw_row_cleans_identity_text_too_and_marks_lossy(field: str) -> None:
    """NUL 하나로 묶음 전체(최대 500건)가 거부되지 않게 — 원문 보존이 recorder 의 목적이다."""
    row = store.raw_row(_env("0|x", source="kis_ws", **{field: DIRTY}))
    d = dict(zip(store.RAW_MESSAGES.columns, row, strict=True))
    assert d[field] == CLEANED and d["lossy"] is True and d["payload_text"] == "0|x"
    # digest 는 넣는 값으로 — 표에 남은 값으로 다시 계산할 수 있다
    clean = store.raw_row(_env("0|x", source="kis_ws", **{field: CLEANED}))
    assert d["digest"] == clean[-1] and clean[-2] is False


def test_quarantine_row_cleans_tr_id_and_key() -> None:
    q = QuarantineRecord(
        ts=T0, trade_date=DAY, session="day", tr_id=DIRTY, key=DIRTY, payload={}, error="e"
    )
    d = dict(zip(store.QUARANTINE.columns, store.quarantine_row(q), strict=True))
    assert (d["tr_id"], d["key"], d["lossy"]) == (CLEANED, CLEANED, True)


def test_health_and_session_log_rows_clean_service_and_kind() -> None:
    ev = AuthHealthEvent(DIRTY, "d", T0, "critical", service=DIRTY)
    d = dict(zip(store.HEALTH_EVENTS.columns, store.health_row(ev), strict=True))
    assert (d["service"], d["kind"]) == (CLEANED, CLEANED)
    poller = HealthEvent(ts=T0, trade_date=DAY, session="day", kind=DIRTY, level="info", message="")
    assert store.health_row(poller)[4] == CLEANED
    s = store.SessionLogRecord(ts=T0, service=DIRTY, kind=DIRTY)
    d = dict(zip(store.SESSION_LOG.columns, store.session_log_row(s), strict=True))
    assert (d["service"], d["kind"]) == (CLEANED, CLEANED)


def test_raw_row_tag_is_both_or_neither() -> None:
    d = dict(zip(store.RAW_MESSAGES.columns, store.raw_row(_env({}, session=None)), strict=True))
    assert (d["trade_date"], d["session"]) == (None, None)


def test_quarantine_row() -> None:
    q = QuarantineRecord(
        ts=T0,
        trade_date=DAY,
        session="day",
        tr_id="FHPIF05030100",
        key="board:WKM:260904",
        payload={"hts_otst_stpl_qty": "x", "acpr": "1100.00"},
        error="이상치: oi",
    )
    d = dict(zip(store.QUARANTINE.columns, store.quarantine_row(q), strict=True))
    assert d["payload"] == '{"acpr":"1100.00","hts_otst_stpl_qty":"x"}'
    assert (d["source"], d["error"], d["lossy"]) == ("kis_rest", "이상치: oi", False)
    q2 = q.model_copy(update={"error": "다른 오류"})
    assert store.quarantine_row(q2)[-1] != d["digest"]


def test_health_row_from_poller_event() -> None:
    ev = HealthEvent(
        ts=T0,
        trade_date=DAY,
        session="day",
        kind="quarantine",
        level="warning",
        message="검증 실패 레코드를 격리했다",
        detail={"job": "board:WKM:260904", "rows": 2},
    )
    d = dict(zip(store.HEALTH_EVENTS.columns, store.health_row(ev), strict=True))
    assert (d["service"], d["kind"], d["level"]) == ("poller", "quarantine", "warning")
    assert d["detail"] == '{"job":"board:WKM:260904","rows":2}'
    assert (d["trade_date"], d["session"]) == (DAY, "day")


def test_health_row_from_auth_event_uses_tagger_and_survives_its_failure() -> None:
    ev = AuthHealthEvent("token_refresh_failed", "발급 실패 (EGW00133)", T0, "critical")
    d = dict(
        zip(
            store.HEALTH_EVENTS.columns,
            store.health_row(ev, lambda _: (DAY, "day")),
            strict=True,
        )
    )
    assert (d["service"], d["level"], d["message"]) == ("auth", "critical", "발급 실패 (EGW00133)")
    assert (d["trade_date"], d["session"], d["detail"]) == (DAY, "day", "{}")

    def boom(_: datetime) -> tuple[date | None, str | None]:
        raise RuntimeError("calendar")

    row = store.health_row(ev, boom)
    assert row[1:3] == (None, None)
    assert store.health_row(ev, lambda _: (DAY, "weird"))[1:3] == (None, None)


# ── data.store: PostgresSink (가짜 연결) ──


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, query: sql.Composed, rows: Sequence[tuple[Any, ...]]) -> None:
        if self.conn.failures:
            raise self.conn.failures.pop(0)
        self.conn.batches.append((query.as_string(), list(rows)))

    def execute(self, query: sql.Composed, params: tuple[Any, ...]) -> None:
        if self.conn.failures:
            raise self.conn.failures.pop(0)
        self.conn.reads.append((query.as_string(), params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.conn.results.pop(0) if self.conn.results else []


class FakeConn:
    def __init__(
        self, failures: list[Exception], results: list[list[tuple[Any, ...]]] | None = None
    ) -> None:
        self.failures = failures  # 연결끼리 나눠 쓰는 목록 — 다시 연결해도 이어진다
        self.results = results if results is not None else []  # 읽기 결과 — 연결끼리 나눠 쓴다
        self.batches: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.reads: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.broken = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self.rollbacks += 1
            raise
        self.commits += 1

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@dataclass
class Factory:
    failures: list[Exception] = field(default_factory=list[Exception])
    conns: list[FakeConn] = field(default_factory=list[FakeConn])
    results: list[list[tuple[Any, ...]]] = field(default_factory=list[list[tuple[Any, ...]]])

    def __call__(self) -> Any:
        if self.failures and isinstance(self.failures[0], ConnectFailure):
            raise psycopg.OperationalError(str(self.failures.pop(0)))
        c = FakeConn(self.failures, self.results)
        self.conns.append(c)
        return c


class ConnectFailure(Exception):
    """Factory 가 연결 자체를 실패하게 하는 표시."""


def _sink(f: Factory, **kw: Any) -> store.PostgresSink:
    return store.PostgresSink(connect=f, service="poller", **kw)


def test_postgres_sink_satisfies_the_poller_sink_protocol() -> None:
    sink: Sink = _sink(Factory())  # pyright 가 구조적으로 확인한다
    assert callable(sink.write_chain)


def test_one_transaction_per_batch_and_nothing_for_empty() -> None:
    f = Factory()
    s = _sink(f)
    s.write_chain([])
    s.write_raw([])
    assert f.conns == []  # 빈 묶음은 연결도 하지 않는다
    s.write_chain([_chain(), _chain(strike=Decimal("1102.50"))])
    s.write_raw([_env({"a": 1})])
    (c,) = f.conns
    assert c.commits == 2 and len(c.batches) == 2
    assert c.batches[0][0].startswith('INSERT INTO "chain_snapshots"')
    assert len(c.batches[0][1]) == 2
    assert s.stats.batches == 2 and s.stats.rows == 3


def test_reconnects_once_on_operational_error_then_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    f = Factory([psycopg.OperationalError("server closed the connection unexpectedly")])
    s = _sink(f)
    with caplog.at_level(logging.WARNING, logger="data.store"):
        s.write_chain([_chain()])
    assert len(f.conns) == 2 and f.conns[0].closed and f.conns[0].rollbacks == 1
    assert f.conns[1].commits == 1 and s.stats.reconnects == 1
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["event"] == "db_reconnect" and rec["service"] == "poller"
    assert (rec["trade_date"], rec["session"], rec["table"]) == (
        "2026-09-28",
        "day",
        "chain_snapshots",
    )


def test_persistent_connection_failure_raises_store_error_after_retries() -> None:
    f = Factory([ConnectFailure("down"), ConnectFailure("down"), ConnectFailure("down")])
    s = _sink(f, retries=2)
    with pytest.raises(store.StoreError, match="OperationalError"):
        s.write_chain([_chain()])
    assert f.failures == [] and s.stats.failures == 1 and s.stats.reconnects == 2


def test_data_errors_are_not_retried() -> None:
    f = Factory([psycopg.errors.CheckViolation("new row violates check constraint")])
    s = _sink(f)
    with pytest.raises(store.StoreError, match="CheckViolation"):
        s.write_chain([_chain()])
    assert len(f.conns) == 1 and f.conns[0].rollbacks == 1 and s.stats.reconnects == 0
    s.write_chain([_chain()])  # 연결은 그대로 쓴다
    assert len(f.conns) == 1 and f.conns[0].commits == 1


def test_secrets_never_reach_errors_logs_or_repr(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """접속한 뒤(쓰는 중) 오류 문구는 싣되 접속 정보를 가린다."""
    pw = "s3cr@t/pw"
    dsn = f"postgresql://gex:{quote(pw, safe='')}@db.invalid:5432/gexlab"
    leak = f"connection to {dsn} failed for password {pw}\nDETAIL: Failing row contains (secret)"
    f = Factory([psycopg.OperationalError(leak), psycopg.OperationalError(leak)])
    monkeypatch.setattr(store, "_connect_dsn", lambda *_: f())
    s = store.PostgresSink(dsn, service="recorder", retries=1)
    with (
        caplog.at_level(logging.WARNING, logger="data.store"),
        pytest.raises(store.StoreError) as e,
    ):
        s.write_raw([_env({"a": 1})])
    text = str(e.value) + "".join(r.getMessage() for r in caplog.records) + repr(s)
    assert pw not in text and quote(pw, safe="") not in text and "db.invalid" not in text
    assert "Failing row" not in text  # DETAIL 줄(행 값)은 싣지 않는다
    assert "***" in str(e.value) and len(f.conns) == 2
    assert e.value.__cause__ is None and e.value.__suppress_context__


def _poller_health() -> HealthEvent:
    return HealthEvent(ts=T0, trade_date=DAY, session="day", kind="x", level="info", message="m")


@pytest.mark.parametrize(
    ("dsn", "fragment"),
    [
        # URL 비밀번호의 '%' 를 인코딩하지 않음 → libpq: invalid percent-encoded token: "Hunter%zz…"
        ("postgresql://gex:Hunter%zz2secret@127.0.0.1:1/gexlab", "zz2secret"),
        # URL 비밀번호에 공백 → libpq: unexpected spaces found in "Hunter2 secretpart"
        ("postgresql://gex:Hunter2 secretpart@127.0.0.1:1/gexlab", "secretpart"),
        # 키=값 형식 비밀번호에 공백 → libpq: missing "=" after "secretpart"
        ("host=127.0.0.1 port=1 user=gex password=Hunter2 secretpart dbname=gexlab", "secretpart"),
    ],
)
def test_malformed_dsn_is_refused_without_quoting_it(
    dsn: str, fragment: str, caplog: pytest.LogCaptureFixture
) -> None:
    """해석 못 하는 접속 문자열은 만들 때 거부한다 — libpq 문구가 틀린 조각(흔히 비밀번호)을
    인용하기 때문이다."""
    with (
        caplog.at_level(logging.DEBUG, logger="data.store"),
        pytest.raises(store.StoreError, match="형식") as e,
    ):
        store.PostgresSink(dsn, service="recorder").write_health([_poller_health()])
    text = str(e.value) + "".join(r.getMessage() for r in caplog.records)
    assert "Hunter" not in text and fragment not in text
    assert e.value.__context__ is None and e.value.__cause__ is None  # 원래 예외를 매달지 않는다


def test_errors_while_connecting_carry_only_type_and_sqlstate(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """해석은 되지만 잘못 나뉜 접속 문자열 — 'p@ss…' 는 비밀번호 'p', 호스트 'ss…@db' 가 되어
    접속 오류가 비밀번호 뒷부분을 호스트로 인용한다. 접속 단계 오류는 종류·SQLSTATE 만 싣는다."""
    dsn = "postgresql://gex:p@ss-fragment@db.invalid:5432/gexlab"
    quoted = "failed to resolve host 'ss-fragment@db.invalid': nodename nor servname provided"

    def connect(*_: object) -> Any:
        raise psycopg.OperationalError(quoted)

    monkeypatch.setattr(store, "_connect_dsn", connect)
    s = store.PostgresSink(dsn, service="recorder", retries=1)
    with (
        caplog.at_level(logging.WARNING, logger="data.store"),
        pytest.raises(store.StoreError, match=r"^raw_messages: OperationalError: 접속 실패") as e,
    ):
        s.write_raw([_env({"a": 1})])
    text = str(e.value) + "".join(r.getMessage() for r in caplog.records)
    assert "ss-fragment" not in text and "db.invalid" not in text and "resolve" not in text
    events = [json.loads(r.getMessage())["event"] for r in caplog.records]
    assert events == ["db_reconnect", "db_write_failed"]

    class AuthFailed(psycopg.OperationalError):
        sqlstate = "28P01"  # 서버가 보낸 오류는 SQLSTATE 로 가른다 (비밀번호 틀림)

    def refused(*_: object) -> Any:
        raise AuthFailed('password authentication failed for user "gex"')

    monkeypatch.setattr(store, "_connect_dsn", refused)
    with pytest.raises(store.StoreError) as e2:
        store.PostgresSink(dsn, retries=0).write_raw([_env({"a": 1})])
    assert str(e2.value).startswith("raw_messages: AuthFailed(28P01): 접속 실패")
    assert "gex" not in str(e2.value)


def test_constructor_needs_exactly_one_of_dsn_or_connect() -> None:
    with pytest.raises(ValueError):
        store.PostgresSink()
    with pytest.raises(ValueError):
        store.PostgresSink("postgresql://x@h/db", connect=Factory())
    with pytest.raises(ValueError):
        store.PostgresSink(connect=Factory(), retries=-1)


# ── config.settings: DATABASE_URL 은 비밀 ──

SECRET_URL = "postgresql://gex:TopSecret@db.invalid:5432/gexlab"


def test_database_url_is_a_secret_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert "TopSecret" not in repr(s) and "TopSecret" not in str(s.model_dump())
    assert s.database_url is not None and s.database_url.get_secret_value() == SECRET_URL
    monkeypatch.setenv("DATABASE_URL", "  ")  # .env.example 을 복사만 한 빈 값
    assert Settings(_env_file=None).database_url is None  # pyright: ignore[reportCallIssue]


def test_sink_and_migrate_take_the_plain_url_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # 로컬 .env 를 읽지 않게
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)
    seen: list[str] = []

    def connect(dsn: str, *_: object) -> Any:
        seen.append(dsn)
        return FakeConn([])

    monkeypatch.setattr(store, "_connect_dsn", connect)
    s = store.PostgresSink.from_settings(service="poller")
    s.write_chain([_chain()])
    assert seen == [SECRET_URL] and "TopSecret" not in repr(s)

    def migrate(dsn: str, **_: object) -> list[str]:
        seen.append(dsn)
        return []

    monkeypatch.setattr(db_migrate, "migrate", migrate)
    assert db_migrate.main([]) == 0 and seen[-1] == SECRET_URL

    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(store.StoreError, match="없다"):
        store.PostgresSink.from_settings()
    assert db_migrate.main([]) == 2


# ── data.store: scheduler 적재 (분봉·KRX 일별·마스터) ──

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAL = TradingCalendar.default()


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _bar(date_s: str, hhmmss: str) -> MinuteBar:
    row = _fixture("kis/minute_day.json")["output2"][0]
    return MinuteBar.model_validate(row | {"stck_bsop_date": date_s, "stck_cntg_hour": hhmmss})


def test_minute_bar_record_fields_are_the_table_columns() -> None:
    assert set(store.MinuteBarRecord.model_fields) == set(store.MINUTE_BARS.columns)
    assert store.MINUTE_BARS.upsert and store.MINUTE_BARS.key == ("code", "market", "ts")


def test_day_minute_bar_from_fixture() -> None:
    raw = _fixture("kis/minute_day.json")["output2"][0]  # 20260928 140100
    rec = store.MinuteBarRecord.from_kis(
        MinuteBar.model_validate(raw), code="A01612", market="F", received_at=T0, calendar=CAL
    )
    d = dict(zip(store.MINUTE_BARS.columns, store.attr_row(store.MINUTE_BARS, rec), strict=True))
    assert d["ts"] == datetime(2026, 9, 28, 5, 1, tzinfo=UTC)  # 14:01 KST
    assert (d["trade_date"], d["session"], d["market"]) == (DAY, "day", "F")
    assert (d["open"], d["high"], d["low"], d["close"]) == (
        Decimal("1097.10"),
        Decimal("1098.25"),
        Decimal("1096.90"),
        Decimal("1098.05"),
    )
    assert (d["volume"], d["cum_value"]) == (347, 21592073175)
    assert d["received_at"].utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("date_s", "hhmmss", "ts_utc", "trade_date"),
    [
        # 설계 §8 예: 20260922 300000 → 2026-09-23 06:00 KST, 귀속 09-23
        ("20260922", "300000", datetime(2026, 9, 22, 21, 0, tzinfo=UTC), date(2026, 9, 23)),
        ("20260922", "181500", datetime(2026, 9, 22, 9, 15, tzinfo=UTC), date(2026, 9, 23)),
        # 금요일 밤 토 04:07 KST → 월요일 귀속
        ("20260918", "280700", datetime(2026, 9, 18, 19, 7, tzinfo=UTC), date(2026, 9, 21)),
    ],
)
def test_night_minute_bar_hours_24_to_30(
    date_s: str, hhmmss: str, ts_utc: datetime, trade_date: date
) -> None:
    rec = store.MinuteBarRecord.from_kis(
        _bar(date_s, hhmmss), code="A01612", market="CM", received_at=T0, calendar=CAL
    )
    assert (rec.ts, rec.trade_date, rec.session) == (ts_utc, trade_date, "night")


def test_minute_bar_market_must_match_session() -> None:
    with pytest.raises(ValidationError):
        store.MinuteBarRecord(
            ts=T0,
            trade_date=DAY,
            session="night",
            received_at=T0,
            code="A01612",
            market="F",
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
        )


def _krx_options() -> list[KrxOptionDaily]:
    rows, bad = parse_option_rows(_fixture("krx/opt_daily.json")["20260923"])
    assert bad == []
    return rows


def test_krx_option_rows_keep_kospi200_family_and_null_night_iv() -> None:
    rows = _krx_options()
    keep = [r for r in rows if store.krx_loadable(r)]
    assert len(rows) == 18 and len(keep) == 13  # 코스닥150(표기 없는 위클리 포함) 5행 제외
    by_key = {
        (r[2], r[1]): dict(zip(store.KRX_OPT_DAILY.columns, r, strict=True))
        for r in (store.krx_option_row(x, T0) for x in keep)
    }
    day = by_key[("B016AZC8", "day")]
    night = by_key[("B016AZC8", "night")]
    assert day["imp_volt"] == Decimal("35.20") and night["imp_volt"] is None  # 야간 '0.00' → NULL
    assert (day["trade_date"], day["expiry"], day["strike"], day["cp"]) == (
        date(2026, 9, 23),
        "202610",
        Decimal("1100.0"),
        "C",
    )
    assert day["ts"] == T0 and day["acc_trdval"] == 762490000
    wk = by_key[("CAFBZZAS", "night")]
    assert (wk["family"], wk["expiry"], wk["expiry_token"]) == (
        "kospi200_weekly_mon",
        "260904",
        "2609W4",
    )


def test_krx_rows_refuse_unloadable_rows_and_naive_ts() -> None:
    rows = _krx_options()
    kosdaq = next(r for r in rows if r.family == "kosdaq150_weekly_thu")
    with pytest.raises(ValueError, match="적재 대상"):
        store.krx_option_row(kosdaq, T0)
    ok = next(r for r in rows if store.krx_loadable(r))
    with pytest.raises(ValueError, match="naive"):
        store.krx_option_row(ok, datetime(2026, 9, 24, 8, 5))  # noqa: DTZ001 — 거부 확인


def test_krx_futures_rows_null_night_settlement() -> None:
    rows, bad = parse_futures_rows(_fixture("krx/fut_daily.json")["rows"])
    assert bad == [] and all(store.krx_loadable(r) for r in rows)
    out = [
        dict(zip(store.KRX_FUT_DAILY.columns, store.krx_futures_row(r, T0), strict=True))
        for r in rows
    ]
    by = {(d["isu_cd"], d["session"]): d for d in out}
    assert by[("A056A000", "day")]["setl_prc"] == Decimal("1120.35")
    assert by[("A056A000", "night")]["setl_prc"] is None
    assert by[("A056A000", "night")]["expiry"] == "202610"


def test_krx_loaded_asks_the_table_of_that_kind_for_the_day() -> None:
    f = Factory(results=[[(True,)], [(False,)]])
    s = _sink(f)
    assert s.krx_loaded("options", date(2026, 9, 23)) is True
    assert s.krx_loaded("futures", date(2026, 9, 24)) is False
    (c,) = f.conns
    assert c.reads == [
        (
            'SELECT EXISTS (SELECT 1 FROM "krx_opt_daily" WHERE trade_date = %s)',
            (date(2026, 9, 23),),
        ),
        (
            'SELECT EXISTS (SELECT 1 FROM "krx_fut_daily" WHERE trade_date = %s)',
            (date(2026, 9, 24),),
        ),
    ]
    assert c.batches == [] and s.stats.batches == 0  # 읽기는 쓰기 통계에 들지 않는다


def test_krx_option_listing_is_distinct_family_expiry_cp_strike() -> None:
    f = Factory(results=[[("kospi200", "202610", "C", Decimal("1100.00"))]])
    s = _sink(f)
    assert s.krx_option_listing(date(2026, 9, 23)) == [
        ("kospi200", "202610", "C", Decimal("1100.00"))
    ]
    ((query, params),) = f.conns[0].reads
    assert query.startswith('SELECT DISTINCT family, expiry, cp, strike FROM "krx_opt_daily"')
    assert params == (date(2026, 9, 23),)


def test_reads_reconnect_then_raise_store_error_and_skip_the_spool(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """읽기는 스풀에 쌓을 수 없다 — 연결 오류는 다시 연결해 한 번 더, 그래도면 StoreError."""
    f = Factory([psycopg.OperationalError("server closed"), psycopg.OperationalError("again")])
    s = _sink(f, spool=DiskSpool(tmp_path / "spool"))
    with (
        caplog.at_level(logging.WARNING, logger="data.store"),
        pytest.raises(store.StoreError, match="krx_opt_daily: OperationalError") as e,
    ):
        s.krx_loaded("options", date(2026, 9, 23))
    assert e.value.__cause__ is None and s.stats.reconnects == 1 and s.stats.failures == 1
    events = [json.loads(r.getMessage())["event"] for r in caplog.records]
    assert events == ["db_reconnect", "db_read_failed"]
    assert not s.degraded and s.spool is not None and not s.spool.pending
    f.results.append([(True,)])
    assert s.krx_loaded("options", date(2026, 9, 23)) is True  # 다음엔 새 연결로
    bad = Factory([psycopg.errors.UndefinedTable('relation "krx_opt_daily" does not exist')])
    with pytest.raises(store.StoreError, match="UndefinedTable"):
        _sink(bad).krx_option_listing(date(2026, 9, 23))
    assert len(bad.conns) == 1  # 데이터 오류는 다시 부르지 않는다


def test_master_rows_from_fixture() -> None:
    rows = parse_master("\n".join(_fixture("kis/master_lines.json")["lines"]))
    out = {
        r.code: dict(
            zip(
                store.MASTER_SNAPSHOTS.columns,
                store.master_row(r, ts=T0, trade_date=DAY, session="day"),
                strict=True,
            )
        )
        for r in rows
    }
    fut = out["A01612"]
    assert (fut["cp"], fut["strike"], fut["atm_cls"], fut["expiry"]) == (None, None, None, "202612")
    atm = out["B01610ZCI"]  # 'C 202610 1,125.0', 다섯째 필드 1 = ATM
    assert (atm["cp"], atm["strike"], atm["atm_cls"]) == ("C", Decimal("1125.00"), "1")
    assert (atm["family"], atm["series"], atm["expiry"]) == ("kospi200", "C 202610", "202610")
    assert (atm["trade_date"], atm["session"], atm["ts"]) == (DAY, "day", T0)
    with pytest.raises(ValueError):
        store.master_row(rows[0], ts=T0, trade_date=DAY, session="DAY")


# ── data.store: 웹소켓 체결·세션 기록·공백 ──

WS = load_fields()


def ws_tick(tr_id: str, **values: str) -> FuturesTick | OptionTick:
    """SYNTHETIC: config/kis_ws_fields.yaml 컬럼으로 만든 레코드 한 건(지정 안 한 칸은 '0')."""
    spec = WS[tr_id]
    rec = [values.get(c, "0") for c in spec.columns]
    frame = parse_frame(f"0|{tr_id}|001|{'^'.join(rec)}", WS)
    assert isinstance(frame, WsData)
    (tick,) = ticks_from(frame, WS)
    return tick


FUT = {
    "futs_shrn_iscd": "A01612",
    "bsop_hour": "142701",
    "futs_prpr": "1095.10",
    "last_cnqn": "3",
    "acml_vol": "82481",
    "seln_cntg_smtn": "41000",
    "shnu_cntg_smtn": "41481",
    "hts_otst_stpl_qty": "136321",
    "otst_stpl_qty_icdc": "-12",
    "futs_bidp1": "1095.05",
    "futs_askp1": "1095.10",
}


@pytest.mark.parametrize(
    ("model", "table"),
    [(store.FuturesTickRecord, store.FUT_TICKS), (store.OptionTickRecord, store.OPT_TICKS)],
)
def test_tick_record_fields_are_the_table_columns(
    model: type[BaseModel], table: store.Table
) -> None:
    assert set(model.model_fields) == set(table.columns)
    assert table.key == ("ts", "code", "seq") and not table.upsert


def test_futures_tick_record_from_ws_frame() -> None:
    tick = ws_tick("H0IFCNT0", **FUT)
    assert isinstance(tick, FuturesTick)
    ts = datetime(2026, 9, 28, 14, 27, 1, tzinfo=KST)
    rec = store.FuturesTickRecord.from_tick(tick, ts=ts, trade_date=DAY, seq=7, received_at=T0)
    d = dict(zip(store.FUT_TICKS.columns, store.attr_row(store.FUT_TICKS, rec), strict=True))
    assert d["ts"] == ts and d["ts"].utcoffset() == timedelta(0)
    assert (d["trade_date"], d["session"], d["tr_id"], d["code"], d["seq"]) == (
        DAY,
        "day",
        "H0IFCNT0",
        "A01612",
        7,
    )
    assert (d["price"], d["qty"], d["cum_buy_qty"], d["cum_sell_qty"]) == (
        Decimal("1095.10"),
        3,
        41481,
        41000,
    )
    assert (d["oi"], d["oi_chg"], d["bid"], d["ask"]) == (
        136321,
        -12,
        Decimal("1095.05"),
        Decimal("1095.10"),
    )


def test_option_tick_record_takes_series_from_master_and_drops_zero_iv() -> None:
    master = next(
        r
        for r in parse_master("\n".join(_fixture("kis/master_lines.json")["lines"]))
        if r.family == "kospi200_weekly_mon" and r.cp == "C"
    )
    tick = ws_tick(
        "H0EUCNT0",
        optn_shrn_iscd=master.code,
        bsop_hour="213000",
        optn_prpr="12.35",
        gama="0.0081",
        delta="0.5123",
        hts_ints_vltl="0",
    )
    assert isinstance(tick, OptionTick)
    rec = store.OptionTickRecord.from_tick(
        tick,
        ts=datetime(2026, 9, 28, 21, 30, tzinfo=KST),
        trade_date=date(2026, 9, 29),
        seq=0,
        received_at=T0,
        master=master,
    )
    assert (rec.session, rec.trade_date) == ("night", date(2026, 9, 29))
    assert (rec.mrkt_cls, rec.expiry, rec.strike, rec.cp) == (
        "WKM",
        master.expiry,
        master.strike,
        "C",
    )
    assert rec.gamma == Decimal("0.0081") and rec.iv is None  # KIS 0 = 값 없음
    other = master.model_copy(update={"code": "X"})
    with pytest.raises(ValueError, match="코드"):
        store.OptionTickRecord.from_tick(
            tick, ts=T0, trade_date=DAY, seq=0, received_at=T0, master=other
        )
    bare = store.OptionTickRecord.from_tick(tick, ts=T0, trade_date=DAY, seq=1, received_at=T0)
    assert (bare.expiry, bare.strike, bare.cp, bare.mrkt_cls) == (None, None, None, None)


def test_session_log_transition_from_state_at() -> None:
    at = datetime(2026, 9, 28, 18, 0, tzinfo=KST)
    info = state_at(at, CAL)
    rec = store.SessionLogRecord.transition(at, info, State.PRE_NIGHT, source="calendar")
    d = dict(zip(store.SESSION_LOG.columns, store.session_log_row(rec), strict=True))
    assert (d["state"], d["prev_state"], d["kind"], d["service"]) == (
        "NIGHT",
        "PRE_NIGHT",
        "transition",
        "scheduler",
    )
    assert (d["trade_date"], d["session"]) == (date(2026, 9, 29), "night")
    assert d["detail"] == '{"source":"calendar"}' and len(d["digest"]) == 32
    idle = store.SessionLogRecord.transition(
        datetime(2026, 9, 28, 15, 45, tzinfo=KST), state_at(at - timedelta(hours=2), CAL), State.DAY
    )
    row = store.session_log_row(idle)
    assert row[1:3] == (None, None) and row[5] == "POST_DAY"
    assert store.session_log_row(rec)[-1] != row[-1]


def test_gap_record_row_and_order() -> None:
    start = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    gap = store.CollectionGapRecord(
        stream="board:WKM:260904",
        start_ts=start,
        end_ts=start + timedelta(seconds=75),
        trade_date=DAY,
        session="day",
        expected=3,
        received=1,
        detected_at=start + timedelta(minutes=2),
        detail={"max_gap_s": 75},
    )
    d = dict(zip(store.COLLECTION_GAPS.columns, store.gap_row(gap), strict=True))
    assert d["start_ts"] == start and d["start_ts"].utcoffset() == timedelta(0)
    assert (d["expected"], d["received"], d["detail"]) == (3, 1, '{"max_gap_s":75}')
    assert store.COLLECTION_GAPS.upsert and store.COLLECTION_GAPS.key == ("stream", "start_ts")
    with pytest.raises(ValidationError):
        store.CollectionGapRecord(
            stream="x",
            start_ts=start,
            end_ts=start - timedelta(seconds=1),
            trade_date=DAY,
            session="day",
            detected_at=start,
        )


# ── 무결측 판정: 리포트 행·읽기·교체 (설계 §9) ──


def _report(stream: str = "fut_board", **kw: Any) -> store.CollectionReportRecord:
    start = datetime(2026, 9, 28, 8, 45, tzinfo=KST)
    base: dict[str, Any] = {
        "trade_date": DAY,
        "session": "day",
        "stream": stream,
        "evaluated_at": datetime(2026, 9, 28, 16, 0, 5, tzinfo=KST),
        "span_start": start,
        "span_end": start + timedelta(hours=7),
        "required": True,
        "status": "ok",
        "expected": 840,
        "received": 840,
        "max_gap_s": 31.5,
        "detail": {"gap_s": 60.0},
    }
    return store.CollectionReportRecord(**(base | kw))


def _gap(stream: str = "fut_board", minute: int = 0) -> store.CollectionGapRecord:
    start = datetime(2026, 9, 28, 10, minute, tzinfo=KST)
    return store.CollectionGapRecord(
        stream=stream,
        start_ts=start,
        end_ts=start + timedelta(seconds=90),
        trade_date=DAY,
        session="day",
        expected=3,
        received=0,
        detected_at=datetime(2026, 9, 28, 16, 0, 5, tzinfo=KST),
    )


def test_report_row_is_utc_and_in_column_order() -> None:
    d = dict(zip(store.COLLECTION_REPORTS.columns, store.report_row(_report()), strict=True))
    assert d["span_start"] == datetime(2026, 9, 27, 23, 45, tzinfo=UTC)
    assert d["span_start"].utcoffset() == timedelta(0)
    assert (d["required"], d["status"], d["gaps"], d["max_gap_s"]) == (True, "ok", 0, 31.5)
    assert d["detail"] == '{"gap_s":60.0}'
    with pytest.raises(ValidationError):
        _report(status="maybe")
    with pytest.raises(ValidationError):
        _report(span_end=datetime(2026, 9, 28, 8, 0, tzinfo=KST))  # 시작보다 앞


def test_gap_reads_ask_the_session_and_map_rows() -> None:
    t = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
    f = Factory(
        results=[
            [("WKM", "260904", "kis", date(2026, 9, 28), t)],
            [("WKM", "260904", t)],
            [(t,)],
            [(t + timedelta(seconds=1),)],
            [("K2I", "F001", t)],
            [("M:202610", t)],
            [("A01612", t, 5), ("A01612", t, None)],
            [("A01612", t, 3)],
            [(7,)],
        ]
    )
    s = _sink(f)
    assert s.series_dates(DAY, "day") == [("WKM", "260904", "kis", date(2026, 9, 28), t)]
    assert s.board_times(DAY, "day") == [("WKM", "260904", t)]
    assert s.fut_board_times(DAY, "day") == [t]
    assert s.underlying_times(DAY, "day") == [t + timedelta(seconds=1)]
    assert s.investor_times(DAY, "day") == [("K2I", "F001", t)]
    assert s.fill1_times(DAY, "day") == [("M:202610", t)]
    assert s.minute_bar_times(DAY, "day") == [("A01612", t, 5), ("A01612", t, None)]
    assert s.fut_tick_minutes(DAY, "day") == [("A01612", t, 3)]
    start = datetime(2026, 9, 28, 8, 45, tzinfo=KST)
    assert s.fut_tick_count(start, start + timedelta(minutes=3)) == 7
    reads = f.conns[0].reads
    assert [q.split(" FROM ")[1].split()[0] for q, _ in reads] == [
        '"series_expiries"',
        '"chain_snapshots"',
        '"fut_board"',
        '"raw_messages"',
        '"investor_flow"',
        '"raw_messages"',
        '"minute_bars"',
        '"fut_ticks"',
        '"fut_ticks"',
    ]
    assert reads[1][1] == (DAY, "day", "board", "invalid")  # 전광판만, 검증 실패 행은 뺀다
    assert reads[3][1] == (DAY, "day", "kis_rest", "underlying|%", "0")  # 기초자산 성공 응답만
    assert reads[5][1] == (DAY, "day", "kis_rest", "fill1:%", "0")  # 보강 1 성공 응답만
    assert "payload->>'rt_cd'" in reads[3][0] and "DISTINCT ts" in reads[3][0]
    assert "split_part(key, ':', 2)" in reads[5][0] and "payload->>'rt_cd'" in reads[5][0]
    assert "date_trunc('minute', ts)" in reads[7][0]
    assert reads[8][1] == (start.astimezone(UTC), (start + timedelta(minutes=3)).astimezone(UTC))
    assert all("%s" in q and "2026" not in q for q, _ in reads)  # 값은 파라미터로만


def test_ws_connection_events_read_the_state_before_and_the_events_inside() -> None:
    t0 = datetime(2026, 9, 28, 8, 45, tzinfo=KST)
    ev = t0 + timedelta(minutes=5)
    f = Factory(results=[[("ws_connected",)], [(ev, "ws_disconnected")]])
    s = _sink(f)
    kinds = ("ws_connected", "ws_disconnected")
    got = s.ws_connection_events(t0, t0 + timedelta(hours=7), kinds)
    assert got == ("ws_connected", [(ev, "ws_disconnected")])
    (before_q, before_p), (events_q, events_p) = f.conns[0].reads
    assert "ORDER BY ts DESC LIMIT 1" in before_q and "ts < %s" in before_q
    assert before_p == ("ws-gateway", list(kinds), t0.astimezone(UTC))
    assert events_p[:2] == ("ws-gateway", list(kinds)) and "ts <= %s" in events_q
    empty = _sink(Factory(results=[[], []]))
    assert empty.ws_connection_events(t0, t0, kinds) == (None, [])


def test_gap_reports_become_records() -> None:
    r = _report()
    row = store.report_row(r)
    f = Factory(results=[[(*row[:-1], {"gap_s": 60.0})]])  # 읽으면 jsonb 는 dict 로 온다
    s = _sink(f)
    assert s.gap_reports(DAY, DAY) == [r]
    ((query, params),) = f.conns[0].reads
    assert query.startswith('SELECT "trade_date", "session", "stream"')
    assert params == (DAY, DAY)


def test_replace_gap_report_is_one_transaction_delete_then_insert() -> None:
    f = Factory()
    s = _sink(f)
    s.replace_gap_report(DAY, "day", [_gap(), _gap(minute=5)], [_report()])
    (c,) = f.conns
    assert c.commits == 1 and c.rollbacks == 0
    queries = [q for q, _ in c.batches]
    assert queries[0] == 'DELETE FROM "collection_gaps" WHERE trade_date = %s AND session = %s'
    assert queries[1] == 'DELETE FROM "collection_reports" WHERE trade_date = %s AND session = %s'
    assert queries[2].startswith('INSERT INTO "collection_gaps"')
    assert queries[3].startswith('INSERT INTO "collection_reports"')
    assert [len(rows) for _, rows in c.batches] == [1, 1, 2, 1]
    assert c.batches[0][1] == [(DAY, "day")]
    assert s.stats.by_table == {"collection_gaps": 2, "collection_reports": 1}
    # 공백이 없어진 세션도 지우기는 한다(옛 행이 남지 않게)
    s.replace_gap_report(DAY, "day", [], [_report()])
    assert [q.split()[0] for q, _ in c.batches[4:]] == ["DELETE", "DELETE", "INSERT"]


def test_replace_gap_report_refuses_mixed_sessions_and_rolls_back_on_errors() -> None:
    s = _sink(Factory())
    with pytest.raises(ValueError, match="섞였다"):
        s.replace_gap_report(DAY, "night", [_gap()], [])
    with pytest.raises(ValueError, match="session"):
        s.replace_gap_report(DAY, "evening", [], [])
    f = Factory([psycopg.errors.CheckViolation("new row violates check constraint")])
    bad = _sink(f)
    with pytest.raises(store.StoreError, match="collection_reports: CheckViolation"):
        bad.replace_gap_report(DAY, "day", [_gap()], [_report()])
    assert f.conns[0].rollbacks == 1 and f.conns[0].commits == 0
    flaky = Factory([psycopg.OperationalError("server closed")])
    ok = _sink(flaky)
    ok.replace_gap_report(DAY, "day", [], [_report()])  # 새 연결로 한 번 더
    assert ok.stats.reconnects == 1 and flaky.conns[-1].commits == 1
