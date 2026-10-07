"""종료 신호·작업 스레드 — GEXLAB `services/runtime.py:install_stop`·`run_in_thread` 그대로.

- SIGTERM·SIGINT → `threading.Event`(graceful stop — 루프가 묶음을 비우고 끝난다)
- `run_in_thread`: 오래 걸릴 수 있는 일(내려받기·적재)을 데몬 스레드 하나로 — 상태 루프는
  `Future.done()` 만 본다. 예외는 Future 에 담긴다(삼키지 않는다)
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from concurrent.futures import Future


def install_stop(stop: threading.Event) -> None:
    """SIGTERM·SIGINT 가 오면 stop 을 세운다(메인 스레드에서 부른다)."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())


def run_in_thread[T](fn: Callable[[], T], *, name: str = "worker") -> Future[T]:
    """데몬 스레드 하나로 부른다 — 부른 쪽은 기다리지 않고, 멈출 때도 기다리지 않는다.
    결과·예외는 Future 에."""
    fut: Future[T] = Future()

    def target() -> None:
        if not fut.set_running_or_notify_cancel():
            return
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)

    threading.Thread(target=target, name=name, daemon=True).start()
    return fut
