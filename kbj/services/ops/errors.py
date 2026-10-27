"""작업 오류 목록 — `python -m kbj.services.ops errors [--since 7d] [--json]`
(docs/ops_error_catalog.md).

최근 기록(`ops.job_run`)을 작업마다 묶어 실패·시간 초과·건너뜀(그중 기다림)이 몇 번인지, 마지막이
언제·무슨 사유였는지 한 줄씩. 맥에서 찍어 붙여 넣으면 오류 카탈로그의 '맥 확인 필요' 칸을 채울 수
있게 만든 읽기 전용 표다.

- 셈은 (작업, as_of) 의 **마지막 시도**(시도 번호가 가장 큰 줄) 기준 — 재시도 끝에 성공한 것은
  실패로 세지 않는다(`check` 와 같은 규칙). 재시도 중간의 실패 줄은 `retries` 로 따로 센다.
- 계획됨(꺼진 작업 — reason `planned`)은 오류가 아니라 뺀다. 기다림(detail.wait — 공표 대기·의존
  막힘)은 건너뜀 안에서 따로 센다.
- 출력은 작업 이름·상태·개수·시각·사유(실행기가 이미 가린 짧은 문장을 한 번 더 가림)뿐 — 값·키 없음.
- 시각은 주입한다(벽시계 금지). 작업 순서: 실패+시간 초과 많은 순 → 건너뜀 많은 순 → 이름.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from kbj.core.masking import mask_text
from kbj.core.time import KST
from kbj.services.ops.source import JobRunRow

__all__ = ["JobErrors", "errors_json", "job_errors", "render_errors"]

PLANNED: Final = "planned"
REASON_SHOW: Final = 160  # 화면 한 줄에 싣는 사유 길이(JSON 은 원문 300자 그대로)


@dataclass(frozen=True)
class JobErrors:
    """작업 하나의 구간 안 문제 요약."""

    job: str
    runs: int  # 구간 안 (작업, as_of) 수 — 마지막 시도 기준
    failed: int
    timeout: int
    skipped: int  # 계획됨 제외, 기다림 포함
    waiting: int  # skipped 중 기다림(공표 대기·의존 막힘)
    retries: int  # 마지막 시도가 아닌 실패 줄(재시도로 이어진 것)
    last_at: datetime | None  # 마지막 문제(실패·시간 초과·건너뜀) 시각
    last_status: str | None
    last_as_of: str | None
    last_reason: str | None

    @property
    def problems(self) -> int:
        return self.failed + self.timeout + self.skipped


def _when(r: JobRunRow) -> datetime:
    return r.finished_at or r.at


def _planned(r: JobRunRow) -> bool:
    return r.status == "skipped" and r.reason == PLANNED


def job_errors(runs: Iterable[JobRunRow]) -> list[JobErrors]:
    """기록 → 문제가 하나라도 있는 작업마다 요약(문제 없는 작업은 빠진다)."""
    last: dict[tuple[str, str], JobRunRow] = {}
    rows: list[JobRunRow] = []
    for r in runs:
        if _planned(r) or r.status in ("queued", "running"):
            continue
        rows.append(r)
        key = (r.job, r.as_of)
        cur = last.get(key)
        if cur is None or (r.attempt, r.at) > (cur.attempt, cur.at):
            last[key] = r
    finals = set(map(id, last.values()))
    by_job: dict[str, list[JobRunRow]] = defaultdict(list)
    for r in last.values():
        by_job[r.job].append(r)
    retries: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.status == "failed" and id(r) not in finals:
            retries[r.job] += 1
    out: list[JobErrors] = []
    for job, rs in by_job.items():
        bad = [r for r in rs if r.status in ("failed", "timeout", "skipped")]
        if not bad:
            continue
        worst = max(bad, key=_when)
        out.append(
            JobErrors(
                job=job,
                runs=len(rs),
                failed=sum(1 for r in rs if r.status == "failed"),
                timeout=sum(1 for r in rs if r.status == "timeout"),
                skipped=sum(1 for r in rs if r.status == "skipped"),
                waiting=sum(1 for r in rs if r.status == "skipped" and r.wait),
                retries=retries[job],
                last_at=_when(worst),
                last_status=worst.status if not worst.wait else f"{worst.status}({worst.wait})",
                last_as_of=worst.as_of,
                last_reason=None if worst.reason is None else mask_text(worst.reason),
            )
        )
    return sorted(out, key=lambda e: (-(e.failed + e.timeout), -e.skipped, e.job))


def errors_json(items: list[JobErrors], since: datetime, now: datetime) -> dict[str, Any]:
    return {
        "since": since.isoformat(),
        "now": now.isoformat(),
        "jobs": [
            {
                "job": e.job,
                "runs": e.runs,
                "failed": e.failed,
                "timeout": e.timeout,
                "skipped": e.skipped,
                "waiting": e.waiting,
                "retries": e.retries,
                "last_at": None if e.last_at is None else e.last_at.isoformat(),
                "last_status": e.last_status,
                "last_as_of": e.last_as_of,
                "last_reason": e.last_reason,
            }
            for e in items
        ],
    }


def _hm(ts: datetime | None) -> str:
    return "-" if ts is None else ts.astimezone(KST).strftime("%m-%d %H:%M")


def render_errors(items: list[JobErrors], since: datetime, now: datetime) -> str:
    head = (
        f"작업 오류 {since.astimezone(KST).isoformat(timespec='minutes')} ~ "
        f"{now.astimezone(KST).isoformat(timespec='minutes')} — 문제 있는 작업 {len(items)}"
    )
    if not items:
        return head + "\n\n실패·시간 초과·건너뜀 없음\n"
    lines = [
        head,
        "(셈은 (작업, as_of) 마지막 시도 — 건너뜀 중 '대기'는 공표 대기·의존 막힘,"
        " 재시도는 중간 실패 줄)",
        "",
        "작업                        실패 시간초과 건너뜀(대기) 재시도  마지막"
        "       상태·as_of · 사유",
    ]
    for e in items:
        reason = (e.last_reason or "-").replace("\n", " ")[:REASON_SHOW]
        lines.append(
            f"{e.job:<27} {e.failed:>4} {e.timeout:>8} {e.skipped:>6}({e.waiting:>3})"
            f" {e.retries:>6}"
            f"  {_hm(e.last_at)}  {e.last_status} {e.last_as_of} · {reason}"
        )
    return "\n".join(lines) + "\n"
