"""kbj/services/scheduler/runner.py — 가짜 시계로 실행기 규칙을 고정한다(설계 §6.1·§6.5·§6.9).

재시도 간격·until·마감, 굳은/무른 의존, 캐치업, 같은 (작업, as_of) 두 번 안 돎, 계획됨·외부 작업,
선점 중복, 지연 개장, 상태 진입, run-once, 그리고 실제 등록부로 하루(2026-10-06)를 돌려 P2 에 켜진
작업만 돌고 나머지는 계획됨으로만 남는지.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from kbj.config.settings import Settings
from kbj.core.calendar import State, TradingCalendar, us_calendar
from kbj.core.time import KST
from kbj.data.catalog import all_datasets
from kbj.data.spec import DataKey
from kbj.services.runtime.health import MemoryHealthSink
from kbj.services.scheduler.claims import MemoryClaimStore, MemoryRunLog, RunRecord
from kbj.services.scheduler.conditions import AsOfUnavailable
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.services.scheduler.registry import Registry
from kbj.services.scheduler.runner import JobRunner, RunEvent, inline_submit

KR = TradingCalendar.default()
US = us_calendar()
CAT = all_datasets()
JOBS = Path(__file__).resolve().parents[3] / "config" / "jobs.yaml"


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


class Notes:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def __call__(self, text: str, **kw: Any) -> Any:
        self.sent.append({"text": text, **kw})
        return None


class Rig:
    def __init__(
        self,
        jobs: list[dict[str, Any]],
        handlers: dict[str, Callable[[JobContext], JobResult]],
        start: datetime,
        *,
        kr: TradingCalendar = KR,
        submit: Any = inline_submit,
        registry: Registry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.registry = registry or Registry.parse({"version": 1, "jobs": jobs})
        self.claims = MemoryClaimStore()
        self.runs = MemoryRunLog()
        self.health = MemoryHealthSink()
        self.notes = Notes()
        self.now = start.astimezone(UTC)
        self.events: list[RunEvent] = []
        self.runner = JobRunner(
            self.registry,
            handlers,
            self.claims,
            kr,
            US,
            runs=self.runs,
            catalog=CAT,
            health=self.health,
            submit=submit,
            notify=self.notes,
            settings=settings,
        )

    def tick(self) -> list[RunEvent]:
        got = self.runner.tick(self.now)
        self.events += got
        return got

    def run_until(self, end: datetime, every: timedelta = timedelta(minutes=1)) -> None:
        end = end.astimezone(UTC)
        while self.now <= end:
            self.tick()
            self.now += every

    def kinds(self, job: str) -> list[str]:
        return [e.kind for e in self.events if e.job == job]

    def times(self, job: str, kind: str) -> list[datetime]:
        return [e.at.astimezone(KST) for e in self.events if e.job == job and e.kind == kind]


def krx_like(**over: Any) -> dict[str, Any]:
    j: dict[str, Any] = {
        "name": "krx.daily",
        "phase": "P3",
        "owner": "tests.fake:krx",
        "schedule": {"cron": "5 8 * * 1-5", "when": "trading_day"},
        "retry": {"every_s": 600, "until": "10:00"},
        "collects": [
            {"source": "KRX", "dataset": "sto/stk_bydd_trd", "as_of": "prev_trading_day"},
            {"source": "KRX", "dataset": "idx/kospi_dd_trd", "as_of": "prev_trading_day"},
        ],
        "budget": "krx",
        "writes": ["prv_market.daily_bar"],
    }
    j.update(over)
    return j


class Publisher:
    """`ready_at` 전에는 not_ready, 뒤에는 받은 키를 다 돌려준다."""

    def __init__(self, ready_at: datetime | None) -> None:
        self.ready_at = ready_at
        self.calls: list[JobContext] = []

    def __call__(self, ctx: JobContext) -> JobResult:
        self.calls.append(ctx)
        if self.ready_at is None or ctx.now < self.ready_at:
            return JobResult("not_ready")
        return JobResult("ok", collected=ctx.keys, rows=10)


# ── 재시도·마감 ───────────────────────────────────────────────────────────────────────────


def test_every_s_retry_until_published() -> None:
    pub = Publisher(kst(2026, 10, 6, 9, 0))
    rig = Rig([krx_like()], {"tests.fake:krx": pub}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 10, 30))
    starts = rig.times("krx.daily", "started")
    assert starts[0] == kst(2026, 10, 6, 8, 5)
    assert [b - a for a, b in itertools.pairwise(starts)] == [timedelta(minutes=10)] * 6
    assert starts[-1] == kst(2026, 10, 6, 9, 5) and rig.kinds("krx.daily")[-1] == "ok"
    # 같은 선점을 이어 썼다 — 키는 10-02(금, 10-05 대체공휴일 앞)
    assert sorted(k.as_of for k in rig.claims.done_keys()) == ["2026-10-02", "2026-10-02"]
    assert {k.venue for k in rig.claims.done_keys()} == {"KRX", ""}
    assert all(c.keys == pub.calls[0].keys for c in pub.calls)
    last = rig.runs.latest("krx.daily", "2026-10-02")
    assert last is not None and (last.status, last.attempt) == ("ok", 7)
    assert rig.notes.sent == []


def test_until_reached_fails_once_and_releases_keys() -> None:
    rig = Rig([krx_like()], {"tests.fake:krx": Publisher(None)}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 11, 0))
    assert len(rig.times("krx.daily", "started")) == 12  # 08:05 … 09:55
    assert rig.kinds("krx.daily")[-1] == "failed"
    assert [n["kind"] for n in rig.notes.sent] == ["ops.job_failed"]
    # 실행 as_of 는 수집 키의 as_of(전 거래일 — 10-05 대체공휴일 앞 금요일)
    assert rig.notes.sent[0]["subject"] == "krx.daily:2026-10-02"
    assert rig.notes.sent[0]["as_of"] == date(2026, 10, 2)
    assert rig.claims.done_keys() == [] and all(r.status == "failed" for r in rig.claims.rows)
    assert "job_failed" in rig.health.kinds()


def test_backoff_max_then_fail() -> None:
    job = krx_like(retry={"max": 2, "backoff_s": [60, 300]})
    rig = Rig([job], {"tests.fake:krx": Publisher(None)}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 9, 0))
    assert rig.times("krx.daily", "started") == [
        kst(2026, 10, 6, 8, 5),
        kst(2026, 10, 6, 8, 6),
        kst(2026, 10, 6, 8, 11),
    ]
    assert rig.kinds("krx.daily")[-1] == "failed"


def test_handler_exception_is_failure_with_masked_reason() -> None:
    def boom(ctx: JobContext) -> JobResult:
        raise RuntimeError("401 crtfc_key=0123456789abcdef0123456789abcdef01234567")

    rig = Rig([krx_like(retry={"max": 0})], {"tests.fake:krx": boom}, kst(2026, 10, 6, 8, 5))
    rig.tick()
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "failed"
    assert "0123456789abcdef0123456789abcdef01234567" not in str(rec.detail)
    assert "0123456789abcdef0123456789abcdef01234567" not in rig.notes.sent[0]["text"]


def test_running_past_deadline_is_timeout_and_late_result_dropped() -> None:
    pending: list[Future[JobResult]] = []

    def never(fn: Callable[[], JobResult]) -> Future[JobResult]:
        fut: Future[JobResult] = Future()
        pending.append(fut)
        return fut

    job = krx_like(deadline_min=30, retry={"max": 0})
    rig = Rig([job], {"tests.fake:krx": Publisher(None)}, kst(2026, 10, 6, 8, 0), submit=never)
    rig.run_until(kst(2026, 10, 6, 8, 40))
    assert rig.kinds("krx.daily") == ["started", "timeout"]
    assert rig.times("krx.daily", "timeout") == [kst(2026, 10, 6, 8, 36)]
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "timeout"
    pending[0].set_result(JobResult("ok"))
    rig.tick()  # 늦은 결과는 버린다(기록이 ok 로 바뀌지 않는다)
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "timeout"
    assert all(r.status == "failed" for r in rig.claims.rows)


def _held_first() -> tuple[Any, list[tuple[Callable[[], JobResult], Future[JobResult]]]]:
    """첫 제출(마감 수집)만 붙잡아 두고 나머지는 그 자리에서 — 잠든 동안 돌고 있던 시도."""
    held: list[tuple[Callable[[], JobResult], Future[JobResult]]] = []

    def submit(fn: Callable[[], JobResult]) -> Future[JobResult]:
        if held:
            return inline_submit(fn)
        fut: Future[JobResult] = Future()
        held.append((fn, fut))
        return fut

    return submit, held


def _slept_close_day(settings: Settings | None) -> Rig:
    # 2026-10-22 재현: 15:35 마감 수집이 도는 중 16:19 덮개를 닫아 잠들고 17:58 에 깼다
    ok = lambda ctx: JobResult("ok", collected=ctx.keys, rows=1)  # noqa: E731
    submit, held = _held_first()
    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": ok, "tests.fake:brief": ok},
        kst(2026, 10, 6, 15, 30),
        submit=submit,
        settings=settings,
    )
    rig.run_until(kst(2026, 10, 6, 16, 19))
    assert rig.kinds("brief.closing") == ["waiting"]
    rig.now = kst(2026, 10, 6, 17, 58).astimezone(UTC)
    rig.tick()
    fn, fut = held[0]
    fut.set_result(fn())  # 깬 뒤 마저 끝난다
    rig.run_until(kst(2026, 10, 6, 18, 5))
    return rig


def test_host_sleep_does_not_count_toward_the_deadline() -> None:
    rig = _slept_close_day(Settings.model_construct(scheduler_catch_up_h=20))
    assert rig.kinds("market.close_collect") == ["started", "ok"]
    # 깬 뒤 SLEEP_SETTLE_S(60초)를 기다려 의존 작업이 돈다
    assert rig.times("brief.closing", "started") == [kst(2026, 10, 6, 17, 59)]
    assert rig.kinds("brief.closing")[-1] == "ok"

    off = _slept_close_day(None)  # 꺼 둔 기본값(VM) — 벽시계 마감(17:35)이 지나 잘린다
    assert off.kinds("market.close_collect") == ["started", "timeout"]
    assert (
        off.kinds("brief.closing")[-1] == "skipped"
    )  # 굳은 의존 timeout 에 막힘(실패는 원인 하나)


def test_overnight_sleep_catches_up_once_instead_of_failing_every_missed_fire() -> None:
    """맥이 밤새 잠들었다 깨면(22:05 → 06:00) 잠든 동안의 5분 발화(워치독 약 95건)·03:00 백업을
    몰아 띄워 '마감을 넘겼다(시작하지 못함)' 실패·알림이 쌓였다 — 켜질 때 따라잡기처럼 작업마다 가장
    최근 회차만 돈다(2026-10-27, docs/ops_error_catalog.md)."""
    ok = lambda ctx: JobResult("ok", collected=ctx.keys)  # noqa: E731
    jobs = [
        {
            "name": "ops.watchdog",
            "phase": "P2",
            "enabled": True,
            "owner": "tests.fake:watch",
            "schedule": {"cron": "*/5 * * * *", "when": "always"},
            "retry": {"max": 0},
            "deadline_min": 10,
            "as_of": "minute",
        },
        {
            "name": "ops.nightly",
            "phase": "P2",
            "enabled": True,
            "owner": "tests.fake:nightly",
            "schedule": {"cron": "0 3 * * *", "when": "always"},
            "retry": {"max": 1, "backoff_s": [600]},
        },
    ]
    rig = Rig(
        jobs,
        {"tests.fake:watch": ok, "tests.fake:nightly": ok},
        kst(2026, 10, 6, 22, 0),
        settings=Settings.model_construct(scheduler_catch_up_h=20),
    )
    rig.run_until(kst(2026, 10, 6, 22, 5))
    rig.now = kst(2026, 10, 7, 6, 0).astimezone(UTC)  # 깸
    rig.run_until(kst(2026, 10, 7, 6, 10))
    assert "failed" not in rig.kinds("ops.watchdog") + rig.kinds("ops.nightly")
    assert rig.notes.sent == []
    woke = [e for e in rig.events if e.kind == "started" and e.at >= kst(2026, 10, 7, 6, 0)]
    # 워치독: 깬 분(06:00)부터 정시 회차만 — 잠든 동안의 약 95회차를 띄우지 않는다
    assert sorted(e.as_of for e in woke if e.job == "ops.watchdog") == [
        "2026-10-07T06:00",
        "2026-10-07T06:05",
        "2026-10-07T06:10",
    ]
    nightly = rig.runs.latest("ops.nightly", "2026-10-07")
    assert nightly is not None and nightly.status == "ok"  # 03:00 백업은 깬 뒤 한 번


def test_host_sleep_keeps_short_deadline_jobs_on_the_wall_clock() -> None:
    # 장중 슬롯처럼 마감이 짧은 작업(60분 미만)은 미루지 않는다 — 늦게 돌면 지난 회차를 지금 값으로
    # 채운다
    pending: list[Future[JobResult]] = []

    def never(fn: Callable[[], JobResult]) -> Future[JobResult]:
        fut: Future[JobResult] = Future()
        pending.append(fut)
        return fut

    job = krx_like(deadline_min=30, retry={"max": 0})
    on = Settings.model_construct(scheduler_catch_up_h=20)
    rig = Rig([job], {"tests.fake:krx": Publisher(None)}, kst(2026, 10, 6, 8, 0), submit=never,
              settings=on)  # fmt: skip
    rig.run_until(kst(2026, 10, 6, 8, 10))
    rig.now = kst(2026, 10, 6, 9, 0).astimezone(UTC)
    rig.tick()
    assert rig.kinds("krx.daily") == ["started", "timeout"]


def test_retry_does_not_start_in_the_first_seconds_after_waking() -> None:
    # 맥은 잠든 중에도 몇 초씩 깨는데(DarkWake) 그때는 네트워크가 없다 — 깬 뒤 60초는 새 시도를
    # 띄우지 않는다
    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": Publisher(None), "tests.fake:brief": Publisher(None)},
        kst(2026, 10, 6, 15, 34),
        settings=Settings.model_construct(scheduler_catch_up_h=20),
    )
    rig.run_until(kst(2026, 10, 6, 15, 36))
    assert rig.kinds("market.close_collect") == ["started", "retry"]
    rig.now = kst(2026, 10, 6, 16, 30).astimezone(UTC)  # 잠들었다 몇 초 깸
    rig.tick()
    rig.now += timedelta(seconds=20)
    rig.tick()
    assert rig.kinds("market.close_collect") == ["started", "retry"]
    rig.now += timedelta(seconds=45)
    rig.tick()
    assert rig.kinds("market.close_collect")[2] == "started"


# ── 의존·캐치업 ───────────────────────────────────────────────────────────────────────────


def close_and_brief(close_retry: Any = None, **brief: Any) -> list[dict[str, Any]]:
    close = {
        "name": "market.close_collect",
        "phase": "P3",
        "owner": "tests.fake:close",
        "schedule": {"equity": {"start": "close+5"}, "when": "trading_day"},
        "retry": close_retry or {"max": 5, "backoff_s": [300]},
        "collects": [{"source": "KIS", "dataset": "stock_quote_eod", "as_of": "trade_date"}],
        "writes": ["prv_market.stock_snapshot"],
    }
    b = {
        "name": "brief.closing",
        "phase": "P3",
        "owner": "tests.fake:brief",
        "schedule": {"cron": "0 16 * * 1-5", "when": "trading_day", "catch_up_until": "20:30"},
        "retry": {"max": 0},
        "depends_on": [{"job": "market.close_collect", "hard": True}],
    }
    b.update(brief)
    return [close, b]


def test_hard_dependency_waits_then_runs() -> None:
    briefs: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        briefs.append(ctx)
        return JobResult("ok")

    rig = Rig(
        close_and_brief(close_retry={"max": 5, "backoff_s": [900]}, deadline_min=240),
        {"tests.fake:close": Publisher(kst(2026, 10, 6, 16, 50)), "tests.fake:brief": brief},
        kst(2026, 10, 6, 15, 30),
    )
    rig.run_until(kst(2026, 10, 6, 18, 0))
    assert rig.times("market.close_collect", "started")[0] == kst(2026, 10, 6, 15, 35)
    assert rig.kinds("brief.closing")[:1] == ["waiting"]
    (started,) = rig.times("brief.closing", "started")
    assert kst(2026, 10, 6, 16, 50) <= started <= kst(2026, 10, 6, 17, 1)
    assert len(briefs) == 1 and briefs[0].as_of == "2026-10-06"


def test_run_once_ignore_deps_skips_the_dependency_check() -> None:
    briefs: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        briefs.append(ctx)
        return JobResult("ok")

    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": Publisher(None), "tests.fake:brief": brief},
        kst(2026, 10, 6, 15, 30),
    )
    # 의존(market.close_collect@2026-10-06)이 없다 — 보통은 기다리다 실패한다
    waiting = rig.runner.run_once("brief.closing", rig.now, as_of="2026-10-06")
    assert not briefs and [e.kind for e in waiting][:1] == ["waiting"]
    rig2 = Rig(
        close_and_brief(),
        {"tests.fake:close": Publisher(None), "tests.fake:brief": brief},
        kst(2026, 10, 6, 15, 30),
    )
    events = rig2.runner.run_once("brief.closing", rig2.now, as_of="2026-10-06", ignore_deps=True)
    assert [e.kind for e in events] == ["started", "ok"] and len(briefs) == 1


def test_hard_dependency_failure_blocks_the_dependent_without_a_second_failure() -> None:
    """굳은 의존이 실패하면 의존 작업은 돌지 않고 skipped(막힘) — 실패·알림은 원인 작업 하나."""
    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": Publisher(None), "tests.fake:brief": lambda c: JobResult("ok")},
        kst(2026, 10, 6, 15, 30),
    )
    rig.run_until(kst(2026, 10, 6, 18, 0))
    assert rig.kinds("market.close_collect")[-1] == "failed"
    assert "started" not in rig.kinds("brief.closing")
    rec = rig.runs.latest("brief.closing", "2026-10-06")
    assert rec is not None and rec.status == "skipped"
    assert rec.detail["wait"] == "blocked"
    assert rec.detail["blocked_by"] == "market.close_collect@2026-10-06"
    subjects = [n["subject"] for n in rig.notes.sent]
    assert subjects == ["market.close_collect:2026-10-06"]


def test_blocked_dependent_runs_again_once_the_cause_is_fixed() -> None:
    """막힘(skipped·wait)은 끝난 실행이 아니다 — 원인을 다시 돌려 성공하면 수동 실행이 돈다."""
    briefs: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        briefs.append(ctx)
        return JobResult("ok")

    close = Publisher(None)
    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": close, "tests.fake:brief": brief},
        kst(2026, 10, 6, 15, 30),
    )
    rig.run_until(kst(2026, 10, 6, 18, 0))
    assert rig.runs.latest("brief.closing", "2026-10-06").status == "skipped"  # type: ignore[union-attr]
    close.ready_at = rig.now
    rig.runner.run_once("market.close_collect", rig.now, as_of="2026-10-06")
    events = rig.runner.run_once("brief.closing", rig.now, as_of="2026-10-06")
    assert "already" not in [e.kind for e in events]
    assert len(briefs) == 1 and rig.runs.latest("brief.closing", "2026-10-06").status == "ok"  # type: ignore[union-attr]


# ── 무른 의존 wait_min(ADR 0018 — 16:00 발송이 같은 16:00 보드를 기다린다) ──────────────────


def close_board_brief(
    board_retry: Any = None, wait_min: int | None = 20, **close: Any
) -> list[dict[str, Any]]:
    """마감 수집(close+5) → 보드(16:00, 굳은 의존) → 마감 요약(16:00, 보드는 무른 의존)."""
    jobs = close_and_brief(**close)
    board = {
        "name": "board.daily",
        "phase": "P3",
        "owner": "tests.fake:board",
        "schedule": {"cron": "0 16 * * 1-5", "when": "trading_day"},
        "retry": board_retry or {"max": 2, "backoff_s": [600]},
        "depends_on": [{"job": "market.close_collect", "hard": True}],
    }
    soft: dict[str, Any] = {"job": "board.daily", "hard": False}
    if wait_min is not None:
        soft["wait_min"] = wait_min
    jobs[1]["depends_on"] = [{"job": "market.close_collect", "hard": True}, soft]
    return [jobs[0], board, jobs[1]]


def _brief_rig(board: Publisher, *jobs_args: Any, **jobs_kw: Any) -> tuple[Rig, list[JobContext]]:
    briefs: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        briefs.append(ctx)
        return JobResult("ok")

    close = jobs_kw.pop("close", Publisher(kst(2026, 10, 6, 0, 0)))
    rig = Rig(
        close_board_brief(*jobs_args, **jobs_kw),
        {"tests.fake:close": close, "tests.fake:board": board, "tests.fake:brief": brief},
        kst(2026, 10, 6, 15, 30),
    )
    return rig, briefs


def _brief_started(rig: Rig) -> tuple[datetime, dict[str, Any]]:
    (ev,) = [e for e in rig.events if e.job == "brief.closing" and e.kind == "started"]
    return ev.at.astimezone(KST), dict(ev.detail)


def test_soft_wait_holds_the_brief_until_the_board_finishes() -> None:
    rig, briefs = _brief_rig(Publisher(kst(2026, 10, 6, 16, 10)))  # 보드는 두 번째 시도(16:10)에 ok
    rig.run_until(kst(2026, 10, 6, 17, 0))
    assert rig.times("board.daily", "started") == [
        kst(2026, 10, 6, 16, 0),
        kst(2026, 10, 6, 16, 10),
    ]
    waits = [e for e in rig.events if e.job == "brief.closing" and e.kind == "waiting"]
    assert [dict(e.detail) for e in waits] == [{"soft_deps": ["board.daily@2026-10-06"]}]
    started, detail = _brief_started(rig)
    assert kst(2026, 10, 6, 16, 10) <= started <= kst(2026, 10, 6, 16, 11)
    assert detail["soft_deps"] == {"board.daily": "ok"}  # 보드가 끝난 뒤에 시작했다
    assert len(briefs) == 1 and rig.kinds("brief.closing")[-1] == "ok"


def test_soft_wait_does_not_care_how_the_board_ended() -> None:
    board = {"max": 1, "backoff_s": [600]}  # 16:00·16:10 둘 다 실패 → 보드 실패
    rig, briefs = _brief_rig(Publisher(None), board)
    rig.run_until(kst(2026, 10, 6, 17, 0))
    assert rig.kinds("board.daily")[-1] == "failed"
    started, detail = _brief_started(rig)
    assert kst(2026, 10, 6, 16, 10) <= started <= kst(2026, 10, 6, 16, 11)  # 시한(16:20) 전
    assert detail["soft_deps"] == {"board.daily": "failed"}
    # 무른 의존 — 보드가 실패해도 마감 요약은 실패하지 않는다
    assert len(briefs) == 1 and rig.kinds("brief.closing")[-1] == "ok"
    assert [n["subject"] for n in rig.notes.sent] == ["board.daily:2026-10-06"]


def test_soft_wait_stops_at_wait_min_and_starts_anyway() -> None:
    rig, briefs = _brief_rig(Publisher(None), None, 15)  # 보드는 16:00·16:10·16:20 시도
    rig.run_until(kst(2026, 10, 6, 17, 0))
    started, detail = _brief_started(rig)
    assert started == kst(2026, 10, 6, 16, 15)  # 굳은 의존 준비(16:00) + 15분
    assert detail["soft_deps"] == {"board.daily": "failed"}  # 첫 실패 뒤 재시도 대기 중이었다
    assert len(briefs) == 1 and rig.kinds("brief.closing")[-1] == "ok"
    assert rig.times("board.daily", "started")[-1] == kst(2026, 10, 6, 16, 20)


def test_soft_dependency_without_wait_min_does_not_hold() -> None:
    rig, briefs = _brief_rig(Publisher(kst(2026, 10, 6, 16, 10)), None, None)
    rig.run_until(kst(2026, 10, 6, 17, 0))
    started, detail = _brief_started(rig)
    assert started == kst(2026, 10, 6, 16, 0)
    assert "waiting" not in rig.kinds("brief.closing")
    # 보드 첫 시도(16:00)는 그 자리에서 끝나 같은 회차에 바로 거둬진다(실패 — 16:10 재시도 대기).
    # 기다리지 않았으니 마감 요약은 그 기록을 그대로 적는다
    assert detail["soft_deps"] == {"board.daily": "failed"}
    assert len(briefs) == 1


def test_soft_wait_counts_from_when_hard_dependencies_are_ready() -> None:
    """수능일처럼 마감 수집이 늦으면(16:50) 보드도 늦다 — 기다림은 발화(16:00)가 아니라 굳은 의존이
    준비된 때부터 센다. 그래서 16:20 이 지났어도 보드를 기다린다."""
    rig, briefs = _brief_rig(
        Publisher(kst(2026, 10, 6, 17, 0)),
        close_retry={"max": 5, "backoff_s": [900]},
        deadline_min=240,
        close=Publisher(kst(2026, 10, 6, 16, 50)),
    )
    rig.run_until(kst(2026, 10, 6, 18, 0))
    assert rig.times("market.close_collect", "ok") == [kst(2026, 10, 6, 16, 50)]
    board = rig.times("board.daily", "started")
    assert kst(2026, 10, 6, 16, 50) <= board[0] <= kst(2026, 10, 6, 16, 51)
    assert rig.times("board.daily", "ok") == [board[0] + timedelta(minutes=10)]
    started, detail = _brief_started(rig)
    assert board[1] <= started <= board[1] + timedelta(minutes=1)
    assert detail["soft_deps"] == {"board.daily": "ok"}
    assert len(briefs) == 1


def test_catch_up_after_restart_and_not_after_deadline() -> None:
    calls: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        calls.append(ctx)
        return JobResult("ok")

    jobs = close_and_brief(depends_on=[])
    handlers = {"tests.fake:close": Publisher(kst(2026, 10, 6, 0, 0)), "tests.fake:brief": brief}
    rig = Rig(jobs, handlers, kst(2026, 10, 6, 18, 0))  # 16:00 에 꺼져 있었다
    rig.run_until(kst(2026, 10, 6, 18, 10))
    assert len(calls) == 1 and calls[0].as_of == "2026-10-06"
    rig.run_until(kst(2026, 10, 6, 20, 40))
    assert len(calls) == 1  # 한 번만

    late = Rig(jobs, handlers, kst(2026, 10, 6, 20, 31))
    late.run_until(kst(2026, 10, 6, 21, 0))
    assert late.kinds("brief.closing") == []

    done = Rig(jobs, handlers, kst(2026, 10, 6, 18, 0))
    done.runs.record(
        RunRecord("brief.closing:2026-10-06:1", "brief.closing", "2026-10-06", 1, "ok")
    )
    done.run_until(kst(2026, 10, 6, 18, 5))
    assert done.kinds("brief.closing") == []


def _woke_jobs() -> list[dict[str, Any]]:
    board = {
        "name": "board.daily",
        "phase": "P3",
        "owner": "tests.fake:board",
        "schedule": {"cron": "0 16 * * 1-5", "when": "trading_day"},
        "retry": {"max": 0},
        "depends_on": [{"job": "market.close_collect", "hard": True}],
    }
    # 따라잡기는 켜진 작업만 본다(등록부 기본값은 꺼짐)
    return [{**j, "enabled": True} for j in (*close_and_brief(depends_on=[]), board)]


def _woke_handlers(seen: list[tuple[str, str]]) -> dict[str, Callable[[JobContext], JobResult]]:
    def h(name: str) -> Callable[[JobContext], JobResult]:
        def run(ctx: JobContext) -> JobResult:
            seen.append((name, ctx.as_of))
            return JobResult("ok", collected=ctx.keys, rows=1)

        return run

    return {f"tests.fake:{n}": h(n) for n in ("close", "brief", "board")}


def test_startup_catch_up_runs_missed_close_jobs_when_enabled() -> None:
    # 맥이 10/06 장 마감 전에 꺼졌다가 다음 날 06:00 에 켜졌다 — 놓친 마감 수집·보드를 한 번씩 돈다
    seen: list[tuple[str, str]] = []
    on = Settings.model_construct(scheduler_catch_up_h=20)
    rig = Rig(_woke_jobs(), _woke_handlers(seen), kst(2026, 10, 7, 6, 0), settings=on)
    rig.run_until(kst(2026, 10, 7, 6, 10))
    assert sorted(seen) == [
        ("board", "2026-10-06"),
        ("brief", "2026-10-06"),
        ("close", "2026-10-06"),
    ]

    seen.clear()  # 꺼 둔 기본값(VM)은 따라잡지 않는다 — catch_up_until 이 있는 작업만 그 시각까지
    off = Rig(_woke_jobs(), _woke_handlers(seen), kst(2026, 10, 7, 6, 0))
    off.run_until(kst(2026, 10, 7, 6, 10))
    assert seen == []


def test_startup_catch_up_skips_the_close_after_the_next_open() -> None:
    # 다음 거래일 개장 뒤에는 마감 수집을 따라잡지 않는다(그날 시세가 섞인다)
    seen: list[tuple[str, str]] = []
    on = Settings.model_construct(scheduler_catch_up_h=20)
    rig = Rig(_woke_jobs(), _woke_handlers(seen), kst(2026, 10, 7, 9, 30), settings=on)
    rig.run_until(kst(2026, 10, 7, 9, 35))
    assert ("close", "2026-10-06") not in seen


def test_same_job_and_as_of_runs_once() -> None:
    calls: list[JobContext] = []

    def h(ctx: JobContext) -> JobResult:
        calls.append(ctx)
        return JobResult("ok")

    job = {
        "name": "ops.check",
        "phase": "P2",
        "owner": "tests.fake:check",
        "schedule": {"cron": "*/10 9-10 * * *", "when": "always"},
        "retry": {"max": 0},
    }
    rig = Rig([job], {"tests.fake:check": h}, kst(2026, 10, 6, 8, 59))
    rig.run_until(kst(2026, 10, 6, 11, 0))
    assert len(calls) == 1 and rig.kinds("ops.check").count("already") == 11


def test_failed_series_continues_attempt_numbers_on_next_fire() -> None:
    job = {
        "name": "trade.customs_monthly",
        "phase": "P6",
        "owner": "tests.fake:customs",
        "schedule": {"cron": "0 10 * * *", "when": "month_days", "month_days": [15, 16, 17]},
        "retry": {"max": 0},
        "collects": [{"source": "DATAGO", "dataset": "15101609", "as_of": "month"}],
        "budget": "datago",
        "writes": ["pub_trade.item_monthly"],
    }
    pub = Publisher(kst(2026, 10, 16, 0, 0))
    rig = Rig([job], {"tests.fake:customs": pub}, kst(2026, 10, 15, 9, 0))
    rig.run_until(kst(2026, 10, 17, 11, 0), every=timedelta(minutes=10))
    recs = rig.runs.of("trade.customs_monthly")
    # 15일은 공표 전 — 기간(월) 수집이라 실패가 아니라 공표 대기(skipped·wait), 16일에 이어 돈다
    assert [(r.as_of, r.attempt, r.status) for r in recs] == [
        ("2026-09", 1, "skipped"),
        ("2026-09", 2, "ok"),
    ]
    assert recs[0].detail["wait"] == "not_ready" and recs[0].detail["result"] == "not_ready"
    assert rig.kinds("trade.customs_monthly")[-1] == "already"  # 17일은 이미 받았다
    assert rig.notes.sent == [] and "job_failed" not in rig.health.kinds()


def test_period_not_ready_is_waiting_but_errors_still_fail() -> None:
    """기간 수집의 공표 전은 알리지 않지만, 출처 오류(failed)는 그대로 실패·알림이다."""
    job = {
        "name": "trade.customs_monthly",
        "phase": "P6",
        "owner": "tests.fake:customs",
        "schedule": {"cron": "0 10 * * *", "when": "month_days", "month_days": [15]},
        "retry": {"max": 0},
        "collects": [{"source": "DATAGO", "dataset": "15101609", "as_of": "month"}],
        "budget": "datago",
        "writes": ["pub_trade.item_monthly"],
    }

    def broken(ctx: JobContext) -> JobResult:
        return JobResult("failed", detail={"reason": "HTTP 500"})

    rig = Rig([job], {"tests.fake:customs": broken}, kst(2026, 10, 15, 9, 59))
    rig.run_until(kst(2026, 10, 15, 10, 1))
    rec = rig.runs.latest("trade.customs_monthly", "2026-09")
    assert rec is not None and rec.status == "failed" and "wait" not in rec.detail
    assert [n["kind"] for n in rig.notes.sent] == ["ops.job_failed"]


def test_date_not_ready_still_fails() -> None:
    """날짜 단위 수집(전 거래일 등)의 공표 전은 다음 날 as_of 가 달라 다시 보지 않는다 — 실패."""
    rig = Rig([krx_like()], {"tests.fake:krx": Publisher(None)}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 11, 0))
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "failed" and "wait" not in rec.detail


# ── 계획됨·외부·선점 ─────────────────────────────────────────────────────────────────────


def test_disabled_job_without_handler_is_recorded_as_planned() -> None:
    job = krx_like(owner="kbj.services.collectors.krx_daily:run")  # P3 — 아직 없는 모듈
    rig = Rig([job], {}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 8, 10))
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "skipped" and rec.detail["reason"] == "planned"
    assert rig.claims.rows == [] and rig.notes.sent == []


def test_planned_record_does_not_block_the_job_once_enabled() -> None:
    """꺼져 있을 때 남은 '계획됨' 기록은 끝난 실행이 아니다 — 켠 뒤 같은 회차가 돈다(2026-10-20:
    trade.customs_monthly@2026-09 가 켠 날 이 기록 때문에 돌지 않았다)."""
    off = Rig([krx_like(owner="kbj.services.collectors.not_yet:run")], {}, kst(2026, 10, 6, 8, 0))
    off.run_until(kst(2026, 10, 6, 8, 10))
    rec = off.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.detail["reason"] == "planned"
    pub = Publisher(kst(2026, 10, 6, 8, 0))
    on = Rig([krx_like()], {"tests.fake:krx": pub}, kst(2026, 10, 6, 12, 0))
    on.runs.records.update(off.runs.records)
    events = on.runner.run_once("krx.daily", on.now, as_of="2026-10-02")
    assert "already" not in [e.kind for e in events]
    assert pub.calls and on.runs.latest("krx.daily", "2026-10-02").status == "ok"  # type: ignore[union-attr]


def test_planned_minute_job_is_recorded_once_per_day() -> None:
    """매분 도는 계획됨 작업(예: filings.dart_feed)도 하루 기록은 1건.

    발화 이벤트는 매번 남는다.
    """
    job = {
        "name": "filings.minutely",
        "phase": "P4",
        "owner": "kbj.services.collectors.not_yet:run",  # 아직 없는 모듈 → 계획됨
        "schedule": {"cron": "* 9-10 * * *", "when": "always"},
        "retry": {"max": 0},
        "as_of": "minute",
        "enabled": False,
    }
    rig = Rig([job], {}, kst(2026, 10, 6, 8, 59))
    rig.run_until(kst(2026, 10, 6, 10, 30))
    recs = rig.runs.of("filings.minutely")
    assert len(recs) == 1 and recs[0].detail["reason"] == "planned"
    assert rig.kinds("filings.minutely").count("planned") > 60
    rig.run_until(kst(2026, 10, 7, 9, 5))  # 다음 날 첫 발화는 다시 1건
    assert len(rig.runs.of("filings.minutely")) == 2


def test_external_job_is_never_run_or_recorded() -> None:
    job = krx_like(
        name="gex.krx_derivatives",
        runner="external",
        owner="legacy:gexlab/services/scheduler/krx.py:KrxDaily",
        collects=[{"source": "KRX", "dataset": "drv/fut_bydd_trd", "as_of": "prev_trading_day"}],
        writes=["prv_gex.krx_fut_daily"],
    )
    rig = Rig([job], {}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 10, 0))
    assert rig.events == [] and rig.runs.records == {}


def test_already_claimed_keys_are_a_duplicate_skip() -> None:
    pub = Publisher(kst(2026, 10, 6, 0, 0))
    rig = Rig([krx_like()], {"tests.fake:krx": pub}, kst(2026, 10, 6, 8, 0))
    for ds in ("sto/stk_bydd_trd", "idx/kospi_dd_trd"):
        venue = "KRX" if ds.startswith("sto") else ""
        key = DataKey("KRX", ds, "2026-10-02", venue)
        rig.claims.claim(key, "market.backfill", "x", rig.now)
        rig.claims.complete(key, "x", 1, rig.now)
    rig.run_until(kst(2026, 10, 6, 8, 10))
    assert rig.kinds("krx.daily") == ["duplicate"] and pub.calls == []
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "skipped" and rec.detail["reason"] == "duplicate"


def test_partial_duplicate_passes_only_claimed_keys() -> None:
    pub = Publisher(kst(2026, 10, 6, 0, 0))
    rig = Rig([krx_like()], {"tests.fake:krx": pub}, kst(2026, 10, 6, 8, 0))
    taken = DataKey("KRX", "sto/stk_bydd_trd", "2026-10-02", "KRX")
    rig.claims.claim(taken, "other.job", "x", rig.now)
    rig.run_until(kst(2026, 10, 6, 8, 10))
    assert [k.dataset for k in pub.calls[0].keys] == ["idx/kospi_dd_trd"]
    assert rig.kinds("krx.daily")[-1] == "ok"


def test_retry_after_partial_success_claims_only_the_rest() -> None:
    """부분 성공 뒤 재시도는 남은 키만 잡는다 — 제 done 키를 '중복'으로 거절당하지 않는다."""
    calls: list[JobContext] = []

    def half_then_rest(ctx: JobContext) -> JobResult:
        calls.append(ctx)
        if len(calls) == 1:  # 지수만 받고 실패
            got = tuple(k for k in ctx.keys if k.dataset == "idx/kospi_dd_trd")
            return JobResult("failed", collected=got, detail={"reason": "주식 아직"})
        return JobResult("ok", collected=ctx.keys)

    rig = Rig([krx_like()], {"tests.fake:krx": half_then_rest}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 8, 30))
    assert [sorted(k.dataset for k in c.keys) for c in calls] == [
        ["idx/kospi_dd_trd", "sto/stk_bydd_trd"],
        ["sto/stk_bydd_trd"],
    ]
    assert rig.kinds("krx.daily") == ["started", "retry", "started", "ok"]
    assert rig.claims.refused == []  # 남의 키도, 제 키도 거절당하지 않았다
    assert len(rig.claims.done_keys()) == 2


def test_notify_ticket_not_ok_is_reported_to_health() -> None:
    class Ticket:
        ok = False
        reason = "Redis 없음"

    rig = Rig(
        [krx_like(retry={"max": 0})], {"tests.fake:krx": Publisher(None)}, kst(2026, 10, 6, 8, 5)
    )
    rig.runner._notify = lambda text, **kw: Ticket()  # pyright: ignore[reportPrivateUsage]
    rig.tick()
    assert rig.kinds("krx.daily")[-1] == "failed"
    kinds = rig.health.kinds()
    assert "job_failed" in kinds and "job_failed_notify_failed" in kinds


class FlakyRuns(MemoryRunLog):
    """한 작업의 기록 조회만 `broken` 동안 실패한다(저장소 오류 흉내)."""

    def __init__(self, bad_job: str) -> None:
        super().__init__()
        self.bad_job = bad_job
        self.broken = True

    def latest(self, job: str, as_of: str) -> RunRecord | None:
        if self.broken and job == self.bad_job:
            raise RuntimeError("DB 접속 실패 password=hunter2")
        return super().latest(job, as_of)


def test_store_error_in_one_job_does_not_block_others_and_is_retried() -> None:
    def ok(ctx: JobContext) -> JobResult:
        return JobResult("ok", collected=ctx.keys)

    bad = krx_like(name="krx.bad", owner="tests.fake:bad", collects=[], writes=[], budget=None)
    good = krx_like(
        name="ops.good",
        owner="tests.fake:good",
        collects=[],
        writes=[],
        budget=None,
        retry={"max": 0},
    )
    rig = Rig([bad, good], {"tests.fake:bad": ok, "tests.fake:good": ok}, kst(2026, 10, 6, 8, 4))
    runs = FlakyRuns("krx.bad")
    rig.runner.runs = runs
    rig.runs = runs
    step = timedelta(seconds=20)
    rig.run_until(kst(2026, 10, 6, 8, 5, 40), every=step)
    assert rig.kinds("ops.good") == ["started", "ok"]  # 앞 작업 오류에 막히지 않았다
    assert rig.kinds("krx.bad") == []
    # 08:05:00·:20·:40 세 번 실패 — health 는 60초에 한 번만
    assert rig.health.kinds().count("job_fire_failed") == 1
    assert "hunter2" not in str([e.detail for e in rig.health.events])
    runs.broken = False
    rig.run_until(kst(2026, 10, 6, 8, 6, 0), every=step)  # 08:05 창을 다시 봐서 놓치지 않는다
    assert rig.kinds("krx.bad") == ["started", "ok"]
    assert rig.times("krx.bad", "started") == [kst(2026, 10, 6, 8, 6)]


def test_run_once_without_as_of_on_a_holiday_is_an_error_not_a_silent_noop() -> None:
    rig = Rig(close_and_brief(), {}, kst(2026, 10, 5, 16, 0))  # 개천절 대체공휴일
    with pytest.raises(AsOfUnavailable):
        rig.runner.run_once("market.close_collect", rig.now)
    assert rig.runs.records == {}


def test_holiday_does_not_fire_trade_date_jobs() -> None:
    rig = Rig(close_and_brief(), {}, kst(2026, 10, 5, 8, 0))  # 개천절 대체공휴일
    rig.run_until(kst(2026, 10, 5, 21, 0), every=timedelta(minutes=5))
    assert rig.events == []


def test_equity_trigger_follows_late_open_day() -> None:
    late = TradingCalendar(late_open={date(2026, 11, 19): (time(10, 0), time(16, 30))})
    calls: list[datetime] = []

    def h(ctx: JobContext) -> JobResult:
        calls.append(ctx.now.astimezone(KST))
        return JobResult("ok", collected=ctx.keys)

    rig = Rig(close_and_brief()[:1], {"tests.fake:close": h}, kst(2026, 11, 19, 15, 0), kr=late)
    rig.run_until(kst(2026, 11, 19, 17, 0))
    assert calls == [kst(2026, 11, 19, 16, 35)]  # 평소 15:35 가 아니라 16:35


def test_intraday_equity_slots_on_new_year_first_day() -> None:
    job = {
        "name": "rules.intraday",
        "phase": "P8",
        "owner": "tests.fake:rules",
        "schedule": {
            "equity": {"start": "open", "end": "close", "every_min": 10},
            "when": "trading_day",
        },
        "retry": {"max": 0},
        "collects": [{"source": "KIS", "dataset": "watch_quotes_intraday", "as_of": "slot10m"}],
    }
    seen: list[str] = []

    def h(ctx: JobContext) -> JobResult:
        seen.append(ctx.as_of)
        return JobResult("ok", collected=ctx.keys)

    rig = Rig([job], {"tests.fake:rules": h}, kst(2027, 1, 4, 8, 0))  # 연초 첫 거래일 10:00 개장
    rig.run_until(kst(2027, 1, 4, 16, 0))
    assert seen[0] == "2027-01-04T10:00" and seen[-1] == "2027-01-04T15:30"
    assert len(seen) == 34


def test_state_enter_and_run_once_backfill() -> None:
    calls: list[JobContext] = []

    def h(ctx: JobContext) -> JobResult:
        calls.append(ctx)
        return JobResult("ok", collected=ctx.keys)

    jobs = [
        krx_like(),
        {
            "name": "market.backfill",
            "phase": "P3",
            "owner": "tests.fake:backfill",
            "schedule": {"manual": True},
            "backfill_of": ["krx.daily"],
            "budget": "krx",
            "writes": ["prv_market.daily_bar"],
        },
        {
            "name": "ops.premaster",
            "phase": "P7",
            "owner": "tests.fake:pre",
            "schedule": {"state_enter": ["PRE_DAY"], "when": "trading_day"},
            "retry": {"max": 0},
        },
    ]
    rig = Rig(jobs, {"tests.fake:backfill": h, "tests.fake:pre": h}, kst(2026, 10, 6, 7, 59))
    rig.runner.on_state(State.PRE_DAY, kst(2026, 10, 6, 8, 0))
    assert calls == [] or calls[0].job == "ops.premaster"
    rig.runner.drain(kst(2026, 10, 6, 8, 0))
    assert [c.job for c in calls] == ["ops.premaster"]
    events = rig.runner.run_once("market.backfill", kst(2026, 10, 6, 12, 0), as_of="2021-10-01")
    assert [e.kind for e in events] == ["started", "ok"]
    assert {k.as_of for k in calls[-1].keys} == {"2021-10-01"} and len(calls[-1].keys) == 2
    rec = rig.runs.latest("market.backfill", "2021-10-01")
    assert rec is not None and rec.source == "manual"
    with pytest.raises(ValueError):
        Rig([krx_like(runner="external", owner="legacy:x")], {}, rig.now).runner.run_once(
            "krx.daily", rig.now
        )


# ── 실제 등록부로 하루 ────────────────────────────────────────────────────────────────────


def test_real_registry_one_day_runs_only_enabled_jobs() -> None:
    """켜진 작업만 처리기를 부른다(P2 셋 + P3 아홉 + P4 아홉 + P5 일곱 + P6 셋 — docs/p3_design.md
    §0.4·p4 §0.4·p5 D-P5-23·p6 D-P6-20, 웨이브 3 묶음이 켰다).

    나머지는 '계획됨'(skipped·planned)으로만 남고 external 은 기록조차 없다. 켜진 작업의 데이터 키만
    선점 장부에 done 으로 남는다."""
    reg = Registry.load(JOBS)
    ran: list[str] = []

    def ok(ctx: JobContext) -> JobResult:
        ran.append(ctx.job)
        return JobResult("ok", collected=ctx.keys)

    enabled = {j.name for j in reg.enabled_jobs()}
    p2 = {"ops.nightly", "filings.corp_code", "ops.watchdog"}
    assert p2 <= enabled
    handlers = {j.owner: ok for j in reg.enabled_jobs()}
    rig = Rig([], handlers, kst(2026, 10, 6, 0, 0), registry=reg)
    rig.run_until(kst(2026, 10, 7, 0, 0), every=timedelta(minutes=1))
    # market.backfill·fin.backfill 은 수동(발화 없음), filings.company_profile 은 토요일 04:00.
    # us.universe 는 일요일 04:00(P5). trade.derive 는 month_days 14~20 만(P6). 테마 마스터 둘은
    # 일요일 12:40(ADR 0039). 나머지 켜진 작업은 10-06(화, 거래일)에 모두 돈다
    assert set(ran) == enabled - {
        "market.backfill", "fin.backfill", "filings.company_profile", "us.universe",
        "trade.derive", "theme.master_sync", "theme.classes_sync",
        # 2026-10-20 켬 — 장 상태 진입(PRE_OPEN·PRE_NIGHT)에만 돈다(이 시험은 상태 전이 없음)
        "kis.master_night", "kis.master",
        # 관세청은 공표일(1·11·21일 뒤)·월중(14~20일)만, 프로그램 백필은 수동
        "trade.customs_tenday", "trade.customs_monthly", "program.backfill",
    }  # fmt: skip
    # P6 웨이브 3 이 켠 매핑표·종목 수출 지수는 매일 돈다(외부 호출 없음)
    assert ran.count("trade.map_load") == ran.count("trade.stock_index") == 1
    # P4 분 단위 작업(as_of: minute — 실행 as_of 하나에 한 번): 발화마다 한 번씩
    assert ran.count("filings.dart_feed") == 13 * 60  # 07:00~19:59 매분
    assert ran.count("earnings.alerts") == 14 * 12  # 07:00~20:55 5분마다
    assert ran.count("fin.quarterly") == 15 * 2  # 07:00~21:30 30분마다
    assert ran.count("filings.derive") == 15 * 2  # 07:10~21:40 — 10·40분
    # P9 WRAP(ADR 0037 §3): 5분마다 매일 24시간 — 창 00:00~다음 날 00:00 양 끝 포함
    assert ran.count("ops.watchdog") == 24 * 12 + 1
    # equity {start: open, end: close, every_min: 10} — 09:00~15:30 양 끝 포함 40 슬롯
    assert ran.count("flows.intraday") == 40
    assert ran.count("market.intraday") == 41  # close+10 — 15:40 슬롯이 마감 지수(종가)를 받는다
    statuses = {(r.job, r.status, r.detail.get("reason")) for r in rig.runs.records.values()}
    for job, status, reason in statuses:
        if job not in enabled:
            assert (status, reason) == ("skipped", "planned"), job
    external = {j.name for j in reg.jobs if j.external}
    assert not any(r.job in external for r in rig.runs.records.values())
    planned = {r.job for r in rig.runs.records.values() if r.detail.get("reason") == "planned"}
    # P5 에서 꺼진 채 남은 것(실측 전 — D-P5-23)은 계획됨으로만
    # (macro.*·market_stats.kofia·market.fsc_daily 는 2026-10-18 에 켰다 — 아래 매일 도는 집합)
    # 2026-10-20 사용자 요청으로 꺼 둔 작업을 전부 켰다. 마지막 꺼짐이던 themes.monitor(처리기·표
    # 없음)는 테마 강도를 API 가 읽을 때 계산하므로(ADR 0039) 등록부에서 뺐다 — 계획됨 0
    assert "themes.monitor" not in {j.name for j in reg.jobs}
    assert planned == set()
    assert {"program.daily", "program.stock_sweep"} <= set(ran)
    assert {"macro.morning", "macro.evening", "market_stats.kofia", "market.fsc_daily"} <= set(ran)
    # P5 웨이브 3 이 켠 것은 돈다 — us.eod 는 10-05(월) 뉴욕장 → 10-06 KST
    assert {"brief.morning", "brief.closing", "flows.report", "us.eod"} <= set(ran)
    assert ran.count("flows.market_minute") == 401  # 09:00~15:40 매분
    assert rig.notes.sent == []
    done = rig.claims.done_keys()
    assert DataKey("DART", "corpCode", "2026-10-06") in done
    owners = {c.dataset_id: j.name for j in reg.jobs for c in j.collects}
    assert {owners[k.dataset_id] for k in done} <= enabled


def test_failure_reason_falls_back_to_detail_error() -> None:
    """처리기가 사유를 detail.error 에 두면(GEX 수집) 그것을 실행 기록 사유로 — '키 1개를 받지
    못했다'만 남던 것(2026-10-20 gex.night_minutes)."""
    rig = Rig(
        [krx_like(retry={"max": 0, "backoff_s": [60]})],
        {
            "tests.fake:krx": lambda ctx: JobResult(
                "failed", detail={"error": "분봉 적재 실패 — 봉 0개"}
            )
        },
        kst(2026, 10, 6, 8, 0),
    )
    rig.run_until(kst(2026, 10, 6, 8, 10))
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "failed"
    assert "분봉 적재 실패" in str(rec.detail.get("reason"))


