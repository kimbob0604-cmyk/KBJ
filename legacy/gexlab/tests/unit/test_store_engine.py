"""engine 저장 — GX 자기 마이그레이션·섀도 점검 SQL (DB 없이, docs/phase3_design.md §4).

KBJ P7(묶음 ENG): 이 파일의 나머지 30 항목(003 표 정의·레코드·engine 쓰기·입력 읽기)은 KBJ
저장소 `kbj.store.repos.gex` 로 옮겨 `tests/unit/store/test_gex_repo_engine.py` 에서 돈다(같은
단언, 대상만 KBJ). 여기 남은 둘은 legacy 에만 있는 것 — GX 자기 마이그레이션(GX 통합 DB)과
legacy 도구 `shadow_report` 의 읽기 SQL(D-P7-4 — 운영 도구는 legacy 에 남는다).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from data import store
from db.migrate import MIGRATIONS_DIR
from tests.unit.test_store_sql import Factory

KST = ZoneInfo("Asia/Seoul")


def test_applied_migrations_are_left_alone() -> None:
    assert "CREATE TABLE levels" not in (MIGRATIONS_DIR / "001_init.sql").read_text("utf-8")
    assert "levels" not in (MIGRATIONS_DIR / "002_collection_reports.sql").read_text("utf-8")


def _sink(f: Factory) -> store.PostgresSink:
    return store.PostgresSink(connect=f, service="engine")


def test_shadow_report_reads_are_grouped_by_output_and_flag_with_parameters_only() -> None:
    """섀도 운영 점검 읽기(scripts/shadow_report.py — 설계 §4): 귀속 거래일 범위의 산출을 (표,
    이름, 행 플래그)별로 센다 — 행·사이클(ts)·invalid·예외(`error` 칸)·null(예외 행 뺌). null 행은
    (표, 이름, 플래그, 범위, 품질, 고른 칸)으로 묶고, engine 예외 health 는 태그가 기간 안이거나
    태그 없이 시각이 창 안인 것만."""
    first, last = date(2026, 9, 22), date(2026, 9, 28)
    start = datetime(2026, 9, 18, 17, 50, tzinfo=KST)
    end = datetime(2026, 9, 28, 17, 50, tzinfo=KST)
    f = Factory(
        results=[
            [("metrics", "vex", "shadow", 30, 10, 3, 1, 2)],
            [("levels", "flip", "visible", "all", "ok", {"crossings": []}, 4)],
            [("engine_metric_failed", "vex: ValueError: x", 2)],
        ]
    )
    s = _sink(f)
    assert s.engine_output_counts(first, last) == [("metrics", "vex", "shadow", 30, 10, 3, 1, 2)]
    assert s.engine_null_groups(first, last, ("crossings", "expiries")) == [
        ("levels", "flip", "visible", "all", "ok", {"crossings": []}, 4)
    ]
    kinds = ("engine_metric_failed", "engine_cycle_failed")
    assert s.engine_failures(first, last, start, end, kinds) == [
        ("engine_metric_failed", "vex: ValueError: x", 2)
    ]
    counts, nulls, health = (q for q, _ in f.conns[0].reads)
    params = [p for _, p in f.conns[0].reads]
    for table in ('"metrics"', '"levels"', '"oi_changes"'):
        assert table in counts and table in nulls
    assert "GROUP BY metric, flag" in counts and "GROUP BY name, flag" in counts
    assert "count(DISTINCT ts)" in counts and "payload ? %s" in counts and "detail ? %s" in counts
    head = ("invalid", "error", "error", first, last)
    assert params[0] == (*head, *head, "invalid", first, last)
    # 고른 칸만 jsonb 로 — 칸 이름은 식별자 검사를 거친 리터럴, 값 없는 행만, 예외 행은 뺀다
    assert "jsonb_build_object('crossings', \"payload\" -> 'crossings'" in nulls
    assert "SELECT 'metrics', metric, flag, scope, quality" in nulls
    assert "SELECT 'oi_changes', 'oi_changes', flag, ''" in nulls and "prev_ts IS NULL" in nulls
    assert params[1] == (first, last, "error", first, last, "error", first, last)
    assert '"health_events"' in health and "trade_date IS NULL AND ts >= %s AND ts < %s" in health
    assert params[2] == ("engine", list(kinds), first, last, start.astimezone(UTC), end)
    assert all("2026" not in q and "engine_" not in q for q in (counts, nulls, health))
    with pytest.raises(ValueError):
        s.engine_null_groups(first, last, ("x'; --",))
    with pytest.raises(ValueError):
        s.engine_null_groups(first, last, ())
    with pytest.raises(ValueError):
        s.engine_failures(first, last, datetime(2026, 9, 18), end, kinds)  # noqa: DTZ001
