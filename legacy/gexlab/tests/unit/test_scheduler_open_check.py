"""scheduler 개장 이중 확인 (services/scheduler/open_check.py, docs/phase1_design.md §6).

가짜 시계·메모리 저장소. 날짜: 2026-09-28(월) 주간·그날 밤(09-29 귀속), 09-23(수 — 다음 날 추석,
그날 밤 없음), 09-24(목 추석 휴장).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis

from core.calendar import TradingCalendar
from data.store import SessionLogRecord, StoreError
from services.auth.health import MemoryHealthSink
from services.scheduler.open_check import OpenCheck, OpenCheckConfig, open_sessions
from services.scheduler.service import Scheduler

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
D28, D29 = date(2026, 9, 28), date(2026, 9, 29)


def kst(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


class Counts:
    """fut_tick_count 흉내 — 구간마다 돌려줄 수(없으면 0), 부른 구간을 기록한다."""

    def __init__(self, by_start: dict[datetime, int] | None = None) -> None:
        self.by_start = by_start or {}
        self.calls: list[tuple[datetime, datetime]] = []
        self.fail: list[Exception] = []

    def fut_tick_count(self, start: datetime, end: datetime) -> int:
        self.calls.append((start, end))
        if self.fail:
            raise self.fail.pop(0)
        return self.by_start.get(start, 0)


class Log:
    def __init__(self) -> None:
        self.rows: list[SessionLogRecord] = []
        self.fail = False

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        if self.fail:
            raise StoreError("session_log: 접속 실패")
        self.rows.extend(rows)


def inline(fn: Callable[[], int]) -> Future[int]:
    fut: Future[int] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


class Deferred:
    """부른 일을 붙잡아 두는 submit — 끝내기 전엔 done() 이 거짓(느린 DB 흉내)."""

    def __init__(self) -> None:
        self.pending: list[tuple[Callable[[], int], Future[int]]] = []

    def __call__(self, fn: Callable[[], int]) -> Future[int]:
        fut: Future[int] = Future()
        self.pending.append((fn, fut))
        return fut

    def finish(self) -> None:
        for fn, fut in self.pending:
            fut.set_result(fn())
        self.pending.clear()


def rig(counts: Counts | None = None, **kw: Any) -> tuple[OpenCheck, Counts, Log, MemoryHealthSink]:
    c, lg, h = counts or Counts(), Log(), MemoryHealthSink()
    kw.setdefault("submit", inline)
    return OpenCheck(c, lg, CAL, h, **kw), c, lg, h


def walk(chk: OpenCheck, start: datetime, end: datetime, step_s: int = 1) -> None:
    t = start
    while t < end:
        chk.step(t)
        t += timedelta(seconds=step_s)


def test_open_sessions_follow_the_calendar() -> None:
    got = [(s.session, s.trade_date, s.opens_at) for s in open_sessions(D28, CAL)]
    assert got == [("day", D28, kst(D28, 8, 45)), ("night", D29, kst(D28, 18))]
    assert [s.session for s in open_sessions(date(2026, 9, 23), CAL)] == [
        "day"
    ]  # 휴장 전날 밤 없음
    assert open_sessions(date(2026, 9, 24), CAL) == []  # 추석
    fri = open_sessions(date(2026, 9, 18), CAL)
    assert [(s.session, s.trade_date) for s in fri] == [
        ("day", date(2026, 9, 18)),
        ("night", date(2026, 9, 21)),  # 금요일 밤 → 월요일 귀속
    ]


def test_no_trades_in_the_first_three_minutes_is_a_calendar_mismatch() -> None:
    chk, counts, lg, h = rig()
    walk(chk, kst(D28, 8, 40), kst(D28, 8, 48, 15))
    assert counts.calls == []  # 08:48:15(3분 + 여유 15초) 전엔 보지 않는다
    walk(chk, kst(D28, 8, 48, 15), kst(D28, 9, 0))
    assert counts.calls == [(kst(D28, 8, 45), kst(D28, 8, 48))]  # 한 번만
    (ev,) = h.of("calendar_mismatch")
    assert (
        ev.severity == "warning" and ev.service == "scheduler" and "주간 09-28 08:45" in ev.detail
    )
    (row,) = lg.rows
    assert (row.kind, row.trade_date, row.session, row.ts) == (
        "calendar_mismatch",
        D28,
        "day",
        kst(D28, 8, 48),
    )
    assert row.detail["calendar"] == "open" and row.detail["observed"] == "no_futures_trades"
    assert row.detail["window_start"] == kst(D28, 8, 45).isoformat()


def test_trades_in_the_window_leave_only_a_log() -> None:
    chk, counts, lg, h = rig(Counts({kst(D28, 8, 45): 12, kst(D28, 18): 3}))
    walk(chk, kst(D28, 8, 48), kst(D28, 8, 50))
    walk(chk, kst(D28, 18, 2), kst(D28, 18, 5))
    assert [c[0] for c in counts.calls] == [kst(D28, 8, 45), kst(D28, 18)]
    assert chk.results == {("day", D28): 12, ("night", D28): 3}
    assert h.events == [] and lg.rows == []


def test_night_check_is_tagged_with_the_next_trading_day() -> None:
    chk, counts, lg, h = rig()
    walk(chk, kst(D28, 18, 3), kst(D28, 18, 4))
    assert counts.calls == [(kst(D28, 18), kst(D28, 18, 3))]
    (row,) = lg.rows
    assert (row.trade_date, row.session, row.ts) == (D29, "night", kst(D28, 18, 3))
    assert "야간 09-28 18:00" in h.of("calendar_mismatch")[0].detail


def test_closed_days_and_nights_are_not_checked() -> None:
    chk, counts, _, h = rig()
    walk(chk, kst(date(2026, 9, 23), 17, 55), kst(date(2026, 9, 23), 19), step_s=10)  # 밤 없음
    walk(chk, kst(date(2026, 9, 24), 8, 40), kst(date(2026, 9, 24), 9), step_s=10)  # 추석
    assert counts.calls == [] and h.events == []


def test_a_late_start_still_checks_the_open_window_once() -> None:
    chk, counts, lg, _ = rig(Counts({kst(D28, 8, 45): 5}))
    walk(chk, kst(D28, 13, 0), kst(D28, 13, 1))
    assert counts.calls == [(kst(D28, 8, 45), kst(D28, 8, 48))] and lg.rows == []
    after = rig()[0]
    walk(after, kst(D28, 15, 45), kst(D28, 15, 46))  # 세션이 끝났다 — 보지 않는다
    assert after.done == set()


def test_read_failures_retry_until_the_session_ends() -> None:
    counts = Counts()
    counts.fail = [StoreError("fut_ticks: OperationalError"), StoreError("again")]
    chk, _, lg, h = rig(counts, config=OpenCheckConfig(retry_s=30, fail_every_s=300))
    walk(chk, kst(D28, 8, 48, 15), kst(D28, 8, 49, 30))
    assert len(counts.calls) == 3  # 08:48:15 실패, 08:48:45 실패, 08:49:15 성공(0건)
    fails = h.of("open_check_failed")
    assert len(fails) == 1 and "30초 뒤 다시" in fails[0].detail  # 5분에 한 번만
    assert len(h.of("calendar_mismatch")) == 1 and len(lg.rows) == 1
    # 세션 끝까지 못 읽으면 그만
    stuck = Counts()
    stuck.fail = [StoreError("down")] * 10_000
    chk2, _, _, _ = rig(stuck, config=OpenCheckConfig(retry_s=600))
    walk(chk2, kst(D28, 8, 48, 15), kst(D28, 16, 0), step_s=30)
    assert len(stuck.calls) == 42 and chk2.done == set()  # 10분마다, 15:45 전까지


def test_a_session_log_failure_still_warns() -> None:
    chk, _, lg, h = rig()
    lg.fail = True
    walk(chk, kst(D28, 8, 48, 15), kst(D28, 8, 48, 16))
    assert h.kinds() == ["session_log_failed", "calendar_mismatch"]


def test_a_slow_read_does_not_block_and_is_applied_when_done() -> None:
    submit = Deferred()
    chk, counts, lg, _ = rig(submit=submit)
    walk(chk, kst(D28, 8, 48, 15), kst(D28, 8, 48, 45))
    assert len(submit.pending) == 1 and counts.calls == []  # 한 번만 넘겼고 기다리지 않는다
    submit.finish()
    chk.step(kst(D28, 8, 48, 46))
    assert chk.done == {("day", D28)} and len(lg.rows) == 1


def test_scheduler_runs_the_check_and_isolates_its_errors() -> None:
    class Store(Log):
        def write_master(self, rows: Any, **kw: Any) -> None:
            pass

        def flush_spool(self) -> bool:
            return True

    class Boom:
        def step(self, now: datetime) -> None:
            raise RuntimeError("bug")

    now = [kst(D28, 8, 48, 20)]
    health = MemoryHealthSink()
    store = Store()
    chk = OpenCheck(Counts(), store, CAL, health, submit=inline)

    def download() -> bytes:
        raise OSError("no master in this test")

    sched = Scheduler(
        fakeredis.FakeRedis(),
        store,
        CAL,
        downloader=download,
        health=health,
        now=lambda: now[0],
        submit=lambda fn: inline(fn),  # type: ignore[arg-type, return-value]
        open_check=chk,
    )
    sched.step()
    assert "calendar_mismatch" in health.kinds()
    assert [r.kind for r in store.rows] == ["transition", "calendar_mismatch"]
    sched.open_check = Boom()  # type: ignore[assignment]
    now[0] += timedelta(seconds=1)
    sched.step()
    assert "open_check_step_failed" in health.kinds()
    assert sched.state is not None  # 상태 루프는 계속
