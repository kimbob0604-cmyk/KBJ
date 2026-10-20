"""`/api/ops/*` 리더 — 운영 점검(`kbj.services.ops.check`)과 **같은 함수**로 페이지 13 표 셋을
만든다(docs/p9_design.md §6.2, §11 Q5, ADR 0037).

- 읽기만(DB `ops.*`·Redis 하트비트). 외부 원천을 부르지 않는다(계약 ⑩).
- 운영 원천(`OpsContext`)은 앱마다 한 번 만든다: 시험·데모는 `app.state.kbj_ops` 에 넣어 두고,
  운영은 `KBJ_DATABASE_URL` 로 `PgOpsSource`·Redis 하트비트·`config/jobs.yaml` 기대값을 묶는다.
  DB 접속 문자열이 없으면 404 `no_data`(화면 `.msg` — 실패가 아니다).
- 발송 기록은 종류·상태·시각·조각 수·가린 사유만(본문·subject 없음).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.core.calendar import TradingCalendar
from kbj.core.masking import mask_text
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.ops import (
    OpsCheckItem,
    OpsJobRow,
    OpsJobs,
    OpsNotify,
    OpsNotifyRow,
    OpsService,
    OpsStatus,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope
from kbj.services.ops.check import HeartbeatAge, job_outcomes, run_check
from kbj.services.ops.source import Expect, OpsSource

__all__ = [
    "SOURCE",
    "STATE_ATTR",
    "OpsContext",
    "ops_context",
    "ops_jobs",
    "ops_notify",
    "ops_status",
]

SOURCE: Final = "KBJ 운영 기록"
STATE_ATTR: Final = "kbj_ops"  # app.state 에 둘 이름(시험·데모가 미리 넣는다)
_LOCK = threading.Lock()


@dataclass(frozen=True)
class OpsContext:
    source: OpsSource
    expect: Expect
    heartbeat_age: HeartbeatAge
    cal: TradingCalendar | None = None  # None = 읽기 문맥의 달력


def ops_context(app_state: Any, build: Callable[[], OpsContext | None]) -> OpsContext:
    """앱에 한 번 — 이미 있으면 그것, 없으면 `build()`(None 이면 404 no_data)."""
    with _LOCK:
        ctx = getattr(app_state, STATE_ATTR, None)
        if isinstance(ctx, OpsContext):
            return ctx
        ctx = build()
        if ctx is None:
            raise NoData("운영 기록 없음 — DB 접속 설정(KBJ_DATABASE_URL)이 없는 실행")
        setattr(app_state, STATE_ATTR, ctx)
        return ctx


def _env[T](read: ReadContext, data: T, now: datetime) -> Envelope[T]:
    return envelope(data, source=SOURCE, as_of=now, quality=Quality.OK, notes=(), generated_at=now)


def ops_status(read: ReadContext, ops: OpsContext) -> Envelope[OpsStatus]:
    """지금 하트비트 + 최근 24시간 기록 — `python -m kbj.services.ops check` 와 같은 판정."""
    now = read.now()
    r = run_check(ops.source, ops.expect, ops.cal or read.cal, ops.heartbeat_age, now)
    data = OpsStatus(
        level=r.level,
        since=r.since,
        services=[
            OpsService(
                name=s.name,
                heartbeat_s=s.heartbeat_s,
                age_s=None if s.age_s is None else round(s.age_s),
                level=s.level,
            )
            for s in r.services
        ],
        items=[
            OpsCheckItem(key=i.key, title=i.title, level=i.level, value=i.value, verdict=i.verdict)
            for i in r.items
        ],
    )
    return _env(read, data, now)


def _day_range(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    return start, start + timedelta(days=1)


def _day(read: ReadContext, day: date | None) -> date:
    return day if day is not None else read.today()


def ops_jobs(read: ReadContext, ops: OpsContext, day: date | None = None) -> Envelope[OpsJobs]:
    """그날(KST) 켜진 작업의 (작업, as_of) 마다 마지막 시도."""
    d = _day(read, day)
    start, end = _day_range(d)
    outs = [o for o in job_outcomes(ops.source.job_runs(start, end), ops.expect.jobs) if o.day == d]
    rows = [
        OpsJobRow(
            job=o.job,
            as_of=o.as_of,
            attempts=o.attempts,
            status=o.status,
            started_at=o.started_at,
            finished_at=o.finished_at,
            retried_ok=o.retried_ok,
            reason=None if o.reason is None else mask_text(o.reason),
            manual=o.manual,
        )
        for o in outs
    ]
    data = OpsJobs(
        date=d,
        total=len(rows),
        failed=sum(1 for o in outs if o.final_failure),
        retried_ok=sum(1 for o in outs if o.retried_ok),
        rows=rows,
    )
    return _env(read, data, read.now())


def ops_notify(read: ReadContext, ops: OpsContext, day: date | None = None) -> Envelope[OpsNotify]:
    """그날(KST) 요청된 발송 기록(시각순)."""
    d = _day(read, day)
    start, end = _day_range(d)
    notes = sorted(ops.source.notifies(start, end), key=lambda n: n.requested_at)
    rows = [
        OpsNotifyRow(
            kind=n.kind,
            status=n.status,
            requested_at=n.requested_at,
            sent_at=n.sent_at,
            parts=n.parts,
            reason=None if n.reason is None else mask_text(n.reason),
        )
        for n in notes
    ]
    data = OpsNotify(
        date=d,
        sent=sum(1 for n in notes if n.status == "sent"),
        failed=sum(1 for n in notes if n.status == "failed"),
        rows=rows,
    )
    return _env(read, data, read.now())
