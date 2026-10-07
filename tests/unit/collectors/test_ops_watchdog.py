"""`ops.watchdog` — 하트비트 끊김·놓친 실행·마감 지난 running 을 찾아 알린다(설계 §1.7)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.time import KST
from kbj.services.collectors import ops_watchdog as wd
from kbj.services.scheduler.claims import MemoryRunLog, RunRecord
from kbj.services.scheduler.handlers import JobContext
from kbj.services.scheduler.registry import Registry

REG = Registry.load(Path(__file__).resolve().parents[3] / "config" / "jobs.yaml")
KR = TradingCalendar.default()
US = us_calendar()
NOW = datetime(2026, 10, 6, 10, 30, tzinfo=KST)


class Ticket:
    def __init__(self, ok: bool, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason


class Notes:
    def __init__(self, ok: bool = True) -> None:
        self.sent: list[dict[str, Any]] = []
        self.ok = ok

    def __call__(self, text: str, **kw: Any) -> Ticket:
        self.sent.append({"text": text, **kw})
        return Ticket(self.ok, "" if self.ok else "Redis 없음")


def healthy(service: str, now: datetime) -> float | None:
    return 5.0


def seeded_runs() -> MemoryRunLog:
    runs = MemoryRunLog()
    for job, as_of in (("ops.nightly", "2026-10-06"), ("filings.corp_code", "2026-10-06")):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    runs.record(
        RunRecord("ops.watchdog:2026-10-06T10:00:1", "ops.watchdog", "2026-10-06T10:00", 1, "ok")
    )
    return runs


def ctx(
    notes: Notes, runs: MemoryRunLog, *, running: list[RunRecord] | None = None, hb: Any = healthy
) -> JobContext:
    return JobContext(
        "ops.watchdog",
        "2026-10-06T10:30",
        "ops.watchdog:2026-10-06T10:30:1",
        1,
        NOW,
        resources={
            "registry": REG,
            "runs": runs,
            "kr": KR,
            "us": US,
            "notify": notes,
            "running": lambda: running or [],
            "heartbeat_age": hb,
        },
    )


def test_quiet_when_everything_is_fine() -> None:
    notes = Notes()
    result = wd.run(ctx(notes, seeded_runs()))
    assert result.status == "ok" and result.detail["problems"] == 0 and notes.sent == []


def test_finds_dead_heartbeat_missed_run_and_overdue_running() -> None:
    runs = seeded_runs()
    runs.records.pop("filings.corp_code:2026-10-06:1")  # 03:05 실행 기록 없음
    stuck = RunRecord(
        "ops.nightly:2026-10-06:2",
        "ops.nightly",
        "2026-10-06",
        2,
        "running",
        started_at=NOW - timedelta(hours=3),
    )

    def hb(service: str, now: datetime) -> float | None:
        return None if service == "auth" else 400.0 if service == "notifier" else 5.0

    notes = Notes()
    result = wd.run(ctx(notes, runs, running=[stuck], hb=hb))
    subjects = sorted(n["subject"] for n in notes.sent)
    assert subjects == [
        "heartbeat:auth",
        "heartbeat:notifier",
        "missed:filings.corp_code:2026-10-06",
        "overdue:ops.nightly:2026-10-06:2",
    ]
    assert all(n["kind"] == "ops.watchdog" for n in notes.sent)
    assert result.status == "ok" and result.detail["problems"] == 4


def test_holiday_has_no_missed_trading_day_runs() -> None:
    hol = datetime(2026, 10, 5, 12, 0, tzinfo=KST)  # 대체공휴일 — ops.watchdog 은 거래일만
    runs = MemoryRunLog()
    for job in ("ops.nightly", "filings.corp_code"):
        runs.record(RunRecord(f"{job}:2026-10-05:1", job, "2026-10-05", 1, "ok"))
    notes = Notes()
    problems = wd.find_problems(REG, runs, [], healthy, KR, US, hol)
    assert problems == [] and notes.sent == []


def test_notify_failure_fails_the_job() -> None:
    runs = seeded_runs()
    runs.records.pop("filings.corp_code:2026-10-06:1")
    result = wd.run(ctx(Notes(ok=False), runs))
    assert result.status == "failed" and "Redis 없음" in result.detail["reason"]
