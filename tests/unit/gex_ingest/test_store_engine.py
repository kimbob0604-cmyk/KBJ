"""섀도 점검 읽기(GX `tests/unit/test_store_engine.py` 의 남은 하나 — P9 GXI 승격).

GX `PostgresSink.engine_output_counts`·`engine_null_groups`·`engine_failures` 는 KBJ
`kbj.store.repos.gex_ingest.PgGexIngestRepo` 에 옮겼다
(`python -m kbj.services.engine.gex_report shadow`).
가짜 연결(`Factory`)만 쓴다 — 실제 DB 없음.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from kbj.store.repos.gex_ingest import PgGexIngestRepo
from tests.unit.gex_ingest.test_store_sql import Factory

KST = ZoneInfo("Asia/Seoul")


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
    s = PgGexIngestRepo(f)
    assert s.engine_output_counts(first, last) == [("metrics", "vex", "shadow", 30, 10, 3, 1, 2)]
    assert s.engine_null_groups(first, last, ("crossings", "expiries")) == [
        ("levels", "flip", "visible", "all", "ok", {"crossings": []}, 4)
    ]
    kinds = ("engine_metric_failed", "engine_cycle_failed")
    assert s.engine_failures(first, last, start, end, kinds) == [
        ("engine_metric_failed", "vex: ValueError: x", 2)
    ]
    reads = [r for c in f.conns for r in c.reads]  # KBJ: 읽기마다 짧은 연결
    counts, nulls, health = (q for q, _ in reads)
    params = [p for _, p in reads]
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
