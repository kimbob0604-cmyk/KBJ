"""운영 점검 시험용 합성 세계 — 7일치 작업 실행·발송 기록·하트비트(docs/p9_design.md §6.2).

합성 값만(이름·상태·시각·개수). 외부 원천·시세가 없다. 고정 날짜 2026-10-12(월)~10-18(일) —
거래일 5일(기본 달력). `tests/unit/api/web_fixtures_p13`·`tests/sim/test_ops_week.py` 도 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Final

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.ops.source import (
    Expect,
    GapEvent,
    JobRunRow,
    MemoryOpsSource,
    NotifyRow,
    ServiceExpect,
    TableSize,
)

WEEK_START: Final = date(2026, 10, 12)
WEEK_END: Final = date(2026, 10, 18)
# 작은 기대값 — 서비스 셋·작업 넷(브리핑 둘 + 백업 + 워치독)
SERVICES: Final = (
    ServiceExpect("auth", "auth", 180),
    ServiceExpect("notifier", "notifier", 60),
    ServiceExpect("gex.engine", "engine", 60),
)
DAILY_JOBS: Final[dict[str, time]] = {  # 작업 → 시작 시각(KST) — 매일
    "ops.nightly": time(3, 0),
}
TRADING_JOBS: Final[dict[str, time]] = {  # 거래일만
    "brief.morning": time(8, 10),
    "board.daily": time(15, 45),
    "brief.closing": time(16, 0),
}
EXPECT: Final = Expect(
    services=SERVICES,
    jobs=frozenset({*DAILY_JOBS, *TRADING_JOBS}),
    deadline_min={"ops.nightly": 60, "brief.morning": 50, "board.daily": 30, "brief.closing": 30},
)


def kst(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


@dataclass
class Ages:
    """가짜 하트비트 — 하트비트 이름 → 마지막 박동 시각(없으면 없음)."""

    last: dict[str, datetime] = field(default_factory=dict[str, datetime])

    def __call__(self, beat: str, now: datetime) -> float | None:
        at = self.last.get(beat)
        return None if at is None else (now - at).total_seconds()


def fresh_ages(now: datetime, *, stale: dict[str, float] | None = None) -> Ages:
    """모든 서비스가 5초 전에 박동(stale 은 이름 → 나이 초)."""
    ages = Ages({s.beat: now - timedelta(seconds=5) for s in SERVICES})
    for beat, age in (stale or {}).items():
        ages.last[beat] = now - timedelta(seconds=age)
    return ages


def run_row(job: str, d: date, t: time, status: str = "ok", attempt: int = 1) -> JobRunRow:
    start = kst(d, t) + timedelta(minutes=5 * (attempt - 1))
    return JobRunRow(
        job=job,
        as_of=d.isoformat(),
        attempt=attempt,
        status=status,  # pyright: ignore[reportArgumentType]
        started_at=start,
        finished_at=None if status == "running" else start + timedelta(minutes=2),
        created_at=start,
    )


def brief_row(kind: str, d: date, t: time, status: str = "sent") -> NotifyRow:
    at = kst(d, t)
    return NotifyRow(
        kind=kind,
        subject=None,
        status=status,
        requested_at=at,
        sent_at=at + timedelta(seconds=3) if status == "sent" else None,
        as_of=d,
        parts=2,
        reason=None if status == "sent" else "telegram 429",
    )


def days(start: date = WEEK_START, end: date = WEEK_END) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def week_source(
    cal: TradingCalendar | None = None,
    *,
    start: date = WEEK_START,
    end: date = WEEK_END,
) -> MemoryOpsSource:
    """무중단 1주 — 모든 작업 성공, 거래일마다 브리핑 1통씩, 끊김 없음."""
    c = cal or TradingCalendar.default()
    src = MemoryOpsSource(
        sizes=[
            TableSize("prv_gex", "tick_raw", 7 * 2**30),
            TableSize("prv_market", "minute_bar", 3 * 2**30),
            TableSize("prv_board", "px", 900 * 2**20),
            TableSize("ops", "job_run", 12 * 2**20),
        ],
        db_bytes=12 * 2**30,
    )
    for d in days(start, end):
        for job, t in DAILY_JOBS.items():
            src.runs.append(run_row(job, d, t))
        if not c.is_trading_day(d):
            continue
        for job, t in TRADING_JOBS.items():
            src.runs.append(run_row(job, d, t))
        src.notify.append(brief_row("brief.morning", d, time(8, 11)))
        src.notify.append(brief_row("brief.closing", d, time(16, 2)))
        src.notify.append(
            NotifyRow("alert.rule", "E1", "sent", kst(d, time(10, 31)), kst(d, time(10, 31)))
        )
    return src


def add_gap(src: MemoryOpsSource, service: str, start: datetime, minutes: int) -> None:
    """워치독이 5분마다 본 끊김 — start 부터 minutes 분(관측 나이는 그때까지 지난 초)."""
    t = start + timedelta(minutes=5)
    while t <= start + timedelta(minutes=minutes):
        src.gaps.append(GapEvent(service, t, (t - start).total_seconds()))
        t += timedelta(minutes=5)
