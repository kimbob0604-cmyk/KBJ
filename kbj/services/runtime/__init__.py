"""서비스 공통 실행 도구 — 로그·health·하트비트·재시도 간격·작업 스레드·healthcheck(설계 §1.1).

GEXLAB `services/runtime.py`·`services/auth/health.py`·`services/healthcheck.py` 승격. 캘린더 태거
`tagger_for`(시각 → 거래일·세션)는 `kbj.core.calendar.session_tag` 위에 있다.
"""

from __future__ import annotations

from kbj.services.runtime.backoff import Backoff
from kbj.services.runtime.clock import utcnow
from kbj.services.runtime.health import (
    DETAIL_MAX,
    HealthEvent,
    HealthSink,
    HealthStore,
    LogHealthSink,
    MemoryHealthSink,
    ServiceHealthSink,
    Severity,
    Tagger,
)
from kbj.services.runtime.heartbeat import (
    HEARTBEAT_EVERY_S,
    HEARTBEAT_TTL_S,
    Heartbeat,
    Heartbeater,
    connect_redis,
    heartbeat_age,
)
from kbj.services.runtime.log import Tag, log_event, setup_logging
from kbj.services.runtime.tagger import tagger_for
from kbj.services.runtime.threads import install_stop, run_in_thread

__all__ = [
    "DETAIL_MAX",
    "HEARTBEAT_EVERY_S",
    "HEARTBEAT_TTL_S",
    "Backoff",
    "HealthEvent",
    "HealthSink",
    "HealthStore",
    "Heartbeat",
    "Heartbeater",
    "LogHealthSink",
    "MemoryHealthSink",
    "ServiceHealthSink",
    "Severity",
    "Tag",
    "Tagger",
    "connect_redis",
    "heartbeat_age",
    "install_stop",
    "log_event",
    "run_in_thread",
    "setup_logging",
    "tagger_for",
    "utcnow",
]
