"""P5 저장소 Postgres 구현이 메모리 구현과 같은 규칙을 지킨다(docs/p5_design.md §1.1 — Docker).

- 0001~0017 을 적용한 DB 하나에서 `tests/unit/store/repo_cases_p5.CASES` 를 돈다. 시험마다 P5
  저장소가
  쓰는 표를 비운다(TRUNCATE).
- Pg 에만 있는 확인: numeric 이 Decimal 로 돌아온다(매크로·금투협 — 공표 자릿수). 분류 사전
  저장소는 테마 사전 통합을 사용자가 따로 진행해 P5 에서 뺐다.
컨테이너는 tests/integration/conftest.py 가 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest

from kbj.store.migrate import migrate
from kbj.store.repos import Repos, pg_repos
from tests.integration.conftest import PgContainer
from tests.unit.store.repo_cases_p5 import CASE_IDS, CASES

pytestmark = pytest.mark.integration

TABLES = (
    "pub_macro.series_meta", "pub_macro.series", "pub_macro.calendar", "prv_macro.series_meta",
    "prv_macro.series", "prv_macro.calendar", "pub_market_stats.credit_balance",
    "pub_market_stats.market_capital",
    "pub_market_stats.fund_nav", "pub_market_stats.cma", "prv_market.us_universe",
    "prv_market.daily_bar", "prv_board.artifact", "prv_gex.investor_flow",
    "prv_flows.program_market_daily", "prv_flows.program_market_intraday",
    "prv_flows.program_investor_daily", "prv_flows.program_stock_intraday",
    "prv_flows.program_stock_daily", "prv_etf.fund", "prv_etf.holding",
)  # fmt: skip


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("repos5"))
    migrate(url)
    return url


@pytest.fixture
def repos(dsn: str) -> Iterator[Repos]:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE".encode())
    yield pg_repos(lambda: psycopg.connect(dsn))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_pg_p5_repos_follow_the_rules(repos: Repos, case: Callable[[Repos], None]) -> None:
    case(repos)
