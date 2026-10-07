"""auth 서비스 health 이벤트 (설계 §2 `auth` 장애 시, §8 `health_events`).

- 이벤트는 `HealthEvent(kind, detail, at)`. detail 은 만드는 쪽이 가린 문장이다 — 토큰·접속키·앱키가
  들어가지 않는다. 싱크는 받은 그대로 쓴다
- DB(`health_events`) 적재는 범위 밖이라 `HealthSink` 프로토콜 뒤에 둔다. 여기엔 메모리
  구현(테스트)과 구조화 JSON 로그 구현(서비스 기본값)만 둔다
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal, Protocol

Severity = Literal["info", "warning", "critical"]
Tagger = Callable[[datetime], tuple[date | None, str | None]]  # 시각 → (거래일, 세션)

_LEVELS: dict[Severity, int] = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "critical": logging.CRITICAL,
}
DETAIL_MAX = 300

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthEvent:
    """health 경고 한 건. `at` 은 aware 시각만 받고 UTC 로 맞춘다."""

    kind: str
    detail: str
    at: datetime
    severity: Severity = "info"
    service: str = "auth"

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        object.__setattr__(self, "at", self.at.astimezone(UTC))
        if len(self.detail) > DETAIL_MAX:
            object.__setattr__(self, "detail", self.detail[: DETAIL_MAX - 1] + "…")

    def as_dict(self) -> dict[str, str]:
        return {
            "service": self.service,
            "kind": self.kind,
            "severity": self.severity,
            "at": self.at.isoformat(),
            "detail": self.detail,
        }


class HealthSink(Protocol):
    def emit(self, event: HealthEvent) -> None: ...


@dataclass
class MemoryHealthSink:
    """받은 이벤트를 목록에 쌓는다 (테스트·단독 실행용)."""

    events: list[HealthEvent] = field(default_factory=list[HealthEvent])

    def emit(self, event: HealthEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def of(self, kind: str) -> list[HealthEvent]:
        return [e for e in self.events if e.kind == kind]


class LogHealthSink:
    """이벤트를 구조화 JSON 한 줄로 로그에 남긴다 (서비스명·거래일·세션 포함).

    tagger 는 시각에서 거래일·세션을 얻는 함수(`core.calendar.state_at` 을 감싼 것). 없거나 실패하면
    둘 다 null — 태깅 실패로 경고가 사라지지 않게 한다.
    """

    def __init__(self, logger: logging.Logger | None = None, tagger: Tagger | None = None) -> None:
        self._log = logger or log
        self._tagger = tagger

    def emit(self, event: HealthEvent) -> None:
        trade_date: date | None = None
        session: str | None = None
        if self._tagger is not None:
            try:
                trade_date, session = self._tagger(event.at)
            except Exception as e:
                self._log.warning("health 태깅 실패: %s", type(e).__name__)
        rec: dict[str, str | None] = {
            **event.as_dict(),
            "trade_date": trade_date.isoformat() if trade_date else None,
            "session": session,
        }
        self._log.log(_LEVELS[event.severity], json.dumps(rec, ensure_ascii=False))
