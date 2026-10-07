"""scheduler KRX 전일 적재 → 실제 TimescaleDB (docs/phase1_design.md §2·§8).

가짜 KRX(httpx.MockTransport + 원본 발췌) → KrxLoader → 스풀 없는 PostgresSink. 다시 돌려도 행이
늘지 않고(멱등), 재기동한 loader 는 DB 를 보고 부르지 않는다(이어 받기). DB 상장 목록으로 마스터 ⊇
KRX 대조까지. 컨테이너는 tests/integration/conftest.py 가 띄우고 지운다 — Docker 가 없으면
건너뛴다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import psycopg
import pytest

from data.krx.eod import FUT_DAILY, OPT_DAILY
from data.store import PostgresSink
from db.migrate import migrate
from services.scheduler.krx import KrxCallBudget, KrxLoader
from services.scheduler.master_check import master_covers_krx
from tests.fakes.kis_server import default_chain
from tests.fakes.krx_server import FakeKrx
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
D23 = date(2026, 9, 23)


def _rows(dsn: str, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as c:
        return c.execute(query.encode()).fetchall()


def test_loading_twice_and_after_a_restart_writes_each_row_once(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("krxload"))
    migrate(url)
    fake = FakeKrx()
    fake.publish(D23)
    redis = fakeredis.FakeRedis()
    at = datetime(2026, 9, 28, 8, 5, tzinfo=KST)
    with PostgresSink(url, service="scheduler") as pg:
        loader = KrxLoader(fake.client(), pg, KrxCallBudget(redis, 200), now=lambda: at)
        first = loader.load(D23)
        assert first.done and [r.rows for r in first.results] == [13, 3]
        assert [r.status for r in loader.load(D23).results] == ["present", "present"]
    with PostgresSink(url, service="scheduler") as pg:  # 재기동 — 새 연결·새 loader
        again = KrxLoader(fake.client(), pg, KrxCallBudget(redis, 200), now=lambda: at)
        assert [r.status for r in again.load(D23).results] == ["present", "present"]
        listing = again.listing(D23)  # 마스터 ⊇ KRX 대조 입력 — DB 에서
    assert fake.requested() == [(OPT_DAILY, "20260923"), (FUT_DAILY, "20260923")]
    opt = _rows(url, "SELECT trade_date, session, imp_volt, ts FROM krx_opt_daily")
    assert len(opt) == 13 and {r[0] for r in opt} == {D23} and {r[3] for r in opt} == {at}
    assert sum(1 for r in opt if r[1] == "night" and r[2] is None) == 6
    assert sum(1 for r in opt if r[1] == "night") == 6
    fut = _rows(url, "SELECT session, setl_prc FROM krx_fut_daily ORDER BY session")
    assert len(fut) == 3 and ("day", Decimal("1120.35")) in fut
    assert ("kospi200_weekly_mon", "260904", "P", Decimal("970.00")) in listing
    report = master_covers_krx(default_chain().master_rows(), listing, D23)
    assert report.ok and report.compared == 3 and report.krx_strikes == 6
