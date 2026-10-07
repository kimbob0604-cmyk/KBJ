"""무결측 리포트 스크립트 → 실제 TimescaleDB (scripts/nogap_report.py, 설계 §9).

scheduler 가 쓰는 교체(`replace_gap_report`)로 09-22·09-23·09-28(추석 휴장 사이) 세션 리포트를 넣고
스크립트가 DB 에서 읽어 '3거래일 연속 무결측'을 판정하는지, 저장된 데이터가 없을 때 `--recompute`
가 무결측으로 보지 않는지 본다. 컨테이너는 tests/integration/conftest.py 가 띄우고 지운다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.calendar import TradingCalendar
from data.store import CollectionReportRecord, PostgresSink
from db.migrate import migrate
from scripts.nogap_report import main, sessions_of
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
TUE, WED, MON = date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 28)


def _report(d: date, session: str, stream: str, status: str = "ok") -> CollectionReportRecord:
    start = datetime(d.year, d.month, d.day, 8, 45, tzinfo=KST)
    return CollectionReportRecord(
        trade_date=d,
        session=session,  # type: ignore[arg-type]
        stream=stream,
        evaluated_at=start + timedelta(hours=8),
        span_start=start,
        span_end=start + timedelta(hours=7),
        required=True,
        status=status,  # type: ignore[arg-type]
        gaps=0 if status != "gaps" else 1,
    )


def test_three_trading_days_across_a_holiday_from_the_database(
    timescale: PgContainer, capsys: pytest.CaptureFixture[str]
) -> None:
    url = timescale.dsn(timescale.fresh_database("nogap"))
    migrate(url)
    with PostgresSink(url, service="scheduler") as pg:
        for d in (TUE, WED, MON):
            for s in sessions_of(d, CAL):
                reports = [_report(d, s, x) for x in ("fut_board", "ws_connection", "fut_trades")]
                pg.replace_gap_report(d, s, [], reports)
        assert main(["--end", "2026-09-28", "--days", "4"], store=pg, cal=CAL) == 0
        out = capsys.readouterr().out
        assert "가장 긴 연속 무결측: 3거래일 (2026-09-22 ~ 2026-09-28" in out
        assert "| 2026-09-21 | 주간 | 리포트 없음 |" in out
        # 수요일 밤에 공백이 생기면 끊긴다
        pg.replace_gap_report(WED, "night", [], [_report(WED, "night", "ws_connection", "gaps")])
        assert main(["--end", "2026-09-28", "--days", "4"], store=pg, cal=CAL) == 1
        # 저장된 데이터가 없으면 다시 판정해도 무결측이 아니다(공백·판정 불가)
        assert main(["--end", "2026-09-28", "--days", "2", "--recompute"], store=pg, cal=CAL) == 1
        out = capsys.readouterr().out
        assert "| 2026-09-28 | 주간 | 공백 |" in out
