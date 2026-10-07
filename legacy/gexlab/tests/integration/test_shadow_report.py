"""섀도 운영 점검 통합 시험 — 실제 TimescaleDB (scripts/shadow_report.py, docs/phase3_design.md §4).

engine 이 2026-09-28 합성 작은 스냅샷(그대로·하루 뒤로 옮긴 것 — 0DTE 가 빈 날)으로 쓴 levels·
metrics 행을 DB 에 넣고 점검을 돌린다: DB 가 돌려준 payload·detail 칸(jsonb)으로도 engine 의 null
행이 모두 명세 null 로 가려지고, 명세 밖 null·예외 행·예외 health 는 그 플래그를 오류로 만든다.

컨테이너는 tests/integration/conftest.py 가 세션마다 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.calendar import TradingCalendar
from data.store import PostgresSink
from db.migrate import migrate
from scripts import shadow_report as sr
from scripts.make_golden import CORE_FIXTURES
from services.auth.health import HealthEvent as AuthHealthEvent
from services.bus import BasisBook
from services.engine.evaluate import evaluate_cycle
from services.engine.records import MetricRecord
from services.engine.service import ENGINE_REGISTRY
from tests.fakes.engine_inputs import snapshot_cycle
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()


def test_engine_rows_from_the_database_have_only_spec_nulls_and_errors_are_flagged(
    timescale: PgContainer, tmp_path: Path
) -> None:
    url = timescale.dsn(timescale.fresh_database("shadow_report"))
    migrate(url)
    flags = tmp_path / "features.yaml"
    flags.write_text("metrics: {}\n", encoding="utf-8")
    book = BasisBook()
    with PostgresSink(url, service="engine") as sink:
        for shift in (None, timedelta(days=1)):
            c = snapshot_cycle(CORE_FIXTURES[0], shift=shift)
            r = evaluate_cycle(c.inp, book, cal=CAL, registry=ENGINE_REGISTRY)
            assert r.status == "ok"
            sink.write_levels(r.levels)
            sink.write_metrics(r.metrics)
            book = r.basis
        clean = sr.build(
            sink, CAL, [date(2026, 9, 28), date(2026, 9, 29)], "all", sr.load_features(flags)
        )
        at = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
        stamp = {"ts": at, "trade_date": date(2026, 9, 29), "session": "day", "flag": "shadow"}
        sink.write_metrics(
            [
                # §6.1 hiro 는 늘 값이 있다 — 값 없는 행은 명세 밖
                MetricRecord(metric="hiro", scope="all", value=None, quality="estimated", **stamp),
                MetricRecord(
                    metric="max_pain",
                    scope="series",
                    key="M:202610",
                    value=None,
                    payload={"error": "KeyError"},
                    quality="invalid",
                    **stamp,
                ),
            ]
        )
        sink.write_health(
            [AuthHealthEvent("engine_level_failed", "all/flip: X: y", at, service="engine")],
            tagger=lambda _: (date(2026, 9, 29), "day"),
        )
        broken = sr.build(
            sink, CAL, [date(2026, 9, 28), date(2026, 9, 29)], "all", sr.load_features(flags)
        )
    got = {f.name: f for f in clean.flags}
    computed = [f for f in clean.flags if f.rows]
    assert {f.name for f in computed} >= {"net_gex", "flip", "top_levels", "vex", "skew_25d"}
    for f in computed:
        assert (f.verdict, f.off_spec, f.errors, f.health) == ("clean", 0, 0, 0), f
    nulls = {o.name: o.spec_reasons for f in computed for o in f.outputs}
    assert nulls["net_gex"]["empty_scope"] == 1  # 09-29 0DTE
    assert nulls["top_levels"]["empty_scope"] == 1
    assert nulls["skew_25d"]["put_out_of_range"] == 2  # 두 날 모두 M:202610
    assert got["hiro"].verdict == "no_rows" and not clean.satisfied
    after = {f.name: f for f in broken.flags}
    assert (after["hiro"].verdict, after["hiro"].off_spec) == ("errors", 1)
    assert (after["max_pain"].verdict, after["max_pain"].errors) == ("errors", 1)
    assert (after["flip"].verdict, after["flip"].health) == ("errors", 1)
    assert after["vex"].verdict == "clean"
