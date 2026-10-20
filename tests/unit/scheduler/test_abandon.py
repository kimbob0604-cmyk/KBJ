"""서비스 시작 때 앞 프로세스가 남긴 running·queued 실행 닫기(kbj.services.scheduler.__main__)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kbj.services.scheduler.__main__ import ABANDON_REASON, abandon_orphans
from kbj.services.scheduler.claims import MemoryRunLog, RunRecord

BOOT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _rec(rid: str, status: str, started: datetime | None) -> RunRecord:
    job, rest = rid.split(":", 1)
    as_of, attempt = rest.rsplit(":", 1)
    return RunRecord(rid, job, as_of, int(attempt), status, started)  # type: ignore[arg-type]


def test_orphans_before_boot_become_timeout_and_rerunnable() -> None:
    runs = MemoryRunLog()
    old = BOOT - timedelta(hours=3)
    runs.record(_rec("us.eod:2026-10-19:1", "running", old))
    runs.record(_rec("earnings.alerts:2026-10-20T17:25:1", "queued", None))
    runs.record(_rec("board.daily:2026-10-20:2", "ok", old))
    runs.record(_rec("rules.close:2026-10-20:1", "running", BOOT + timedelta(seconds=5)))  # 새것
    closed = abandon_orphans(runs, BOOT)
    assert sorted(closed) == ["earnings.alerts@2026-10-20T17:25", "us.eod@2026-10-19"]
    eod = runs.latest("us.eod", "2026-10-19")
    assert eod is not None and eod.status == "timeout" and eod.finished_at == BOOT
    assert eod.detail["reason"] == ABANDON_REASON and eod.detail["abandoned"] is True
    ok = runs.latest("board.daily", "2026-10-20")
    assert ok is not None and ok.status == "ok"
    fresh = runs.latest("rules.close", "2026-10-20")
    assert fresh is not None and fresh.status == "running"
    assert abandon_orphans(runs, BOOT) == []  # 두 번 닫지 않는다
