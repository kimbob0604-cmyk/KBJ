"""1주 무중단 판정 시뮬레이션 — 운영 등록부로 7일을 돌린 기록에 `ops week` 를 대고 손계산과 맞춘다
(docs/p9_design.md §6.2·§11 Q6, ADR 0037).

- 7일 = 2026-10-12(월)~18(일), 거래일 5일. 시계는 가짜(5분 칸) — 벽시계·네트워크를 쓰지 않는다.
- 작업: 운영 등록부(`config/jobs.yaml`)의 켜진 cron 작업을 cron·when 조건대로 발화시켜 실행 기록을
  만든다(처리기는 돌리지 않는다 — 판정은 기록만 본다). 브리핑 작업이 돌면 그 종류 발송 기록 1통.
- 하트비트: 켜진 상시 서비스가 계속 박동하고, 변형이 넣은 끊김 동안만 멈춘다. 워치독 대역
  (`record_heartbeat_gaps`)이 5분마다 나이를 보고 `heartbeat_gap` 관측을 남긴다.
- 변형: 무중단(통과) · 7분 끊김(기준 안 — 통과) · 15분 끊김 하나(그날 실패) · 최종 실패 하나(그날
  실패) · 재시도 성공(통과).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final

import pytest

from kbj.config.files import REPO_ROOT
from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.time import KST
from kbj.services.ops.check import run_week
from kbj.services.ops.source import (
    JobRunRow,
    MemoryOpsSource,
    NotifyRow,
    load_expect,
    record_heartbeat_gaps,
)
from kbj.services.scheduler.conditions import holds
from kbj.services.scheduler.registry import JobSpec, Registry

pytestmark = pytest.mark.sim

START: Final = date(2026, 10, 12)
END: Final = date(2026, 10, 18)
TICK: Final = timedelta(minutes=5)
JOBS_YAML: Final = REPO_ROOT / "config" / "jobs.yaml"
KR = TradingCalendar.default()
US = us_calendar()


@dataclass(frozen=True)
class Outage:
    beat: str
    start: datetime
    minutes: int


@dataclass(frozen=True)
class Variant:
    outages: tuple[Outage, ...] = ()
    fail: tuple[str, date] | None = None  # (작업, 날) — 재시도 끝까지 실패
    retry_ok: tuple[str, date] | None = None  # (작업, 날) — 1회 실패 뒤 성공


def _kst(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


def _days() -> Iterator[date]:
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def _firings(job: JobSpec) -> Iterator[datetime]:
    spec = job.schedule.cron_spec()
    if spec is None or job.external:
        return
    for d in _days():
        if not spec.day_matches(d):
            continue
        if not holds(job.schedule.when, d, KR, US, month_days=job.schedule.month_days):
            continue
        for t in spec.times_on(d):
            yield datetime.combine(d, t, tzinfo=job.schedule.zone)


def simulate(v: Variant) -> MemoryOpsSource:
    reg = Registry.load(JOBS_YAML)
    expect = load_expect(JOBS_YAML)
    src = MemoryOpsSource()
    for job in reg.enabled_jobs():
        for at in _firings(job):
            day = at.astimezone(KST).date()
            as_of = at.isoformat(timespec="minutes")
            attempts = ["ok"]
            if v.fail == (job.name, day):
                attempts = ["failed"] * (job.retry.max + 1)
            elif v.retry_ok == (job.name, day):
                attempts = ["failed", "ok"]
            for n, status in enumerate(attempts, start=1):
                started = at + timedelta(minutes=5 * (n - 1))
                src.runs.append(
                    JobRunRow(job.name, as_of, n, status, started,  # pyright: ignore[reportArgumentType]
                              started + timedelta(minutes=1), started)
                )  # fmt: skip
            if (
                attempts[-1] == "ok"
                and job.notify is not None
                and job.notify.kind.startswith("brief.")
            ):
                sent = at + timedelta(minutes=2)
                src.notify.append(NotifyRow(job.notify.kind, None, "sent", sent, sent, as_of=day))

    def age(beat: str, now: datetime) -> float | None:
        last = now
        for o in v.outages:
            if o.beat == beat and o.start <= now < o.start + timedelta(minutes=o.minutes):
                last = o.start
        return (now - last).total_seconds() + 5.0  # 박동 주기 10초 — 평균 5초 전

    now = _kst(START, time(0, 0))
    end = _kst(END + timedelta(days=1), time(0, 0))
    while now < end:
        record_heartbeat_gaps(expect.services, age, now, src)
        now += TICK
    return src


def _week(v: Variant) -> tuple[bool, list[date]]:
    w = run_week(simulate(v), load_expect(JOBS_YAML), KR, END)
    assert w.trading_days == 5  # 손계산: 10/12~16 평일, 휴일 없음
    return w.passed, [d.day for d in w.days if not d.passed]


def test_week_without_trouble_passes() -> None:
    src = simulate(Variant())
    w = run_week(src, load_expect(JOBS_YAML), KR, END)
    assert w.passed and w.exit_code == 0
    assert src.gaps == []  # 끊김 없음 — 관측 0
    for d in w.days:  # 손계산: 거래일마다 아침·마감 1통, 주말은 마감 0
        assert d.final_failures == 0 and d.long_gaps == 0
        assert d.closing_sent == (1 if d.trading else 0)
        if d.trading:
            assert d.morning_sent == 1


def test_short_outage_within_limit_passes() -> None:
    o = Outage("notifier", _kst(date(2026, 10, 14), time(10, 2)), 7)
    assert _week(Variant(outages=(o,))) == (True, [])


def test_one_long_outage_fails_that_day() -> None:
    o = Outage("notifier", _kst(date(2026, 10, 14), time(10, 2)), 15)
    assert _week(Variant(outages=(o,))) == (False, [date(2026, 10, 14)])


def test_overnight_outage_counts_on_last_seen_day() -> None:
    o = Outage("auth", _kst(date(2026, 10, 17), time(23, 50)), 30)
    assert _week(Variant(outages=(o,))) == (False, [date(2026, 10, 18)])


def test_one_final_failure_fails_that_day() -> None:
    assert _week(Variant(fail=("brief.closing", date(2026, 10, 15)))) == (
        False,
        [date(2026, 10, 15)],
    )


def test_retry_success_passes() -> None:
    assert _week(Variant(retry_ok=("brief.morning", date(2026, 10, 13)))) == (True, [])
