"""scheduler 무결측 판정 구동 — 세션이 끝나고 분봉 적재 뒤 공백·리포트 (docs/phase1_design.md §9).

판정 계산은 services/gaps.py(`evaluate_session`), 읽기·쓰기는 스풀 없는 저장소(`GapStore` —
data/store.py `PostgresSink` 의 판정 입력 읽기 + `replace_gap_report`).

- 언제(`GapDailyConfig`, 시각은 KST — 분봉 적재 창과 같다, 설계 §8): 거래일 T 의 16:00~17:50
  (POST_DAY)에 T 주간, 06:10~08:00(IDLE)에 T 로 귀속되는 밤(열린 밤만 — 금요일 밤은 월요일
  06:10). 선물 체결 공백은 분봉과 대조하므로 그 세션 분봉 적재(`MinuteDaily`)가 끝난 뒤에 본다.
  적재가 끝나지 않으면 `fallback`(17:20·07:30 **[확인 필요]**)에 판정하고 선물 체결은
  판정 불가(`unverified` — 분봉 partial 로 본다). 분봉 적재를 하지 않는 조립(KIS 앱키 없음)이면
  창이 열리자마자 판정하고 DB 에 있는 분봉으로 대조한다(없으면 unverified)
- 한 번 = 작업 스레드에서 입력 읽기 → 판정 → 그 세션의 collection_gaps·collection_reports 교체
  (트랜잭션 하나 — 다시 판정해도 옛 행이 남지 않는다). step 은 끝났는지만 본다
- 결과: 스트림마다 구조화 로그 `gap_stream_report`(기대·실수신·공백 수·최대 공백·판정·필수),
  세션 요약 health — 필수 스트림이 모두 ok 면 info `nogap_session_ok`, 공백이 있으면 warning
  `collection_gaps_found`, 공백은 없고 판정 불가가 있으면 warning `nogap_unverified`
- 실패(DB 읽기·쓰기)면 warning `gap_report_failed` + `retry_s`(5분) 뒤 다시, 다음 시도가 창 끝을
  넘으면 그 세션은 그만 + warning `gap_report_missing`. 재기동하면 창 안에서 다시 판정한다(멱등).
  창을 통째로 놓친 세션은 scripts/nogap_report.py `--recompute` 로 판정할 수 있다
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.calendar import KST, TradingCalendar
from data.store import CollectionGapRecord, CollectionReportRecord
from services.auth.health import HealthEvent, HealthSink, Severity
from services.gaps import (
    GapConfig,
    GapReader,
    MinuteStatus,
    Session,
    SessionReport,
    evaluate_session,
    load_inputs,
    session_span,
)
from services.runtime import log_event, run_in_thread
from services.scheduler.minute import MinuteDaily

SERVICE = "scheduler"

log = logging.getLogger("services.scheduler.gaps")


class GapDailyConfig(BaseModel):
    """판정 구동 시각(KST)·재시도. 창은 분봉 적재(scheduler/minute.py `MinuteConfig`)와 같다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    day_start: time = time(16, 0)
    day_fallback: time = time(17, 20)  # 확인 필요: 분봉 적재가 안 끝나도 이때 판정
    day_end: time = time(17, 50)
    night_start: time = time(6, 10)
    night_fallback: time = time(7, 30)
    night_end: time = time(8, 0)
    retry_s: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> GapDailyConfig:
        if not (self.day_start <= self.day_fallback < self.day_end):
            raise ValueError("day_start <= day_fallback < day_end")
        if not (self.night_start <= self.night_fallback < self.night_end):
            raise ValueError("night_start <= night_fallback < night_end")
        return self


class GapStore(GapReader, Protocol):
    def replace_gap_report(
        self,
        trade_date: date,
        session: str,
        gaps: Sequence[CollectionGapRecord],
        reports: Sequence[CollectionReportRecord],
    ) -> None: ...


class Submit(Protocol):
    def __call__(self, fn: Callable[[], SessionReport], /) -> Future[SessionReport]: ...


