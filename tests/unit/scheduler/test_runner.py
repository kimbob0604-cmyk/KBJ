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
        "schedule": {"cron": "40 16 * * 1-5", "when": "trading_day", "catch_up_until": "20:30"},
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


def test_hard_dependency_failure_fails_the_dependent() -> None:
    rig = Rig(
        close_and_brief(),
        {"tests.fake:close": Publisher(None), "tests.fake:brief": lambda c: JobResult("ok")},
        kst(2026, 10, 6, 15, 30),
    )
    rig.run_until(kst(2026, 10, 6, 18, 0))
    assert rig.kinds("market.close_collect")[-1] == "failed"
    assert rig.kinds("brief.closing")[-1] == "failed"
    assert "started" not in rig.kinds("brief.closing")
    subjects = [n["subject"] for n in rig.notes.sent]
    assert subjects == ["market.close_collect:2026-10-06", "brief.closing:2026-10-06"]


def test_catch_up_after_restart_and_not_after_deadline() -> None:
    calls: list[JobContext] = []

    def brief(ctx: JobContext) -> JobResult:
        calls.append(ctx)
        return JobResult("ok")

    jobs = close_and_brief(depends_on=[])
    handlers = {"tests.fake:close": Publisher(kst(2026, 10, 6, 0, 0)), "tests.fake:brief": brief}
    rig = Rig(jobs, handlers, kst(2026, 10, 6, 18, 0))  # 16:40 에 꺼져 있었다
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
    assert [(r.as_of, r.attempt, r.status) for r in recs] == [
        ("2026-09", 1, "failed"),
        ("2026-09", 2, "ok"),
    ]
    assert rig.kinds("trade.customs_monthly")[-1] == "already"  # 17일은 이미 받았다


# ── 계획됨·외부·선점 ─────────────────────────────────────────────────────────────────────


def test_disabled_job_without_handler_is_recorded_as_planned() -> None:
    job = krx_like(owner="kbj.services.collectors.krx_daily:run")  # P3 — 아직 없는 모듈
    rig = Rig([job], {}, kst(2026, 10, 6, 8, 0))
    rig.run_until(kst(2026, 10, 6, 8, 10))
    rec = rig.runs.latest("krx.daily", "2026-10-02")
    assert rec is not None and rec.status == "skipped" and rec.detail["reason"] == "planned"
    assert rig.claims.rows == [] and rig.notes.sent == []


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
    """켜진 작업만 처리기를 부른다(P2 셋 + P3 아홉 — docs/p3_design.md §0.4, 묶음 S 가 켰다).

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
    # market.backfill 은 수동(발화 없음). 나머지 켜진 작업은 10-06(화, 거래일)에 모두 돈다
    assert set(ran) == enabled - {"market.backfill"}
    assert ran.count("ops.watchdog") == 26  # 08:00~20:30 30분마다
    # equity {start: open, end: close, every_min: 10} — 09:00~15:30 양 끝 포함 40 슬롯
    assert ran.count("flows.intraday") == ran.count("market.intraday") == 40
    statuses = {(r.job, r.status, r.detail.get("reason")) for r in rig.runs.records.values()}
    for job, status, reason in statuses:
        if job not in enabled:
            assert (status, reason) == ("skipped", "planned"), job
    external = {j.name for j in reg.jobs if j.external}
    assert not any(r.job in external for r in rig.runs.records.values())
    planned = {r.job for r in rig.runs.records.values() if r.detail.get("reason") == "planned"}
    assert {"brief.morning", "brief.closing", "flows.report"} <= planned  # P5 로 옮긴 것 포함
    assert "us.eod" in planned  # 10-05(월) 뉴욕장 → 10-06 05:10 KST
    assert rig.notes.sent == []
    done = rig.claims.done_keys()
    assert DataKey("DART", "corpCode", "2026-10-06") in done
    owners = {c.dataset_id: j.name for j in reg.jobs for c in j.collects}
    assert {owners[k.dataset_id] for k in done} <= enabled
