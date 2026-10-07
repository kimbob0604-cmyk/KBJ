"""health 이벤트·싱크 — GEXLAB `services/auth/health.py` + `runtime.py:ServiceHealthSink` 승격.

- 이벤트는 `HealthEvent(kind, detail, at, severity, service)`. detail 은 만드는 쪽이 가린 문장이다 —
  토큰·접속키·앱키가 들어가지 않는다. 그래도 싱크에 닿기 전에 한 번 더 형태로 가린다(절대 규칙 5).
- DB(`ops.health_events`) 적재는 `HealthStore` 프로토콜 뒤에 둔다(묶음 G). 여기엔 메모리
  구현(시험)과 구조화 JSON 로그 구현(서비스 기본값), 둘을 묶은 `ServiceHealthSink` 를 둔다.
- 한쪽 실패가 다른 쪽·서비스를 막지 않는다(절대 규칙 4 — 실패는 로그로 남긴다).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

from kbj.core.masking import mask_text

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
    """health 경고 한 건. `at` 은 aware 시각만 받고 UTC 로 맞춘다. detail 은 가리고 300자로."""

    kind: str
    detail: str
    at: datetime
    severity: Severity = "info"
    service: str = "auth"

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        object.__setattr__(self, "at", self.at.astimezone(UTC))
        detail = mask_text(self.detail)
        if len(detail) > DETAIL_MAX:
            detail = detail[: DETAIL_MAX - 1] + "…"
        object.__setattr__(self, "detail", detail)

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
    """받은 이벤트를 목록에 쌓는다(시험·단독 실행용)."""

    events: list[HealthEvent] = field(default_factory=list[HealthEvent])

    def emit(self, event: HealthEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def of(self, kind: str) -> list[HealthEvent]:
        return [e for e in self.events if e.kind == kind]


class LogHealthSink:
    """이벤트를 구조화 JSON 한 줄로 로그에 남긴다(서비스명·거래일·세션 포함).

    tagger 는 시각에서 거래일·세션을 얻는 함수(캘린더 태거). 없거나 실패하면 둘 다 null — 태깅
    실패로 경고가 사라지지 않게 한다.
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
            except Exception as e:  # 태깅 실패는 경고만 — 이벤트는 그대로 남긴다
                self._log.warning("health 태깅 실패: %s", type(e).__name__)
        rec: dict[str, str | None] = {
            **event.as_dict(),
            "trade_date": trade_date.isoformat() if trade_date else None,
            "session": session,
        }
        self._log.log(_LEVELS[event.severity], json.dumps(rec, ensure_ascii=False))


class HealthStore(Protocol):
    """health 이벤트 저장소(`ops.health_events` — 묶음 G)."""

    def write_health(
        self, events: Sequence[HealthEvent], *, tagger: Tagger | None = None
    ) -> Any: ...


class ServiceHealthSink:
    """health 이벤트 → JSON 로그(거래일·세션 태그) + 저장소. 저장 실패는 로그만."""

    def __init__(
        self,
        store: HealthStore | None,
        tagger: Tagger | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._store = store
        self._tagger = tagger
        self._log = LogHealthSink(logger, tagger=tagger)

    def emit(self, event: HealthEvent) -> None:
        self._log.emit(event)
        if self._store is None:
            return
        try:
            self._store.write_health([event], tagger=self._tagger)
        except Exception as e:  # 저장 실패가 서비스를 멈추지 않는다 — 유형만 남긴다
            logging.getLogger("kbj.services").error(
                json.dumps(
                    {
                        "service": event.service,
                        "event": "health_write_failed",
                        "trade_date": None,
                        "session": None,
                        "kind": event.kind,
                        "error": type(e).__name__,
                    },
                    ensure_ascii=False,
                )
            )
