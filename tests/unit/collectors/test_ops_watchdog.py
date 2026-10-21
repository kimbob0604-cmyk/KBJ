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
from kbj.store.db import StoreError

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
    # 2026-10-18 에 켠 매크로(06:20 아침 둘·09:10 월간·09:15 월간 로그인)
    for job, as_of in (
        ("macro.morning", "2026-10-06"),
        ("macro.morning_prv", "2026-10-06"),
        ("macro.monthly", "2026-10-06"),
        ("macro.monthly_prv", "2026-09"),
    ):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    # P6(웨이브 3 WRAP 이 켰다 — docs/p6_design.md D-P6-20): 매핑표 07:35. trade.derive 는
    # 14~20일만, trade.stock_index 는 21:00 이라 10:30 기준 끝났어야 할 실행이 없다
    runs.record(RunRecord("trade.map_load:2026-10-06:1", "trade.map_load", "2026-10-06", 1, "ok"))
    # P7(웨이브 3 WRAP 이 켰다 — docs/p7_design.md §1.7-1): diag.public 08:40, gex.alert 는 정규장
    # 매분(마지막 발화 하나). diag.daily 는 16:00 이라 10:30 기준 끝났어야 할 실행이 없다
    for job, as_of in (("diag.public", "2026-10-06"), ("gex.alert", "2026-10-06T10:30")):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    # P8(웨이브 3 WRAP 이 켰다 — docs/p8_design.md §1.6-1): rules.outcome 08:30·
    # rules.backtest_nightly 08:45(as_of 전 거래일 10-02 — 10-05 는 휴장). rules.close 는
    # 16:00 이라 10:30 기준 끝났어야 할 실행이 없다
    for job in ("rules.outcome", "rules.backtest_nightly"):
        runs.record(RunRecord(f"{job}:2026-10-02:1", job, "2026-10-02", 1, "ok"))
    # 2026-10-20 사용자 요청으로 켠 작업 — 10:30 기준 끝났어야 할 실행(VKOSPI·KRX 파생은 전 거래일
    # 10-02, GX 밤 분봉은 06:10 그날, 지수 일정은 when: always 06:41 — 10-05 휴장일에도 돈다)
    for job, as_of in (
        ("krx.vkospi", "2026-10-02"),
        ("gex.krx_derivatives", "2026-10-02"),
        ("gex.night_minutes", "2026-10-06"),
        ("index.calendar", "2026-10-06"),
        ("gex.open_check_day", "2026-10-06"),  # 08:49 개장 확인
    ):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    # P9 WRAP: 워치독 자신은 5분마다(마감 10분) — 10:30 기준 끝났어야 할 마지막 발화는 10:20
    runs.record(
        RunRecord("ops.watchdog:2026-10-06T10:20:1", "ops.watchdog", "2026-10-06T10:20", 1, "ok")
    )
    return runs


