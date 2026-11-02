"""`ops.watchdog` — 운영 공백을 찾아 `ops.watchdog` 알림으로 보낸다(설계 §1.7·§6.7, P2).

원본 참고(내용만): SD `server.py:_market_watchdog`:14998·`_watchdog_notify`:14989. SD 는 공백을
찾으면 스스로 다시 받았지만, KBJ 워치독은 **알리기만** 한다(수집은 그 작업 한 곳 — 같은 데이터를 두
곳이 받지 않게).

찾는 것(시각은 모두 주입한 `ctx.now`):

1. **하트비트 끊김**: 등록부의 켜진 상시 서비스(`services`, heartbeat_s 가 있는 것)의 하트비트가
   없거나   heartbeat_s 보다 오래됐다.
2. **놓친 실행**: 켜진 cron 작업(워치독 자신 제외)의 오늘 마지막 예정 시각이 `GRACE_MIN` 분
   넘게 지났는데 그 as_of 의 실행 기록이 없다(scheduler 가 멈췄던 경우). 단 같은 as_of 굳은 의존이
   아직 끝나지 않았으면(기록 없음·queued·running — 예: 16:00 작업들이 마감 수집을 기다림) 작업마다
   알리지 않고 그 의존 하나로 묶은 `waiting:` 한 통.
3. **마감 지난 실행**: `running` 기록이 시작 + deadline_min 을 넘겼다(실행기가 timeout 으로 바꾸지
   못한 경우 — 예: scheduler 재기동으로 진행 목록을 잃음).

문제마다 알림 하나(subject = 문제 id — notify.yaml `ops.watchdog` 쿨다운 30분이 같은 공백을 다시
보내지 않는다). 알림을 넣지 못하면 작업 실패(삼키지 않는다).

호스트(맥)가 잠들었다 깬 뒤 `WAKE_QUIET_S`(실행기와 같은 창) 동안은 위 문제를 알리지 않는다 — 잠든
구간 때문에 생긴 끊김·누락이고, 실행기가 '맥 잠듦' 요약 한 통으로 알린다(2026-10-28 점심 37분
잠듦에 워치독 경고 14통). 하트비트 끊김 기록(ops_gaps)은 그대로 남긴다. 창이 지나도 남은 문제는
다시 알린다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

import psycopg

from kbj.core.calendar import TradingCalendar
from kbj.services.ops.source import GapWriter, ServiceExpect, record_heartbeat_gaps
from kbj.services.scheduler.claims import RunLog, RunRecord
from kbj.services.scheduler.conditions import AsOfUnavailable, holds, resolve_as_of
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.services.scheduler.registry import JobSpec, Registry
from kbj.services.scheduler.runner import WAKE_QUIET_S
from kbj.store.db import StoreError

__all__ = ["GRACE_MIN", "Problem", "find_problems", "in_wake_quiet", "pg_running_runs", "run"]

GRACE_MIN: Final = 10
KIND: Final = "ops.watchdog"
SOURCE: Final = "ops.watchdog"

HeartbeatAge = Callable[[str, datetime], float | None]  # (서비스, 지금) → 지난 초 | 없음
HostWake = Callable[[], tuple[datetime, datetime] | None]  # () → (잠든 때, 깬 때) | 없음


@dataclass(frozen=True)
class Problem:
    subject: str  # 알림 중복 키(공백 없는 id)
    text: str


def _waiting_on(job: JobSpec, runs: RunLog, as_of: str) -> tuple[str, str] | None:
    """같은 as_of 굳은 의존 중 아직 안 끝난(기록 없음·queued·running) 첫 의존 → (작업, 상태)."""
    for d in job.depends_on:
        if not d.hard or d.as_of != "same":
            continue
        rec = runs.latest(d.job, as_of)
        if rec is None or rec.status in ("queued", "running"):
            return d.job, "기록 없음" if rec is None else rec.status
    return None


def _missed(
    registry: Registry, runs: RunLog, kr: TradingCalendar, us: TradingCalendar, now: datetime
) -> list[Problem]:
    out: list[Problem] = []
    # 굳은 의존이 아직 끝나지 않아 발화 뒤 기다리는 작업(실행기는 의존이 끝나야 기록을 남긴다)은
    # 작업마다 알리지 않고 의존 하나로 묶어 한 통 — 진짜 원인(의존과 그 상태)을 짚는다
    waiting: dict[tuple[str, str, str], list[str]] = {}
    for job in registry.enabled_jobs():
        spec = job.schedule.cron_spec()
        # 워치독 자신은 뺀다 — 지금 돌고 있으면 scheduler 는 살아 있고, 재기동 사이 빠진 5분 한 칸은
        # 손쓸 일이 없다(같은 공백은 하루 한 번 도는 작업들의 놓친 실행으로 드러난다)
        if spec is None or job.external or job.name == KIND:
            continue
        local = (now - timedelta(minutes=GRACE_MIN)).astimezone(job.schedule.zone)
        due = spec.prev_at_or_before(local)
        if due is None:
            continue
        if not holds(job.schedule.when, due.date(), kr, us, month_days=job.schedule.month_days):
            continue
        try:
            as_of = resolve_as_of(job.run_as_of(), due, kr, us)
        except AsOfUnavailable:
            continue
        if runs.latest(job.name, as_of) is None:
            dep = _waiting_on(job, runs, as_of)
            if dep is not None:
                waiting.setdefault((dep[0], as_of, dep[1]), []).append(f"{job.name}({due:%H:%M})")
                continue
            out.append(
                Problem(
                    f"missed:{job.name}:{as_of}",
                    f"[워치독] {job.name} 의 {due:%H:%M} 실행 기록이 없다(as_of {as_of}) "
                    "— scheduler 확인",
                )
            )
    for (dep_job, as_of, state), names in sorted(waiting.items()):
        out.append(
            Problem(
                f"waiting:{dep_job}:{as_of}",
                f"[워치독] {dep_job}(as_of {as_of}, {state}) 가 아직 끝나지 않아 "
                f"{len(names)}개 작업이 기다린다: {', '.join(names)}",
            )
        )
    return out


def find_problems(
    registry: Registry,
    runs: RunLog,
    running: Sequence[RunRecord],
    heartbeat_age: HeartbeatAge,
    kr: TradingCalendar,
    us: TradingCalendar,
    now: datetime,
) -> list[Problem]:
    out: list[Problem] = []
    for svc in registry.services:
        if not svc.enabled or svc.heartbeat_s is None:
            continue
        age = heartbeat_age(svc.beat_name, now)
        if age is None or age > svc.heartbeat_s:
            seen = "없음" if age is None else f"{age:.0f}초 전"
            out.append(
                Problem(
                    f"heartbeat:{svc.name}",
                    f"[워치독] 서비스 {svc.name} 하트비트 끊김"
                    f"(마지막 {seen}, 기준 {svc.heartbeat_s}초)",
                )
            )
    out += _missed(registry, runs, kr, us, now)
    jobs = {j.name: j for j in registry.jobs}
    for rec in running:
        job = jobs.get(rec.job)
        if job is None or rec.started_at is None:
            continue
        if now - rec.started_at > timedelta(minutes=job.deadline_min):
            out.append(
                Problem(
                    f"overdue:{rec.run_id}",
                    f"[워치독] {rec.job} @ {rec.as_of} 가 마감({job.deadline_min}분)을 넘겨 "
                    "running 으로 남았다",
                )
            )
    return out


def in_wake_quiet(wake: tuple[datetime, datetime] | None, now: datetime) -> bool:
    """호스트가 깬 뒤 묶는 창(`WAKE_QUIET_S`) 안인가."""
    return wake is not None and wake[1] <= now < wake[1] + timedelta(seconds=WAKE_QUIET_S)


def redis_host_wake(redis: Any) -> tuple[datetime, datetime] | None:
    """`sched:host_wake`(실행기가 쓴 잠든 구간). 없거나 형식이 틀리면 None."""
    from kbj.store.redis_keys import HOST_WAKE

    raw = redis.get(HOST_WAKE)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return datetime.fromisoformat(d["slept_from"]), datetime.fromisoformat(d["woke_at"])
    except (ValueError, KeyError, TypeError):
        return None


def pg_running_runs(
    connect_fn: Callable[[], psycopg.Connection[Any]], since: date
) -> list[RunRecord]:
    """`ops.job_run` 의 running 기록(그날 이후 시작)."""
    q = (
        "SELECT run_id, job, as_of, attempt, status, started_at, finished_at, detail, source "
        "FROM ops.job_run WHERE status = 'running' AND started_at >= %s"
    )
    try:
        with connect_fn() as conn, conn.cursor() as cur:
            cur.execute(q, (since,))
            rows = cur.fetchall()
    except psycopg.Error as e:
        raise StoreError(
            f"실행 기록 조회 실패: {type(e).__name__}", cause=type(e).__name__
        ) from None
    return [RunRecord(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7] or {}, r[8]) for r in rows]


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.ops_watchdog:run`).

    자원: registry·runs·kr·us·notify, 그리고 running(목록을 돌려주는 함수 — 없으면 connect 로 Pg)·
    heartbeat_age(없으면 redis 로 `kbj.services.runtime.heartbeat.heartbeat_age`).
    """
    registry: Registry = ctx.resource("registry")
    runs: RunLog = ctx.resource("runs")
    kr: TradingCalendar = ctx.resource("kr")
    us: TradingCalendar = ctx.resource("us")
    notify: Callable[..., Any] = ctx.resource("notify")
    running_fn: Callable[[], Sequence[RunRecord]] | None = ctx.resources.get("running")
    if running_fn is None:
        connect_fn = ctx.resource("connect")
        since = (ctx.now - timedelta(days=1)).date()

        def _pg_running() -> Sequence[RunRecord]:
            return pg_running_runs(connect_fn, since)

        running_fn = _pg_running
    hb: HeartbeatAge | None = ctx.resources.get("heartbeat_age")
    if hb is None:
        from kbj.services.runtime.heartbeat import heartbeat_age

        redis = ctx.resource("redis")

        def _redis_age(service: str, now: datetime) -> float | None:
            return heartbeat_age(redis, service, now)

        hb = _redis_age
    problems = find_problems(registry, runs, running_fn(), hb, kr, us, ctx.now)
    wake_fn: HostWake | None = ctx.resources.get("host_wake")
    if wake_fn is None and "redis" in ctx.resources:
        redis_w = ctx.resources["redis"]

        def _wake() -> tuple[datetime, datetime] | None:
            return redis_host_wake(redis_w)

        wake_fn = _wake
    held = 0
    if problems and wake_fn is not None and in_wake_quiet(wake_fn(), ctx.now):
        held, problems = len(problems), []  # 깬 직후 — 실행기의 '맥 잠듦' 요약 한 통으로
    failed: list[str] = []
    # P9 WRAP(ADR 0037 §3): 지금 끊긴 상시 서비스를 ops.health_events 'heartbeat_gap' 으로 남긴다 —
    # 1주 판정 ①(끊김 >10분)의 입력. 자원 ops_gaps(쓰기 — 운영은 PgOpsSource)가 없으면 남기지 않는다
    gaps: GapWriter | None = ctx.resources.get("ops_gaps")
    gap_error = ""
    if gaps is not None:
        expect = [
            ServiceExpect(s.name, s.beat_name, s.heartbeat_s)
            for s in registry.services
            if s.enabled and s.heartbeat_s is not None
        ]
        try:
            record_heartbeat_gaps(expect, hb, ctx.now, gaps)
        except StoreError as e:  # 알림은 그대로 보내고 실행은 실패로 남긴다(삼키지 않는다)
            gap_error = f"하트비트 끊김 기록 실패: {e.cause or type(e).__name__}"
    for p in problems:
        ticket = notify(p.text, kind=KIND, source=SOURCE, subject=p.subject)
        if not getattr(ticket, "ok", False):
            failed.append(f"{p.subject}: {getattr(ticket, 'reason', '알림 실패')}")
    detail: dict[str, Any] = {"problems": len(problems)}
    if held:
        detail["held_after_wake"] = held
    if failed:
        return JobResult("failed", detail={**detail, "reason": f"알림을 넣지 못했다: {failed[0]}"})
    if gap_error:
        return JobResult("failed", detail={**detail, "reason": gap_error})
    return JobResult("ok", detail=detail)
