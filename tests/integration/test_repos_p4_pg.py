"""P4 저장소 Postgres 구현이 메모리 구현과 같은 규칙을 지킨다(docs/p4_design.md §1.1 — Docker).

- 0001~0013 을 적용한 DB 하나에서 `tests/unit/store/repo_cases_p4.CASES` 를 돈다. 시험마다 P4
  저장소가
  쓰는 표를 비운다(TRUNCATE).
- Pg 에만 있는 확인: 금액 열이 정수로 돌아온다(소수 없는 numeric·bigint), jsonb·text[] 왕복, 연결
  공장을 부를 때마다 닫는다, 통째로 바꾸기가 한 트랜잭션(지우고 넣다가 실패하면 되돌림),
  `corp_index` 가 P2 pub_filings.corp_code 를 읽는다.
컨테이너는 tests/integration/conftest.py 가 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import psycopg
import pytest

from kbj.store.migrate import migrate
from kbj.store.repos import Repos, pg_repos
from tests.integration.conftest import PgContainer
from tests.unit.store.repo_cases_p4 import CASE_IDS, CASES, JOB, NOW, disc, quarter, rc

pytestmark = pytest.mark.integration

TABLES = (
    "pub_filings.disclosure", "pub_filings.corp_profile", "pub_filings.earnings_prelim",
    "pub_filings.earnings_schedule", "pub_filings.overhang_event", "pub_filings.overhang_adjust",
    "pub_filings.overhang_state", "pub_filings.overhang_summary", "pub_filings.insider_report",
    "pub_filings.major_holder_report", "pub_filings.insider_window", "pub_filings.buyback_event",
    "pub_filings.share_count", "pub_filings.corp_code",
    "pub_fin.statement_raw", "pub_fin.quarterly", "pub_fin.ratio", "pub_fin.report",
    "pub_fin.segment",
    "prv_fin.consensus_snapshot", "prv_fin.invest_opinion", "prv_fin.target_consensus",
    "prv_fin.revision", "prv_fin.valuation_daily", "prv_fin.valuation_band", "prv_fin.scenario",
    "prv_fin.earnings_surprise", "prv_market.share_class", "prv_journal.watchlist",
)  # fmt: skip


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("repos4"))
    migrate(url)
    return url


@pytest.fixture
def repos(dsn: str) -> Iterator[Repos]:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)}".encode())
    yield pg_repos(lambda: psycopg.connect(dsn))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_pg_p4_repos_follow_the_rules(repos: Repos, case: Callable[[Repos], None]) -> None:
    case(repos)


def test_money_comes_back_as_int_and_jsonb_round_trips(repos: Repos) -> None:
    repos.fin.upsert_quarters([quarter(2026, 3, rcept=date(2026, 11, 13))], loaded_by=JOB, now=NOW)
    q = repos.fin.quarters("99400010")[0]
    assert type(q.revenue) is int and type(q.op) is int and type(q.eps_basic) is float
    assert q.reported_3m == {"revenue": 1_000_000_000_000} and q.notes == ("합성",)
    repos.filings.upsert_disclosures([disc(1)], loaded_by=JOB, now=NOW)
    repos.filings.set_scores({rc(1): (8, ["잠정", "실적"])})
    d = repos.filings.disclosure(rc(1))
    assert d is not None and d.kw_matched == ("잠정", "실적")


def test_corp_index_reads_p2_corp_code(repos: Repos, dsn: str) -> None:
    with psycopg.connect(dsn) as c:
        c.execute(
            b"INSERT INTO pub_filings.corp_code (corp_code, stock_code, corp_name, received_at) "
            b"VALUES ('99400010', '994010', %s, now()), ('99400099', NULL, %s, now())",
            ("합성전자", "비상장합성"),
        )
    assert repos.filings.corp_index() == {"994010": ("99400010", "합성전자")}


def test_replace_is_one_transaction(repos: Repos, dsn: str) -> None:
    """지우고 넣다가 실패하면(표 제약을 일부러 깨서) 지운 것도 되돌린다."""
    from kbj.core.fin_rows import WatchItem

    items = [WatchItem("main", "994010", "합성", None, 0, source="cli")]
    repos.journal.replace_watchlist("main", items, loaded_by=JOB, now=NOW)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(b"ALTER TABLE prv_journal.watchlist ADD CONSTRAINT t_block CHECK (sort < 5)")
    try:
        bad = [WatchItem("main", "994020", "합성2", None, 9, source="cli")]
        with pytest.raises(psycopg.errors.CheckViolation):
            repos.journal.replace_watchlist("main", bad, loaded_by=JOB, now=NOW)
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(b"ALTER TABLE prv_journal.watchlist DROP CONSTRAINT t_block")
    assert repos.journal.watch_codes() == {"994010"}  # 그대로 남았다


def test_each_call_closes_its_connection(dsn: str) -> None:
    opened: list[psycopg.Connection[Any]] = []

    def factory() -> psycopg.Connection[Any]:
        c = psycopg.connect(dsn)
        opened.append(c)
        return c

    r = pg_repos(factory)
    r.filings.seen(["20261113000001"])
    r.fin.quarter_corps()
    r.consensus.share_classes()
    r.journal.watchlist()
    assert len(opened) == 4 and all(c.closed for c in opened)
