"""지금 도는 작업 이름 — 실행기가 처리기 스레드 안에서 정하고, 하루 예산이 작업별로 센다(운영 진단).

설정하는 곳은 실행기 하나(`kbj.services.scheduler.runner`), 읽는 곳은 `kbj.data.budget` 하나다.
값은 작업 이름뿐(비밀 없음). 정하지 않은 실행(시험·CLI 직접 호출)은 None.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Final

_JOB: Final[ContextVar[str | None]] = ContextVar("kbj_job", default=None)


def current_job() -> str | None:
    return _JOB.get()


def run_as_job[T](job: str, fn: Callable[[], T]) -> T:
    """`fn` 을 부르는 동안 작업 이름을 `job` 으로 둔다."""
    token = _JOB.set(job)
    try:
        return fn()
    finally:
        _JOB.reset(token)