def ctx(
    notes: Notes,
    runs: MemoryRunLog,
    *,
    running: list[RunRecord] | None = None,
    hb: Any = healthy,
    extra: dict[str, Any] | None = None,
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
            **(extra or {}),
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


def test_watchdog_does_not_report_its_own_missed_slot() -> None:
    runs = seeded_runs()
    for key in [k for k, r in runs.records.items() if r.job == "ops.watchdog"]:
        runs.records.pop(key)  # 재기동 사이 워치독 5분 회차가 빠졌다
    notes = Notes()
    wd.run(ctx(notes, runs))
    assert not [n for n in notes.sent if n["subject"].startswith("missed:ops.watchdog")]


def test_holiday_has_no_missed_trading_day_runs() -> None:
    hol = datetime(2026, 10, 5, 12, 0, tzinfo=KST)  # 대체공휴일
    runs = MemoryRunLog()
    # public.export(P3)는 when: always — 휴장일에도 05:30 에 돈다(그 기록은 있어야 한다)
    # P6 trade.map_load(07:35)도 when: always
    for job in ("ops.nightly", "filings.corp_code", "public.export", "trade.map_load"):
        runs.record(RunRecord(f"{job}:2026-10-05:1", job, "2026-10-05", 1, "ok"))
    # P4 공시·재무 작업도 when: always — DART 는 거래소 휴장일에도 접수한다 [확인 필요 #28].
    # P9 WRAP: ops.watchdog 자신도 when: always(5분마다) — 휴장일에도 돈다
    for job, hhmm in (
        ("ops.watchdog", "11:50"),
        ("filings.dart_feed", "11:50"),
        ("earnings.alerts", "11:50"),
        ("fin.quarterly", "11:30"),
        ("filings.derive", "11:40"),
    ):
        as_of = f"2026-10-05T{hhmm}"
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    # 매크로 월간(when: always)은 휴장일에도 돈다 — 09:10 월간·09:15 월간 로그인(월 단위 as_of)
    for job, as_of in (
        ("macro.monthly", "2026-10-05"),
        ("macro.monthly_prv", "2026-09"),
        ("index.calendar", "2026-10-05"),  # 2026-10-20 켬 — when: always 06:41
    ):
        runs.record(RunRecord(f"{job}:{as_of}:1", job, as_of, 1, "ok"))
    notes = Notes()
    problems = wd.find_problems(REG, runs, [], healthy, KR, US, hol)
    assert problems == [] and notes.sent == []


def test_notify_failure_fails_the_job() -> None:
    runs = seeded_runs()
    runs.records.pop("filings.corp_code:2026-10-06:1")
    result = wd.run(ctx(Notes(ok=False), runs))
    assert result.status == "failed" and "Redis 없음" in result.detail["reason"]


class GapSink:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[Any] = []
        self.fail = fail

    def write_gaps(self, events: Any) -> int:
        if self.fail:
            raise StoreError("하트비트 끊김 기록 실패: OperationalError", cause="OperationalError")
        self.events.extend(events)
        return len(events)


def test_dead_heartbeats_are_recorded_as_gap_events() -> None:
    """P9 WRAP(ADR 0037 §3): 자원 ops_gaps 가 있으면 지금 끊긴 켜진 상시 서비스를 남긴다 —
    1주 판정 ①(끊김 >10분)의 입력. 기준은 등록부 heartbeat_s, 이름은 등록부 이름."""

    def hb(service: str, now: datetime) -> float | None:
        return None if service == "auth" else 400.0 if service == "notifier" else 5.0

    sink = GapSink()
    result = wd.run(ctx(Notes(), seeded_runs(), hb=hb, extra={"ops_gaps": sink}))
    assert result.status == "ok"
    assert sorted((e.service, e.age_s) for e in sink.events) == [
        ("auth", None),
        ("notifier", 400.0),
    ]
    assert {e.ts for e in sink.events} == {NOW}


def test_gap_write_failure_fails_the_job_after_notifying() -> None:
    def hb(service: str, now: datetime) -> float | None:
        return None if service == "auth" else 5.0

    notes = Notes()
    result = wd.run(ctx(notes, seeded_runs(), hb=hb, extra={"ops_gaps": GapSink(fail=True)}))
    assert [n["subject"] for n in notes.sent] == ["heartbeat:auth"]  # 알림은 그대로
    assert result.status == "failed" and "OperationalError" in result.detail["reason"]


def test_heartbeat_is_read_under_the_process_name() -> None:
    """P7 GEX 셋은 등록부 이름(gex.*)이 아니라 프로세스 이름(engine·ws-gateway·recorder)으로
    하트비트를 남긴다 — 워치독은 `heartbeat_key` 로 읽는다(아니면 늘 끊김으로 알린다)."""
    asked: list[str] = []

    def by_process_name(service: str, now: datetime) -> float | None:
        asked.append(service)
        return None if service.startswith("gex.") else 5.0

    notes = Notes()
    result = wd.run(ctx(notes, seeded_runs(), hb=by_process_name))
    assert result.status == "ok" and result.detail["problems"] == 0, notes.sent
    assert {"engine", "ws-gateway", "recorder"} <= set(asked)
    assert not [a for a in asked if a.startswith("gex.")]
