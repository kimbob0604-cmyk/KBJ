"""운영 점검·1주 판정 — `python -m kbj.services.ops check|week` 와 `/api/ops/*` 가 같은 함수를 쓴다
(docs/p9_design.md §6.1·§6.2, §11 Q6, ADR 0037).

판정 기준(§11 Q6 — 본문 §6.1 보다 우선):
1. 켜진 상시 서비스의 하트비트 끊김 중 **10분(600초) 넘는 것 0**.
2. 켜진 작업의 **최종 실패 0**(재시도로 성공한 것은 '재시도 성공' 으로 셈만, timeout 은 실패).
3. 거래일마다 아침 브리핑·마감 브리핑 **1통씩**(`ops.notify_log` status sent), 발송 실패 0.

판정에 들지 않는 참고(WARN 만 — 종료 코드를 바꾸지 않는다): 수집 결측·격리 수, 마지막 야간 백업
시각, 디스크 여유, 표 크기 상위 10(압축 판단 — §0.4 #15).

- 시각은 모두 주입한다(`now` — 벽시계 금지). 하루 = KST 0시~24시.
- 출력은 이름·상태·개수·시각뿐 — 시세·키·접속 문자열이 없다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final, Literal

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.ops.source import (
    BRIEF_KINDS,
    Expect,
    GapEvent,
    JobRunRow,
    NotifyRow,
    OpsSource,
    TableSize,
)

__all__ = [
    "BACKUP_JOB",
    "GAP_LIMIT_S",
    "CheckItem",
    "CheckReport",
    "DayVerdict",
    "JobOutcome",
    "Level",
    "Outage",
    "ServiceState",
    "WeekReport",
    "brief_counts",
    "job_outcomes",
    "outages",
    "run_check",
    "run_week",
    "service_states",
]

Level = Literal["OK", "WARN", "FAIL"]
GAP_LIMIT_S: Final = 600  # §11 Q6 — 10분 넘는 끊김
BACKUP_JOB: Final = "ops.nightly"
BACKUP_STALE_H: Final = 26  # 03:00 백업 + 여유
DISK_WARN_RATIO: Final = 0.10
TOP_TABLES: Final = 10
WEEK_DAYS: Final = 7
MIN_TRADING_DAYS: Final = 5
_FINAL_FAIL: Final = frozenset({"failed", "timeout"})
_FINAL: Final = frozenset({"ok", "failed", "skipped", "timeout"})
_SAME_OUTAGE_S: Final = 120.0  # 끊긴 시작 시각이 이만큼 안이면 같은 끊김

HeartbeatAge = Callable[[str, datetime], float | None]
DiskUsage = Callable[[], tuple[int, int] | None]  # (여유 바이트, 전체 바이트)


def _kst_day(ts: datetime) -> date:
    return ts.astimezone(KST).date()


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0), tzinfo=KST)
    return start, start + timedelta(days=1)


# ── 서비스 상태(지금) ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceState:
    name: str
    beat: str
    heartbeat_s: int
    age_s: float | None  # None = 하트비트 없음
    level: Level


def service_states(
    expect: Expect, heartbeat_age: HeartbeatAge, now: datetime
) -> list[ServiceState]:
    """켜진 상시 서비스마다 지금 하트비트 나이 — 기준 안 OK, 10분 안 WARN, 넘거나 없으면 FAIL."""
    out: list[ServiceState] = []
    for s in expect.services:
        age = heartbeat_age(s.beat, now)
        if age is not None and age <= s.heartbeat_s:
            level: Level = "OK"
        elif age is not None and age <= GAP_LIMIT_S:
            level = "WARN"
        else:
            level = "FAIL"
        out.append(ServiceState(s.name, s.beat, s.heartbeat_s, age, level))
    return out


# ── 하트비트 끊김(기록) ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outage:
    """같은 끊김을 묶은 것 — start(추정 시작)·last_seen(마지막 관측)·longest_s(본 나이 중 최대)."""

    service: str
    start: datetime
    last_seen: datetime
    longest_s: float | None  # None = 하트비트가 아예 없어 길이를 모른다

    @property
    def over_limit(self) -> bool:
        """10분을 넘었는가 — 길이를 모르면(하트비트 없음) 넘은 것으로 친다(관대하게 보지 않는다)."""
        return self.longest_s is None or self.longest_s > GAP_LIMIT_S


def outages(events: Iterable[GapEvent]) -> list[Outage]:
    """관측(ts, 나이) → 끊김. 시작 시각(ts − 나이)이 120초 안이면 같은 끊김으로 묶는다.

    나이를 모르는 관측(하트비트 없음)은 바로 앞 끊김에 이어 붙이고(앞이 없으면 새 끊김), 길이를
    모름으로 둔다.
    """
    by_svc: dict[str, list[GapEvent]] = defaultdict(list)
    for ev in events:
        by_svc[ev.service].append(ev)
    out: list[Outage] = []
    for svc in sorted(by_svc):
        cur: Outage | None = None
        for ev in sorted(by_svc[svc], key=lambda e: e.ts):
            start = ev.ts if ev.age_s is None else ev.ts - timedelta(seconds=ev.age_s)
            same = cur is not None and (
                ev.age_s is None
                or cur.longest_s is None
                or abs((start - cur.start).total_seconds()) <= _SAME_OUTAGE_S
            )
            if cur is not None and same:
                longest = (
                    None
                    if ev.age_s is None or cur.longest_s is None
                    else max(cur.longest_s, ev.age_s)
                )
                cur = Outage(svc, min(cur.start, start), ev.ts, longest)
                out[-1] = cur
            else:
                cur = Outage(svc, start, ev.ts, ev.age_s)
                out.append(cur)
    return out


# ── 작업 ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JobOutcome:
    """(작업, as_of) 하나의 최종 결과."""

    job: str
    as_of: str
    attempts: int
    status: str  # 마지막 시도의 상태
    started_at: datetime | None
    finished_at: datetime | None
    day: date  # 마지막 시도를 센 날(KST)
    reason: str | None = None  # 마지막 시도의 사유(실패·건너뜀 — 가린 짧은 문장)
    manual: bool = False  # 마지막 시도가 수동(kbj.sh job)

    @property
    def final_failure(self) -> bool:
        return self.status in _FINAL_FAIL

    @property
    def retried_ok(self) -> bool:
        return self.status == "ok" and self.attempts > 1


def job_outcomes(runs: Iterable[JobRunRow], enabled: Iterable[str]) -> list[JobOutcome]:
    """실행기(scheduler)가 켜진 작업을 돌린 (작업, as_of) 만 — 마지막 시도(시도 번호가 가장 큰
    줄)는 출처를 가리지 않는다: 실행기가 실패한 뒤 수동으로 다시 돌려 성공했으면 성공이다
    (2026-10-20 점검 — 수동 재실행을 빼고 보아 고친 실패가 '실패'로 남았다)."""
    on = frozenset(enabled)
    last: dict[tuple[str, str], JobRunRow] = {}
    tries: dict[tuple[str, str], int] = defaultdict(int)
    by_scheduler: set[tuple[str, str]] = set()
    for r in runs:
        if r.job not in on:
            continue
        key = (r.job, r.as_of)
        if r.source == "scheduler":
            by_scheduler.add(key)
        tries[key] += 1
        cur = last.get(key)
        if cur is None or (r.attempt, r.at) > (cur.attempt, cur.at):
            last[key] = r
    out = [
        JobOutcome(
            r.job, r.as_of, max(r.attempt, tries[k]), r.status, r.started_at, r.finished_at,
            _kst_day(r.at), r.reason, r.source != "scheduler",
        )
        for k, r in last.items()
        if k in by_scheduler
    ]  # fmt: skip
    floor = datetime.min.replace(tzinfo=KST)
    return sorted(out, key=lambda o: (o.started_at or floor, o.job, o.as_of))


def _overdue(runs: Iterable[JobRunRow], expect: Expect, now: datetime) -> int:
    n = 0
    for r in runs:
        if r.status != "running" or r.started_at is None or r.job not in expect.jobs:
            continue
        if now - r.started_at > timedelta(minutes=expect.deadline_min.get(r.job, 120)):
            n += 1
    return n


# ── 브리핑 ───────────────────────────────────────────────────────────────────────────


def _brief_day(n: NotifyRow) -> date:
    return n.as_of if isinstance(n.as_of, date) else _kst_day(n.requested_at)


def brief_counts(rows: Iterable[NotifyRow], day: date) -> dict[str, tuple[int, int]]:
    """그날(기준일) 브리핑 종류별 (보냄 수, 실패 수)."""
    out = {k: (0, 0) for k in BRIEF_KINDS}
    for n in rows:
        if n.kind not in out or _brief_day(n) != day:
            continue
        sent, failed = out[n.kind]
        if n.status == "sent":
            sent += 1
        elif n.status == "failed":
            failed += 1
        out[n.kind] = (sent, failed)
    return out


# ── check ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckItem:
    key: str
    title: str
    level: Level
    value: str  # 개수·시각 요약(시세 없음)
    verdict: bool  # 판정 항목인가(아니면 참고)


@dataclass(frozen=True)
class CheckReport:
    now: datetime
    since: datetime
    level: Level
    services: list[ServiceState]
    items: list[CheckItem]
    tables: list[TableSize]
    database_bytes: int | None

    @property
    def exit_code(self) -> int:
        return 1 if self.level == "FAIL" else 0


def _worst(levels: Iterable[Level]) -> Level:
    ls = set(levels)
    return "FAIL" if "FAIL" in ls else "WARN" if "WARN" in ls else "OK"


def _brief_items(
    notes: Sequence[NotifyRow], expect: Expect, cal: TradingCalendar, since: datetime, now: datetime
) -> CheckItem:
    """구간 안 날짜 중 마감(catch_up_until)이 지난 거래일마다 브리핑 1통."""
    problems: list[str] = []
    checked = 0
    d = _kst_day(since)
    while d <= _kst_day(now):
        if cal.covered(d) and cal.is_trading_day(d):
            counts = brief_counts(notes, d)
            for kind in BRIEF_KINDS:
                until = datetime.combine(d, expect.brief_until[kind], tzinfo=KST)
                if now < until or until < since:
                    continue
                checked += 1
                sent, failed = counts[kind]
                if sent != 1 or failed:
                    problems.append(f"{d.isoformat()} {kind} 보냄 {sent}·실패 {failed}")
        d += timedelta(days=1)
    if problems:
        return CheckItem("briefs", "브리핑 거래일 1통", "FAIL", "; ".join(problems), True)
    return CheckItem("briefs", "브리핑 거래일 1통", "OK", f"확인 {checked}건", True)


def run_check(
    source: OpsSource,
    expect: Expect,
    cal: TradingCalendar,
    heartbeat_age: HeartbeatAge,
    now: datetime,
    *,
    since: timedelta = timedelta(hours=24),
    disk: DiskUsage | None = None,
) -> CheckReport:
    """지금 상태 + 최근 `since` 동안의 기록으로 OK/WARN/FAIL 표."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 는 시간대가 있는 시각")
    start = now - since
    services = service_states(expect, heartbeat_age, now)
    items: list[CheckItem] = []

    down = [s for s in services if s.level != "OK"]
    items.append(
        CheckItem(
            "heartbeat_now", "하트비트(지금)", _worst(s.level for s in services),
            f"켜진 서비스 {len(services)} 중 기준 밖 {len(down)}", True,
        )
    )  # fmt: skip
    long_gaps = [o for o in outages(source.gap_events(start, now)) if o.over_limit]
    items.append(
        CheckItem(
            "heartbeat_gaps", "하트비트 끊김 >10분(기록)", "FAIL" if long_gaps else "OK",
            f"{len(long_gaps)}건", True,
        )
    )  # fmt: skip
    runs = source.job_runs(start, now)
    outs = job_outcomes(runs, expect.jobs)
    fails = [o for o in outs if o.final_failure]
    retried = sum(1 for o in outs if o.retried_ok)
    overdue = _overdue(runs, expect, now)
    items.append(
        CheckItem(
            "jobs", "켜진 작업 최종 실패", "FAIL" if fails or overdue else "OK",
            f"최종 {len(outs)}·실패 {len(fails)}·마감 초과 {overdue}·재시도 성공 {retried}", True,
        )
    )  # fmt: skip
    # 브리핑은 그날 0시부터 읽는다(마감 시각이 구간 안인 날의 발송이 구간 밖 이른 시각일 수 있다)
    day0, _ = _day_bounds(_kst_day(start))
    items.append(_brief_items(source.notifies(day0, now), expect, cal, start, now))

    issues = source.issues(start, now)
    items.append(
        CheckItem(
            "collection", "수집 결측·격리(참고)",
            "WARN" if issues.collection_gaps or issues.quarantine else "OK",
            f"결측 {issues.collection_gaps}·격리 {issues.quarantine}", False,
        )
    )  # fmt: skip
    backups = [r for r in source.job_runs(now - timedelta(days=8), now) if r.job == BACKUP_JOB]
    ok_backups = [r.finished_at or r.at for r in backups if r.status == "ok"]
    last = max(ok_backups) if ok_backups else None
    stale = last is None or now - last > timedelta(hours=BACKUP_STALE_H)
    items.append(
        CheckItem(
            "backup", "마지막 야간 백업(참고)", "WARN" if stale else "OK",
            "없음" if last is None else last.astimezone(KST).isoformat(timespec="minutes"), False,
        )
    )  # fmt: skip
    if disk is not None:
        du = disk()
        if du is None:
            items.append(CheckItem("disk", "디스크 여유(참고)", "WARN", "모름", False))
        else:
            free, total = du
            ratio = free / total if total > 0 else 0.0
            items.append(
                CheckItem(
                    "disk", "디스크 여유(참고)", "WARN" if ratio < DISK_WARN_RATIO else "OK",
                    f"{free / 2**30:.1f} GiB ({ratio:.0%})", False,
                )
            )  # fmt: skip
    level = _worst(i.level for i in items if i.verdict)
    if level == "OK" and any(i.level != "OK" for i in items):
        level = "WARN"
    return CheckReport(
        now, start, level, services, items, source.table_sizes(TOP_TABLES), source.database_bytes()
    )


