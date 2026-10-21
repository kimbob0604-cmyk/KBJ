"""오늘 호출 예산 — 출처별 쓴 수·닫힘 사유·작업별 셈(`python -m kbj.services.ops budget`).

Redis 의 `budget:<출처>[:<범위>]:<YYYYMMDD>` 카운터와 `…:by_job` 해시를 읽기만 한다. 작업별 셈은
실행기 안에서 부른 호출만 작업 이름이 붙고, 그 밖(수동 CLI 등)은 '-' 로 센다. 키·토큰은 없다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from kbj.store.redis_keys import BUDGET_CLOSED_SUFFIX, BUDGET_PREFIX, job_counts_key


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def budget_lines(redis: Any, day: date) -> list[str]:
    ymd = f"{day:%Y%m%d}"
    counters = sorted(k for k in (_s(x) for x in redis.scan_iter(match=f"{BUDGET_PREFIX}*:{ymd}")))
    if not counters:
        return [f"{day} — 기록된 호출 예산 없음"]
    out = [f"{day} 호출 예산"]
    for k in counters:
        name = k[len(BUDGET_PREFIX) : -(len(ymd) + 1)]
        used = _s(redis.get(k) or "0")
        closed = redis.get(k + BUDGET_CLOSED_SUFFIX)
        tail = f" — 닫힘: {_s(closed)[:120]}" if closed is not None else ""
        out.append(f"■ {name}: {used}{tail}")
        jobs = {_s(a): int(b) for a, b in redis.hgetall(job_counts_key(k)).items()}
        for job, n in sorted(jobs.items(), key=lambda x: -x[1]):
            out.append(f"    {n:>7}  {job}")
    return out
