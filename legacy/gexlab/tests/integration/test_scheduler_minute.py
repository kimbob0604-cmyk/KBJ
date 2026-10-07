"""scheduler 분봉 적재 → 실제 TimescaleDB (docs/phase1_design.md §8).

가짜 KIS 분봉(tests/fakes/kis_server.py) → MinuteLoader → PostgresSink. 주간(F)·야간(CM) 세션을
적재하고 다시 적재해도 minute_bars 행이 늘지 않으며(유니크 (code, market, ts) DO UPDATE) 고쳐진 봉
값은 바뀌는지, 야간 24~30시 봉이 다음 날 달력 시각·귀속 거래일로 들어가는지, 원문이 raw_messages
에 남는지 본다. scheduler 조립처럼 하루 구동(MinuteDaily)·Redis 레이트리미터·auth 토큰 읽기·스풀
있는 싱크로도 한 번. 컨테이너는 tests/integration/conftest.py 가 띄우고 지운다 — Docker 가 없으면
건너뛴다.
"""

from __future__ import annotations

import time as time_mod
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import psycopg
import pytest

from core.calendar import TradingCalendar
from data.kis.ratelimit import LocalRateLimiter
from data.kis.rest import MINUTE_TR
from data.spool import DiskSpool
from data.store import PostgresSink
from db.migrate import migrate
from services.auth.health import MemoryHealthSink
from services.bus import MasterSnapshot
from services.chain_feed import load_context
from services.scheduler.minute import (
    MinuteDaily,
    MinuteLoader,
    SessionJob,
    minute_thread_submit,
    reader_kis_client,
)
from tests.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    day_bar_times,
    default_chain,
    fake_settings,
    make_client,
    night_bar_times,
    seed_cached_token,
)
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
D28, D29 = date(2026, 9, 28), date(2026, 9, 29)
CODE = "A01612"


def kst(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), tzinfo=KST)


def _rows(dsn: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as c:
        return c.execute(query.encode(), params).fetchall()


def _master() -> MasterSnapshot:
    text = "\n".join(default_chain().master_lines()) + "\n"
    digest = MasterSnapshot.digest(text)
    return MasterSnapshot(
        asof=kst(D28, 8), trade_date=D28, session="day", rows=1, sha256=digest, text=text
    )


def test_day_and_night_sessions_land_once_with_their_tags(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("minute"))
    migrate(url)
    clock = FakeClock(kst(D28, 16))
    server = FakeKisServer(clock, default_chain(), CAL)
    server.add_minute_bars(CODE, "F", day_bar_times(D28))
    server.add_minute_bars(CODE, "CM", night_bar_times(D28))
    kis = make_client(server, LocalRateLimiter(clock=clock))
    master = _master()

    def day() -> SessionJob:
        return SessionJob("day", D28, D28, deadline=kst(D28, 17, 50), next_try=kst(D28, 16))

    def night() -> SessionJob:
        return SessionJob("night", D28, D29, deadline=kst(D29, 8), next_try=kst(D29, 6, 10))

    with PostgresSink(url, service="scheduler") as pg:
        loader = MinuteLoader(kis, pg, CAL, now=clock.now)
        assert loader.run(day(), master).ok
        clock.set(kst(D29, 6, 10))
        assert loader.run(night(), master).ok
        last = kst(D29, 6)
        server.add_minute_bars(CODE, "CM", [last], base=Decimal("1300.00"))  # 고쳐진 마지막 봉
        clock.set(kst(D29, 6, 20))
        assert loader.run(night(), master).ok  # 다시 — 행은 그대로, 값만 고친다
    counts = _rows(
        url,
        "SELECT market, session, trade_date, count(*), min(ts), max(ts) FROM minute_bars "
        "GROUP BY market, session, trade_date ORDER BY market",
    )
    assert counts == [
        ("CM", "night", D29, 721, kst(D28, 18), kst(D29, 6)),
        ("F", "day", D28, 411, kst(D28, 8, 45), kst(D28, 15, 45)),
    ]
    (row,) = _rows(
        url,
        "SELECT close, received_at FROM minute_bars WHERE market = 'CM' "
        "AND ts = '2026-09-28 21:00:00+00'",  # 20260928 300000 = 09-29 06:00 KST
    )
    assert row[0] >= Decimal("1300") and row[1] >= kst(D29, 6, 20)  # 다시 받은 시각
    raw = _rows(
        url,
        "SELECT trade_date, session, count(*) FROM raw_messages WHERE tr_id = %s "
        "GROUP BY trade_date, session ORDER BY session",
        (MINUTE_TR,),
    )
    assert raw == [(D28, "day", 5), (D29, "night", 16)]


def test_the_scheduler_assembly_loads_through_the_spooled_sink(
    timescale: PgContainer, tmp_path: Path
) -> None:
    """main() 과 같은 조립: MinuteDaily(작업 스레드) → MinuteLoader(Redis 레이트리미터·auth 토큰
    읽기·Redis 문맥) → 상태 루프 싱크(스풀). 16:00 주간, 다음 날 06:10 밤, 창 안 재기동은 멱등."""
    url = timescale.dsn(timescale.fresh_database("minute_daily"))
    migrate(url)
    clock = FakeClock(kst(D28, 16))
    server = FakeKisServer(clock, default_chain(), CAL)
    server.add_minute_bars(CODE, "F", day_bar_times(D28))
    server.add_minute_bars(CODE, "CM", night_bar_times(D28))
    r = fakeredis.FakeRedis()
    seed_cached_token(r, clock.now())
    health = MemoryHealthSink()
    master = _master()

    def daily(pg: PostgresSink) -> MinuteDaily:
        kis = reader_kis_client(
            fake_settings(), r, clock=clock, now=clock.now, transport=server.transport
        )
        loader = MinuteLoader(kis, pg, CAL, now=clock.now, context=lambda: load_context(r))
        return MinuteDaily(loader, CAL, health, submit=minute_thread_submit)

    def drive(d: MinuteDaily, start: datetime, loaded: int) -> None:
        clock.set(start)
        t = start
        d.step(t, master)
        while len(health.of("minute_bars_loaded")) < loaded:  # 작업 스레드 — 끝날 때까지 step
            assert t - start < timedelta(seconds=30), health.kinds()
            t += timedelta(milliseconds=50)
            d.step(t, master)
            time_mod.sleep(0.05)

    spool = DiskSpool(tmp_path / "spool" / "scheduler")
    with PostgresSink(url, service="scheduler", spool=spool) as pg:
        drive(daily(pg), kst(D28, 16), 1)
        drive(daily(pg), kst(D29, 6, 10), 2)
        drive(daily(pg), kst(D29, 6, 30), 3)  # 재기동 — 같은 밤을 다시
        assert pg.flush_spool() and not spool.pending
    counts = _rows(
        url,
        "SELECT market, session, trade_date, count(*) FROM minute_bars "
        "GROUP BY market, session, trade_date ORDER BY market",
    )
    assert counts == [("CM", "night", D29, 721), ("F", "day", D28, 411)]
    assert server.token_posts == 0 and health.kinds() == ["minute_bars_loaded"] * 3
