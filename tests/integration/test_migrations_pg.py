"""마이그레이션 0001~0006 을 실제 TimescaleDB 에 적용(docs/p2_design.md §8.2·§8.8 — Docker).

- 적용기: 두 번 적용(두 번째는 할 일 없음)·기록·동시 실행·autocommit 요구·compose initdb 뒤 재적용
- 파일 자체도 멱등: 적용된 DB 에 각 파일을 한 번 더 돌려도 오류가 없다
- hypertable·청크 간격·압축 정책(GX 그대로 + daily_bar·stock_investor_daily 365일)
- 권한: kbj_public_export 는 pub_* 만 읽고, prv_*·ops 는 못 읽고, 아무것도 쓰지 못한다
- 제약: data_claim 부분 유일(claimed·done 하나), notify_message → notify_log 외래키(커밋 때 검사),
  pub_filings.corp_code 출처는 DART 만, 뷰 market_investor_intraday = GX investor_flow
- kbj.store.db: connect(UTC·search_path·접속 오류 문구에 비밀 없음), PgHealthStore(멱등)
컨테이너는 tests/integration/conftest.py 가 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr

from kbj.services.runtime.health import HealthEvent
from kbj.store.db import PgHealthStore, StoreError, connect
from kbj.store.migrate import MIGRATIONS_DIR, MigrationError, apply, discover, migrate
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

P2 = [
    "0001_schemas.sql",
    "0002_ops_core.sql",
    "0003_market_flows.sql",
    "0004_filings_corp.sql",
    "0005_alerts_inbox.sql",
    "0006_gex.sql",
]
GX_DAILY = {
    "prv_gex.raw_messages", "prv_gex.fut_ticks", "prv_gex.opt_ticks", "prv_gex.chain_snapshots",
    "prv_gex.fut_board", "prv_gex.investor_flow", "prv_gex.series_expiries",
    "prv_gex.minute_bars", "prv_gex.levels", "prv_gex.metrics", "prv_gex.strike_gex",
    "prv_gex.option_iv", "prv_gex.oi_changes", "ops.session_log", "ops.health_events",
    "ops.collection_gaps", "ops.quarantine", "ops.collection_reports",
}  # fmt: skip
YEARLY = {
    "prv_gex.krx_fut_daily", "prv_gex.krx_opt_daily", "prv_gex.master_snapshots",
    "prv_market.daily_bar", "prv_flows.stock_investor_daily",
}  # fmt: skip


class _Settings:
    def __init__(self, url: str) -> None:
        self.database_url = SecretStr(url)


def _rows(dsn: str, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as c:
        return c.execute(query.encode(), params or None).fetchall()


def _one(dsn: str, query: str, *params: object) -> Any:
    rows = _rows(dsn, query, *params)
    assert len(rows) == 1
    return rows[0][0]


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    """마이그레이션을 두 번 적용한 DB(두 번째는 할 일이 없어야 한다)."""
    url = timescale.dsn(timescale.fresh_database("mig"))
    assert [m.name for m in discover()] == P2
    assert migrate(url) == P2
    assert migrate(url) == []
    return url


def test_migrations_are_recorded_once(dsn: str) -> None:
    rows = _rows(dsn, "SELECT version, name, checksum FROM ops.schema_migrations ORDER BY version")
    assert rows == [(m.version, m.name, m.checksum) for m in discover()]


def test_every_file_can_run_again_on_a_migrated_database(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("again"))
    migrate(url)
    with psycopg.connect(url, autocommit=True) as c:
        for name in P2:
            with c.transaction():
                c.execute((MIGRATIONS_DIR / name).read_text("utf-8").encode())


def test_initdb_then_migrate_reapplies_0001_safely(timescale: PgContainer) -> None:
    """compose 는 0001 을 initdb 로 먼저 돌린다 — 기록이 없으니 적용기가 0001 부터 다시 돈다."""
    url = timescale.dsn(timescale.fresh_database("initdb"))
    with psycopg.connect(url, autocommit=True) as c:
        c.execute((MIGRATIONS_DIR / "0001_schemas.sql").read_text("utf-8").encode())
    assert migrate(url) == P2


def test_hypertables_and_chunk_intervals(dsn: str) -> None:
    ht = _rows(
        dsn,
        "SELECT h.hypertable_schema || '.' || h.hypertable_name, d.column_name, d.time_interval "
        "FROM timescaledb_information.hypertables h "
        "JOIN timescaledb_information.dimensions d USING (hypertable_schema, hypertable_name)",
    )
    got = {name: (col, iv) for name, col, iv in ht}
    assert set(got) == GX_DAILY | YEARLY
    assert {t: iv for t, (_, iv) in got.items() if t in YEARLY} == dict.fromkeys(
        YEARLY, timedelta(days=365)
    )
    assert {iv for t, (_, iv) in got.items() if t in GX_DAILY} == {timedelta(days=1)}
    assert got["prv_market.daily_bar"][0] == "trade_date"
    assert got["ops.collection_gaps"][0] == "start_ts"
    assert got["ops.health_events"][0] == "ts"
    # GX tests/integration/test_store.py 의 같은 시험이 보던 시간 열(원본 단언 그대로)
    assert got["prv_gex.krx_opt_daily"][0] == "trade_date"
    assert got["ops.collection_reports"][0] == "trade_date"
    assert got["prv_gex.chain_snapshots"][0] == "ts"


def test_raw_messages_compression_policy(dsn: str) -> None:
    assert (
        _one(
            dsn,
            "SELECT compression_enabled FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = 'prv_gex' AND hypertable_name = 'raw_messages'",
        )
        is True
    )
    after = _one(
        dsn,
        "SELECT config->>'compress_after' FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_compression' AND hypertable_name = 'raw_messages'",
    )
    assert after == "3 days"


def test_no_table_lands_in_the_public_schema(dsn: str) -> None:
    n = _one(
        dsn,
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
        "AND table_type = 'BASE TABLE'",
    )
    assert n == 0


def test_apply_requires_autocommit(dsn: str) -> None:
    with psycopg.connect(dsn) as c, pytest.raises(MigrationError):
        apply(c)


def test_concurrent_migrate_on_fresh_db_applies_once(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("race"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _: migrate(url), range(2)))
    assert results == [[], P2]
    assert _one(url, "SELECT count(*) FROM ops.schema_migrations") == len(P2)


# ── 권한 (ADR 0002 §2.2) ──


def _as_export(dsn: str, query: str) -> None:
    with psycopg.connect(dsn) as c:
        c.execute("SET ROLE kbj_public_export")
        c.execute(query.encode())


def test_export_role_reads_pub_only(dsn: str) -> None:
    _as_export(dsn, "SELECT count(*) FROM pub_filings.corp_code")
    for table in ("prv_market.daily_bar", "prv_flows.stock_investor_daily", "ops.kv",
                  "prv_alerts.tg_inbox", "prv_gex.investor_flow"):  # fmt: skip
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as_export(dsn, f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 — 상수 표 이름
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_export(
            dsn,
            "INSERT INTO pub_filings.corp_code (corp_code, corp_name, received_at) "
            "VALUES ('00000001', 'x', now())",
        )


# ── 제약 ──


def test_data_claim_allows_one_live_claim_per_key(dsn: str) -> None:
    ins = (
        "INSERT INTO ops.data_claim (source, dataset, as_of, venue, job, status, claimed_at) "
        "VALUES ('KRX', 'sto/stk_bydd_trd', '2026-10-02', 'KRX', %s, 'claimed', now()) "
        "RETURNING id"
    )
    with psycopg.connect(dsn, autocommit=True) as c:
        first = c.execute(ins, ("market.krx_daily",)).fetchone()
        assert first is not None
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(ins, ("other.job",))
        c.execute(
            "UPDATE ops.data_claim SET status = 'failed', done_at = now() WHERE id = %s",
            (first[0],),
        )
        assert c.execute(ins, ("market.krx_daily",)).fetchone() is not None  # 실패 뒤 다시
        # 다른 거래소 구분은 다른 키
        c.execute(ins.replace("'KRX', %s", "'NXT', %s"), ("market.krx_daily",))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "INSERT INTO ops.data_claim (source, dataset, as_of, job, status, claimed_at) "
                "VALUES ('A', 'b', 'c', 'j', 'done', now())"
            )  # done 인데 끝난 시각이 없다


def test_notify_message_needs_its_log_row_at_commit(dsn: str) -> None:
    with psycopg.connect(dsn) as c:  # 같은 트랜잭션이면 순서가 상관없다(외래키 검사를 미룬다)
        c.execute("INSERT INTO prv_alerts.notify_message (dedup_key, body) VALUES ('k1', '본문')")
        c.execute(
            "INSERT INTO ops.notify_log (kind, dedup_key, status, requested_at) "
            "VALUES ('brief.closing', 'k1', 'queued', now())"
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(dsn) as c:
        c.execute("INSERT INTO prv_alerts.notify_message (dedup_key, body) VALUES ('k2', 'x')")


def test_corp_code_source_is_dart_only(dsn: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(
            "INSERT INTO pub_filings.corp_code (corp_code, corp_name, source, received_at) "
            "VALUES ('00126380', '합성', 'KIS', now())"
        )


def test_market_investor_intraday_view_reads_gx_investor_flow(dsn: str) -> None:
    ts = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    with psycopg.connect(dsn) as c:
        c.execute(
            "INSERT INTO prv_gex.investor_flow (ts, trade_date, session, market_code, "
            "sector_code, investor, net_value, quality) VALUES "
            "(%s, '2026-10-02', 'day', 'K2I', 'F001', 'foreign', -1200, 'ok')",
            (ts,),
        )
    row = _rows(
        dsn,
        "SELECT market_code, investor, net_value, quality, source "
        "FROM prv_flows.market_investor_intraday WHERE ts = %s",
        ts,
    )
    assert row == [("K2I", "foreign", -1200, "ok", "KIS")]


# ── kbj.store.db ──


def test_connect_sets_utc_and_search_path(dsn: str) -> None:
    with connect(_Settings(dsn), search_path=("prv_market", "ops"), service="test") as c:
        assert c.execute("SHOW TIME ZONE").fetchone() == ("UTC",)
        assert c.execute("SHOW search_path").fetchone() == ("prv_market, ops",)
        assert c.execute("SELECT current_setting('application_name')").fetchone() == ("kbj-test",)
        c.execute("SELECT count(*) FROM daily_bar")  # 스키마 없이도 보인다


def test_connect_errors_hide_the_password(timescale: PgContainer) -> None:
    bad = timescale.dsn().replace(timescale.password, "wrong-SYNTHETIC-pw")
    with pytest.raises(StoreError) as e:
        connect(_Settings(bad), retries=1, sleep=lambda _: None)
    assert "wrong-SYNTHETIC-pw" not in str(e.value) and timescale.password not in str(e.value)
    assert "127.0.0.1" not in str(e.value)
    with pytest.raises(ValueError, match="search_path"):
        connect(_Settings(bad), search_path=("public",))


def test_health_store_writes_once_per_event(dsn: str) -> None:
    store = PgHealthStore(lambda: psycopg.connect(dsn))
    ev = HealthEvent(
        kind="token_throttled", detail="발급 1분 제한", at=datetime(2026, 10, 2, 0, 1, tzinfo=UTC),
        severity="warning", service="auth",
    )  # fmt: skip

    def tagger(_: datetime) -> tuple[date | None, str | None]:
        return date(2026, 10, 2), "day"

    store.write_health([ev], tagger=tagger)
    store.write_health([ev], tagger=tagger)  # 같은 이벤트 — (ts, digest) 로 한 줄
    rows = _rows(
        dsn,
        "SELECT trade_date, session, service, kind, level, message FROM ops.health_events "
        "WHERE kind = 'token_throttled'",
    )
    assert rows == [
        (date(2026, 10, 2), "day", "auth", "token_throttled", "warning", "발급 1분 제한")
    ]


def test_migration_files_only_touch_tiered_schemas(dsn: str) -> None:
    owned = _rows(
        dsn,
        "SELECT DISTINCT table_schema FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "AND table_schema NOT LIKE '\\_timescaledb%' AND table_schema NOT LIKE 'timescaledb%' "
        "AND table_schema NOT LIKE 'pg\\_%'",
    )
    schemas = {r[0] for r in owned}
    assert schemas <= {s for s in schemas if s == "ops" or s.startswith(("pub_", "prv_"))}
    assert {"ops", "prv_market", "prv_flows", "prv_gex", "prv_alerts", "pub_filings"} <= schemas
