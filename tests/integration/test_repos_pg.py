"""저장소 Postgres 구현이 메모리 구현과 같은 규칙을 지킨다(docs/p3_design.md §1.1 — Docker).

- 0001~0009 를 적용한 DB 하나에서 `tests/unit/store/repo_cases.CASES` 를 돈다. 시험마다 P3 저장소가
  쓰는 표를 비운다(TRUNCATE — 서로의 행이 섞이지 않게).
- 같은 묶음을 메모리 구현으로 도는 것은 `tests/unit/store/test_repos_memory.py`.
- Pg 에만 있는 확인: 잠정→확정이 한 트랜잭션(차이 기록 실패 시 덮어쓰기도 되돌림), 금액 열이
  정수로 돌아온다(소수 없는 numeric), 스냅 extra jsonb 모양, 연결 공장을 부를 때마다 닫는다,
  유니버스 원천별 마지막 날이 메모리판과 같다(원천 넷·다른 시장 행이 섞인 표).
컨테이너는 tests/integration/conftest.py 가 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Investor, InvestorDay, UniverseRow
from kbj.store.migrate import migrate
from kbj.store.repos import Repos, memory_repos, pg_repos
from tests.integration.conftest import PgContainer
from tests.unit.store.repo_cases import CASE_IDS, CASES, snap

pytestmark = pytest.mark.integration

TABLES = (
    "prv_market.daily_bar", "prv_market.stock_snapshot", "prv_market.universe",
    "prv_market.index_intraday", "prv_market.sector_intraday",
    "prv_market.turnover_rank_intraday", "prv_market.eod_reconcile",
    "prv_flows.stock_investor_daily", "prv_flows.market_investor_daily",
    "prv_flows.investor_intraday", "prv_flows.investor_revision", "prv_flows.ledger_check",
    "prv_board.alltime", "prv_board.label", "prv_board.split_check", "prv_board.stock_day",
    "prv_board.artifact",
    "prv_etf.etf_daily", "prv_etf.quote_intraday", "prv_etf.meta", "prv_etf.split_event",
    "prv_etf.fund", "prv_etf.holding", "prv_etf.change_log",
)  # fmt: skip
NOW = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
D = date(2026, 10, 6)


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("repos"))
    migrate(url)
    return url


@pytest.fixture
def repos(dsn: str) -> Iterator[Repos]:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)}".encode())
    yield pg_repos(lambda: psycopg.connect(dsn))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_pg_repos_follow_the_rules(repos: Repos, case: Callable[[Repos], None]) -> None:
    case(repos)


def _one(dsn: str, query: str, *params: object) -> Any:
    with psycopg.connect(dsn) as c:
        row = c.execute(query.encode(), params or None).fetchone()
    assert row is not None
    return row[0]


def test_snapshot_extra_jsonb_shape(repos: Repos, dsn: str) -> None:
    repos.market.upsert_snapshots([snap("000010", D, 1.0, flags=("halted",))], loaded_by="t")
    repos.market.set_snapshot_quality(
        D, "000010", source="kis", venue="KRX", quality=Quality.INVALID, note="거래대금 다름"
    )
    extra = _one(dsn, "SELECT extra FROM prv_market.stock_snapshot WHERE code = '000010'")
    assert extra == {"kind": "common", "status_flags": ["halted"], "quality_note": "거래대금 다름"}
    assert _one(dsn, "SELECT segment FROM prv_market.stock_snapshot") == "KOSPI"
    assert _one(dsn, "SELECT market FROM prv_market.stock_snapshot") == "KR"


def test_revision_and_overwrite_share_one_transaction(repos: Repos, dsn: str) -> None:
    """차이 기록이 실패하면(여기서는 표 제약을 일부러 깨서) 잠정 행 삭제·확정 쓰기도 되돌린다."""
    est = InvestorDay(
        "000010", D, Investor.FOREIGN, 10, None, "kis.prelim", "KRX", Quality.ESTIMATED
    )
    repos.flows.upsert_investor_days([est], revise=False, loaded_by="t", now=NOW)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(
            b"ALTER TABLE prv_flows.investor_revision ADD CONSTRAINT t_block CHECK (diff IS NULL)"
        )
    try:
        final = InvestorDay("000010", D, Investor.FOREIGN, 15, None, "kis", "KRX", Quality.OK)
        with pytest.raises(psycopg.errors.CheckViolation):
            repos.flows.upsert_investor_days([final], revise=True, loaded_by="t", now=NOW)
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(b"ALTER TABLE prv_flows.investor_revision DROP CONSTRAINT t_block")
    rows = repos.flows.window(None, D, 1)["000010"]
    assert [(r.source, r.net_value) for r in rows] == [("kis.prelim", 10)]  # 그대로 남았다
    assert repos.flows.revisions(D) == []


def test_money_columns_come_back_as_int(repos: Repos) -> None:
    repos.market.upsert_snapshots([snap("000010", D, 1.0)], loaded_by="t")
    s = repos.market.snapshot(D)[0]["000010"]
    assert type(s.turnover) is int and type(s.mktcap) is int and type(s.volume) is int


def test_universe_latest_per_source_matches_memory(repos: Repos, dsn: str) -> None:
    """유니버스 읽기(원천을 재귀 CTE 로 나열 → 원천마다 asof 이하 마지막 날 —
    universe_source_idx)가 메모리판과 같은 행: 원천 넷·asof 뒤에만 있는 원천·두 원천에 든
    종목(원천 순위)·asof 앞은 빈 결과. 다른 시장(US) 행은 색인에 market 이 없어 거르기로
    빠진다 — 저장소는 KR 만 쓰므로 표에 직접 넣는다."""
    d1, d2, d3, d4 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
    ok, est = Quality.OK, Quality.ESTIMATED
    rows = [
        UniverseRow("000010", d1, "합성A", "KOSPI", "common", None, "krx", ok),
        UniverseRow("000020", d1, "합성B", "KOSDAQ", "pref", None, "krx", ok, flags={"rank": 3}),
        UniverseRow("000010", d3, "합성A", "KOSPI", "common", None, "krx", ok),
        UniverseRow("000010", d2, "합성A", "KOSPI", "common", None, "kis", est),
        UniverseRow("000030", d2, "합성C", "KOSPI", "common", None, "kis", est),
        UniverseRow("000040", d2, "합성D", "KOSDAQ", "common", None, "sd.index", ok),
        UniverseRow("000050", d4, "합성E", "KOSDAQ", "common", d4, "sd.index", ok),
        UniverseRow("000060", d4, "합성F", "KOSPI", "common", d4, "zz.late", ok),
    ]
    mem = memory_repos().market
    for m in (repos.market, mem):
        assert m.upsert_universe(rows, loaded_by="t") == len(rows)
    with psycopg.connect(dsn) as c:  # 다른 시장 행 — krx 의 KR 마지막 날(d3)보다 늦다
        c.execute(
            b"INSERT INTO prv_market.universe (market, code, as_of, source, loaded_by) "
            b"VALUES ('US', 'ZZZZ', %s, 'krx', 't')",
            (d4,),
        )
    for asof in (d1 - timedelta(days=1), d1, d2, d3, d4, d4 + timedelta(days=30)):
        assert repos.market.universe(asof) == mem.universe(asof), asof
    got = repos.market.universe(d4)
    assert [(u.code, u.source, u.as_of) for u in got] == [
        ("000010", "krx", d3), ("000030", "kis", d2), ("000050", "sd.index", d4),
        ("000060", "zz.late", d4),
    ]  # fmt: skip
    assert [u.code for u in repos.market.universe(d1)] == ["000010", "000020"]
    assert repos.market.universe(d1 - timedelta(days=1)) == []


def test_each_call_closes_its_connection(dsn: str) -> None:
    opened: list[psycopg.Connection[Any]] = []

    def factory() -> psycopg.Connection[Any]:
        c = psycopg.connect(dsn)
        opened.append(c)
        return c

    r = pg_repos(factory)
    r.market.trading_days(D, 5)
    r.board.split_cleared()
    r.etf.funds()
    assert len(opened) == 3 and all(c.closed for c in opened)
