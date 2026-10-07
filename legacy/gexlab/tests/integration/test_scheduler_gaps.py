"""scheduler 무결측 판정 → 실제 TimescaleDB (docs/phase1_design.md §9).

합성한 2026-09-28(월) 주간 하루(tests/fakes/gap_inputs.py `perfect_day` — SYNTHETIC)를 수집기·
ws-gateway·scheduler 가 쓰는 레코드로 표에 쓰고(chain_snapshots·fut_board·investor_flow·
series_expiries·raw_messages 보강 1·기초자산 원문·minute_bars·fut_ticks·health_events),
`GapDaily` 가 PostgresSink(스풀 없음)로 읽어 판정해 collection_reports·collection_gaps 를 쓰는지
본다. 공백을 만든 뒤 늦게 든 데이터로 다시 판정하면 옛 공백 행이 사라지는지도. 컨테이너는
tests/integration/conftest.py 가 띄우고 지운다 — Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from core.calendar import TradingCalendar
from data.store import FuturesTickRecord, MinuteBarRecord, PostgresSink
from db.migrate import migrate
from scripts.probe_common import TR_PRICE, TR_TOP
from services.auth.health import HealthEvent as AuthHealthEvent
from services.auth.health import MemoryHealthSink
from services.gaps import SessionInputs
from services.poller.records import ChainRecord, ExpiryRecord, FuturesRecord, InvestorRecord
from services.recorder.envelope import wrap
from services.scheduler.gaps import GapDaily
from tests.fakes.gap_inputs import D28, kst, perfect_day
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

CAL = TradingCalendar.default()
TAG = (D28, "day")


def inline[T](fn: Callable[[], T]) -> Future[T]:
    fut: Future[T] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


def _rows(dsn: str, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as c:
        return c.execute(query.encode(), params or None).fetchall()


def write_session(pg: PostgresSink, inputs: SessionInputs, d: date = D28) -> None:
    """판정 입력을 서비스들이 쓰는 레코드로 표에 쓴다."""
    pg.write_expiries(
        [
            ExpiryRecord(
                ts=ts,
                trade_date=d,
                session="day",
                mrkt_cls=c,
                expiry=m,
                source="kis",
                last_trade_date=last,
            )
            for c, m, _, last, ts in inputs.series_dates
        ]
    )
    pg.write_chain(
        [
            ChainRecord(
                ts=ts,
                trade_date=d,
                session="day",
                mrkt_cls=c,
                expiry=m,
                strike=Decimal("1100.00"),
                cp="C",
                source="board",
                last=Decimal("12.5"),
            )
            for c, m, ts in inputs.board
        ]
    )
    pg.write_futures(
        [
            FuturesRecord(
                ts=ts, trade_date=d, session="day", code="A01612", market="F", source="board"
            )
            for ts in inputs.fut_board
        ]
    )
    pg.write_investor(
        [
            InvestorRecord(
                ts=ts, trade_date=d, session="day", market_code=m, sector_code=s, investor="frgn"
            )
            for m, s, ts in inputs.investor
        ]
    )
    pg.write_raw(
        [
            wrap(
                {"rt_cd": "0", "msg_cd": "MCA00000", "output1": {"futs_prpr": "12.5"}},
                source="kis_rest",
                tr_id=TR_PRICE,
                received_at=ts,
                tagger=lambda _t: TAG,
                key=f"fill1:{label}:1100.00:C|FID_COND_MRKT_DIV_CODE=O&FID_INPUT_ISCD=B01610",
            )
            for label, ts in inputs.fill1
        ]
        + [
            wrap(
                {"rt_cd": "0", "msg_cd": "MCA00000", "output1": {}, "output2": []},
                source="kis_rest",
                tr_id=TR_TOP,
                received_at=ts,
                tagger=lambda _t: TAG,
                key="underlying|FID_COND_MRKT_DIV_CODE=F&FID_INPUT_ISCD=A01612",
            )
            for ts in inputs.underlying
        ]
    )
    got = kst(d, 16)
    pg.write_minute_bars(
        [
            MinuteBarRecord(
                ts=ts,
                trade_date=d,
                session="day",
                received_at=got,
                code=code,
                market="F",
                open=Decimal("1100"),
                high=Decimal("1101"),
                low=Decimal("1099"),
                close=Decimal("1100.5"),
                volume=vol,
            )
            for code, ts, vol in inputs.bars
        ]
    )
    pg.write_fut_ticks(
        [
            FuturesTickRecord(
                ts=m + timedelta(seconds=7),
                trade_date=d,
                session="day",
                received_at=m + timedelta(seconds=7, milliseconds=30),
                tr_id="H0IFCNT0",
                code=code,
                seq=i,
                price=Decimal("1100.05"),
            )
            for i, (code, m, _) in enumerate(inputs.tick_minutes)
        ]
    )
    pg.write_health(
        [AuthHealthEvent("ws_connected", "1번째 연결", kst(d, 8, 0, 5), service="ws-gateway")]
    )


def test_a_day_written_by_the_services_is_judged_from_the_database(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("gapday"))
    migrate(url)
    inputs = perfect_day()
    with PostgresSink(url, service="scheduler") as pg:
        write_session(pg, inputs)
        health = MemoryHealthSink()
        daily = GapDaily(pg, CAL, health, submit=inline)
        daily.step(kst(D28, 16))
        assert health.kinds() == ["nogap_session_ok"], health.events
        rows = _rows(
            url,
            "SELECT stream, status, required, expected, received, gaps FROM collection_reports "
            "WHERE trade_date = %s AND session = 'day' ORDER BY stream",
            D28,
        )
        by = {r[0]: r[1:] for r in rows}
        assert by["fut_board"] == ("ok", True, 840, 840, 0)
        assert by["underlying"] == ("ok", True, 840, 840, 0)
        assert by["board:WKM:260904"][0] == "ok" and by["board:WKM:261001"][0] == "ok"
        assert by["fill1:M:202610"][:2] == ("ok", True)
        assert by["ws_connection"] == ("ok", True, 25200, 25200, 0)
        assert by["fut_trades"] == ("ok", True, 411, 411, 0)
        assert len([s for s in by if s.startswith("investor:")]) == 7
        assert _rows(url, "SELECT count(*) FROM collection_gaps") == [(0,)]

        # 13:00~13:04 체결이 DB 에 없다 → 공백 한 줄. 다시 판정하면 리포트가 바뀐다
        cut = (kst(D28, 13).astimezone(UTC), kst(D28, 13, 5).astimezone(UTC))
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(b"DELETE FROM fut_ticks WHERE ts >= %s AND ts < %s", cut)
        again = GapDaily(pg, CAL, health, submit=inline)
        again.step(kst(D28, 16, 30))
        assert health.kinds()[-1] == "collection_gaps_found"
        gaps = _rows(
            url, "SELECT stream, start_ts, end_ts, expected, received, detail FROM collection_gaps"
        )
        # 창 [m − 60초, m + 60초): 13:00 봉은 12:59 체결로, 13:05 는 13:05 체결로 덮인다
        assert gaps == [("fut_trades", kst(D28, 13, 1), kst(D28, 13, 5), 4, 0, {"code": "A01612"})]
        status = _rows(
            url,
            "SELECT status, gaps, max_gap_s FROM collection_reports WHERE stream = 'fut_trades'",
        )
        assert status == [("gaps", 1, 240.0)]

        # 늦게 든 체결(스풀 재적재 등) 뒤 다시 판정 — 옛 공백 행이 남지 않는다
        write_session(pg, inputs)
        third = GapDaily(pg, CAL, health, submit=inline)
        third.step(kst(D28, 17))
        assert health.kinds()[-1] == "nogap_session_ok"
        assert _rows(url, "SELECT count(*) FROM collection_gaps") == [(0,)]
        assert _rows(url, "SELECT count(*) FROM collection_reports") == [(len(by),)]
