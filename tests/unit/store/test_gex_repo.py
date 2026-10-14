"""`kbj.store.repos.gex` — GX 싱크 모양 저장소(docs/p7_design.md §1.1·D-P7-8).

메모리판 규칙(키·덮어쓰기·최신 고르기)·SQL 문자열(스키마 이름·충돌 키)·열 목록이 0006/0002 DDL 과
같은지 본다. 실제 Postgres 는 ENG 의 통합 시험(`tests/integration/test_gex_engine_pg.py`).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kbj.core.calendar import State
from kbj.core.gex.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    LevelRecord,
    MetricRecord,
    OiChangeRecord,
    RawEnvelope,
    SessionLogRecord,
    StrikeGexRecord,
)
from kbj.store.repos import gex as G
from kbj.store.repos.gex import GexRepo, MemoryGexRepo

ROOT = Path(__file__).resolve().parents[3]
MIG = ROOT / "kbj" / "store" / "migrations"
D = date(2026, 10, 13)
T0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)


def _ddl_columns(table: str) -> list[str]:
    body = "\n".join(
        line.split("--", 1)[0]
        for f in ("0002_ops_core.sql", "0006_gex.sql")
        for line in (MIG / f).read_text(encoding="utf-8").splitlines()
    )
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);", body, re.S)
    assert m is not None, table
    cols: list[str] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s or s.split()[0].upper() in {"CHECK", "UNIQUE", "PRIMARY", "CONSTRAINT"}:
            continue
        cols.append(s.split()[0])
    return cols


ALL = (
    G.RAW_MESSAGES, G.FUT_TICKS, G.OPT_TICKS, G.CHAIN_SNAPSHOTS, G.FUT_BOARD, G.INVESTOR_FLOW,
    G.SERIES_EXPIRIES, G.SESSION_LOG, G.HEALTH_EVENTS, *G.ENGINE_TABLES,
)  # fmt: skip


@pytest.mark.parametrize("table", ALL, ids=lambda t: t.qualified)
def test_columns_match_ddl(table: G.GxTable) -> None:
    assert set(table.columns) == set(_ddl_columns(table.qualified))


def test_insert_sql_is_schema_qualified_with_gx_conflict_rules() -> None:
    q = G.LEVELS.insert_sql().as_string(None)  # pyright: ignore[reportArgumentType]
    assert q.startswith('INSERT INTO "prv_gex"."levels"')
    assert 'ON CONFLICT ("ts", "scope", "name") DO UPDATE SET' in q
    assert G.RAW_MESSAGES.insert_sql().as_string(None).endswith("DO NOTHING")  # pyright: ignore[reportArgumentType]
    oi = G.OI_CHANGES.insert_sql().as_string(None)  # pyright: ignore[reportArgumentType]
    assert oi.endswith('WHERE EXCLUDED."change" IS NOT NULL')
    assert G.SESSION_LOG.qualified == "ops.session_log"


def level(ts: datetime, name: str, value: float | None, flag: str = "visible") -> LevelRecord:
    return LevelRecord.model_validate(
        dict(ts=ts, trade_date=D, session="day", scope="all", name=name, value=value,
             quality="ok", flag=flag)
    )  # fmt: skip


def metric(ts: datetime, name: str, value: float, key: str = "") -> MetricRecord:
    return MetricRecord.model_validate(
        dict(ts=ts, trade_date=D, session="day", metric=name, scope="all", key=key, value=value,
             payload={"x": 1}, quality="ok", flag="shadow")
    )  # fmt: skip


def test_memory_satisfies_protocol_and_screen_reads() -> None:
    repo: GexRepo = MemoryGexRepo()
    repo.write_levels([level(T0, "flip", 350.0), level(T0, "vex", 1.0, "shadow")])
    t1 = T0 + timedelta(minutes=1)
    repo.write_levels([level(t1, "flip", 351.0), level(t1, "call_wall", None)])
    got = repo.levels_latest("all")
    assert [(r.name, r.value) for r in got] == [("call_wall", None), ("flip", 351.0)]
    assert [r.name for r in repo.levels_latest("all", upto=T0)] == ["flip", "vex"]
    assert repo.levels_latest("0dte") == []
    repo.write_metrics([metric(T0, "vex", 1.5)])
    assert repo.flags_of(T0) == {"flip": "visible", "vex": "shadow"}
    s = repo.metrics_series("vex", T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert [m.value for m in s] == [1.5] and s[0].payload == {"x": 1}
    assert [m.metric for m in repo.metric_last(D, "day", "vex", "all")] == ["vex"]
    assert len(repo.metric_history("vex", "all", "", D, D)) == 1
    repo.write_strike_gex(
        [StrikeGexRecord.model_validate(
            dict(ts=T0, trade_date=D, session="day", mrkt_cls="", expiry="202611",
                 strike=Decimal("350.00"), gex_call=1.0, gex_put=-2.0, gex=-1.0, forward=351.0,
                 excluded_oi_ratio=0.0, quality="ok")
        )]
    )  # fmt: skip
    assert [r.gex for r in repo.strike_profile(T0)] == [-1.0]


def test_upsert_and_keep_if_null_rules() -> None:
    repo = MemoryGexRepo()
    repo.write_levels([level(T0, "flip", 1.0)])
    repo.write_levels([level(T0, "flip", 2.0)])  # 같은 사이클 재계산 = 최신
    assert repo.levels_latest("all")[0].value == 2.0
    base = dict(ts=T0, trade_date=D, session="day", mrkt_cls="", expiry="202611",
                strike=Decimal("350.00"), cp="C", oi=10, prev_ts=None, quality="ok")  # fmt: skip
    repo.write_oi_changes([OiChangeRecord.model_validate({**base, "change": 3, "prev_oi": 7})])
    repo.write_oi_changes([OiChangeRecord.model_validate({**base, "change": None})])
    (row,) = repo.tables["prv_gex.oi_changes"].dicts()
    assert row["change"] == 3  # 첫 스냅샷 칸은 저장된 증감을 덮지 않는다


def test_raw_digest_is_stable_and_idempotent() -> None:
    env = RawEnvelope(
        received_at=T0, source="kis_rest", tr_id="FHMIF10010000", key="k",
        payload={"output1": {"a": "1"}, "rt_cd": "0"}, trade_date=D, session="day",
    )  # fmt: skip
    assert G.raw_row(env)[-1] == G.raw_row(env.model_copy())[-1]
    repo = MemoryGexRepo()
    repo.write_raw([env, env])
    assert len(repo.tables["prv_gex.raw_messages"].rows) == 1
    out = repo.kis_rest_outputs("FHMIF10010000", T0 - timedelta(seconds=1), T0)
    assert out == [(T0, D, "day", {"a": "1"})]
    with pytest.raises(ValueError, match="식별자"):
        repo.kis_rest_outputs("x", T0, T0, output="Bad-Name")
    nul = RawEnvelope(
        received_at=T0, source="kis_ws", tr_id="H0IFCNT0", payload="a\x00b", trade_date=None,
        session=None,
    )  # fmt: skip
    row = G.raw_row(nul)
    assert row[7] == "a�b" and row[8] is True and row[1] is None


def test_chain_futures_expiry_reads_pick_latest_valid() -> None:
    repo = MemoryGexRepo()

    def chain(ts: datetime, last: str, quality: str = "ok") -> ChainRecord:
        return ChainRecord.model_validate(
            dict(ts=ts, trade_date=D, session="day", mrkt_cls="", expiry="202611",
                 strike=Decimal("350"), cp="C", source="board", last=Decimal(last),
                 quality=quality)
        )  # fmt: skip

    t1, t2 = T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)
    repo.seed_chain([chain(T0, "1.0"), chain(t1, "1.5"), chain(t2, "9.9", "invalid")])
    assert repo.chain_max_ts(D, "day") == t1
    assert [c.last for c in repo.chain_latest(D, "day", t2)] == [Decimal("1.5")]
    fut = dict(trade_date=D, session="day", code="A01612", market="F", source="board")
    repo.seed_futures([
        FuturesRecord.model_validate({**fut, "ts": T0, "price": Decimal("350.1")}),
        FuturesRecord.model_validate({**fut, "ts": t1, "price": None}),
    ])  # fmt: skip
    assert [f.price for f in repo.futures_latest(D, "day", t2)] == [Decimal("350.1")]
    exp = dict(trade_date=D, session="day", mrkt_cls="", expiry="202611")
    repo.seed_expiries([
        ExpiryRecord.model_validate({**exp, "ts": t1, "last_trade_date": date(2026, 11, 13),
                                     "source": "calendar"}),
        ExpiryRecord.model_validate({**exp, "ts": T0, "last_trade_date": date(2026, 11, 12),
                                     "source": "kis"}),
    ])  # fmt: skip
    assert repo.expiry_dates(D) == [("", "202611", date(2026, 11, 12), "kis")]


def test_session_log_and_health_rows() -> None:
    repo = MemoryGexRepo()
    log = SessionLogRecord(ts=T0, trade_date=D, session="day", kind="transition", state=State.DAY)
    repo.write_session_log([log, log])
    assert len(repo.tables["ops.session_log"].rows) == 1
    ev = HealthEvent(ts=T0, trade_date=D, session="day", kind="k", level="info", message="m")
    repo.write_health([ev])
    (row,) = repo.tables["ops.health_events"].dicts()
    assert row["service"] == "poller" and row["detail"] == "{}"


def test_opt_tick_days_rejects_bad_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        MemoryGexRepo().opt_tick_days(D, 0)
