"""마이그레이션 0001~0023 을 실제 TimescaleDB 에 적용(Docker).

근거: docs/p2_design.md §8.2·§8.8, docs/p3_design.md §3.4.

- 적용기: 두 번 적용(두 번째는 할 일 없음)·기록·동시 실행·autocommit 요구·compose initdb 뒤 재적용
- 파일 자체도 멱등: 적용된 DB 에 각 파일을 한 번 더 돌려도 오류가 없다
- hypertable·청크 간격·압축 정책(GX 그대로 + daily_bar·stock_investor_daily 365일)
- P3(0007~0009): 장중 이력 표는 청크 1일 + 보존 정책(90일·ETF 30일), 일 단위 표는 365일
- P4(0010~0013, docs/p4_design.md §3.4): 공개 표(pub_filings·pub_fin)는 내보내기 역할이 읽고,
  로그인·개인 표(prv_fin·prv_market.share_class·prv_journal.watchlist)는 못 읽는다. 공개 표 source
  는
  DART 만, 엑셀 2 MiB 상한, 조정 공시 연결 실패는 invalid + 사유. 밸류 일별은 hypertable(365일)
- P5(0014~0017, docs/p5_design.md §3.3 — 테마 표는 사용자 별도 진행으로 뺐다): 공개 표
  (pub_macro·pub_market_stats)는 내보내기 역할이 읽고 로그인 표(prv_macro·prv_market.us_universe·
  prv_flows.program_*)는 못 읽는다. 값 없는 행은 ok 불가, 공개 캘린더는 공개 출처만, 보드 산출
  이름에 us_summary·us_brief(US 만).
  프로그램 장중 표는 청크 1일 + 보존 90일
- 권한: kbj_public_export 는 pub_* 만 읽고, prv_*(P3 새 표 포함)·ops 는 못 읽고, 아무것도
  쓰지 못한다
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
    "0007_board.sql",
    "0008_market_flows_p3.sql",
    "0009_etf.sql",
    "0010_filings.sql",
    "0011_fin.sql",
    "0012_fin_prv.sql",
    "0013_journal_watchlist.sql",
    "0014_macro.sql",
    "0015_us_board.sql",
    "0016_market_stats.sql",
    "0017_program.sql",
    "0018_index.sql",
    "0019_trade.sql",
    "0020_diag.sql",
    "0021_rules_journal.sql",
    "0022_gex_ingest.sql",
    "0023_themes.sql",
    "0024_trade_country_code.sql",
    "0025_index_constituent_etf.sql",
    "0026_largest_holder.sql",
]  # 이름은 P2 시험 그대로 — 0001~0023 전부(P3 0007~0009, P4 0010~0013, P5 0014~0017,
#   P5 2차 IDX 0018, P6 0019, P7 0020, P8 0021, P9 GXI 0022 — 쓰기 주인 COMMENT 만,
#   페이지 9 테마 0023 — ADR 0039)
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
    # P3(0007~0009)
    "prv_board.label", "prv_board.stock_day", "prv_market.eod_reconcile",
    "prv_flows.investor_revision", "prv_etf.etf_daily", "prv_etf.holding",
    # P4(0012)
    "prv_fin.valuation_daily",
    # P5(0017)
    "prv_flows.program_stock_daily",
}  # fmt: skip
# P3 장중 이력 — 청크 1일 + 보존(docs/p3_design.md §3.4, R21 [제안])
P3_INTRADAY = {
    "prv_market.index_intraday": "90 days",
    "prv_market.sector_intraday": "90 days",
    "prv_market.turnover_rank_intraday": "90 days",
    "prv_flows.investor_intraday": "90 days",
    "prv_etf.quote_intraday": "30 days",
}
# P5 장중 프로그램 — 청크 1일 + 보존 90일 [제안](docs/p5_design.md §3.3 — 0008 장중 표와 같다)
P5_INTRADAY = {
    "prv_flows.program_market_intraday": "90 days",
    "prv_flows.program_stock_intraday": "90 days",
}
# P8(0021): 관심종목 장중 시세 슬롯 — 7일 청크·730일 보존(백테스트·일치 감시가 읽는다)
P8_INTRADAY = {"prv_alerts.watch_quote_slot": "730 days"}
P3_TABLES = {
    "prv_board.alltime", "prv_board.label", "prv_board.split_check", "prv_board.stock_day",
    "prv_board.artifact", "prv_market.index_intraday", "prv_market.sector_intraday",
    "prv_market.turnover_rank_intraday", "prv_market.eod_reconcile",
    "prv_flows.investor_intraday", "prv_flows.investor_revision", "prv_flows.ledger_check",
    "prv_etf.etf_daily", "prv_etf.quote_intraday", "prv_etf.meta", "prv_etf.split_event",
    "prv_etf.fund", "prv_etf.holding", "prv_etf.change_log",
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
    assert set(got) == GX_DAILY | YEARLY | set(P3_INTRADAY) | set(P5_INTRADAY) | set(P8_INTRADAY)
    assert {got[t] for t in P8_INTRADAY} == {("slot_ts", timedelta(days=7))}
    assert {t: iv for t, (_, iv) in got.items() if t in YEARLY} == dict.fromkeys(
        YEARLY, timedelta(days=365)
    )
    assert {
        iv for t, (_, iv) in got.items() if t in GX_DAILY | set(P3_INTRADAY) | set(P5_INTRADAY)
    } == {timedelta(days=1)}
    assert {got[t][0] for t in P3_INTRADAY} == {"ts"}
    assert {got[t][0] for t in P5_INTRADAY} == {"ts"}
    assert got["prv_etf.holding"][0] == "asof"
    assert got["prv_board.label"][0] == "trade_date"
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


def test_p3_retention_policies(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT hypertable_schema || '.' || hypertable_name, config->>'drop_after' "
        "FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'",
    )
    # 보존 정책은 P3·P5·P8 장중 이력 표에만(원본 녹화는 P7 — 0006 주석)
    assert dict(rows) == P3_INTRADAY | P5_INTRADAY | P8_INTRADAY


def test_p3_tables_exist(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('prv_board', 'prv_etf') OR table_name IN ('index_intraday', "
        "'sector_intraday', 'turnover_rank_intraday', 'eod_reconcile', 'investor_intraday', "
        "'investor_revision', 'ledger_check')",
    )
    assert {r[0] for r in rows} == P3_TABLES


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
                  "prv_alerts.tg_inbox", "prv_gex.investor_flow", *sorted(P3_TABLES)):  # fmt: skip
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
    assert {
        "ops",
        "prv_market",
        "prv_flows",
        "prv_gex",
        "prv_alerts",
        "pub_filings",
        "prv_board",
        "prv_etf",
    } <= schemas


# ── P4(0010~0013 — docs/p4_design.md §3.4) ──

P4_PUB = {
    "pub_filings.disclosure", "pub_filings.corp_profile", "pub_filings.earnings_prelim",
    "pub_filings.earnings_schedule", "pub_filings.overhang_event", "pub_filings.overhang_adjust",
    "pub_filings.overhang_state", "pub_filings.overhang_summary", "pub_filings.insider_report",
    "pub_filings.major_holder_report", "pub_filings.insider_window", "pub_filings.buyback_event",
    "pub_filings.share_count", "pub_filings.largest_holder", "pub_fin.statement_raw",
    "pub_fin.quarterly", "pub_fin.ratio", "pub_fin.report", "pub_fin.segment",
}  # fmt: skip
P4_PRV = {
    "prv_fin.consensus_snapshot", "prv_fin.invest_opinion", "prv_fin.target_consensus",
    "prv_fin.revision", "prv_fin.valuation_daily", "prv_fin.valuation_band", "prv_fin.scenario",
    "prv_fin.earnings_surprise", "prv_market.share_class", "prv_journal.watchlist",
}  # fmt: skip


def test_p4_tables_exist(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('pub_filings', 'pub_fin', 'prv_fin', 'prv_journal') "
        "OR table_name = 'share_class'",
    )
    # P8(0021)이 prv_journal 에 일지·매매·종목 기록 표를 더했다(P8 시험이 따로 본다)
    p8_journal = {t for t in P8_TABLES if t.startswith("prv_journal.")}
    assert {r[0] for r in rows} == P4_PUB | P4_PRV | {"pub_filings.corp_code"} | p8_journal


def test_export_role_reads_p4_pub_tables_but_not_prv(dsn: str) -> None:
    for table in sorted(P4_PUB):
        _as_export(dsn, f"SELECT count(*) FROM {table}")  # noqa: S608 — 상수 표 이름
    for table in sorted(P4_PRV):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as_export(dsn, f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_export(dsn, "DELETE FROM pub_fin.quarterly")


BAD_P4_ROWS = (
    # 공개 표에 로그인 원천을 쓰지 않는다
    "INSERT INTO pub_filings.disclosure (rcept_no, corp_code, corp_name, title, base_title, "
    "rcept_dt, kind, first_seen_at, source, received_at, loaded_by) VALUES ('20261113000001', "
    "'99400010', '합성', 't', 't', '2026-11-13', 'other', now(), 'KIS', now(), 'test')",
    # 대상 결정 공시를 못 이으면 invalid + 사유
    "INSERT INTO pub_filings.overhang_adjust (rcept_no, corp_code, rcept_dt, kind, received_at, "
    "loaded_by) VALUES ('20261113000002', '99400010', '2026-11-13', 'exercise', now(), 'test')",
    # 엑셀 2 MiB 상한
    "INSERT INTO pub_fin.report (stock_code, built_on, corp_code, year_from, year_to, "
    "fs_div_used, payload, xlsx, xlsx_sha256, engine_version, input_digest, generated_at, "
    "loaded_by) VALUES ('994010', '2026-11-16', '99400010', 2023, 2026, 'CFS', '{}', "
    "decode(repeat('00', 2097153), 'hex'), repeat('a', 64), 'v', 'd', now(), 't')",
    # 법정 기한은 추정
    "INSERT INTO pub_filings.earnings_schedule (corp_code, period_end, kind, fiscal_year, "
    "fiscal_q, planned_date, quality, computed_at, loaded_by) VALUES ('99400010', '2026-09-30', "
    "'statutory', 2026, 3, '2026-11-14', 'ok', now(), 't')",
    # 우선주의 보통주 코드는 자기 코드가 아니다
    "INSERT INTO prv_market.share_class (code, share_class, common_code, source, quality, "
    "updated_at, loaded_by) VALUES ('994015', 'pref', '994015', 'krx', 'ok', now(), 't')",
)


@pytest.mark.parametrize("stmt", BAD_P4_ROWS)
def test_p4_constraints(dsn: str, stmt: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(stmt.encode())


# ── P5(0014~0017 — docs/p5_design.md §3.3) ──

P5_PUB = {
    "pub_macro.series_meta", "pub_macro.series", "pub_macro.calendar",
    "pub_market_stats.credit_balance", "pub_market_stats.market_capital",
    "pub_market_stats.fund_nav", "pub_market_stats.cma",
}  # fmt: skip
P5_PRV = {
    "prv_macro.series_meta", "prv_macro.series", "prv_macro.calendar",
    "prv_market.us_universe", "prv_flows.program_market_daily",
    "prv_flows.program_market_intraday", "prv_flows.program_investor_daily",
    "prv_flows.program_stock_intraday", "prv_flows.program_stock_daily",
}  # fmt: skip


def test_p5_tables_exist(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('pub_macro', 'pub_market_stats', 'prv_macro') "
        "OR table_name IN ('us_universe') OR table_name LIKE 'program\\_%'",
    )
    assert {r[0] for r in rows} == P5_PUB | P5_PRV


def test_export_role_reads_p5_pub_tables_but_not_prv(dsn: str) -> None:
    for table in sorted(P5_PUB):
        _as_export(dsn, f"SELECT count(*) FROM {table}")  # noqa: S608 — 상수 표 이름
    for table in sorted(P5_PRV):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as_export(dsn, f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_export(dsn, "DELETE FROM pub_macro.series")


BAD_P5_ROWS = (
    # 값이 없는 행은 ok 일 수 없다(0 으로 채우지 않는다)
    "INSERT INTO pub_macro.series (series_id, period, value, unit, source, as_of, quality, "
    "received_at, loaded_by) VALUES ('FRED:DGS10', '2026-11-13', NULL, '%', 'FRED', now(), 'ok', "
    "now(), 't')",
    # 공개 시리즈 표에 로그인 출처를 쓰지 않는다
    "INSERT INTO pub_macro.series (series_id, period, value, unit, source, as_of, quality, "
    "received_at, loaded_by) VALUES ('YAHOO:CL=F', '2026-11-13', 1, 'USD', 'YAHOO', now(), 'ok', "
    "now(), 't')",
    # 공개 메타에 저작권 시리즈 데이터셋·로그인 tier 를 쓰지 않는다
    "INSERT INTO pub_macro.series_meta (series_id, tab, title, unit, freq, tier, source, dataset, "
    "updated_at, loaded_by) VALUES ('FRED:SP500', 'us', 'S&P', 'pt', 'D', 'public', 'FRED', "
    "'FRED:restricted_series', now(), 't')",
    "INSERT INTO pub_macro.series_meta (series_id, tab, title, unit, freq, tier, source, dataset, "
    "updated_at, loaded_by) VALUES ('FRED:DGS2', 'us', '2Y', '%', 'D', 'private', 'FRED', "
    "'FRED:gov_series', now(), 't')",
    # 공개 캘린더는 공개 출처만(ForexFactory 는 로그인 — D-P5-8)
    "INSERT INTO pub_macro.calendar (event_id, kind, event_date, title, country, importance, "
    "source, quality, received_at, loaded_by) VALUES ('US:release:2026-11-13:cpi', 'release', "
    "'2026-11-13', 'CPI', 'US', 1, 'ff', 'ok', now(), 't')",
    # 금투협 통계 source 는 DATAGO 만
    "INSERT INTO pub_market_stats.credit_balance (stat_date, item, value, unit, source, as_of, "
    "quality, received_at, loaded_by) VALUES ('2026-11-13', 'credit_total', 1, '원', 'KIS', "
    "now(), 'ok', now(), 't')",
    # us_* 산출은 미국 행만
    "INSERT INTO prv_board.artifact (market, trade_date, name, payload, engine_version, "
    "input_digest, as_of, source, quality, generated_at, loaded_by) VALUES ('KR', '2026-11-13', "
    "'us_brief', '{}', 'v', 'd', now(), 's', 'ok', now(), 't')",
    "INSERT INTO prv_board.artifact (market, trade_date, name, payload, engine_version, "
    "input_digest, as_of, source, quality, generated_at, loaded_by) VALUES ('US', '2026-11-13', "
    "'us_other', '{}', 'v', 'd', now(), 's', 'ok', now(), 't')",
    # 프로그램 kind·side 는 KIS 칸 이름만
    "INSERT INTO prv_flows.program_market_daily (trade_date, market, venue, kind, side, source, "
    "quality, received_at, loaded_by) VALUES ('2026-11-13', 'K', 'KRX', 'total', 'smtn', 'kis', "
    "'ok', now(), 't')",
)


@pytest.mark.parametrize("stmt", BAD_P5_ROWS)
def test_p5_constraints(dsn: str, stmt: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(stmt.encode())


def test_p5_us_artifact_names_and_gx_comment(dsn: str) -> None:
    with psycopg.connect(dsn) as c:
        c.execute(
            b"INSERT INTO prv_board.artifact (market, trade_date, name, payload, engine_version, "
            b"input_digest, as_of, source, quality, generated_at, loaded_by) VALUES ('US', "
            b"'2026-11-13', 'us_brief', '{}', 'v', 'd', now(), 's', 'ok', now(), 't'), ('KR', "
            b"'2026-11-13', 'newhigh', '{}', 'v', 'd', now(), 's', 'ok', now(), 't')"
        )
        c.rollback()
    names = _rows(
        dsn,
        "SELECT conname FROM pg_constraint WHERE conrelid = 'prv_board.artifact'::regclass "
        "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%universe_meta%'",
    )
    assert names == [("artifact_name_p5_check",)]  # 0007 의 옛 이름 제약은 지워졌다
    note = _one(dsn, "SELECT obj_description('prv_gex.investor_flow'::regclass, 'pg_class')")
    assert "flows.market_minute" in note and "백만원" in note


# ── P5 2차 묶음 IDX(0018 — 지수 정기변경 예측, 로그인 등급 prv_index) ──

IDX_PRV = {
    "prv_index.constituents", "prv_index.review_calendar", "prv_index.prediction_run",
    "prv_index.predictions", "prv_index.passive_aum",
}  # fmt: skip


def test_idx_tables_exist_and_export_role_cannot_read(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema = 'prv_index'",
    )
    assert {r[0] for r in rows} == IDX_PRV
    for table in sorted(IDX_PRV):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as_export(dsn, f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 — 상수 표 이름


BAD_IDX_ROWS = (
    # 목록 없음 실행은 후보를 낼 수 없다(지어내지 않음)
    "INSERT INTO prv_index.prediction_run (index_id, review_id, base_date, cutoff_kind, status, "
    "n_members, n_add, n_delete, n_borderline, data_as_of, source, quality, computed_at, "
    "loaded_by) VALUES ('msci_korea', '2026-11', '2026-10-30', 'model', 'no_constituents', 0, 1, "
    "0, 0, now(), 's', 'estimated', now(), 't')",
    # 확인 전 일정은 quality ok 가 아니다
    "INSERT INTO prv_index.review_calendar (index_id, review_id, index_title, display_order, "
    "base_date, announce_date, trade_date, effective_date, confirmed, source, quality, "
    "updated_at, loaded_by) VALUES ('msci_korea', '2026-11', 'MSCI Korea', 1, '2026-10-30', "
    "'2026-11-11', '2026-11-30', '2026-12-01', false, 's', 'ok', now(), 't')",
    # 구성종목 출처는 user·press·synthetic 만
    "INSERT INTO prv_index.constituents (index_id, code, valid_from, source, quality, "
    "received_at, loaded_by) VALUES ('msci_korea', '993001', '2026-06-01', 'msci', 'ok', now(), "
    "'t')",
)


@pytest.mark.parametrize("stmt", BAD_IDX_ROWS)
def test_idx_constraints(dsn: str, stmt: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(stmt.encode())


# ── P6 묶음 FND(0019 — 수출입·매핑, 공개 등급 pub_trade — docs/p6_design.md §3.3) ──

P6_PUB = {
    "pub_trade.ten_day", "pub_trade.ten_day_columns", "pub_trade.item_monthly",
    "pub_trade.country_monthly", "pub_trade.item_country_monthly", "pub_trade.sigungu_monthly",
    "pub_trade.hs_dim", "pub_trade.surge", "pub_trade.item_group_monthly",
    "pub_trade.stock_map", "pub_trade.stock_export",
}  # fmt: skip


def test_p6_tables_exist_and_export_role_reads_them(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema = 'pub_trade'",
    )
    assert {r[0] for r in rows} == P6_PUB
    for table in sorted(P6_PUB):
        _as_export(dsn, f"SELECT count(*) FROM {table}")  # noqa: S608 — 상수 표 이름
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_export(dsn, "DELETE FROM pub_trade.stock_map")


BAD_P6_ROWS = (
    # 값이 없는 행은 ok 일 수 없다(0 으로 채우지 않는다)
    "INSERT INTO pub_trade.item_monthly (ym, hs, hs_len, source, as_of, quality, received_at, "
    "loaded_by) VALUES ('2026-09', '8542', 4, 'DATAGO:15101609', now(), 'ok', now(), 't')",
    # hs_len 은 hs 의 자릿수
    "INSERT INTO pub_trade.item_monthly (ym, hs, hs_len, exp_usd, source, as_of, quality, "
    "received_at, loaded_by) VALUES ('2026-09', '8542', 6, 1, 'DATAGO:15101609', now(), 'ok', "
    "now(), 't')",
    # 관세청 행의 source 는 DATAGO:<포털 ID>
    "INSERT INTO pub_trade.country_monthly (ym, country, country_name, exp_usd, source, as_of, "
    "quality, received_at, loaded_by) VALUES ('2026-09', 'US', '미국', 1, 'KIS', now(), 'ok', "
    "now(), 't')",
    # 10일 구간은 1~3
    "INSERT INTO pub_trade.ten_day (kind, ym, seg, col_no, name, usd, first_seen_at, span_text, "
    "source, as_of, quality, received_at, loaded_by) VALUES ('exp_item', '2026-09', 4, 0, '전체', "
    "1000, now(), '01~10', 'DATAGO:15157908', now(), 'estimated', now(), 't')",
    # 지역 매핑 행은 HS6·수출만
    "INSERT INTO pub_trade.stock_map (code, hs, flow, region_id, name, weight, origin, reviewed, "
    "map_version, source, as_of, quality, countries, evidence, received_at, loaded_by) VALUES "
    "('000000', '8542', 'exp', 'KR-41-x', '예시', 0.5, 'public', false, 'v', 'KBJ:trade_map', "
    "now(), 'ok', '{}', '[]', now(), 't')",
    # 겹쳐 보기 상태는 네 가지
    "INSERT INTO pub_trade.stock_export (code, period, status, map_version, source, as_of, "
    "quality, lag_corr, computed_at, loaded_by) VALUES ('000000', '2026Q3', 'great', 'v', "
    "'KBJ:trade.stock_index', now(), 'ok', '{}', now(), 't')",
)


@pytest.mark.parametrize("stmt", BAD_P6_ROWS)
def test_p6_constraints(dsn: str, stmt: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(stmt.encode())


# ── P7 묶음 FND-B(0020 — 진단, pub_diag 공개·prv_diag 로그인 — docs/p7_design.md §3.3) ──

P7_PUB = {"pub_diag.indicator", "pub_diag.gauge", "pub_diag.event_marker"}
P7_PRV = {"prv_diag.indicator", "prv_diag.fear_greed", "prv_diag.verdict", "prv_diag.vol_index"}


def test_p7_tables_exist_and_export_role_reads_pub_only(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('pub_diag', 'prv_diag')",
    )
    assert {r[0] for r in rows} == P7_PUB | P7_PRV
    for table in sorted(P7_PUB):
        _as_export(dsn, f"SELECT count(*) FROM {table}")  # noqa: S608 — 상수 표 이름
    for table in sorted(P7_PRV):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as_export(dsn, f"SELECT count(*) FROM {table}")  # noqa: S608
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_export(dsn, "DELETE FROM pub_diag.gauge")


def test_p7_owner_comments_on_gex_tables(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT c.relname, obj_description(c.oid, 'pg_class') FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'prv_gex' "
        "AND c.relkind IN ('r', 'p')",
    )
    comments = {r[0]: r[1] or "" for r in rows}
    assert "gex.engine" in comments["levels"]
    assert "gex.recorder" in comments["raw_messages"]
    assert "flows.market_minute" in comments["investor_flow"]  # 0017 그대로


BAD_P7_ROWS = (
    # 값이 없는 지표는 ok 일 수 없다
    "INSERT INTO pub_diag.indicator (trade_date, key, unit, source, as_of, quality, computed_at, "
    "loaded_by) VALUES ('2026-10-13', 'credit_spread', '%p', 'KBJ:diag.public', now(), 'ok', "
    "now(), 't')",
    # 보류 판정에는 점수가 없다
    "INSERT INTO pub_diag.gauge (trade_date, label, score, source, as_of, quality, computed_at, "
    "loaded_by) VALUES ('2026-10-13', 'hold', 0, 'KBJ:diag.public', now(), 'invalid', now(), 't')",
    # 이벤트 종류는 넷
    "INSERT INTO pub_diag.event_marker (date, kind, title, source, as_of, quality, computed_at, "
    "loaded_by) VALUES ('2026-10-13', 'holiday', 'x', 'KBJ', now(), 'ok', now(), 't')",
    # 공포탐욕 점수 0~100, 점수·구간은 같이
    "INSERT INTO prv_diag.fear_greed (trade_date, score, band, source, as_of, quality, "
    "computed_at, loaded_by) VALUES ('2026-10-13', 120, 'greed', 'KBJ:diag.daily', now(), 'ok', "
    "now(), 't')",
    "INSERT INTO prv_diag.fear_greed (trade_date, score, source, as_of, quality, computed_at, "
    "loaded_by) VALUES ('2026-10-13', 50, 'KBJ:diag.daily', now(), 'ok', now(), 't')",
    # 종가 없는 VKOSPI 는 ok 일 수 없다
    "INSERT INTO prv_diag.vol_index (trade_date, index_id, source, as_of, quality, received_at, "
    "loaded_by) VALUES ('2026-10-13', 'vkospi', 'KRX:idx/drvprod_dd_trd', now(), 'ok', now(), 't')",
)


@pytest.mark.parametrize("stmt", BAD_P7_ROWS)
def test_p7_constraints(dsn: str, stmt: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(stmt.encode())


# ── P8 묶음 FND(0021 — 규칙 알림·백테스트·저널, 전부 로그인 — docs/p8_design.md §3.3) ──

P8_TABLES = {
    "prv_alerts.rule",
    "prv_alerts.rule_version",
    "prv_alerts.watch_quote_slot",
    "prv_alerts.signal_intraday",
    "prv_alerts.signal_close",
    "prv_alerts.signal_outcome",
    "prv_alerts.backtest_run",
    "prv_alerts.backtest_signal",
    "prv_alerts.match_check",
    "prv_journal.analysis",
    "prv_journal.trade",
    "prv_journal.pick",
}


def test_p8_tables_exist_and_export_role_cannot_read(dsn: str) -> None:
    rows = _rows(
        dsn,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('prv_alerts', 'prv_journal') AND table_type = 'BASE TABLE'",
    )
    assert P8_TABLES <= {r[0] for r in rows}
    for table in sorted(P8_TABLES | {"prv_alerts.signal"}):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as_export(dsn, f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 — 상수 표 이름
    hyper = _rows(
        dsn,
        "SELECT hypertable_schema || '.' || hypertable_name "
        "FROM timescaledb_information.hypertables WHERE hypertable_schema = 'prv_alerts'",
    )
    assert {r[0] for r in hyper} == {"prv_alerts.watch_quote_slot"}


BAD_P8_ROWS = (
    # 규칙 id 형식(r_ + 소문자 영숫자 12자)
    "INSERT INTO prv_alerts.rule (rule_id, name, current_version, enabled, source, created_at, "
    "updated_at, loaded_by) VALUES ('r_bad', 'x', 1, false, 'api', now(), now(), 't')",
    # 슬롯 현재가가 없으면 quality ok 일 수 없다
    "INSERT INTO prv_alerts.watch_quote_slot (slot_ts, code, quality, source) "
    "VALUES (now(), '005930', 'ok', 'KIS')",
    # 성과는 값이 없으면 사유가 있어야 한다
    "INSERT INTO prv_alerts.signal_outcome (signal_id, horizon, due_date, quality, computed_at, "
    "source) VALUES ('si_01JBX0000000000000000000AB', 1, '2026-11-03', 'invalid', now(), 'KBJ')",
    # 매매 수량은 양수, 쪽은 buy·sell 만
    "INSERT INTO prv_journal.trade (code, side, trade_date, price, qty, fee, tax, source, "
    "created_at) VALUES ('005930', 'short', '2026-11-02', 100, 1, 0, 0, 'api', now())",
    "INSERT INTO prv_journal.trade (code, side, trade_date, price, qty, fee, tax, source, "
    "created_at) VALUES ('005930', 'buy', '2026-11-02', 100, 0, 0, 0, 'api', now())",
)


@pytest.mark.parametrize("stmt", BAD_P8_ROWS)
def test_p8_constraints(dsn: str, stmt: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as c:
        c.execute(stmt.encode())
