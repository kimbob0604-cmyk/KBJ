"""TimescaleDB 저장 통합 테스트 (PLAN §4.5, docs/phase1_design.md §8·§10).

- 마이그레이션(두 번 적용·hypertable·압축 정책·동시 실행)
- 가짜 KIS 서버 → poller 수집기 → PostgresSink 로 쓴 실제 레코드를 읽어 메모리 싱크와 맞춘다
  (trade_date·session·UTC ts·값). 같은 묶음을 다시 써도 행이 늘지 않는다
- recorder 원본(웹소켓 문자열·장 밖 수신)·auth health·재연결·오류 가림·압축 청크 중복
- scheduler 적재: KRX 일별(야간 IMP_VOLT '0.00' → NULL 유지)·분봉(야간 24~30시)·마스터
- ws-gateway 체결(같은 초는 seq 로)·세션 상태 전이 기록·공백 재판정
- 무결측 판정 입력 읽기(수집기가 쓴 것 = 메모리 것)·리포트 교체(설계 §9, 002_collection_reports)

컨테이너는 tests/integration/conftest.py 가 세션마다 직접 띄우고 지운다. Docker 가 없으면 건너뛴다.
시나리오마다 빈 DB 를 새로 만들어 서로의 행이 섞이지 않게 한다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import sql

from core.calendar import Session, State, TradingCalendar, state_at
from data.kis.master import parse_master
from data.kis.models import MinuteBar
from data.kis.ratelimit import LocalRateLimiter
from data.kis.ws import FuturesTick, OptionTick, WsData, load_fields, parse_frame, ticks_from
from data.krx.models import parse_futures_rows, parse_option_rows
from data.store import (
    CHAIN_SNAPSHOTS,
    AuthHealthSink,
    CollectionGapRecord,
    CollectionReportRecord,
    FuturesTickRecord,
    MinuteBarRecord,
    OptionTickRecord,
    PostgresSink,
    SessionLogRecord,
    StoreError,
    attr_row,
    krx_loadable,
)
from db.migrate import MigrationError, apply, discover, migrate
from scripts.probe_common import TR_CALLPUT, TR_INVESTOR
from services.auth.health import HealthEvent as AuthHealthEvent
from services.poller.collector import Collector
from services.poller.config import PollerConfig
from services.poller.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
)
from services.poller.sink import InMemorySink
from services.recorder.envelope import RawEnvelope, wrap
from tests.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    StaticTokenProvider,
    default_chain,
    make_client,
)
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
DAY = date(2026, 9, 28)
HYPERTABLES = {
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
    "session_log",
    "health_events",
    "collection_gaps",
    "quarantine",
    "collection_reports",
    "levels",  # 003_engine
    "metrics",
    "strike_gex",
    "option_iv",
    "oi_changes",
}
MIGRATIONS = [m.name for m in discover()]
Query = str | sql.Composed


def _rows(dsn: str, query: Query, *params: object) -> list[tuple[Any, ...]]:
    q = query.encode() if isinstance(query, str) else query
    with psycopg.connect(dsn) as c:
        return c.execute(q, params or None).fetchall()


def _one(dsn: str, query: Query, *params: object) -> Any:
    rows = _rows(dsn, query, *params)
    assert len(rows) == 1
    return rows[0][0]


def _count(dsn: str, table: str) -> int:
    n = _one(dsn, sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
    assert isinstance(n, int)
    return n


def _migrated(pg: PgContainer, prefix: str) -> str:
    url = pg.dsn(pg.fresh_database(prefix))
    migrate(url)
    return url


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    """마이그레이션을 두 번 적용한 DB (두 번째는 할 일이 없어야 한다)."""
    url = timescale.dsn(timescale.fresh_database("store"))
    want = ["001_init.sql", "002_collection_reports.sql", "003_engine.sql", "004_flags.sql"]
    assert migrate(url) == MIGRATIONS == want
    assert migrate(url) == []
    return url


# ── 마이그레이션 ──


def test_migrations_are_recorded_once(dsn: str) -> None:
    rows = _rows(dsn, "SELECT version, name, checksum FROM schema_migrations")
    assert rows == [(m.version, m.name, m.checksum) for m in discover()]


def test_every_table_is_a_hypertable_with_its_chunk_interval(dsn: str) -> None:
    ht = _rows(
        dsn,
        "SELECT h.hypertable_name, d.column_name, d.time_interval "
        "FROM timescaledb_information.hypertables h "
        "JOIN timescaledb_information.dimensions d USING (hypertable_schema, hypertable_name)",
    )
    got = {name: (col, iv) for name, col, iv in ht}
    assert set(got) == HYPERTABLES
    yearly = {"krx_fut_daily", "krx_opt_daily", "master_snapshots"}  # 일 단위 자료 — 청크 1년
    assert {t: iv for t, (_, iv) in got.items() if t in yearly} == dict.fromkeys(
        yearly, timedelta(days=365)
    )
    assert {iv for t, (_, iv) in got.items() if t not in yearly} == {timedelta(days=1)}
    assert got["krx_opt_daily"][0] == "trade_date" and got["collection_gaps"][0] == "start_ts"
    assert got["collection_reports"][0] == "trade_date"
    assert got["chain_snapshots"][0] == "ts"


def test_raw_messages_compression_policy(dsn: str) -> None:
    compressed = _one(
        dsn,
        "SELECT compression_enabled FROM timescaledb_information.hypertables "
        "WHERE hypertable_name = 'raw_messages'",
    )
    assert compressed is True
    after = _one(
        dsn,
        "SELECT config->>'compress_after' FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_compression' AND hypertable_name = 'raw_messages'",
    )
    assert after == "3 days"


def test_apply_requires_autocommit(dsn: str) -> None:
    with psycopg.connect(dsn) as c, pytest.raises(MigrationError):
        apply(c)


def test_concurrent_migrate_on_fresh_db_applies_once(timescale: PgContainer) -> None:
    """두 프로세스가 동시에 돌려도 한쪽만 적용한다 (advisory lock + schema_migrations)."""
    url = timescale.dsn(timescale.fresh_database("race"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _: migrate(url), range(2)))
    assert results == [[], MIGRATIONS]
    assert _count(url, "schema_migrations") == len(MIGRATIONS)


# ── PostgresSink: poller Sink 프로토콜 (가짜 KIS → 수집기 → DB) ──


class TeeSink:
    """수집기 산출을 메모리와 DB 에 똑같이 쓴다 — DB 에서 읽은 것을 메모리 것과 맞춘다."""

    def __init__(self, pg: PostgresSink) -> None:
        self.mem = InMemorySink()
        self.pg = pg

    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        self.mem.write_chain(rows)
        self.pg.write_chain(rows)

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self.mem.write_futures(rows)
        self.pg.write_futures(rows)

    def write_investor(self, rows: Sequence[InvestorRecord]) -> None:
        self.mem.write_investor(rows)
        self.pg.write_investor(rows)

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None:
        self.mem.write_expiries(rows)
        self.pg.write_expiries(rows)

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        self.mem.write_raw(envelopes)
        self.pg.write_raw(envelopes)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        self.mem.write_quarantine(rows)
        self.pg.write_quarantine(rows)

    def write_health(self, events: Sequence[HealthEvent]) -> None:
        self.mem.write_health(events)
        self.pg.write_health(events)


def _bad_board(body: dict[str, Any]) -> None:
    body["output1"][0]["acpr"] = "abc"  # 행사가를 못 읽는 행 — 격리만
    body["output2"][1]["hts_otst_stpl_qty"] = "x"  # 행사가는 읽힌다 — invalid 레코드


def _collect(
    pg: PostgresSink,
    start: datetime,
    end: datetime,
    cfg: PollerConfig | None = None,
    faults: bool = False,
) -> InMemorySink:
    clock = FakeClock(start)
    chain = default_chain()
    server = FakeKisServer(clock, chain)
    kis = make_client(server, LocalRateLimiter(clock=clock), StaticTokenProvider())
    tee = TeeSink(pg)
    col = Collector(kis, tee, calendar=CAL, clock=clock, config=cfg, master=chain.master_rows())
    if faults:  # 검증 실패 행 → quarantine + invalid 레코드 + health
        server.inject(
            "malformed", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "260904"}, mutate=_bad_board
        )
        server.inject(
            "malformed",
            tr_id=TR_INVESTOR,
            params={"FID_INPUT_ISCD_2": "F001"},
            mutate=lambda b: b["output"][0].pop("frgn_seln_vol"),
        )
    col.run_until(end)
    assert col.stats.sink_errors == 0
    return tee.mem


POLLER_TABLES = {
    "chain_snapshots": "chain",
    "fut_board": "futures",
    "investor_flow": "investor",
    "series_expiries": "expiries",
    "raw_messages": "raw",
    "quarantine": "quarantine",
    "health_events": "health",
}


def _replay(pg: PostgresSink, mem: InMemorySink) -> None:
    pg.write_chain(mem.chain)
    pg.write_futures(mem.futures)
    pg.write_investor(mem.investor)
    pg.write_expiries(mem.expiries)
    pg.write_raw(mem.raw)
    pg.write_quarantine(mem.quarantine)
    pg.write_health(mem.health)


@dataclass(frozen=True)
class Run:
    dsn: str
    mem: InMemorySink
    counts: dict[str, int]


@pytest.fixture(scope="module")
def day_run(timescale: PgContainer) -> Run:
    """2026-09-28 10:00~10:01 KST 주간 수집(검증 실패 행 포함)을 빈 DB 로."""
    url = _migrated(timescale, "day")
    start = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    with PostgresSink(url, service="poller") as pg:
        mem = _collect(pg, start, start + timedelta(minutes=1), faults=True)
    return Run(url, mem, {t: _count(url, t) for t in POLLER_TABLES})


def test_poller_records_round_trip_with_trade_date_session_and_utc_ts(day_run: Run) -> None:
    mem, url = day_run.mem, day_run.dsn
    assert day_run.counts == {t: len(getattr(mem, attr)) for t, attr in POLLER_TABLES.items()}
    assert day_run.counts["chain_snapshots"] > 1000
    assert day_run.counts["quarantine"] > 0 and day_run.counts["health_events"] > 0
    for t in POLLER_TABLES:
        q = sql.SQL("SELECT DISTINCT trade_date, session FROM {}").format(sql.Identifier(t))
        assert _rows(url, q) == [(DAY, "day")], t
    # 시각: 10:00~10:01 KST = 01:00~01:01 UTC, 메모리의 ts 와 마이크로초까지 같다
    assert {r[0] for r in _rows(url, "SELECT ts FROM chain_snapshots")} == {c.ts for c in mem.chain}
    lo, hi = _rows(url, "SELECT min(ts), max(ts) FROM raw_messages")[0]
    utc_start = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
    assert utc_start <= lo <= hi < utc_start + timedelta(minutes=1)


def test_chain_values_and_quality_survive_the_round_trip(day_run: Run) -> None:
    cols = sql.SQL(", ").join(map(sql.Identifier, CHAIN_SNAPSHOTS.columns))
    q = sql.SQL("SELECT {} FROM chain_snapshots").format(cols)
    got = {r[:9]: r for r in _rows(day_run.dsn, q)}
    chain = day_run.mem.chain
    sample = chain[:: max(1, len(chain) // 50)] + [c for c in chain if c.quality == "invalid"]
    for rec in sample:
        row = attr_row(CHAIN_SNAPSHOTS, rec)
        assert got[row[:9]] == row  # 값·Decimal 자릿수·NULL 까지 같다
    quoted, fills = _rows(
        day_run.dsn,
        "SELECT count(*) FILTER (WHERE bid IS NOT NULL OR ask IS NOT NULL), count(*) "
        "FROM chain_snapshots WHERE source = 'fill'",
    )[0]
    assert quoted == 0 and fills > 0  # 단건엔 호가가 없다
    n_invalid = _one(day_run.dsn, "SELECT count(*) FROM chain_snapshots WHERE quality = 'invalid'")
    assert n_invalid == sum(c.quality == "invalid" for c in chain) > 0


def test_quarantine_health_and_raw_payloads_round_trip(day_run: Run) -> None:
    mem, url = day_run.mem, day_run.dsn
    q = {(r[0], r[1]): r[2] for r in _rows(url, "SELECT tr_id, error, payload FROM quarantine")}
    assert q == {(r.tr_id, r.error): r.payload for r in mem.quarantine}
    h = _rows(url, "SELECT service, kind, level, detail FROM health_events")
    assert {r[:3] for r in h} == {("poller", e.kind, e.level) for e in mem.health}
    assert sorted(r[3]["job"] for r in h if r[1] == "quarantine") == sorted(
        e.detail["job"] for e in mem.health if e.kind == "quarantine"
    )
    raw_rows = _rows(url, "SELECT ts, tr_id, key, payload FROM raw_messages")
    raw = {(r[0], r[1], r[2]): r[3] for r in raw_rows}
    assert raw == {(e.received_at, e.tr_id, e.key): e.payload for e in mem.raw}


def test_rewriting_the_same_batches_adds_nothing(day_run: Run) -> None:
    with PostgresSink(day_run.dsn, service="poller") as pg:
        _replay(pg, day_run.mem)
        _replay(pg, day_run.mem)
        assert pg.stats.failures == 0 and pg.stats.batches == 14
    assert {t: _count(day_run.dsn, t) for t in POLLER_TABLES} == day_run.counts


def test_night_run_is_tagged_next_trading_day(timescale: PgContainer) -> None:
    url = _migrated(timescale, "night")
    start = datetime(2026, 9, 28, 21, 0, tzinfo=KST)
    with PostgresSink(url, service="poller") as pg:
        mem = _collect(pg, start, start + timedelta(seconds=40), PollerConfig(night_mode="A"))
    assert mem.chain and _count(url, "chain_snapshots") == len(mem.chain)
    for t in ("chain_snapshots", "raw_messages", "investor_flow"):
        q = sql.SQL("SELECT DISTINCT trade_date, session FROM {}").format(sql.Identifier(t))
        assert _rows(url, q) == [(date(2026, 9, 29), "night")], t  # 월요일 밤 → 화요일 귀속
    lo = _one(url, "SELECT min(ts) FROM chain_snapshots")
    assert lo >= datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # 21:00 KST


# ── recorder·auth·연결 ──


def _tagger(ts: datetime) -> tuple[date | None, Session | None]:
    info = state_at(ts, CAL)
    return info.trade_date, info.session


def test_websocket_text_frames_and_idle_envelopes(dsn: str) -> None:
    at = datetime(2026, 9, 28, 14, 27, 1, 123456, tzinfo=KST)
    frame = "0|H0IFCNT0|001|A01612^142701^1095.10"
    ws = wrap(frame, source="kis_ws", tr_id="H0IFCNT0", received_at=at, tagger=_tagger)
    idle_at = datetime(2026, 9, 27, 12, 0, tzinfo=KST)  # 일요일 — 태그 없음
    idle = wrap({"x": 1}, source="krx", tr_id="opt_bydd_trd", received_at=idle_at, tagger=_tagger)
    with PostgresSink(dsn, service="recorder") as pg:
        pg.write_raw([ws, idle])
        pg.write_raw([ws, idle])
    got = _rows(
        dsn,
        "SELECT ts, trade_date, session, payload, payload_text, lossy FROM raw_messages "
        "WHERE tr_id IN ('H0IFCNT0', 'opt_bydd_trd') ORDER BY ts",
    )
    assert got == [
        (idle_at, None, None, {"x": 1}, None, False),
        (at, DAY, "day", None, frame, False),
    ]


def test_nul_in_identity_text_keeps_the_whole_batch(dsn: str) -> None:
    """tr_id 의 NUL 하나로 같은 묶음의 멀쩡한 원문까지 거부되지 않는다 — 바꿔 넣고 lossy."""
    at = datetime(2026, 9, 28, 14, 30, tzinfo=KST)
    bad = wrap("0|x", source="kis_ws", tr_id="NUL\x00CNT0", received_at=at, tagger=_tagger)
    ok = wrap("0|y", source="kis_ws", tr_id="NUL_OK", received_at=at, tagger=_tagger)
    with PostgresSink(dsn, service="recorder") as pg:
        pg.write_raw([bad, ok])
        pg.write_raw([bad, ok])  # 다시 써도 그대로 (digest 는 넣은 값으로)
        pg.write_health([AuthHealthEvent("nul\x00kind", "d", at, "warning", service="au\x00th")])
    got = _rows(
        dsn,
        "SELECT tr_id, payload_text, lossy FROM raw_messages "
        "WHERE tr_id LIKE 'NUL%' ORDER BY lossy DESC",
    )
    assert got == [("NUL\ufffdCNT0", "0|x", True), ("NUL_OK", "0|y", False)]
    assert _rows(dsn, "SELECT service FROM health_events WHERE kind = 'nul\ufffdkind'") == [
        ("au\ufffdth",)
    ]


def test_auth_health_sink_tags_events(dsn: str) -> None:
    at = datetime(2026, 9, 28, 21, 30, tzinfo=KST)
    with PostgresSink(dsn, service="auth") as pg:
        sink = AuthHealthSink(pg, tagger=_tagger)
        sink.emit(AuthHealthEvent("ws_key_refresh_failed", "발급 실패 EGW00133", at, "critical"))
        sink.emit(AuthHealthEvent("ws_key_refresh_failed", "발급 실패 EGW00133", at, "critical"))
    got = _rows(
        dsn,
        "SELECT ts, trade_date, session, service, level, message FROM health_events "
        "WHERE kind = 'ws_key_refresh_failed'",
    )
    assert got == [(at, date(2026, 9, 29), "night", "auth", "critical", "발급 실패 EGW00133")]


def _health(kind: str) -> HealthEvent:
    return HealthEvent(
        ts=datetime.now(UTC),
        trade_date=None,
        session=None,
        kind=f"probe_{kind}",
        level="info",
        message="연결 확인",
    )


def test_sink_reconnects_after_the_server_drops_it(dsn: str, timescale: PgContainer) -> None:
    with PostgresSink(dsn, service="reconnect-probe") as pg:
        pg.write_health([_health("before")])
        with timescale.admin() as admin:
            killed = admin.execute(
                b"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                b"WHERE application_name = 'gexlab-reconnect-probe'"
            ).fetchone()
        assert killed == (1,)
        pg.write_health([_health("after")])
        assert pg.stats.reconnects == 1 and pg.stats.failures == 0
    kinds = {r[0] for r in _rows(dsn, "SELECT kind FROM health_events WHERE kind LIKE 'probe_%'")}
    assert kinds == {"probe_before", "probe_after"}


def test_errors_are_store_errors_without_the_password(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("nomig"))  # 마이그레이션 안 한 DB → 표 없음
    with PostgresSink(url, service="poller") as pg, pytest.raises(StoreError) as e:
        pg.write_health([_health("x")])
    assert "UndefinedTable" in str(e.value) and timescale.password not in str(e.value)
    wrong = url.replace(timescale.password, "wrong-" + timescale.password)
    with PostgresSink(wrong, retries=0) as bad, pytest.raises(StoreError) as e2:
        bad.write_health([_health("x")])
    assert timescale.password not in str(e2.value) and "OperationalError" in str(e2.value)


def test_duplicates_are_found_in_compressed_raw_chunks(dsn: str) -> None:
    """압축된 청크(3일 뒤)에 다시 써도 중복이 생기지 않는다 — digest 가 압축 orderby 에 있다."""
    old = datetime.now(UTC) - timedelta(days=5)
    envs = [
        wrap({"i": i}, source="kis_rest", tr_id="COMPRESS", received_at=old, tagger=_tagger)
        for i in range(3)
    ]
    with PostgresSink(dsn, service="recorder") as pg:
        pg.write_raw(envs)
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(
                b"SELECT compress_chunk(ch, if_not_compressed => true) "
                b"FROM show_chunks('raw_messages', older_than => now() - INTERVAL '4 days') ch"
            )
        compressed = _one(
            dsn,
            "SELECT count(*) FROM timescaledb_information.chunks "
            "WHERE hypertable_name = 'raw_messages' AND is_compressed",
        )
        assert compressed >= 1
        pg.write_raw(envs)
    assert _one(dsn, "SELECT count(*) FROM raw_messages WHERE tr_id = 'COMPRESS'") == 3


# ── scheduler 적재: KRX 일별·분봉·마스터 ──

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_krx_daily_night_iv_stays_null_and_reload_updates(timescale: PgContainer) -> None:
    url = _migrated(timescale, "krx")
    raw = _fixture("krx/opt_daily.json")
    opt = parse_option_rows(raw["20260923"])[0] + parse_option_rows(raw["20100104"])[0]
    fut = parse_futures_rows(_fixture("krx/fut_daily.json")["rows"])[0]
    first = datetime(2026, 9, 24, 8, 5, tzinfo=KST)
    with PostgresSink(url, service="scheduler") as pg:
        assert pg.write_krx_options(opt, ts=first) == 15  # 코스피200 계열 13 + 2010 2
        assert pg.write_krx_futures(fut, ts=first) == 3
        again = first + timedelta(hours=1)
        assert pg.write_krx_options(opt, ts=again) == 15  # 다시 받아도 행은 그대로, 값은 최신
    rows = _rows(url, "SELECT trade_date, session, isu_cd, imp_volt, ts FROM krx_opt_daily")
    assert len(rows) == 15 and {r[4] for r in rows} == {again}
    night = [r for r in rows if r[1] == "night"]
    assert len(night) == 6 and all(r[3] is None for r in night)  # 야간 '0.00' → NULL
    day_iv = {(r[0], r[2]): r[3] for r in rows if r[1] == "day"}
    assert day_iv[(date(2026, 9, 23), "B016AZC8")] == Decimal("35.20")
    assert day_iv[(date(2010, 1, 4), "301E3Z2O")] == Decimal("20.80")
    iv_null_by_session = _rows(
        url,
        "SELECT session, count(*) FILTER (WHERE imp_volt IS NULL) FROM krx_opt_daily "
        "WHERE trade_date = '2026-09-23' GROUP BY session ORDER BY session",
    )
    assert iv_null_by_session == [("day", 0), ("night", 6)]
    setl_rows = _rows(url, "SELECT session, setl_prc FROM krx_fut_daily WHERE isu_cd = 'A056A000'")
    setl = {r[0]: r[1] for r in setl_rows}
    assert setl == {"day": Decimal("1120.35"), "night": None}


def test_krx_loaded_and_option_listing_read_what_was_written(timescale: PgContainer) -> None:
    """scheduler KRX 적재의 이어 받기(그날 행이 있나)·마스터 대조 입력(상장 목록)."""
    url = _migrated(timescale, "krxread")
    opt = parse_option_rows(_fixture("krx/opt_daily.json")["20260923"])[0]
    fut = parse_futures_rows(_fixture("krx/fut_daily.json")["rows"])[0]
    at = datetime(2026, 9, 28, 8, 5, tzinfo=KST)
    d23 = date(2026, 9, 23)
    with PostgresSink(url, service="scheduler") as pg:
        assert not pg.krx_loaded("options", d23) and not pg.krx_loaded("futures", d23)
        assert pg.krx_option_listing(d23) == []
        pg.write_krx_options(opt, ts=at)
        assert pg.krx_loaded("options", d23) and not pg.krx_loaded("futures", d23)
        pg.write_krx_futures(fut, ts=at)
        assert pg.krx_loaded("futures", d23)
        assert not pg.krx_loaded("options", date(2026, 9, 22))
        listing = pg.krx_option_listing(d23)
    want = sorted({(r.family, r.expiry, r.cp, r.strike) for r in opt if krx_loadable(r)})
    assert len(want) == 11 and listing == want  # 주간·야간 두 행은 하나로
    assert ("kospi200_weekly_mon", "260904", "P", Decimal("970.00")) in listing


def _bar(date_s: str, hhmmss: str, **kw: str) -> MinuteBar:
    row = _fixture("kis/minute_day.json")["output2"][0]
    return MinuteBar.model_validate(row | {"stck_bsop_date": date_s, "stck_cntg_hour": hhmmss} | kw)


def test_minute_bars_day_and_night_with_reload_update(dsn: str) -> None:
    got_at = datetime(2026, 9, 28, 15, 50, tzinfo=KST)
    day = [
        MinuteBarRecord.from_kis(
            MinuteBar.model_validate(r), code="A01612", market="F", received_at=got_at, calendar=CAL
        )
        for r in _fixture("kis/minute_day.json")["output2"]
    ]
    night = [
        MinuteBarRecord.from_kis(
            _bar(d, h), code="A01612", market="CM", received_at=got_at, calendar=CAL
        )
        for d, h in [("20260922", "300000"), ("20260922", "181500"), ("20260918", "280700")]
    ]
    with PostgresSink(dsn, service="scheduler") as pg:
        pg.write_minute_bars(day + night)
        fixed = MinuteBarRecord.from_kis(
            _bar("20260922", "300000", futs_prpr="1100.05"),
            code="A01612",
            market="CM",
            received_at=got_at,
            calendar=CAL,
        )
        pg.write_minute_bars([*day, fixed])  # 다시 적재 — 마지막 봉 값만 바뀐다
    rows = _rows(dsn, "SELECT ts, trade_date, session, market, close FROM minute_bars ORDER BY ts")
    assert len(rows) == 6
    by_ts = {r[0]: r[1:] for r in rows}
    assert by_ts[datetime(2026, 9, 22, 21, 0, tzinfo=UTC)] == (
        date(2026, 9, 23),
        "night",
        "CM",
        Decimal("1100.05"),
    )
    assert by_ts[datetime(2026, 9, 18, 19, 7, tzinfo=UTC)][:2] == (date(2026, 9, 21), "night")
    assert by_ts[datetime(2026, 9, 28, 5, 1, tzinfo=UTC)][:3] == (DAY, "day", "F")


def test_master_snapshots_per_session(dsn: str) -> None:
    rows = parse_master("\n".join(_fixture("kis/master_lines.json")["lines"]))
    at = datetime(2026, 9, 28, 8, 1, tzinfo=KST)
    with PostgresSink(dsn, service="scheduler") as pg:
        pg.write_master(rows, ts=at, trade_date=DAY, session="day")
        pg.write_master(
            rows, ts=at + timedelta(hours=10), trade_date=date(2026, 9, 29), session="night"
        )
        pg.write_master(rows, ts=at + timedelta(minutes=5), trade_date=DAY, session="day")
    counts = _rows(
        dsn,
        "SELECT trade_date, session, count(*), max(ts) FROM master_snapshots "
        "GROUP BY 1, 2 ORDER BY 1",
    )
    assert counts == [
        (DAY, "day", len(rows), at + timedelta(minutes=5)),
        (date(2026, 9, 29), "night", len(rows), at + timedelta(hours=10)),
    ]
    atm = _rows(
        dsn,
        "SELECT cp, strike, atm_cls, expiry FROM master_snapshots "
        "WHERE code = 'B01610ZCI' AND session = 'day'",
    )
    assert atm == [("C", Decimal("1125.00"), "1", "202610")]
    fut = _rows(dsn, "SELECT cp, strike, atm_cls FROM master_snapshots WHERE code = 'A01612'")
    assert set(fut) == {(None, None, None)}


# ── ws-gateway 체결·scheduler 기록 ──


def _fut_tick(price: str, qty: str) -> FuturesTick:
    """SYNTHETIC: config/kis_ws_fields.yaml 컬럼으로 만든 주간 선물 체결 한 건."""
    spec = load_fields()["H0IFCNT0"]
    vals = {"futs_shrn_iscd": "A01612", "bsop_hour": "142701", "futs_prpr": price, "last_cnqn": qty}
    rec = "^".join(vals.get(c, "0") for c in spec.columns)
    frame = parse_frame(f"0|H0IFCNT0|001|{rec}")
    assert isinstance(frame, WsData)
    (tick,) = ticks_from(frame)
    assert isinstance(tick, FuturesTick)
    return tick


def test_ticks_same_second_are_kept_by_seq_and_rewrites_add_nothing(dsn: str) -> None:
    ts = datetime(2026, 9, 28, 14, 27, 1, tzinfo=KST)
    got = ts + timedelta(milliseconds=40)
    recs = [
        FuturesTickRecord.from_tick(_fut_tick(p, q), ts=ts, trade_date=DAY, seq=i, received_at=got)
        for i, (p, q) in enumerate([("1095.10", "3"), ("1095.15", "1"), ("1095.10", "2")])
    ]
    with PostgresSink(dsn, service="ws-gateway") as pg:
        pg.write_fut_ticks(recs)
        pg.write_fut_ticks(recs)
    rows = _rows(dsn, "SELECT seq, price, qty, trade_date, session FROM fut_ticks ORDER BY seq")
    assert rows == [
        (0, Decimal("1095.10"), 3, DAY, "day"),
        (1, Decimal("1095.15"), 1, DAY, "day"),
        (2, Decimal("1095.10"), 2, DAY, "day"),
    ]


def test_option_ticks_carry_series_from_master(dsn: str) -> None:
    master = next(
        r
        for r in parse_master("\n".join(_fixture("kis/master_lines.json")["lines"]))
        if r.family == "kospi200_weekly_thu" and r.cp == "P"
    )
    spec = load_fields()["H0EUCNT0"]
    vals = {"optn_shrn_iscd": master.code, "bsop_hour": "213000", "optn_prpr": "4.15"}
    frame = parse_frame("0|H0EUCNT0|001|" + "^".join(vals.get(c, "0") for c in spec.columns))
    assert isinstance(frame, WsData)
    (tick,) = ticks_from(frame)
    assert isinstance(tick, OptionTick)
    ts = datetime(2026, 9, 28, 21, 30, tzinfo=KST)
    rec = OptionTickRecord.from_tick(
        tick, ts=ts, trade_date=date(2026, 9, 29), seq=0, received_at=ts, master=master
    )
    with PostgresSink(dsn, service="ws-gateway") as pg:
        pg.write_opt_ticks([rec, rec])
    rows = _rows(dsn, "SELECT trade_date, session, mrkt_cls, expiry, strike, cp, iv FROM opt_ticks")
    assert rows == [(date(2026, 9, 29), "night", "WKI", master.expiry, master.strike, "P", None)]


def test_session_log_for_a_day_of_transitions(dsn: str) -> None:
    """2026-09-28(월): IDLE → PRE_DAY → DAY → POST_DAY → PRE_NIGHT → NIGHT(09-29 귀속) → IDLE."""
    times = [(8, 0), (8, 45), (15, 45), (17, 50), (18, 0)]
    recs: list[SessionLogRecord] = []
    prev = State.IDLE
    for h, m in times:
        at = datetime(2026, 9, 28, h, m, tzinfo=KST)
        info = state_at(at, CAL)
        recs.append(SessionLogRecord.transition(at, info, prev))
        prev = info.state
    end = datetime(2026, 9, 29, 6, 0, tzinfo=KST)
    recs.append(SessionLogRecord.transition(end, state_at(end, CAL), prev))
    recs.append(
        SessionLogRecord(
            ts=datetime(2026, 9, 28, 18, 3, tzinfo=KST),
            trade_date=date(2026, 9, 29),
            session="night",
            kind="calendar_mismatch",
            detail={"reason": "개장 3분 안에 선물 체결 없음"},
        )
    )
    with PostgresSink(dsn, service="scheduler") as pg:
        pg.write_session_log(recs)
        pg.write_session_log(recs)
    rows = _rows(dsn, "SELECT kind, state, trade_date, session FROM session_log ORDER BY ts, kind")
    assert rows == [
        ("transition", "PRE_DAY", None, None),
        ("transition", "DAY", DAY, "day"),
        ("transition", "POST_DAY", None, None),
        ("transition", "PRE_NIGHT", None, None),
        ("transition", "NIGHT", date(2026, 9, 29), "night"),
        ("calendar_mismatch", None, date(2026, 9, 29), "night"),
        ("transition", "IDLE", None, None),
    ]


def test_collection_gap_is_updated_when_judged_again(dsn: str) -> None:
    start = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    gap = CollectionGapRecord(
        stream="board:WKM:260904",
        start_ts=start,
        end_ts=start + timedelta(seconds=75),
        trade_date=DAY,
        session="day",
        expected=3,
        received=1,
        detected_at=start + timedelta(minutes=2),
    )
    longer = gap.model_copy(
        update={"end_ts": start + timedelta(seconds=140), "expected": 5, "detected_at": start}
    )
    with PostgresSink(dsn, service="scheduler") as pg:
        pg.write_gaps([gap])
        pg.write_gaps([longer])
    rows = _rows(dsn, "SELECT stream, start_ts, end_ts, expected, received FROM collection_gaps")
    assert rows == [("board:WKM:260904", start, start + timedelta(seconds=140), 5, 1)]


# ── 무결측 판정 입력·리포트 (설계 §9) ──


def test_gap_inputs_read_what_the_collector_wrote(day_run: Run) -> None:
    """수집기가 쓴 것을 판정 입력으로 읽으면 메모리 싱크에서 고른 것과 같다 — 검증 실패 행은 빼고,
    보강 1·기초자산은 원문 일 키·rt_cd 0 으로."""
    mem = day_run.mem
    with PostgresSink(day_run.dsn, service="scheduler") as pg:
        board = pg.board_times(DAY, "day")
        fut = pg.fut_board_times(DAY, "day")
        under = pg.underlying_times(DAY, "day")
        inv = pg.investor_times(DAY, "day")
        fill1 = pg.fill1_times(DAY, "day")
        dates = pg.series_dates(DAY, "day")
        assert pg.board_times(DAY, "night") == [] and pg.fill1_times(date(2026, 9, 29), "day") == []
    want_board = {
        (r.mrkt_cls, r.expiry, r.ts)
        for r in mem.chain
        if r.source == "board" and r.quality != "invalid"
    }
    assert set(board) == want_board and len(board) == len(want_board) > 0
    assert set(fut) == {r.ts for r in mem.futures if r.quality != "invalid"}
    assert set(inv) == {
        (r.market_code, r.sector_code, r.ts) for r in mem.investor if r.quality != "invalid"
    }
    want_under = {
        e.received_at
        for e in mem.raw
        if e.key.startswith("underlying|")
        and isinstance(e.payload, dict)
        and e.payload.get("rt_cd") == "0"
    }
    assert set(under) == want_under and len(under) == len(want_under) > 0
    want_fill1 = {
        (":".join(e.key.split(":")[1:3]), e.received_at)
        for e in mem.raw
        if e.key.startswith("fill1:")
        and isinstance(e.payload, dict)
        and e.payload.get("rt_cd") == "0"
    }
    assert set(fill1) == want_fill1 and want_fill1
    assert {label for label, _ in fill1} == {"M:202610"}  # 주간 보강 1 = 월물
    assert set(dates) == {
        (r.mrkt_cls, r.expiry, r.source, r.last_trade_date, r.ts) for r in mem.expiries
    }
    assert all(t.utcoffset() == timedelta(0) for _, _, t in board)


def test_ws_events_minute_bars_and_tick_minutes_read_back(timescale: PgContainer) -> None:
    url = _migrated(timescale, "gapread")
    t0 = datetime(2026, 9, 28, 8, 44, tzinfo=KST)
    events = [
        AuthHealthEvent(
            "ws_connected", "1번째 연결", t0 - timedelta(hours=1), service="ws-gateway"
        ),
        AuthHealthEvent(
            "ws_disconnected", "끊김", t0 + timedelta(hours=1), "warning", "ws-gateway"
        ),
        AuthHealthEvent(
            "ws_connected", "2번째 연결", t0 + timedelta(hours=1, seconds=3), service="ws-gateway"
        ),
        AuthHealthEvent("ws_connected", "다른 서비스", t0 + timedelta(hours=2), service="auth"),
        AuthHealthEvent("ws_plan_session", "구독", t0 + timedelta(hours=2), service="ws-gateway"),
    ]
    got_at = datetime(2026, 9, 28, 16, 0, tzinfo=KST)
    bars = [
        MinuteBarRecord.from_kis(
            _bar("20260928", hhmm + "00", cntg_vol=vol),
            code="A01612",
            market="F",
            received_at=got_at,
            calendar=CAL,
        )
        for hhmm, vol in [("0845", "12"), ("0846", "0")]
    ]
    ticks = [
        FuturesTickRecord.from_tick(
            _fut_tick("1095.10", "1"),
            ts=datetime(2026, 9, 28, 8, 45, s, tzinfo=KST),
            trade_date=DAY,
            seq=s,
            received_at=got_at,
        )
        for s in (1, 30, 59)
    ]
    with PostgresSink(url, service="scheduler") as pg:
        pg.write_health(events, tagger=_tagger)
        pg.write_minute_bars(bars)
        pg.write_fut_ticks(ticks)
        start = datetime(2026, 9, 28, 8, 45, tzinfo=KST)
        before, inside = pg.ws_connection_events(
            start,
            start + timedelta(hours=7),
            ("ws_connected", "ws_disconnected", "ws_connect_failed"),
        )
        assert before == "ws_connected"
        assert inside == [
            (t0 + timedelta(hours=1), "ws_disconnected"),
            (t0 + timedelta(hours=1, seconds=3), "ws_connected"),
        ]
        assert pg.minute_bar_times(DAY, "day") == [
            ("A01612", datetime(2026, 9, 27, 23, 45, tzinfo=UTC), 12),
            ("A01612", datetime(2026, 9, 27, 23, 46, tzinfo=UTC), 0),
        ]
        assert pg.fut_tick_minutes(DAY, "day") == [
            ("A01612", datetime(2026, 9, 27, 23, 45, tzinfo=UTC), 3)
        ]
        assert pg.fut_tick_count(start, start + timedelta(minutes=3)) == 3
        assert pg.fut_tick_count(start + timedelta(minutes=1), start + timedelta(minutes=3)) == 0


def _session_report(d: date, session: Session, stream: str, **kw: Any) -> CollectionReportRecord:
    start = datetime.combine(d, datetime.min.time(), tzinfo=KST)
    return CollectionReportRecord(
        trade_date=d,
        session=session,
        stream=stream,
        evaluated_at=start + timedelta(hours=16),
        span_start=start + timedelta(hours=8, minutes=45),
        span_end=start + timedelta(hours=15, minutes=45),
        required=True,
        **({"status": "ok", "expected": 840, "received": 840, "max_gap_s": 30.5} | kw),
    )


def test_replace_gap_report_swaps_only_that_session(timescale: PgContainer) -> None:
    url = _migrated(timescale, "gaprep")
    d29 = date(2026, 9, 29)
    start = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    gap = CollectionGapRecord(
        stream="fut_board",
        start_ts=start,
        end_ts=start + timedelta(seconds=95),
        trade_date=DAY,
        session="day",
        expected=3,
        received=0,
        detected_at=start + timedelta(hours=6),
        detail={"why": "SYNTHETIC"},
    )
    other = gap.model_copy(
        update={
            "start_ts": start + timedelta(days=1),
            "end_ts": start + timedelta(days=1, seconds=70),
            "trade_date": d29,
        }
    )
    first = [
        _session_report(DAY, "day", "fut_board", status="gaps", gaps=1, max_gap_s=95.0),
        _session_report(DAY, "day", "ws_connection"),
    ]
    with PostgresSink(url, service="scheduler") as pg:
        pg.replace_gap_report(d29, "day", [other], [_session_report(d29, "day", "fut_board")])
        pg.replace_gap_report(DAY, "day", [gap], first)
        assert pg.gap_reports(DAY, DAY) == first
        assert _rows(
            url, "SELECT stream, detail FROM collection_gaps WHERE trade_date = %s", DAY
        ) == [("fut_board", {"why": "SYNTHETIC"})]
        # 다시 판정 — 늦게 든 데이터로 공백이 없어졌다: 옛 공백 행이 남지 않는다
        again = [
            _session_report(DAY, "day", "fut_board"),
            _session_report(DAY, "day", "ws_connection"),
        ]
        pg.replace_gap_report(DAY, "day", [], again)
        assert pg.gap_reports(DAY, DAY) == again
        assert _rows(url, "SELECT trade_date FROM collection_gaps") == [(d29,)]
        assert [r.trade_date for r in pg.gap_reports(DAY, d29)] == [DAY, DAY, d29]
        with pytest.raises(StoreError, match="collection_reports"):
            bad = _session_report(DAY, "day", "x").model_copy(update={"status": "weird"})
            pg.replace_gap_report(DAY, "day", [], [bad])
        assert pg.gap_reports(DAY, DAY) == again  # 실패한 교체는 아무것도 지우지 않았다
