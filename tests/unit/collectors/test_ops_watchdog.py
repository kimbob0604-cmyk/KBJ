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
    # P3(묶음 S 가 켰다 — docs/p3_design.md §0.4): 10:30 전에 끝났어야 할 켜진 cron 작업.
    # 10-05 대체공휴일이라 전 거래일은 10-02
    for job, as_of in (
        ("public.export", "2026-10-06"),
        ("etf.collect", "2026-10-06"),
        ("krx.daily", "2026-10-02"),
        ("board.confirm", "2026-10-02"),
    ):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    # P4(묶음 S 가 켰다 — docs/p4_design.md §0.4): 10:30 기준 마지막으로 끝났어야 할 실행. 분 단위
    # 작업(as_of: minute)은 마지막 발화 하나만 본다(워치독 규칙 그대로)
    for job, as_of in (
        ("earnings.backfill", "2026-10-06"),  # 06:30 거래일
        ("fin.valuation_band", "2026-10-02"),  # 08:50 — 전 거래일
        ("filings.dart_feed", "2026-10-06T10:20"),
        ("earnings.alerts", "2026-10-06T10:20"),
        ("fin.quarterly", "2026-10-06T10:00"),
        ("filings.derive", "2026-10-06T10:10"),
    ):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    # P5(웨이브 3 WRAP 이 켰다 — docs/p5_design.md D-P5-23): 10:30 전에 끝났어야 할 켜진 작업.
    # us.eod 는 뉴욕 날 기준(10-05 월 16:10 ET = 10-06 05:10 KST)
    for job, as_of in (
        ("us.eod", "2026-10-05"),
        ("brief.morning", "2026-10-06"),  # 08:10
        ("etf.report", "2026-10-06"),  # 08:20
    ):
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
    # public.export(P3)는 when: always — 휴장일에도 05:30 에 돈다(그 기록은 있어야 한다)
    for job in ("ops.nightly", "filings.corp_code", "public.export"):
        runs.record(RunRecord(f"{job}:2026-10-05:1", job, "2026-10-05", 1, "ok"))
    # P4 공시·재무 작업도 when: always — DART 는 거래소 휴장일에도 접수한다 [확인 필요 #28]
    for job, hhmm in (
        ("filings.dart_feed", "11:50"),
        ("earnings.alerts", "11:50"),
        ("fin.quarterly", "11:30"),
        ("filings.derive", "11:40"),
    ):
        as_of = f"2026-10-05T{hhmm}"
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    notes = Notes()
    problems = wd.find_problems(REG, runs, [], healthy, KR, US, hol)
    assert problems == [] and notes.sent == []


def test_notify_failure_fails_the_job() -> None:
    runs = seeded_runs()
    runs.records.pop("filings.corp_code:2026-10-06:1")
    result = wd.run(ctx(Notes(ok=False), runs))
    assert result.status == "failed" and "Redis 없음" in result.detail["reason"]