def test_same_failure_is_alerted_once_per_day() -> None:
    """매분 작업이 같은 사유(숫자만 다름)로 계속 실패해도 텔레그램은 하루 한 통(2026-10-21
    filings.dart_feed 예산 소진). health 에는 매번 남는다. 다른 사유·다음 날은 다시 보낸다."""
    n = itertools.count(17990)

    def failing(ctx: JobContext) -> JobResult:
        return JobResult("failed", detail={"reason": f"dart 호출 {next(n)}/18000 — 그날 상한"})

    job = {
        "name": "filings.minutely",
        "phase": "P4",
        "owner": "tests.fake:fail",
        "schedule": {"cron": "* 9-10 * * *", "when": "always"},
        "retry": {"max": 0},
        "as_of": "minute",
        "enabled": True,
    }
    rig = Rig([job], {"tests.fake:fail": failing}, kst(2026, 10, 6, 8, 59))
    rig.run_until(kst(2026, 10, 6, 9, 30))
    assert rig.kinds("filings.minutely").count("failed") > 20
    assert [s["kind"] for s in rig.notes.sent] == ["ops.job_failed"]
    assert rig.health.kinds().count("job_failed") > 20
    rig.run_until(kst(2026, 10, 7, 9, 5))
    assert len(rig.notes.sent) == 2


