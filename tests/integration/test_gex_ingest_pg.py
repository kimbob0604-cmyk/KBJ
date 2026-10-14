"""GEX 수집 경로 저장 통합 시험 — 실제 TimescaleDB (KBJ `GexIngestStore`·`PgGexIngestRepo`).

P9 WRAP(docs/p9_design.md §7.2 WRAP-1, ADR 0036): `legacy/gexlab` 을 지우며 GX 통합 시험 중 KBJ 로
옮긴 코드(poller·분봉·마스터·KRX 일별·무결측 판정·nogap 보고)를 보는 항목을 KBJ 저장소로 옮겼다 —
단언은 같고 대상만 바꿨다(GX `PostgresSink` → `GexIngestStore`(스풀 없음), GX `db.migrate` →
`kbj.store.migrate`, 표 이름에 `prv_gex.`·`ops.`). 옮긴 항목(GX 파일::함수):

- `test_scheduler_krx::test_loading_twice_and_after_a_restart_writes_each_row_once`
- `test_store::test_poller_records_round_trip_…`·`test_chain_values_and_quality_survive_…`·
  `test_quarantine_health_and_raw_payloads_round_trip`·
  `test_rewriting_the_same_batches_adds_nothing`(GX 싱크 통계 `pg.stats` 대신 행 수만)
- `test_store::test_minute_bars_day_and_night_with_reload_update`·
  `test_master_snapshots_per_session`·`test_replace_gap_report_swaps_only_that_session`
- `test_nogap_report::test_three_trading_days_across_a_holiday_from_the_database`

옮기지 않은 GX 통합 항목과 사유는 `docs/migration/gexlab.md` 'P9 WRAP' 절. 컨테이너는
tests/integration/conftest.py 가 세션마다 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import psycopg
import pytest
from psycopg import sql

from kbj.core.calendar import TradingCalendar
from kbj.core.gex.records import MinuteBarRecord
from kbj.data.private.kis.derivatives import TR_CALLPUT, TR_INVESTOR, MinuteBar
from kbj.data.private.kis.master import parse_master
from kbj.data.private.krx.client import FUT_DAILY, OPT_DAILY
from kbj.data.ratelimit import LocalRateLimiter
from kbj.services.collectors.gex_krx import KrxCallBudget, KrxLoader
from kbj.services.collectors.gex_master_check import master_covers_krx
from kbj.services.engine.gex_report.nogap import main as nogap_main
from kbj.services.engine.gex_report.nogap import sessions_of
from kbj.services.gex_poller.collector import Collector
from kbj.services.gex_poller.sink import InMemorySink
from kbj.services.gex_poller.store import GexIngestStore
from kbj.store.db import StoreError
from kbj.store.migrate import migrate
from kbj.store.repos.gex import CHAIN_SNAPSHOTS, attr_row
from kbj.store.repos.gex_ingest import CollectionGapRecord, CollectionReportRecord
from tests.integration.conftest import PgContainer
from tests.unit.gex_ingest.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    StaticTokenProvider,
    default_chain,
    make_client,
)
from tests.unit.gex_ingest.fakes.krx_server import FakeKrx

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
DAY = date(2026, 9, 28)
D23 = date(2026, 9, 23)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
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
    schema, name = table.split(".")
    n = _one(
        dsn,
        sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(name)),
    )
    assert isinstance(n, int)
    return n


def _migrated(pg: PgContainer, prefix: str) -> str:
    url = pg.dsn(pg.fresh_database(prefix))
    migrate(url)
    return url


def _store(url: str, service: str = "scheduler") -> GexIngestStore:
    """GX `PostgresSink(url, service=…)`(스풀 없음) 자리 — 쓸 때마다 짧게 연다."""
    return GexIngestStore(lambda: psycopg.connect(url), service=service)


def _fixture(rel: str) -> Any:
    return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    return _migrated(timescale, "ingest")


# ── KRX 전일 적재(GX test_scheduler_krx) ──────────────────────────────────────────────


def test_loading_twice_and_after_a_restart_writes_each_row_once(timescale: PgContainer) -> None:
    url = _migrated(timescale, "krxload")
    fake = FakeKrx()
    fake.publish(D23)
    redis = fakeredis.FakeRedis()
    at = datetime(2026, 9, 28, 8, 5, tzinfo=KST)
    loader = KrxLoader(fake.client(), _store(url), KrxCallBudget(redis, 200), now=lambda: at)
    first = loader.load(D23)
    assert first.done and [r.rows for r in first.results] == [13, 3]
    assert [r.status for r in loader.load(D23).results] == ["present", "present"]
    # 재기동 — 새 저장소·새 loader 는 DB 를 보고 부르지 않는다
    again = KrxLoader(fake.client(), _store(url), KrxCallBudget(redis, 200), now=lambda: at)
    assert [r.status for r in again.load(D23).results] == ["present", "present"]
    listing = again.listing(D23)  # 마스터 ⊇ KRX 대조 입력 — DB 에서
    assert fake.requested() == [(OPT_DAILY, "20260923"), (FUT_DAILY, "20260923")]
    opt = _rows(url, "SELECT trade_date, session, imp_volt, ts FROM prv_gex.krx_opt_daily")
    assert len(opt) == 13 and {r[0] for r in opt} == {D23} and {r[3] for r in opt} == {at}
    assert sum(1 for r in opt if r[1] == "night" and r[2] is None) == 6
    assert sum(1 for r in opt if r[1] == "night") == 6
    fut = _rows(url, "SELECT session, setl_prc FROM prv_gex.krx_fut_daily ORDER BY session")
    assert len(fut) == 3 and ("day", Decimal("1120.35")) in fut
    assert ("kospi200_weekly_mon", "260904", "P", Decimal("970.00")) in listing
    report = master_covers_krx(default_chain().master_rows(), listing, D23)
    assert report.ok and report.compared == 3 and report.krx_strikes == 6


# ── poller 수집기 → 저장 왕복(GX test_store) ───────────────────────────────────────────


class TeeSink:
    """수집기 산출을 메모리와 DB 에 똑같이 쓴다 — DB 에서 읽은 것을 메모리 것과 맞춘다."""

    def __init__(self, pg: GexIngestStore) -> None:
        self.mem = InMemorySink()
        self.pg = pg

    def write_chain(self, rows: Sequence[Any]) -> None:
        self.mem.write_chain(rows)
        self.pg.write_chain(rows)

    def write_futures(self, rows: Sequence[Any]) -> None:
        self.mem.write_futures(rows)
        self.pg.write_futures(rows)

    def write_expiries(self, rows: Sequence[Any]) -> None:
        self.mem.write_expiries(rows)
        self.pg.write_expiries(rows)

    def write_raw(self, envelopes: Sequence[Any]) -> None:
        self.mem.write_raw(envelopes)
        self.pg.write_raw(envelopes)

    def write_quarantine(self, rows: Sequence[Any]) -> None:
        self.mem.write_quarantine(rows)
        self.pg.write_quarantine(rows)

    def write_health(self, events: Sequence[Any]) -> None:
        self.mem.write_health(events)
        self.pg.write_health(events)


def _bad_board(body: dict[str, Any]) -> None:
    body["output1"][0]["acpr"] = "abc"  # 행사가를 못 읽는 행 — 격리만
    body["output2"][1]["hts_otst_stpl_qty"] = "x"  # 행사가는 읽힌다 — invalid 레코드


def _collect(pg: GexIngestStore, start: datetime, end: datetime) -> InMemorySink:
    clock = FakeClock(start)
    chain = default_chain()
    server = FakeKisServer(clock, chain)
    kis = make_client(server, LocalRateLimiter(clock=clock), StaticTokenProvider())
    tee = TeeSink(pg)
    col = Collector(kis, tee, calendar=CAL, clock=clock, master=chain.master_rows())
    server.inject(
        "malformed", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "260904"}, mutate=_bad_board
    )
    col.run_until(end)
    assert col.stats.sink_errors == 0
    # D-P7-5(ADR 0019 §2-2): 투자자별 TR 은 한 번도 부르지 않는다
    assert server.quote_calls(TR_INVESTOR) == [] and server.calls
    return tee.mem


# poller 싱크 프로토콜의 표 → 메모리 싱크 속성. investor_flow 는 poller 가 쓰지 않는다(ADR 0019)
POLLER_TABLES = {
    "prv_gex.chain_snapshots": "chain",
    "prv_gex.fut_board": "futures",
    "prv_gex.series_expiries": "expiries",
    "prv_gex.raw_messages": "raw",
    "ops.quarantine": "quarantine",
    "ops.health_events": "health",
}


def _replay(pg: GexIngestStore, mem: InMemorySink) -> None:
    pg.write_chain(mem.chain)
    pg.write_futures(mem.futures)
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
    url = _migrated(timescale, "pollday")
    start = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    mem = _collect(_store(url, "poller"), start, start + timedelta(minutes=1))
    return Run(url, mem, {t: _count(url, t) for t in POLLER_TABLES})


def test_poller_records_round_trip_with_trade_date_session_and_utc_ts(day_run: Run) -> None:
    mem, url = day_run.mem, day_run.dsn
    assert day_run.counts == {t: len(getattr(mem, attr)) for t, attr in POLLER_TABLES.items()}
    assert day_run.counts["prv_gex.chain_snapshots"] > 1000
    assert day_run.counts["ops.quarantine"] > 0 and day_run.counts["ops.health_events"] > 0
    assert _count(url, "prv_gex.investor_flow") == 0
    for t in POLLER_TABLES:
        schema, name = t.split(".")
        q = sql.SQL("SELECT DISTINCT trade_date, session FROM {}.{}").format(
            sql.Identifier(schema), sql.Identifier(name)
        )
        assert _rows(url, q) == [(DAY, "day")], t
    # 시각: 10:00~10:01 KST = 01:00~01:01 UTC, 메모리의 ts 와 마이크로초까지 같다
    got = {r[0] for r in _rows(url, "SELECT ts FROM prv_gex.chain_snapshots")}
    assert got == {c.ts for c in mem.chain}
    lo, hi = _rows(url, "SELECT min(ts), max(ts) FROM prv_gex.raw_messages")[0]
    utc_start = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
    assert utc_start <= lo <= hi < utc_start + timedelta(minutes=1)


def test_chain_values_and_quality_survive_the_round_trip(day_run: Run) -> None:
    cols = sql.SQL(", ").join(map(sql.Identifier, CHAIN_SNAPSHOTS.columns))
    q = sql.SQL("SELECT {} FROM prv_gex.chain_snapshots").format(cols)
    got = {r[:9]: r for r in _rows(day_run.dsn, q)}
    chain = day_run.mem.chain
    sample = chain[:: max(1, len(chain) // 50)] + [c for c in chain if c.quality == "invalid"]
    for rec in sample:
        row = attr_row(CHAIN_SNAPSHOTS, rec)
        assert got[row[:9]] == row  # 값·Decimal 자릿수·NULL 까지 같다
    quoted, fills = _rows(
        day_run.dsn,
        "SELECT count(*) FILTER (WHERE bid IS NOT NULL OR ask IS NOT NULL), count(*) "
        "FROM prv_gex.chain_snapshots WHERE source = 'fill'",
    )[0]
    assert quoted == 0 and fills > 0  # 단건엔 호가가 없다
    n_invalid = _one(
        day_run.dsn, "SELECT count(*) FROM prv_gex.chain_snapshots WHERE quality = 'invalid'"
    )
    assert n_invalid == sum(c.quality == "invalid" for c in chain) > 0


def test_quarantine_health_and_raw_payloads_round_trip(day_run: Run) -> None:
    mem, url = day_run.mem, day_run.dsn
    q = {(r[0], r[1]): r[2] for r in _rows(url, "SELECT tr_id, error, payload FROM ops.quarantine")}
    assert q == {(r.tr_id, r.error): r.payload for r in mem.quarantine}
    h = _rows(url, "SELECT service, kind, level, detail FROM ops.health_events")
    assert {r[:3] for r in h} == {("poller", e.kind, e.level) for e in mem.health}
    assert sorted(r[3]["job"] for r in h if r[1] == "quarantine") == sorted(
        e.detail["job"] for e in mem.health if e.kind == "quarantine"
    )
    raw_rows = _rows(url, "SELECT ts, tr_id, key, payload FROM prv_gex.raw_messages")
    raw = {(r[0], r[1], r[2]): r[3] for r in raw_rows}
    assert raw == {(e.received_at, e.tr_id, e.key): e.payload for e in mem.raw}


def test_rewriting_the_same_batches_adds_nothing(day_run: Run) -> None:
    pg = _store(day_run.dsn, "poller")
    _replay(pg, day_run.mem)
    _replay(pg, day_run.mem)
    assert {t: _count(day_run.dsn, t) for t in POLLER_TABLES} == day_run.counts


# ── 분봉·마스터(GX test_store) ────────────────────────────────────────────────────────


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
    pg = _store(dsn)
    pg.write_minute_bars(day + night)
    fixed = MinuteBarRecord.from_kis(
        _bar("20260922", "300000", futs_prpr="1100.05"),
        code="A01612",
        market="CM",
        received_at=got_at,
        calendar=CAL,
    )
    pg.write_minute_bars([*day, fixed])  # 다시 적재 — 마지막 봉 값만 바뀐다
    rows = _rows(
        dsn, "SELECT ts, trade_date, session, market, close FROM prv_gex.minute_bars ORDER BY ts"
    )
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
    pg = _store(dsn)
    pg.write_master(rows, ts=at, trade_date=DAY, session="day")
    pg.write_master(
        rows, ts=at + timedelta(hours=10), trade_date=date(2026, 9, 29), session="night"
    )
    pg.write_master(rows, ts=at + timedelta(minutes=5), trade_date=DAY, session="day")
    counts = _rows(
        dsn,
        "SELECT trade_date, session, count(*), max(ts) FROM prv_gex.master_snapshots "
        "GROUP BY 1, 2 ORDER BY 1",
    )
    assert counts == [
        (DAY, "day", len(rows), at + timedelta(minutes=5)),
        (date(2026, 9, 29), "night", len(rows), at + timedelta(hours=10)),
    ]
    atm = _rows(
        dsn,
        "SELECT cp, strike, atm_cls, expiry FROM prv_gex.master_snapshots "
        "WHERE code = 'B01610ZCI' AND session = 'day'",
    )
    assert atm == [("C", Decimal("1125.00"), "1", "202610")]
    fut = _rows(
        dsn, "SELECT cp, strike, atm_cls FROM prv_gex.master_snapshots WHERE code = 'A01612'"
    )
    assert set(fut) == {(None, None, None)}


# ── 무결측 판정 리포트(GX test_store·test_nogap_report) ───────────────────────────────


def _session_report(d: date, session: str, stream: str, **kw: Any) -> CollectionReportRecord:
    start = datetime.combine(d, datetime.min.time(), tzinfo=KST)
    return CollectionReportRecord.model_validate(
        {
            "trade_date": d,
            "session": session,
            "stream": stream,
            "evaluated_at": start + timedelta(hours=16),
            "span_start": start + timedelta(hours=8, minutes=45),
            "span_end": start + timedelta(hours=15, minutes=45),
            "required": True,
            **({"status": "ok", "expected": 840, "received": 840, "max_gap_s": 30.5} | kw),
        }
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
    pg = _store(url)
    pg.replace_gap_report(d29, "day", [other], [_session_report(d29, "day", "fut_board")])
    pg.replace_gap_report(DAY, "day", [gap], first)
    assert pg.gap_reports(DAY, DAY) == first
    assert _rows(
        url, "SELECT stream, detail FROM ops.collection_gaps WHERE trade_date = %s", DAY
    ) == [("fut_board", {"why": "SYNTHETIC"})]
    # 다시 판정 — 늦게 든 데이터로 공백이 없어졌다: 옛 공백 행이 남지 않는다
    again = [
        _session_report(DAY, "day", "fut_board"),
        _session_report(DAY, "day", "ws_connection"),
    ]
    pg.replace_gap_report(DAY, "day", [], again)
    assert pg.gap_reports(DAY, DAY) == again
    assert _rows(url, "SELECT trade_date FROM ops.collection_gaps") == [(d29,)]
    assert [r.trade_date for r in pg.gap_reports(DAY, d29)] == [DAY, DAY, d29]
    with pytest.raises(StoreError, match="collection_reports"):
        bad = _session_report(DAY, "day", "x").model_copy(update={"status": "weird"})
        pg.replace_gap_report(DAY, "day", [], [bad])
    assert pg.gap_reports(DAY, DAY) == again  # 실패한 교체는 아무것도 지우지 않았다


TUE, WED, MON = date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 28)


def _report(d: date, session: str, stream: str, status: str = "ok") -> CollectionReportRecord:
    start = datetime(d.year, d.month, d.day, 8, 45, tzinfo=KST)
    return CollectionReportRecord.model_validate(
        {
            "trade_date": d,
            "session": session,
            "stream": stream,
            "evaluated_at": start + timedelta(hours=8),
            "span_start": start,
            "span_end": start + timedelta(hours=7),
            "required": True,
            "status": status,
            "gaps": 0 if status != "gaps" else 1,
        }
    )


def test_three_trading_days_across_a_holiday_from_the_database(
    timescale: PgContainer, capsys: pytest.CaptureFixture[str]
) -> None:
    url = _migrated(timescale, "nogap")
    pg = _store(url)
    for d in (TUE, WED, MON):
        for s in sessions_of(d, CAL):
            reports = [_report(d, s, x) for x in ("fut_board", "ws_connection", "fut_trades")]
            pg.replace_gap_report(d, s, [], reports)
    assert nogap_main(["--end", "2026-09-28", "--days", "4"], store=pg, cal=CAL) == 0
    out = capsys.readouterr().out
    assert "가장 긴 연속 무결측: 3거래일 (2026-09-22 ~ 2026-09-28" in out
    assert "| 2026-09-21 | 주간 | 리포트 없음 |" in out
    # 수요일 밤에 공백이 생기면 끊긴다
    pg.replace_gap_report(WED, "night", [], [_report(WED, "night", "ws_connection", "gaps")])
    assert nogap_main(["--end", "2026-09-28", "--days", "4"], store=pg, cal=CAL) == 1
    # 저장된 데이터가 없으면 다시 판정해도 무결측이 아니다(공백·판정 불가)
    assert nogap_main(["--end", "2026-09-28", "--days", "2", "--recompute"], store=pg, cal=CAL) == 1
    out = capsys.readouterr().out
    assert "| 2026-09-28 | 주간 | 공백 |" in out
