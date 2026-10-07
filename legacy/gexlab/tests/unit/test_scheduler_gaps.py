"""scheduler 무결측 판정 구동 (services/scheduler/gaps.py, docs/phase1_design.md §9).

가짜 시계·메모리 판정 저장소(tests/fakes/gap_inputs.py)·가짜 분봉 적재기. 날짜: 2026-09-28(월)
주간·그날 밤(09-29 귀속), 09-28 06:10(전날 밤 09-23 은 안 열림), 금요일 09-18 밤 → 월요일 09-21.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from pydantic import ValidationError

from core.calendar import TradingCalendar
from services.auth.health import MemoryHealthSink
from services.bus import MasterSnapshot
from services.gaps import SessionReport
from services.scheduler.gaps import GapDaily, GapDailyConfig, run_report, summarize
from services.scheduler.minute import (
    Attempt,
    MinuteConfig,
    MinuteDaily,
    SessionJob,
    Window,
    WindowRun,
)
from services.scheduler.service import Scheduler
from tests.fakes.gap_inputs import (
    D28,
    D29,
    MemoryGapStore,
    every,
    kst,
    minutes,
    perfect_day,
    perfect_night,
    ticks_for,
)

CAL = TradingCalendar.default()


def inline[T](fn: Callable[[], T]) -> Future[T]:
    fut: Future[T] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


class Loader:
    """분봉 적재기 흉내 — loaded 면 성공, partial 이면 호출 상한 창, missing 이면 실패."""

    def __init__(self, outcome: str = "loaded") -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, date]] = []

    def run(self, job: SessionJob, master: MasterSnapshot | None) -> Attempt:
        self.calls.append(job.key)
        if self.outcome == "missing":
            return Attempt(False, 1, "HTTP 500")
        runs: list[WindowRun] = []
        if self.outcome == "partial":
            w = Window(
                "A01612",
                "F",
                "day",
                job.start_date,
                "160000",
                job.start_date,
                kst(job.start_date, 8, 45),
                5,
            )
            runs.append(WindowRun(w, calls=5, earliest=kst(job.start_date, 9), outcome="capped"))
        job.runs = runs
        return Attempt(True, 1)


class Rig:
    def __init__(self, *, outcome: str | None = "loaded", **kw: Any) -> None:
        self.store = MemoryGapStore()
        self.store.inputs[(D28, "day")] = perfect_day()
        self.store.inputs[(D29, "night")] = perfect_night()
        self.health = MemoryHealthSink()
        self.minute: MinuteDaily | None = None
        if outcome is not None:
            self.loader = Loader(outcome)
            self.minute = MinuteDaily(
                self.loader,
                CAL,
                MemoryHealthSink(),
                config=MinuteConfig(max_attempts=1),
                submit=inline,
            )
        kw.setdefault("submit", inline)
        self.gaps = GapDaily(self.store, CAL, self.health, minute=self.minute, **kw)

    def walk(self, start: datetime, end: datetime, step_s: int = 30) -> None:
        t = start
        while t < end:
            if self.minute is not None:
                self.minute.step(t)
            self.gaps.step(t)
            t += timedelta(seconds=step_s)


def test_day_is_judged_once_after_the_minute_load() -> None:
    rig = Rig()
    rig.walk(kst(D28, 15, 45), kst(D28, 17, 50))
    assert rig.store.replaced == [(D28, "day")]
    (ev,) = rig.health.events
    assert (ev.kind, ev.severity) == ("nogap_session_ok", "info")
    assert "2026-09-28 주간 무결측" in ev.detail and ev.service == "scheduler"
    reports = rig.store.reports[(D28, "day")]
    assert {r.stream for r in reports} >= {
        "fut_board",
        "ws_connection",
        "fut_trades",
        "board:M:202610",
    }
    assert all(r.status == "ok" for r in reports) and rig.store.gaps[(D28, "day")] == []
    assert {r.evaluated_at for r in reports} == {kst(D28, 16)}
    trades = next(r for r in reports if r.stream == "fut_trades")
    assert trades.detail["minute_status"] == "loaded"


def test_gaps_are_written_and_warned_with_the_streams() -> None:
    rig = Rig()
    base = perfect_day()
    hole = (kst(D28, 13), kst(D28, 13, 5))
    rig.store.inputs[(D28, "day")] = replace(
        base,
        fut_board=[t for t in base.fut_board if not hole[0] <= t < hole[1]],
        ws_events=[(kst(D28, 11), "ws_disconnected"), (kst(D28, 11, 0, 4), "ws_connected")],
    )
    rig.walk(kst(D28, 16), kst(D28, 16, 1))
    (ev,) = rig.health.events
    assert (ev.kind, ev.severity) == ("collection_gaps_found", "warning")
    assert "fut_board(1)" in ev.detail and "ws_connection(1)" in ev.detail
    gaps = rig.store.gaps[(D28, "day")]
    assert sorted(g.stream for g in gaps) == ["fut_board", "ws_connection"]
    fb = next(g for g in gaps if g.stream == "fut_board")
    assert (fb.start_ts, fb.end_ts) == (kst(D28, 12, 59, 30), kst(D28, 13, 5))


def test_the_night_is_judged_at_0610_after_its_minute_load() -> None:
    rig = Rig()
    rig.walk(kst(D29, 6), kst(D29, 8, 0))
    assert rig.store.replaced == [(D29, "night")]
    assert rig.loader.calls == [("night", D28)]
    assert (
        rig.health.kinds() == ["nogap_session_ok"]
        and "2026-09-29 야간" in rig.health.events[0].detail
    )


def test_closed_nights_and_holidays_are_not_judged() -> None:
    rig = Rig()
    rig.walk(kst(D28, 6, 5), kst(D28, 8))  # 09-28 로 귀속되는 밤(09-23 시작)은 없다
    rig.walk(kst(date(2026, 9, 24), 15, 50), kst(date(2026, 9, 24), 18))  # 추석
    assert rig.store.replaced == [] and rig.health.events == []


def test_friday_night_is_judged_on_monday_morning() -> None:
    fri, mon = date(2026, 9, 18), date(2026, 9, 21)
    a, b = kst(fri, 18), kst(date(2026, 9, 19), 6)
    bars = [("A01612", t, 1) for t in minutes(a, b)]
    rig = Rig()
    rig.store.inputs[(mon, "night")] = replace(
        perfect_night(),
        series_dates=[
            ("WKI", "260924", "kis", date(2026, 9, 23), kst(fri, 17, 51)),
            ("", "202610", "kis", date(2026, 10, 8), kst(fri, 17, 51)),
        ],
        fut_board=every(a, b, 30),
        fill1=[("WKI:260924", t) for t in every(a, b, 30)]
        + [("M:202610", t) for t in every(a, b, 30)],
        bars=bars,
        tick_minutes=ticks_for(bars),
    )
    rig.walk(kst(date(2026, 9, 19), 6), kst(date(2026, 9, 19), 8))  # 토요일 — 없음
    assert rig.store.replaced == []
    rig.walk(kst(mon, 6, 10), kst(mon, 6, 11))
    assert rig.store.replaced == [(mon, "night")] and rig.loader.calls == [("night", fri)]
    assert rig.health.kinds() == ["nogap_session_ok"]


def test_it_waits_for_the_minute_load_then_falls_back() -> None:
    rig = Rig(outcome="missing")  # 적재 실패 → missing 으로 끝남 (max_attempts=1)
    rig.walk(kst(D28, 16), kst(D28, 16, 1))
    trades = next(r for r in rig.store.reports[(D28, "day")] if r.stream == "fut_trades")
    assert trades.status == "unverified" and trades.detail["minute_status"] == "missing"
    assert rig.health.kinds() == ["nogap_unverified"]
    # 적재기가 끝나지 않으면(작업 중) fallback 17:20 까지 기다렸다가 partial 로 본다
    never = Rig()
    assert never.minute is not None
    never.minute.step = lambda now, master=None: None  # type: ignore[method-assign]
    never.walk(kst(D28, 16), kst(D28, 17, 20))
    assert never.store.replaced == []
    never.walk(kst(D28, 17, 20), kst(D28, 17, 21))
    trades = next(r for r in never.store.reports[(D28, "day")] if r.stream == "fut_trades")
    assert trades.detail["minute_status"] == "missing"  # 적재 일을 만들지도 못했다


def test_a_partial_minute_load_leaves_trades_unverified() -> None:
    rig = Rig(outcome="partial")
    rig.walk(kst(D28, 16), kst(D28, 16, 1))
    trades = next(r for r in rig.store.reports[(D28, "day")] if r.stream == "fut_trades")
    assert trades.status == "unverified" and "partial" in trades.detail["reason"]
    assert "fut_trades: 분봉 적재 partial" in rig.health.of("nogap_unverified")[0].detail


def test_without_a_minute_loader_it_judges_at_once_from_stored_bars() -> None:
    rig = Rig(outcome=None)
    rig.walk(kst(D28, 16), kst(D28, 16, 1))
    trades = next(r for r in rig.store.reports[(D28, "day")] if r.stream == "fut_trades")
    assert trades.status == "ok" and trades.detail["minute_status"] is None


def test_failures_retry_every_five_minutes_until_the_window_ends() -> None:
    rig = Rig()
    rig.store.fail_reads = 2
    rig.walk(kst(D28, 16), kst(D28, 16, 11))
    assert rig.health.kinds() == ["gap_report_failed", "gap_report_failed", "nogap_session_ok"]
    assert (
        "OperationalError" in rig.health.events[0].detail
        and "5분 뒤 다시" in rig.health.events[0].detail
    )
    stuck = Rig()
    stuck.store.fail_writes = 10_000
    stuck.walk(kst(D28, 16), kst(D28, 17, 50), step_s=60)
    kinds = stuck.health.kinds()
    assert kinds[-1] == "gap_report_missing" and kinds.count("gap_report_failed") == 21
    assert stuck.gaps.jobs[-1].attempts == 22


def test_the_report_runs_in_a_worker_and_step_does_not_wait() -> None:
    held: list[tuple[Callable[[], SessionReport], Future[SessionReport]]] = []

    def later(fn: Callable[[], SessionReport]) -> Future[SessionReport]:
        fut: Future[SessionReport] = Future()
        held.append((fn, fut))
        return fut

    rig = Rig(submit=later)
    rig.walk(kst(D28, 16), kst(D28, 16, 5))
    assert len(held) == 1 and rig.store.replaced == []
    fn, fut = held[0]
    fut.set_result(fn())
    rig.gaps.step(kst(D28, 16, 5))
    assert rig.store.replaced == [(D28, "day")] and rig.health.kinds() == ["nogap_session_ok"]


def test_stream_reports_are_structured_logs(caplog: pytest.LogCaptureFixture) -> None:
    import json

    rig = Rig()
    with caplog.at_level(logging.INFO, logger="services.scheduler.gaps"):
        rig.walk(kst(D28, 16), kst(D28, 16, 1))
    recs = [json.loads(r.getMessage()) for r in caplog.records]
    fb = next(r for r in recs if r["event"] == "gap_stream_report" and r["stream"] == "fut_board")
    assert (fb["trade_date"], fb["session"], fb["service"]) == ("2026-09-28", "day", "scheduler")
    assert fb["expected"] == 840 and fb["received"] == 840 and fb["gaps"] == 0
    assert fb["status"] == "ok" and fb["required"] is True


def test_run_report_can_judge_without_writing() -> None:
    store = MemoryGapStore()
    store.inputs[(D28, "day")] = perfect_day()
    rep = run_report(store, CAL, D28, "day", kst(D28, 16), write=False)
    assert rep.clean and store.replaced == []
    with pytest.raises(ValueError, match="열리지 않은"):
        run_report(store, CAL, date(2026, 9, 24), "day", kst(D28, 16))
    assert summarize(rep)[0] == "nogap_session_ok"


def test_config_is_validated() -> None:
    from datetime import time

    with pytest.raises(ValidationError):
        GapDailyConfig(day_fallback=time(18, 0))
    with pytest.raises(ValidationError):
        GapDailyConfig(night_fallback=time(6, 0))


def test_scheduler_runs_the_report_and_isolates_its_errors() -> None:
    class Store:
        def write_session_log(self, rows: Any) -> None:
            pass

        def write_master(self, rows: Any, **kw: Any) -> None:
            pass

        def flush_spool(self) -> bool:
            return True

    class Boom:
        def step(self, now: datetime) -> None:
            raise RuntimeError("bug")

    now = [kst(D28, 16, 0, 1)]
    health = MemoryHealthSink()
    gap_store = MemoryGapStore()
    gap_store.inputs[(D28, "day")] = perfect_day()

    def download() -> bytes:
        raise OSError("no master in this test")

    sched = Scheduler(
        fakeredis.FakeRedis(),
        Store(),
        CAL,
        downloader=download,
        health=health,
        now=lambda: now[0],
        submit=lambda fn: inline(fn),  # type: ignore[arg-type, return-value]
        gaps=GapDaily(gap_store, CAL, health, submit=inline),
    )
    sched.step()
    assert "nogap_session_ok" in health.kinds() and gap_store.replaced == [(D28, "day")]
    sched.gaps = Boom()  # type: ignore[assignment]
    now[0] += timedelta(seconds=1)
    sched.step()
    assert "gap_step_failed" in health.kinds()
