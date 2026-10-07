"""health 이벤트·싱크 — KBJ P2: 정본은 `kbj.services.runtime.health`(설계 §1.1, 이 파일에서 승격).

legacy GX 서비스가 쓰는 이름을 그대로 다시 내보낸다. KBJ 쪽은 detail 을 싱크에 닿기 전에 한 번 더
형태로 가린다(절대 규칙 5).
"""

from kbj.services.runtime.health import (
    DETAIL_MAX,
    HealthEvent,
    HealthSink,
    LogHealthSink,
    MemoryHealthSink,
    Severity,
    Tagger,
)

__all__ = [
    "DETAIL_MAX",
    "HealthEvent",
    "HealthSink",
    "LogHealthSink",
    "MemoryHealthSink",
    "Severity",
    "Tagger",
]