def test_run_once_force_reruns_a_finished_as_of() -> None:
    """2026-10-22 실측: 07:35 에 옛 매핑표로 끝난 trade.map_load 를 같은 날 다시 싣지 못했다."""
    briefs: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        briefs.append(ctx)
        return JobResult("ok")

    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": Publisher(None), "tests.fake:brief": brief},
        kst(2026, 10, 6, 17, 0),
    )
    run = rig.runner.run_once
    first = run("brief.closing", rig.now, as_of="2026-10-06", ignore_deps=True)
    again = run("brief.closing", rig.now, as_of="2026-10-06", ignore_deps=True)
    assert [e.kind for e in first] == ["started", "ok"]
    assert [e.kind for e in again] == ["already"] and len(briefs) == 1
    forced = run("brief.closing", rig.now, as_of="2026-10-06", ignore_deps=True, force=True)
    assert [e.kind for e in forced] == ["started", "ok"] and len(briefs) == 2
    assert forced[-1].attempt == first[-1].attempt + 1


def test_run_once_lines_carry_the_failure_reason() -> None:
    # 2026-10-22 fill-trade: run-once 가 'failed … 시도 2' 만 찍어 무엇이 실패했는지 몰랐다
    from kbj.services.scheduler.__main__ import event_lines

    at = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
    ok = RunEvent("ok", "trade.map_load", "2026-10-22", at, 2, {"rows": 3})
    assert event_lines(ok) == ["ok trade.map_load 2026-10-22 시도 2"]
    detail = {
        "reason": "매핑표 검증 실패 — 옛 판 유지",
        "public_first": ["000000 region_missing KR-99-x"],
    }
    bad = RunEvent("failed", "trade.map_load", "2026-10-22", at, 2, detail)
    assert event_lines(bad) == [
        "failed trade.map_load 2026-10-22 시도 2 — 매핑표 검증 실패 — 옛 판 유지",
        "  · 000000 region_missing KR-99-x",
    ]
    bare = RunEvent("timeout", "trade.map_load", "2026-10-22", at, 1)
    assert event_lines(bare) == ["timeout trade.map_load 2026-10-22 시도 1"]