def gap_thread_submit(fn: Callable[[], SessionReport], /) -> Future[SessionReport]:
    return run_in_thread(fn, name="gap-report")


@dataclass
class GapJob:
    """세션 하나의 판정 — 귀속 거래일·세션, 창 끝, 다음 시도."""

    trade_date: date
    session: Session
    start_date: date  # 세션이 시작한 달력일 (분봉 적재 키)
    fallback: datetime
    deadline: datetime
    next_try: datetime
    attempts: int = 0
    finished: bool = False
    report: SessionReport | None = None

    @property
    def key(self) -> tuple[date, Session]:
        return self.trade_date, self.session

    def label(self) -> str:
        return f"{self.trade_date} {'주간' if self.session == 'day' else '야간'}"


def _kst(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


def run_report(
    store: GapStore,
    cal: TradingCalendar,
    trade_date: date,
    session: Session,
    now: datetime,
    *,
    config: GapConfig | None = None,
    minute_status: MinuteStatus | None = None,
    write: bool = True,
) -> SessionReport:
    """세션 하나를 읽어 판정하고(write 면) 교체해 쓴다. 저장 오류는 예외 그대로."""
    span = session_span(trade_date, session, cal)
    if span is None:
        raise ValueError(f"{trade_date} {session}: 캘린더상 열리지 않은 세션")
    inputs = load_inputs(store, trade_date, session, span)
    report = evaluate_session(inputs, cal, trade_date, session, config, minute_status=minute_status)
    if write:
        store.replace_gap_report(
            trade_date, session, report.gap_records(now), report.report_records(now)
        )
    return report


def log_report(report: SessionReport) -> None:
    """스트림마다 구조화 로그 한 줄."""
    tag = (report.trade_date, report.session)
    for s in report.streams:
        log_event(
            log,
            logging.INFO if s.status == "ok" or not s.required else logging.WARNING,
            SERVICE,
            "gap_stream_report",
            tag,
            stream=s.stream,
            status=s.status,
            required=s.required,
            expected=s.expected,
            received=s.received,
            gaps=len(s.gaps),
            max_gap_s=s.max_gap_s,
            span=[s.span_start.isoformat(), s.span_end.isoformat()],
            reason=s.detail.get("reason"),
        )


def summarize(report: SessionReport) -> tuple[str, Severity, str]:
    """세션 요약 health (종류, 수준, 문장)."""
    name = f"{report.trade_date} {'주간' if report.session == 'day' else '야간'}"
    req = [s for s in report.streams if s.required]
    gapped = [s for s in req if s.status == "gaps"]
    unknown = [s for s in req if s.status == "unverified"]
    if gapped:
        worst = max(s.max_gap_s or 0.0 for s in gapped)
        heads = ", ".join(f"{s.stream}({len(s.gaps)})" for s in gapped[:5])
        more = f" 외 {len(gapped) - 5}" if len(gapped) > 5 else ""
        msg = f"{name} 필수 스트림 공백 — {heads}{more}, 최대 {worst:g}초"
        return "collection_gaps_found", "warning", msg
    if unknown:
        heads = ", ".join(f"{s.stream}: {s.detail.get('reason', '?')}" for s in unknown[:3])
        return "nogap_unverified", "warning", f"{name} 판정 불가 스트림 — {heads}"
    return "nogap_session_ok", "info", f"{name} 무결측 — 필수 스트림 {len(req)}개 공백 0"


class GapDaily:
    """거래일 T 의 창에서 세션 하나를 판정한다 — scheduler 가 1초마다 `step(now)`."""

    def __init__(
        self,
        store: GapStore,
        calendar: TradingCalendar,
        health: HealthSink,
        *,
        minute: MinuteDaily | None = None,
        config: GapDailyConfig | None = None,
        gap_config: GapConfig | None = None,
        submit: Submit = gap_thread_submit,
    ) -> None:
        self._store = store
        self._cal = calendar
        self._health = health
        self._minute = minute
        self.cfg = config or GapDailyConfig()
        self.gap_cfg = gap_config or GapConfig()
        self._submit = submit
        self.job: GapJob | None = None
        self.jobs: list[GapJob] = []  # 최근 것 (시험·진단)
        self._inflight: tuple[GapJob, Future[SessionReport]] | None = None

    def step(self, now: datetime) -> None:
        self._collect(now)
        job = self._current(now)
        if job is None or job.finished or self._inflight is not None or now < job.next_try:
            return
        ready, status = self._minute_state(job, now)
        if not ready:
            return
        self._start(now, job, status)

    # ── 언제 ──

    def _current(self, now: datetime) -> GapJob | None:
        k = now.astimezone(KST)
        d, t = k.date(), k.time()
        if not self._cal.is_trading_day(d):
            return None
        c = self.cfg
        if c.day_start <= t < c.day_end:
            return self._job(now, d, "day", d, _kst(d, c.day_fallback), _kst(d, c.day_end))
        if c.night_start <= t < c.night_end and session_span(d, "night", self._cal) is not None:
            start = self._cal.prev_trading_day(d)
            return self._job(
                now, d, "night", start, _kst(d, c.night_fallback), _kst(d, c.night_end)
            )
        return None

    def _job(
        self,
        now: datetime,
        trade_date: date,
        session: Session,
        start: date,
        fallback: datetime,
        deadline: datetime,
    ) -> GapJob:
        if self.job is None or self.job.key != (trade_date, session):
            self.job = GapJob(trade_date, session, start, fallback, deadline, next_try=now)
            self.jobs = [*self.jobs[-7:], self.job]
        return self.job

    def _minute_state(self, job: GapJob, now: datetime) -> tuple[bool, MinuteStatus | None]:
        """(판정해도 되나, 분봉 적재 결과). 적재가 없으면 기다리지 않는다."""
        if self._minute is None:
            return True, None
        mj = self._minute.session_job(job.session, job.start_date)
        if mj is not None and mj.finished:
            return True, mj.status
        if now >= job.fallback:
            return True, "partial" if mj is not None else "missing"
        return False, None

    # ── 한 번 ──

    def _start(self, now: datetime, job: GapJob, status: MinuteStatus | None) -> None:
        job.attempts += 1
        store, cal, cfg = self._store, self._cal, self.gap_cfg

        def work() -> SessionReport:
            return run_report(
                store, cal, job.trade_date, job.session, now, config=cfg, minute_status=status
            )

        try:
            fut = self._submit(work)
        except Exception as e:  # 스레드를 못 띄웠다 — 실패처럼 다시
            self._failed(now, job, f"시작 실패: {type(e).__name__}")
            return
        self._inflight = (job, fut)
        self._collect(now)

    def _collect(self, now: datetime) -> None:
        if self._inflight is None:
            return
        job, fut = self._inflight
        if not fut.done():
            return
        self._inflight = None
        try:
            report = fut.result()
        except Exception as e:
            text = str(e).splitlines()[0][:160] if str(e) else ""
            self._failed(now, job, f"{type(e).__name__}: {text}")
            return
        job.finished = True
        job.report = report
        log_report(report)
        kind, severity, msg = summarize(report)
        self._emit(now, kind, msg, severity)

    def _failed(self, now: datetime, job: GapJob, detail: str) -> None:
        nxt = now + timedelta(seconds=self.cfg.retry_s)
        if nxt >= job.deadline:
            job.finished = True
            msg = f"{job.label()} 무결측 판정을 그만둔다(시도 {job.attempts}회) — {detail}"
            self._emit(now, "gap_report_missing", msg, "warning")
            return
        job.next_try = nxt
        again = f"{self.cfg.retry_s / 60:g}분 뒤 다시"
        self._emit(now, "gap_report_failed", f"{job.label()} 무결측 판정 실패: {detail} — {again}")

    def _emit(self, now: datetime, kind: str, detail: str, severity: Severity = "warning") -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)
