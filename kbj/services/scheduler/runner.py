"""작업 실행기 — 등록부의 작업을 시각·캘린더 조건·의존·재시도·마감대로 돌린다(설계 §6.1·§6.5·§6.9).

`JobRunner.tick(now)` 를 scheduler 서비스가 1초마다 부른다(시험·시뮬레이션은 가짜 시계로).

1. **발화**: 지난 tick 뒤 `(지난 tick, now]` 에 든 시각마다 — `cron`(작업 `tz` 벽시계), `equity`
   (주식 정규장 기준 — `TradingCalendar.equity_bounds` 라 연초·수능일 지연 개장을 따른다).
   `state_enter` 는 서비스가 상태 전이 때 `on_state` 로 알린다. `manual` 은 `run_once` 로만.
   조건(`when`)이 거짓이거나 as_of 가 없는 날(휴장일의 trade_date)이면 아무것도 하지 않는다.
   첫 tick 은 지금 분의 발화만 본다(재기동이 지난 시각을 몰아서 돌리지 않는다). 지난 하루 1회
   작업은 `catch_up_until` 까지 따라잡는다(기록이 없을 때만).
2. **계획됨**: 처리기가 없는 작업(꺼진 작업 — P2 의 대부분)은 실행하지 않고 실행 기록만
   `skipped` + `{"reason": "planned"}` 로 남긴다(§6.9 — legacy 를 부르지 않는다).
   `runner: external` 작업은 kbj 가 돌리지 않으므로 아무 기록도 하지 않는다.
3. **같은 (작업, as_of) 는 두 번 돌지 않는다**: 진행 중이거나 이미 `ok`·`skipped` 면 새 발화를
   무시한다. 앞 시도가 실패로 끝났으면(예: 월 15~17일 재시도) 시도 번호를 이어 다시 돈다.
4. **의존**: 굳은 의존(hard)은 그 작업의 같은(또는 앞) as_of 실행이 `ok` 일 때까지 기다린다 —
   `catch_up_until`(없으면 마감)까지. 의존 작업이 실패로 끝났으면 바로 실패. 무른 의존(soft)은
   기다리지 않고 상태만 기록한다.
5. **선점**: 수집 데이터 키를 모두 잡아 본다(`claims.py`). 하나도 못 잡으면 `skipped(duplicate)`.
   잡은 키만 처리기에 넘긴다. 처리기가 돌려준 키는 `done`, 못 돌려준 키는 재시도가 이어 쓰거나
   끝내 실패면 `failed` 로 놓아 준다. 재시도는 같은 시리즈가 이미 `done` 으로 만든 키를 다시 잡지
   않는다(남은 키만).
6. **재시도·마감**: `failed`·`not_ready` 는 `retry` 대로 다시(마감 안에서). 다 쓰거나 마감을
   넘기면 실패 — health `job_failed` + 알림 `ops.job_failed`(subject `<작업>:<as_of>`, 작업·as_of
   마다 1회 — notify.yaml). 처리기가 마감을 넘겨 돌고 있으면 `timeout` 으로 기록한다(스레드는
   멈출 수 없으니 결과가 나중에 와도 버리고 로그만).
7. **격리**: 처리기는 `submit`(기본 데몬 스레드)으로 돌아 상태 루프를 막지 않는다. 처리기 예외·
   저장소 오류는 그 작업만 실패시킨다(절대 규칙 4). 실행기 안의 오류(기록 저장소·캘린더 등)도 그
   작업에서 멈춘다 — 발화 오류는 그 작업의 창을 남겨 다음 tick 에 다시 보고, 진행 오류는 그 실행만
   `DEP_POLL_S` 뒤로 미룬다(health `job_fire_failed`·`job_advance_failed`·`job_finish_failed`,
   작업마다 60초에 한 번). 사유 문구는 가린다(절대 규칙 5). `job_failed` 알림이 들어가지 못하면
   (예외든 `ok=False` 표든) health `job_failed_notify_failed`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal

from kbj.config.settings import Settings
from kbj.core.calendar import State, TradingCalendar
from kbj.core.masking import mask_text
from kbj.core.time import KST
from kbj.data.catalog import claimable, keys_for
from kbj.data.spec import DataKey, DatasetSpec
from kbj.services.runtime.health import HealthEvent, HealthSink
from kbj.services.runtime.log import log_event
from kbj.services.runtime.threads import run_in_thread
from kbj.services.scheduler.claims import ClaimStore, RunLog, RunRecord, RunSource, make_run_id
from kbj.services.scheduler.conditions import (
    AsOfUnavailable,
    equity_anchor,
    holds,
    parse_anchor,
    prev_as_of,
    resolve_as_of,
)
from kbj.services.scheduler.handlers import Handler, HandlerMissing, JobContext, JobResult, resolve
from kbj.services.scheduler.registry import JobSpec, Registry, hhmm_time

__all__ = ["JobRunner", "RunEvent", "Submit", "inline_submit", "thread_submit"]

SERVICE: Final = "scheduler"
DEP_POLL_S: Final = 30  # 굳은 의존을 다시 보는 간격
REASON_MAX: Final = 300
PLANNED: Final = "planned"

log = logging.getLogger("kbj.services.scheduler")

Submit = Callable[[Callable[[], JobResult]], "Future[JobResult]"]
NotifyFn = Callable[..., Any]
EventKind = Literal[
    "planned",
    "started",
    "ok",
    "skipped",
    "duplicate",
    "retry",
    "failed",
    "timeout",
    "waiting",
    "already",
]


def thread_submit(fn: Callable[[], JobResult]) -> Future[JobResult]:
    """작업 하나를 데몬 스레드로(GX `run_in_thread`) — 상태 루프가 기다리지 않는다."""
    return run_in_thread(fn, name="job")


def inline_submit(fn: Callable[[], JobResult]) -> Future[JobResult]:
    """그 자리에서 부른다 — 가짜 시계 시험·시뮬레이션용."""
    fut: Future[JobResult] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


@dataclass(frozen=True)
class RunEvent:
    """tick 이 한 일 하나(시험·로그용)."""

    kind: EventKind
    job: str
    as_of: str
    at: datetime
    attempt: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class _Pending:
    job: JobSpec
    as_of: str
    fire_at: datetime  # UTC
    deadline: datetime  # UTC — 실행이 이때까지 끝나야 한다
    wait_until: datetime  # UTC — 굳은 의존을 이때까지 기다린다
    source: RunSource
    key_as_of: str | None = None  # run-once 로 준 as_of(수집 키에도 쓴다)
    attempt: int = 0  # 시작한 시도 수(이전 실패 시리즈 포함)
    next_at: datetime | None = None  # UTC — 다음에 볼 시각
    future: Future[JobResult] | None = None
    started_at: datetime | None = None
    keys: tuple[DataKey, ...] = ()  # 이번 시도에 잡은 키
    held: set[DataKey] = field(default_factory=set[DataKey])  # 재시도로 이어 쥘 키
    done: set[DataKey] = field(
        default_factory=set[DataKey]
    )  # 이 시리즈가 이미 받은 키(재시도 제외)
    waiting_logged: bool = False


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return ts.astimezone(UTC)


def _reason(e: BaseException | str) -> str:
    text = e if isinstance(e, str) else f"{type(e).__name__}: {e}"
    return mask_text(text)[:REASON_MAX]


def _as_date(as_of: str) -> date | None:
    try:
        return date.fromisoformat(as_of[:10])
    except ValueError:
        return None


class JobRunner:
    """등록부 실행기. 상태는 메모리(진행 중 시도) + `RunLog`(끝난 실행 — 재기동해도 이어진다)."""

    def __init__(
        self,
        registry: Registry,
        handlers: Mapping[str, Handler] | None,
        claims: ClaimStore,
        kr: TradingCalendar,
        us: TradingCalendar,
        *,
        runs: RunLog,
        catalog: Mapping[str, DatasetSpec],
        health: HealthSink,
        submit: Submit = thread_submit,
        notify: NotifyFn | None = None,
        run_planned: bool = False,
        settings: Settings | None = None,
        resources: Mapping[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self._handlers = dict(handlers or {})
        self.claims = claims
        self.kr = kr
        self.us = us
        self.runs = runs
        self.catalog = catalog
        self._health = health
        self._submit = submit
        self._notify = notify
        self._run_planned = run_planned
        self._settings = settings
        self._resources = dict(resources or {})
        self._last: datetime | None = None
        self._behind: dict[str, datetime] = {}  # 발화 중 오류가 난 작업 → 다시 볼 창의 시작
        self._pending: dict[tuple[str, str], _Pending] = {}
        self._abandoned: list[_Pending] = []
        self._caught: set[tuple[str, str]] = set()
        self._resolved: dict[str, Handler | None] = {}
        self._error_at: dict[tuple[str, str], datetime] = {}
        # 처리기 없는 작업(계획됨)은 작업·현지 날짜마다 기록 1건만 남긴다(매분 작업이 하루
        # 780행을 쌓지 않게 — 메인 결정 2026-10-07). 재기동하면 그날 1건이 더 생길 수 있다.
        self._planned_days: set[tuple[str, date]] = set()

    # ── 처리기 ───────────────────────────────────────────────────────────────────────

    def handler_for(self, job: JobSpec) -> Handler | None:
        """처리기. 없으면 None(= 계획됨). 켜진 작업의 import 실패는 `HandlerMissing` 그대로."""
        if job.external:
            return None
        if job.owner in self._handlers:
            return self._handlers[job.owner]
        if not job.enabled and not self._run_planned:
            return None
        if job.owner not in self._resolved:
            try:
                self._resolved[job.owner] = resolve(job.owner)
            except HandlerMissing:
                if job.enabled:
                    raise
                self._resolved[job.owner] = None
        return self._resolved[job.owner]

    # ── 바깥에서 부르는 것 ───────────────────────────────────────────────────────────

    @property
    def pending(self) -> list[tuple[str, str]]:
        return sorted(self._pending)

    def tick(self, now: datetime) -> list[RunEvent]:
        now = _utc(now)
        events: list[RunEvent] = []
        self._collect(now, events)
        last = self._last
        if last is None:
            last = now.replace(second=0, microsecond=0) - timedelta(microseconds=1)
        if now > last:
            for job in self.registry.jobs:
                if job.external:
                    continue
                since = self._behind.get(job.name, last)
                try:
                    for fire_at in self._fires(job, since, now):
                        self._fire(job, fire_at, now, events)
                    if job.schedule.catch_up_until is not None:
                        self._catch_up(job, now, events)
                except Exception as e:  # 격리 — 이 작업만 멈추고 창은 남겨 다음 tick 에 다시 본다
                    self._behind.setdefault(job.name, since)
                    self._job_error(now, "job_fire_failed", job.name, e)
                    continue
                self._behind.pop(job.name, None)
            self._last = now
        self._advance(now, events)
        self._collect(now, events)  # 그 자리에서 끝난 시도(inline·빠른 처리기)를 바로 거둔다
        return events

    def on_state(self, state: State, now: datetime) -> list[RunEvent]:
        """세션 상태 진입(서비스가 전이 때 부른다) — `state_enter` 작업 발화."""
        now = _utc(now)
        events: list[RunEvent] = []
        for job in self.registry.jobs:
            if not job.external and state in job.schedule.state_enter:
                self._fire(job, now, now, events)
        self._advance(now, events)
        return events

    def run_once(
        self, name: str, now: datetime, *, as_of: str | None = None, source: RunSource = "manual"
    ) -> list[RunEvent]:
        """작업 하나를 지금 한 번(조건은 보지 않는다).

        `as_of` 를 주면 실행·수집 키에 그 값을 쓴다. 주지 않았는데 지금 시각으로 as_of 를 정할 수
        없으면(휴장일의 trade_date, event) `AsOfUnavailable` — 조용히 끝나지 않는다.
        """
        now = _utc(now)
        job = self.registry.by_name(name)
        if job.external:
            raise ValueError(f"{name}: runner external 작업은 kbj 가 돌리지 않는다")
        if as_of is None:
            try:
                resolve_as_of(job.run_as_of(), now, self.kr, self.us)
            except AsOfUnavailable as e:
                raise AsOfUnavailable(
                    f"{name}: 지금은 as_of 를 정할 수 없다 — as_of 를 준다({e})"
                ) from None
        events: list[RunEvent] = []
        self._fire(job, now, now, events, as_of=as_of, source=source, check_when=False)
        self._advance(now, events)
        self._collect(now, events)  # 그 자리에서 끝난 시도(inline)를 거둔다
        return events

    def drain(self, now: datetime) -> list[RunEvent]:
        """끝난 시도를 거두고 진행한다(발화 없이) — 서비스 종료·시험용."""
        now = _utc(now)
        events: list[RunEvent] = []
        self._collect(now, events)
        self._advance(now, events)
        return events

    # ── 발화 ─────────────────────────────────────────────────────────────────────────

    def _fires(self, job: JobSpec, last: datetime, now: datetime) -> Iterable[datetime]:
        sched = job.schedule
        tz = sched.zone
        spec = sched.cron_spec()
        if spec is not None:
            cur = spec.next_after(last.astimezone(tz))
            while True:
                at = _utc(cur.replace(tzinfo=tz))
                if at > now:
                    return
                yield at
                cur = spec.next_after(cur)
        elif sched.equity is not None:
            d = last.astimezone(KST).date()
            while d <= now.astimezone(KST).date():
                for at in self._equity_times(job, d):
                    if last < at <= now:
                        yield at
                d += timedelta(days=1)

    def _equity_times(self, job: JobSpec, d: date) -> list[datetime]:
        eq = job.schedule.equity
        if eq is None or not self.kr.is_trading_day(d):
            return []
        start = _utc(equity_anchor(d, parse_anchor(eq.start), self.kr))
        if eq.end is None or eq.every_min is None:
            return [start]
        end = _utc(equity_anchor(d, parse_anchor(eq.end), self.kr))
        out: list[datetime] = []
        t = start
        while t <= end:
            out.append(t)
            t += timedelta(minutes=eq.every_min)
        return out

    def _catch_up(self, job: JobSpec, now: datetime, events: list[RunEvent]) -> None:
        spec = job.schedule.cron_spec()
        cu = job.schedule.catch_up_until
        if spec is None or cu is None:
            return
        local = now.astimezone(job.schedule.zone)
        if local.time() > hhmm_time(cu):
            return
        due = spec.prev_at_or_before(local)
        if due is None:
            return
        fire_at = _utc(due)
        if not holds(
            job.schedule.when,
            due.date(),
            self.kr,
            self.us,
            month_days=job.schedule.month_days,
        ):
            return
        try:
            as_of = resolve_as_of(job.run_as_of(), fire_at, self.kr, self.us)
        except AsOfUnavailable:
            return
        if (job.name, as_of) in self._caught or (job.name, as_of) in self._pending:
            return
        self._caught.add((job.name, as_of))
        if self.runs.latest(job.name, as_of) is None:
            self._fire(job, fire_at, now, events, catch_up=True)

    def _fire(
        self,
        job: JobSpec,
        fire_at: datetime,
        now: datetime,
        events: list[RunEvent],
        *,
        as_of: str | None = None,
        source: RunSource = "scheduler",
        check_when: bool = True,
        catch_up: bool = False,
    ) -> None:
        local_day = fire_at.astimezone(job.schedule.zone).date()
        if check_when and not holds(
            job.schedule.when, local_day, self.kr, self.us, month_days=job.schedule.month_days
        ):
            return
        if as_of is None:
            try:
                as_of = resolve_as_of(job.run_as_of(), fire_at, self.kr, self.us)
            except AsOfUnavailable:
                return
        key = (job.name, as_of)
        if key in self._pending:
            events.append(RunEvent("already", job.name, as_of, now))
            return
        try:
            handler = self.handler_for(job)
        except HandlerMissing as e:
            self._record_final(job, as_of, 1, "failed", now, now, {"reason": _reason(e)}, source)
            self._alert(job, as_of, f"처리기를 부를 수 없다 — {_reason(e)}", now)
            events.append(RunEvent("failed", job.name, as_of, now, 1, {"reason": _reason(e)}))
            return
        prev = self.runs.latest(job.name, as_of)
        # ok·skipped 는 끝난 실행 — 다시 돌지 않는다. running·queued 인데 진행 목록에 없으면 앞
        # 프로세스가 도중에 죽은 것이다 — 시도 번호를 이어 다시 돈다(선점은 같은 작업이 이어 쥔다).
        if prev is not None and prev.status in ("ok", "skipped"):
            events.append(RunEvent("already", job.name, as_of, now, prev.attempt))
            return
        attempt0 = prev.attempt if prev is not None else 0
        if handler is None:
            day_key = (job.name, local_day)
            if day_key not in self._planned_days:
                self._record_final(
                    job,
                    as_of,
                    attempt0 + 1,
                    "skipped",
                    None,
                    now,
                    {"reason": PLANNED, "phase": job.phase},
                    source,
                )
                self._planned_days.add(day_key)
            events.append(RunEvent("planned", job.name, as_of, now, attempt0 + 1))
            return
        deadline = fire_at + timedelta(minutes=job.deadline_min)
        wait_until = deadline
        cu = job.schedule.catch_up_until
        if cu is not None:
            cu_at = _utc(
                datetime.combine(local_day, hhmm_time(cu)).replace(tzinfo=job.schedule.zone)
            )
            wait_until = max(cu_at, fire_at)
            deadline = max(deadline, cu_at + timedelta(minutes=job.deadline_min))
        self._pending[key] = _Pending(
            job=job,
            as_of=as_of,
            fire_at=fire_at,
            deadline=deadline,
            wait_until=wait_until,
            source=source,
            key_as_of=as_of if source == "manual" else None,
            attempt=attempt0,
            next_at=now,
        )
        log_event(
            log, logging.INFO, SERVICE, "job_fired", job=job.name, as_of=as_of, catch_up=catch_up
        )

    # ── 진행 ─────────────────────────────────────────────────────────────────────────

    def _advance(self, now: datetime, events: list[RunEvent]) -> None:
        for key in list(self._pending):
            p = self._pending.get(key)
            if p is None:
                continue
            try:
                self._advance_one(p, now, events)
            except Exception as e:  # 격리 — 이 실행만 늦추고(저장소 오류 등) 다른 작업은 계속
                p.next_at = now + timedelta(seconds=DEP_POLL_S)
                self._job_error(now, "job_advance_failed", p.job.name, e)

    def _advance_one(self, p: _Pending, now: datetime, events: list[RunEvent]) -> None:
        if p.future is not None:
            if now > p.deadline:
                self._timeout(p, now, events)
            return
        if p.next_at is not None and p.next_at > now:
            return
        if now > p.deadline:
            self._give_up(p, now, events, "마감을 넘겼다(시작하지 못함)")
            return
        if not self._deps_ready(p, now, events):
            return
        self._start(p, now, events)

    def _deps_ready(self, p: _Pending, now: datetime, events: list[RunEvent]) -> bool:
        waiting: list[str] = []
        for d in p.job.depends_on:
            if not d.hard:
                continue
            dep = self.registry.by_name(d.job)
            dep_as_of = p.as_of
            if d.as_of == "prev":
                dep_as_of = prev_as_of(dep.run_as_of(), p.as_of, self.kr, self.us)
            rec = self.runs.latest(d.job, dep_as_of)
            if rec is not None and rec.status == "ok":
                continue
            if (
                rec is not None
                and rec.status in ("failed", "timeout")
                and ((d.job, dep_as_of) not in self._pending)
            ):
                self._give_up(p, now, events, f"굳은 의존 {d.job}@{dep_as_of} 가 {rec.status}")
                return False
            if rec is not None and rec.status == "skipped":
                self._give_up(p, now, events, f"굳은 의존 {d.job}@{dep_as_of} 가 실행되지 않았다")
                return False
            waiting.append(f"{d.job}@{dep_as_of}")
        if not waiting:
            return True
        if now >= p.wait_until:
            self._give_up(
                p, now, events, f"굳은 의존을 기다리다 시한을 넘겼다: {', '.join(waiting)}"
            )
            return False
        p.next_at = now + timedelta(seconds=DEP_POLL_S)
        if not p.waiting_logged:
            p.waiting_logged = True
            events.append(
                RunEvent("waiting", p.job.name, p.as_of, now, p.attempt, {"deps": waiting})
            )
        return False

    def _soft_deps(self, p: _Pending) -> dict[str, str]:
        out: dict[str, str] = {}
        for d in p.job.depends_on:
            if d.hard:
                continue
            dep = self.registry.by_name(d.job)
            try:
                dep_as_of = resolve_as_of(dep.run_as_of(), p.fire_at, self.kr, self.us)
            except AsOfUnavailable:
                out[d.job] = "n/a"
                continue
            rec = self.runs.latest(d.job, dep_as_of)
            out[d.job] = "none" if rec is None else rec.status
        return out

    def _keys(self, p: _Pending) -> list[DataKey]:
        collects = list(p.job.collects)
        for b in p.job.backfill_of:
            collects += list(self.registry.by_name(b).collects)
        keys: list[DataKey] = []
        for c in collects:
            spec = self.catalog[c.dataset_id]
            if not claimable(spec):
                continue
            if p.key_as_of is not None:
                key_as_of = p.key_as_of
            else:
                try:
                    key_as_of = resolve_as_of(c.as_of, p.fire_at, self.kr, self.us)
                except AsOfUnavailable:
                    continue
            keys += keys_for(spec, key_as_of, c.venues or None)
        return keys

    def _start(self, p: _Pending, now: datetime, events: list[RunEvent]) -> None:
        job = p.job
        handler = self.handler_for(job)
        if handler is None:  # _fire 가 처리기 없는 작업은 계획됨으로 끝냈다 — 여기 오면 버그
            raise RuntimeError(f"{job.name}: 처리기가 없는데 시작하려 했다")
        attempt = p.attempt + 1
        run_id = make_run_id(job.name, p.as_of, attempt)
        try:
            wanted_all = self._keys(p)
            # 이 시리즈가 앞 시도에서 이미 받은(done) 키는 다시 잡지 않는다 — 부분 성공 뒤 재시도가
            # 제 키를 '중복'으로 거절당하지 않게(MemoryClaimStore.refused 는 남의 키만 남는다)
            wanted = [k for k in wanted_all if k not in p.done]
            got: list[DataKey] = []
            for k in wanted:
                if self.claims.claim(k, job.name, run_id, now):
                    got.append(k)
        except Exception as e:  # 선점 저장소 오류 — 이번 시도 실패로
            p.attempt = attempt
            self._after_failure(p, now, events, run_id, _reason(e), started=None)
            return
        if wanted_all and not got:
            detail = {"reason": "duplicate", "keys": len(wanted_all)}
            self._record_final(job, p.as_of, attempt, "skipped", now, now, detail, p.source)
            events.append(RunEvent("duplicate", job.name, p.as_of, now, attempt, detail))
            del self._pending[(job.name, p.as_of)]
            return
        p.attempt = attempt
        p.keys = tuple(got)
        p.held.update(got)
        p.started_at = now
        ctx = JobContext(
            job=job.name,
            as_of=p.as_of,
            run_id=run_id,
            attempt=attempt,
            now=now,
            keys=p.keys,
            settings=self._settings,
            resources=self._resources,
        )
        detail: dict[str, Any] = {"keys": len(got)}
        if len(got) < len(wanted):
            detail["duplicate_keys"] = len(wanted) - len(got)
        if p.done:
            detail["done_keys"] = len(p.done)
        soft = self._soft_deps(p)
        if soft:
            detail["soft_deps"] = soft
        self.runs.record(
            RunRecord(run_id, job.name, p.as_of, attempt, "running", now, None, detail, p.source)
        )
        events.append(RunEvent("started", job.name, p.as_of, now, attempt, detail))
        p.future = self._submit(lambda: handler(ctx))

    def _collect(self, now: datetime, events: list[RunEvent]) -> None:
        for key in list(self._pending):
            p = self._pending.get(key)
            if p is None or p.future is None or not p.future.done():
                continue
            try:
                self._finish(p, now, events)
            except Exception as e:  # 격리 — 기록 저장소 오류 등. 이 실행은 재시도 간격 뒤 다시
                p.next_at = now + timedelta(seconds=DEP_POLL_S)
                self._job_error(now, "job_finish_failed", p.job.name, e)
        for p in list(self._abandoned):
            if p.future is not None and p.future.done():
                self._abandoned.remove(p)
                log_event(
                    log,
                    logging.WARNING,
                    SERVICE,
                    "late_result_dropped",
                    job=p.job.name,
                    as_of=p.as_of,
                )

    def _finish(self, p: _Pending, now: datetime, events: list[RunEvent]) -> None:
        fut = p.future
        if fut is None:
            return
        p.future = None
        run_id = make_run_id(p.job.name, p.as_of, p.attempt)
        try:
            result = fut.result()
        except Exception as e:  # 처리기 예외는 실패로(삼키지 않는다)
            self._after_failure(p, now, events, run_id, _reason(e), started=p.started_at)
            return
        stray = [k for k in result.collected if k not in p.keys]
        try:
            for k in result.collected:
                if k in p.keys:
                    rows = result.rows if len(p.keys) == 1 else None
                    self.claims.complete(k, run_id, rows, now)
                    p.held.discard(k)
                    p.done.add(k)
        except Exception as e:
            self._after_failure(p, now, events, run_id, _reason(e), started=p.started_at)
            return
        missing = [k for k in p.keys if k not in result.collected]
        detail: dict[str, Any] = {**dict(result.detail), "collected": len(result.collected)}
        if result.rows is not None:
            detail["rows"] = result.rows
        if stray:
            detail["stray_keys"] = len(stray)  # 선점하지 않은 키를 돌려줬다 — 반영하지 않는다
        if result.status == "ok" and not missing:
            self._release(p, now, "")
            self._record_final(p.job, p.as_of, p.attempt, "ok", p.started_at, now, detail, p.source)
            events.append(RunEvent("ok", p.job.name, p.as_of, now, p.attempt, detail))
            del self._pending[(p.job.name, p.as_of)]
            return
        if result.status == "skipped":
            self._release(p, now, "처리기가 할 일이 없다고 했다")
            self._record_final(
                p.job, p.as_of, p.attempt, "skipped", p.started_at, now, detail, p.source
            )
            events.append(RunEvent("skipped", p.job.name, p.as_of, now, p.attempt, detail))
            del self._pending[(p.job.name, p.as_of)]
            return
        why = str(result.detail.get("reason", "")) or (
            "아직 공표 전" if result.status == "not_ready" else f"키 {len(missing)}개를 받지 못했다"
        )
        self._after_failure(
            p, now, events, run_id, _reason(why), started=p.started_at, status=result.status
        )

    def _after_failure(
        self,
        p: _Pending,
        now: datetime,
        events: list[RunEvent],
        run_id: str,
        reason: str,
        *,
        started: datetime | None,
        status: str = "failed",
    ) -> None:
        job = p.job
        can_retry = bool(job.retry.backoff_s) or job.retry.every_s is not None
        next_at = now + timedelta(seconds=job.retry.delay_s(p.attempt)) if can_retry else None
        retry_ok = (
            next_at is not None
            and job.retry.allows(p.attempt, next_at.astimezone(job.schedule.zone))
            and next_at <= p.deadline
        )
        detail: dict[str, Any] = {"reason": reason, "result": status}
        if retry_ok and next_at is not None:
            detail["retry_at"] = next_at.isoformat()
            self.runs.record(
                RunRecord(
                    run_id, job.name, p.as_of, p.attempt, "failed", started, now, detail, p.source
                )
            )
            p.next_at = next_at
            events.append(RunEvent("retry", job.name, p.as_of, now, p.attempt, detail))
            return
        self._release(p, now, reason)
        self.runs.record(
            RunRecord(
                run_id, job.name, p.as_of, p.attempt, "failed", started, now, detail, p.source
            )
        )
        events.append(RunEvent("failed", job.name, p.as_of, now, p.attempt, detail))
        del self._pending[(job.name, p.as_of)]
        self._alert(job, p.as_of, reason, now)

    def _give_up(self, p: _Pending, now: datetime, events: list[RunEvent], reason: str) -> None:
        attempt = max(p.attempt, 1)
        self._release(p, now, reason)
        detail = {"reason": _reason(reason)}
        self._record_final(p.job, p.as_of, attempt, "failed", None, now, detail, p.source)
        events.append(RunEvent("failed", p.job.name, p.as_of, now, attempt, detail))
        del self._pending[(p.job.name, p.as_of)]
        self._alert(p.job, p.as_of, reason, now)

    def _timeout(self, p: _Pending, now: datetime, events: list[RunEvent]) -> None:
        reason = f"마감({p.job.deadline_min}분)을 넘겨 아직 돌고 있다"
        self._release(p, now, reason)
        detail = {"reason": reason}
        self._record_final(
            p.job, p.as_of, p.attempt, "timeout", p.started_at, now, detail, p.source
        )
        events.append(RunEvent("timeout", p.job.name, p.as_of, now, p.attempt, detail))
        del self._pending[(p.job.name, p.as_of)]
        self._abandoned.append(p)
        self._alert(p.job, p.as_of, reason, now)

    def _release(self, p: _Pending, now: datetime, reason: str) -> None:
        """쥐고 있는 키를 놓는다(실패로) — 다른 시도가 다시 잡을 수 있게."""
        run_id = make_run_id(p.job.name, p.as_of, max(p.attempt, 1))
        for k in sorted(p.held):
            try:
                self.claims.fail(k, run_id, reason or "끝남", now)
            except Exception as e:
                log_event(
                    log,
                    logging.ERROR,
                    SERVICE,
                    "claim_release_failed",
                    key=k.label(),
                    error=type(e).__name__,
                )
        p.held.clear()

    # ── 기록·알림 ────────────────────────────────────────────────────────────────────

    def _record_final(
        self,
        job: JobSpec,
        as_of: str,
        attempt: int,
        status: Literal["ok", "failed", "skipped", "timeout"],
        started: datetime | None,
        now: datetime,
        detail: Mapping[str, Any],
        source: RunSource,
    ) -> None:
        rec = RunRecord(
            make_run_id(job.name, as_of, attempt),
            job.name,
            as_of,
            attempt,
            status,
            started,
            now,
            dict(detail),
            source,
        )
        try:
            self.runs.record(rec)
        except Exception as e:  # 기록 실패가 실행기를 멈추지 않는다 — health 로
            self._emit(now, "job_run_record_failed", f"{job.name}@{as_of}: {_reason(e)}")

    def _alert(self, job: JobSpec, as_of: str, reason: str, now: datetime) -> None:
        text = f"[작업 실패] {job.name} @ {as_of}\n{_reason(reason)}"
        self._emit(now, "job_failed", f"{job.name}@{as_of}: {_reason(reason)}")
        if self._notify is None:
            return
        try:
            ticket = self._notify(
                text,
                kind="ops.job_failed",
                source=SERVICE,
                subject=f"{job.name}:{as_of}",
                as_of=_as_date(as_of),
            )
        except Exception as e:  # 알림 실패도 health 로 남긴다(삼키지 않는다)
            self._emit(now, "job_failed_notify_failed", f"{job.name}@{as_of}: {_reason(e)}")
            return
        # notifier 는 예외 대신 ok=False 표(NotifyTicket)로 실패를 알린다 — 그것도 health 로
        if getattr(ticket, "ok", True) is False:
            why = str(getattr(ticket, "reason", "") or "알림을 넣지 못했다")
            self._emit(now, "job_failed_notify_failed", f"{job.name}@{as_of}: {_reason(why)}")

    def _job_error(self, now: datetime, kind: str, job: str, e: BaseException) -> None:
        """실행기 내부 오류(저장소·캘린더 등) — 작업·종류마다 60초에 한 번 health·로그."""
        last = self._error_at.get((kind, job))
        if last is not None and (now - last).total_seconds() < 60:
            return
        self._error_at[(kind, job)] = now
        log_event(log, logging.ERROR, SERVICE, kind, job=job, error=type(e).__name__)
        self._emit(now, kind, f"{job}: {_reason(e)}")

    def _emit(self, now: datetime, kind: str, detail: str) -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, now, "warning", service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)