# ── week ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayVerdict:
    day: date
    trading: bool
    long_gaps: int
    final_failures: int
    retried_ok: int
    morning_sent: int
    closing_sent: int
    brief_failed: int

    @property
    def briefs_ok(self) -> bool:
        if self.brief_failed:
            return False
        if not self.trading:
            return True
        return self.morning_sent == 1 and self.closing_sent == 1

    @property
    def passed(self) -> bool:
        return self.long_gaps == 0 and self.final_failures == 0 and self.briefs_ok


@dataclass(frozen=True)
class WeekReport:
    start: date
    end: date
    days: list[DayVerdict]

    @property
    def trading_days(self) -> int:
        return sum(1 for d in self.days if d.trading)

    @property
    def passed(self) -> bool:
        return self.trading_days >= MIN_TRADING_DAYS and all(d.passed for d in self.days)

    @property
    def reason(self) -> str:
        if self.trading_days < MIN_TRADING_DAYS:
            return f"거래일 {self.trading_days}일 — {MIN_TRADING_DAYS}일 미만이라 판정 불가"
        bad = [d.day.isoformat() for d in self.days if not d.passed]
        return "" if not bad else "실패한 날: " + ", ".join(bad)

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def run_week(source: OpsSource, expect: Expect, cal: TradingCalendar, end: date) -> WeekReport:
    """end 를 끝으로 7일(KST) 일자별 판정 + 최종 통과/실패(거래일 5일 이상 포함)."""
    first = end - timedelta(days=WEEK_DAYS - 1)
    start_ts, _ = _day_bounds(first)
    _, end_ts = _day_bounds(end)
    # 끊김은 앞날에 시작해 넘어온 것도 묶을 수 있게 하루 앞부터 읽고, 마지막 관측일로 센다
    outs = outages(source.gap_events(start_ts - timedelta(days=1), end_ts))
    jobs = job_outcomes(source.job_runs(start_ts, end_ts), expect.jobs)
    notes = source.notifies(start_ts, end_ts + timedelta(days=1))
    days: list[DayVerdict] = []
    for i in range(WEEK_DAYS):
        d = first + timedelta(days=i)
        trading = cal.covered(d) and cal.is_trading_day(d)
        counts = brief_counts(notes, d)
        days.append(
            DayVerdict(
                day=d,
                trading=trading,
                long_gaps=sum(1 for o in outs if o.over_limit and _kst_day(o.last_seen) == d),
                final_failures=sum(1 for o in jobs if o.day == d and o.final_failure),
                retried_ok=sum(1 for o in jobs if o.day == d and o.retried_ok),
                morning_sent=counts["brief.morning"][0],
                closing_sent=counts["brief.closing"][0],
                brief_failed=counts["brief.morning"][1] + counts["brief.closing"][1],
            )
        )
    return WeekReport(first, end, days)
