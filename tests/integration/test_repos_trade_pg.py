"""수출입 저장소(`kbj.store.repos.trade`) Postgres 판 — HS 접두·종목 읽기가 메모리판과
같다(Docker).

- HS 접두는 `LIKE '<접두>%' ESCAPE '\\'`(저장소 공용 `like_prefix`): 접두 안의 %·_ 는 글자
  그대로라 메모리판(startswith)과 같은 행이다. 예전 `left(hs, n) = p` 는 식이라
  (hs text_pattern_ops, ym) 색인을 못 타 item_monthly 를 부를 때마다 순차로 훑었다(2026-11-10
  실측) — 저장소가 보내는 질의 그대로 색인을 쓰는 계획인지 본다(순차 훑기를 끄고 — 작은 시험
  표에서도 계획이 흔들리지 않게).
- 매핑표: 종목 하나만 읽기(`stock_map(code=…)`)·존재 질의(`has_stock_map` — 화면의 NO_MAP 과
  NOT_MAPPED 를 가른다).
- 행은 모두 합성(고정 규칙으로 만든 HS 코드·금액 — 실데이터 아님).
컨테이너는 tests/integration/conftest.py 가 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import sql

from kbj.core import trade_rows as tr
from kbj.core.quality import Quality
from kbj.store.migrate import migrate
from kbj.store.repos.trade import MemoryTradeRepo, PgTradeRepo
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

TABLES = (
    "pub_trade.item_monthly", "pub_trade.item_country_monthly", "pub_trade.hs_dim",
    "pub_trade.stock_map",
)  # fmt: skip
T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
JOB = "test"
# 류 10~89 × 호 01~03 = HS4 240개(+ 더 깊은 자릿수 몇 개) × 24개월 — 접두 하나가 표의 1% 안팎.
# 품목×국가는 12개월 × 8개국: 기간·국가(기본 키 앞 두 칸)보다 접두가 훨씬 좁다(운영 표와 같은 꼴)
HS4 = tuple(f"{ch:02d}{sub:02d}" for ch in range(10, 90) for sub in (1, 2, 3))
DEEP = ("100101", "1001011010", "100201")
MONTHS = tuple(f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13))
COUNTRIES = ("CN", "DE", "JP", "MX", "SG", "TW", "US", "VN")
# 와일드카드·이스케이프 문자를 품은 접두는 어떤 HS 코드와도 맞지 않아야 한다(글자 그대로)
PREFIXES = ("10", "1001", "100101", "1001011010", "8903", "99", "", "1%", "1_", "_", "%", "10\\")


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("trade"))
    migrate(url)
    return url


@pytest.fixture
def pg(dsn: str) -> Iterator[PgTradeRepo]:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)}".encode())
    yield PgTradeRepo(lambda: psycopg.connect(dsn))


def _item(ym: str, hs: str, usd: int) -> tr.ItemMonthRow:
    return tr.ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs), exp_usd=Decimal(usd), exp_kg=None, imp_usd=None,
        imp_kg=None, bal_usd=None, source="DATAGO:15101609", as_of=T0, quality=Quality.OK,
    )  # fmt: skip


def _item_cc(ym: str, cc: str, hs: str, usd: int) -> tr.ItemCountryMonthRow:
    return tr.ItemCountryMonthRow(
        ym=ym, country=cc, hs=hs, hs_len=len(hs), exp_usd=Decimal(usd), exp_kg=None,
        imp_usd=None, imp_kg=None, bal_usd=None, source="DATAGO:15100475", as_of=T0,
        quality=Quality.OK,
    )  # fmt: skip


def _dim(hs: str) -> tr.HsDim:
    return tr.HsDim(
        hs=hs, hs_len=len(hs), name_ko="가상 품목", first_ym=MONTHS[0], last_ym=MONTHS[-1],
        source="DATAGO:15101609", as_of=T0, quality=Quality.OK,
    )  # fmt: skip


def _smap(code: str, hs: str, origin: tr.MapOrigin = "public") -> tr.StockMapRow:
    return tr.StockMapRow(
        code=code, hs=hs, flow="exp", region_id="", name="가상", weight=0.5, origin=origin,
        reviewed=False, map_version="v1", source="KBJ:trade_map", as_of=T0, quality=Quality.OK,
    )  # fmt: skip


def _fill(repo: PgTradeRepo | MemoryTradeRepo) -> None:
    codes = (*HS4, *DEEP)
    pairs = [(ym, hs) for ym in MONTHS for hs in codes]
    items = [_item(ym, hs, 1_000 + i) for i, (ym, hs) in enumerate(pairs)]
    repo.upsert_item_monthly(items, loaded_by=JOB, received_at=T0)
    triples = [(ym, cc, hs) for ym in MONTHS[-12:] for cc in COUNTRIES for hs in codes]
    ics = [_item_cc(ym, cc, hs, 10 + i) for i, (ym, cc, hs) in enumerate(triples)]
    repo.upsert_item_country_monthly(ics, loaded_by=JOB, received_at=T0)
    repo.upsert_hs_dim([_dim(h) for h in codes], loaded_by=JOB, received_at=T0)


def test_hs_prefix_reads_match_memory(pg: PgTradeRepo) -> None:
    mem = MemoryTradeRepo()
    _fill(pg)
    _fill(mem)
    for p in PREFIXES:
        assert pg.item_monthly(prefix=p) == mem.item_monthly(prefix=p), p
        a, b = "2026-01", "2026-06"
        assert pg.item_monthly(prefix=p, ym_from=a, ym_to=b) == mem.item_monthly(
            prefix=p, ym_from=a, ym_to=b
        ), p
        assert pg.item_country_monthly(prefix=p, country="US") == mem.item_country_monthly(
            prefix=p, country="US"
        ), p
        assert pg.hs_dim(prefix=p) == mem.hs_dim(prefix=p), p
    # 이스케이프가 없으면 '1%'·'_' 가 수천 행을 돌려준다
    assert pg.item_monthly(prefix="1%") == [] and pg.hs_dim(prefix="_") == []
    assert len(pg.item_monthly(prefix="1001")) == 3 * len(MONTHS)  # 1001·100101·1001011010
    assert len(pg.item_monthly(prefix="")) == len(MONTHS) * (len(HS4) + len(DEEP))


class _Capture:
    """저장소가 보낼 읽기 질의만 받아 두는 가짜 연결(빈 결과) — 같은 질의를 진짜 DB 에서
    EXPLAIN 한다."""

    def __init__(self) -> None:
        self.sent: list[tuple[sql.Composable, tuple[Any, ...]]] = []

    def __call__(self) -> Any:
        return self

    def __enter__(self) -> _Capture:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: sql.Composable, params: tuple[Any, ...] = ()) -> _Capture:
        self.sent.append((query, params))
        return self

    def fetchall(self) -> list[Any]:
        return []


def _plan(dsn: str, call: Callable[[PgTradeRepo], object]) -> str:
    cap = _Capture()
    call(PgTradeRepo(cap))
    ((query, params),) = cap.sent
    with psycopg.connect(dsn) as c:
        c.execute("SET enable_seqscan = off")
        rows = c.execute(sql.SQL("EXPLAIN (COSTS OFF) ") + query, params).fetchall()
    return "\n".join(str(r[0]) for r in rows)


def test_hs_prefix_query_uses_text_pattern_index(pg: PgTradeRepo, dsn: str) -> None:
    _fill(pg)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("ANALYZE pub_trade.item_monthly, pub_trade.item_country_monthly")
    plan = _plan(dsn, lambda r: r.item_monthly(prefix="1002", ym_from="2025-07", ym_to="2026-06"))
    assert "item_monthly_hs_idx" in plan, plan
    plan = _plan(
        dsn,
        lambda r: r.item_country_monthly(
            prefix="1002", country="US", ym_from="2026-01", ym_to="2026-06"
        ),
    )
    assert "item_country_monthly_hs_idx" in plan, plan


def test_stock_map_one_code_and_existence(pg: PgTradeRepo) -> None:
    assert pg.has_stock_map() is False and pg.stock_map(code="000010") == []
    pg.replace_stock_map(
        [_smap("000010", "8703"), _smap("000010", "8542"), _smap("000020", "8703")],
        origin="public", loaded_by=JOB, received_at=T0,
    )  # fmt: skip
    pg.replace_stock_map([_smap("000030", "85", "local")], origin="local", loaded_by=JOB,
                         received_at=T0)  # fmt: skip
    assert pg.has_stock_map() is True
    got = pg.stock_map(code="000010")
    assert [(r.code, r.hs) for r in got] == [("000010", "8542"), ("000010", "8703")]  # 키 순
    assert pg.stock_map(code="000040") == []
    pg.replace_stock_map([], origin="public", loaded_by=JOB, received_at=T0)
    assert pg.has_stock_map() is True  # local 줄이 남았다
    pg.replace_stock_map([], origin="local", loaded_by=JOB, received_at=T0)
    assert pg.has_stock_map() is False
