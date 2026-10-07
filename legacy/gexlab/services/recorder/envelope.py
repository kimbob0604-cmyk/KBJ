"""원본 메시지 녹화 단위 (PLAN §4.1 recorder, §4.5 raw_messages).

파싱 전 원문을 그대로 담는다 — 파서가 틀려도 녹화는 남아 리플레이·골든 테스트에 쓴다.
거래일·세션 태깅은 주입받은 함수(`core.calendar.state_at` 을 감싼 것)로 한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

Source = Literal["kis_ws", "kis_rest", "krx"]
SessionName = Literal["day", "night"]
Tagger = Callable[[datetime], tuple[date | None, SessionName | None]]


class RawEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    received_at: AwareDatetime
    source: Source
    tr_id: str
    key: str = ""
    payload: str | dict[str, Any]
    trade_date: date | None
    session: SessionName | None

    @field_validator("received_at")
    @classmethod
    def _to_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


def wrap(
    payload: str | dict[str, Any],
    *,
    source: Source,
    tr_id: str,
    received_at: datetime,
    tagger: Tagger,
    key: str = "",
) -> RawEnvelope:
    """수신 시각으로 거래일·세션을 붙여 봉투를 만든다. naive 시각은 거부한다."""
    if received_at.tzinfo is None:
        raise ValueError("naive datetime 금지")
    trade_date, session = tagger(received_at)
    return RawEnvelope(
        received_at=received_at,
        source=source,
        tr_id=tr_id,
        key=key,
        payload=payload,
        trade_date=trade_date,
        session=session,
    )


@dataclass
class Batcher:
    """DB 쓰기 묶음. 개수(max_items)나 첫 항목 대기 시간(max_age_s)을 넘으면 비운다."""

    max_items: int = 500
    max_age_s: float = 1.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _items: list[RawEnvelope] = field(default_factory=list[RawEnvelope], repr=False)
    _first_at: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.max_items < 1 or self.max_age_s <= 0:
            raise ValueError("max_items >= 1, max_age_s > 0")

    def __len__(self) -> int:
        return len(self._items)

    def add(self, env: RawEnvelope) -> list[RawEnvelope] | None:
        """넣고, 개수가 차면 비운 묶음을 돌려준다(아니면 None)."""
        if not self._items:
            self._first_at = self.clock()
        self._items.append(env)
        return self.flush() if len(self._items) >= self.max_items else None

    def due(self) -> bool:
        return self._first_at is not None and self.clock() - self._first_at >= self.max_age_s

    def flush(self) -> list[RawEnvelope]:
        out, self._items, self._first_at = self._items, [], None
        return out
