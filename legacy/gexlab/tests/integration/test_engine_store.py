"""engine 저장 통합 시험 — 섀도 운영 점검 읽기만 (실제 TimescaleDB, docs/phase3_design.md §4).

KBJ P7(묶음 ENG): 이 파일의 나머지 8 항목(003 표·산출 쓰기·입력 읽기)은 KBJ 저장소로 옮겨
`tests/integration/test_gex_engine_pg.py` 에서 돈다(같은 단언, 대상만 KBJ `PgGexRepo`·0006). 여기
남은 하나는 legacy 도구 `scripts/shadow_report.py` 의 읽기(D-P7-4 — 운영 도구는 legacy 에 남는다).

컨테이너는 tests/integration/conftest.py 가 세션마다 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from data.store import PostgresSink
from db.migrate import migrate
from services.auth.health import HealthEvent as AuthHealthEvent
from services.engine.records import LevelRecord, MetricRecord, OiChangeRecord
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")


def test_shadow_report_reads_count_outputs_by_flag_and_the_engine_failures(
    timescale: PgContainer,
) -> None:
    """(표, 이름, 행 플래그)별 행·사이클·invalid·예외·null — 기간 밖 행은 세지 않고, 플래그가
    기간 중에 바뀌면 두 줄. null 묶음은 고른 칸만(없으면 None), 예외(`error`) 행은 빼고. health 는
    engine 의 고른 종류만 — 태그가 기간 안이거나 태그 없이 시각이 창 안."""
    url = timescale.dsn(timescale.fresh_database("shadow"))
    migrate(url)
    d1, d2, before = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 18)
    t1 = datetime(2026, 9, 21, 10, 0, tzinfo=KST)
    t2 = t1 + timedelta(seconds=30)

    def metric(at: datetime, td: date, scope: str, value: float | None, **kw: Any) -> MetricRecord:
        base: dict[str, Any] = {"quality": "ok", "flag": "shadow", "payload": {}}
        row = {"ts": at, "trade_date": td, "session": "day", "metric": "vex", "scope": scope}
        return MetricRecord.model_validate(row | {"value": value} | base | kw)

    rows = [
        metric(t1, d1, "all", 1.5e8, payload={"expiries": ["WKM:261001"]}),
        metric(t1, d1, "0dte", None, payload={"expiries": []}),  # 빈 범위 — 명세 null
        metric(t2, d1, "all", None, payload={"error": "ValueError"}, quality="invalid"),
        metric(t2, d2, "nearest", None, payload={"expiries": ["M:202610"]}, flag="visible"),
        metric(t1 - timedelta(days=3), before, "all", None),  # 기간 밖
    ]
    flip = LevelRecord(
        ts=t1,
        trade_date=d1,
        session="day",
        scope="all",
        name="flip",
        value=None,
        detail={"crossings": [], "multi_cross": False},
        quality="ok",
    )
    cells = [
        OiChangeRecord(
            ts=at,
            trade_date=d1,
            session="day",
            mrkt_cls="WKM",
            expiry="261001",
            strike=Decimal("1100.00"),
            cp="C",
            oi=oi,
            prev_ts=prev,
            prev_oi=None if prev is None else 10,
            change=None if prev is None else oi - 10,
            quality="ok",
        )
        for at, oi, prev in ((t1, 10, None), (t2, 15, t1))
    ]

    def tag(_: datetime) -> tuple[date, str]:
        return d1, "day"

    def ev(kind: str, text: str, at: datetime, service: str = "engine") -> AuthHealthEvent:
        return AuthHealthEvent(kind, text, at, "warning", service=service)

    start = datetime(2026, 9, 18, 17, 50, tzinfo=KST)
    end = datetime(2026, 9, 22, 17, 50, tzinfo=KST)
    boom = "vex/all/: ValueError: boom"
    with PostgresSink(url, service="engine") as sink:
        sink.write_metrics(rows)
        sink.write_levels([flip])
        sink.write_oi_changes(cells)
        sink.write_health(
            [
                ev("engine_metric_failed", boom, t2),
                ev("engine_metric_failed", boom, t2 + timedelta(minutes=10)),  # 10분 뒤 한 번 더
                ev("engine_series_stale", "M:202610: 옛 행", t2),  # 고르지 않은 종류
                ev("engine_metric_failed", "x", t2, service="poller"),  # engine 이 아니다
            ],
            tagger=tag,
        )
        sink.write_health(
            [
                ev("engine_daily_failed", "일별 지표 실패(2026-09-21): E: x", t1.replace(hour=16)),
                ev("engine_daily_failed", "일별 지표 실패(2026-09-17): E: x", start - timedelta(1)),
            ]
        )
        counts = sink.engine_output_counts(d1, d2)
        nulls = sink.engine_null_groups(d1, d2, ("expiries", "crossings"))
        kinds = ("engine_metric_failed", "engine_daily_failed", "engine_cycle_failed")
        failures = sink.engine_failures(d1, d2, start, end, kinds)
    assert counts == [
        ("levels", "flip", "visible", 1, 1, 0, 0, 1),
        ("metrics", "vex", "shadow", 3, 2, 1, 1, 1),
        ("metrics", "vex", "visible", 1, 1, 0, 0, 1),
        ("oi_changes", "oi_changes", "shadow", 2, 2, 0, 0, 1),
    ]
    assert nulls == [
        ("levels", "flip", "visible", "all", "ok", {"expiries": None, "crossings": []}, 1),
        ("metrics", "vex", "shadow", "0dte", "ok", {"expiries": [], "crossings": None}, 1),
        (
            "metrics",
            "vex",
            "visible",
            "nearest",
            "ok",
            {"expiries": ["M:202610"], "crossings": None},
            1,
        ),
        ("oi_changes", "oi_changes", "shadow", "", "ok", {"first_snapshot": True}, 1),
    ]
    assert failures == [
        ("engine_daily_failed", "일별 지표 실패(2026-09-21): E: x", 1),
        ("engine_metric_failed", boom, 2),
    ]
