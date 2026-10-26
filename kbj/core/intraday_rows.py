"""주식 분봉 행(`prv_market.stock_bar_5m` — 0029). 수집(KIS 파서)·저장소·엔진이 같이 쓴다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

__all__ = ["IntraBar"]


@dataclass(frozen=True)
class IntraBar:
    """5분봉 하나 — ts = 봉 시작(시간대 있음), minutes = 그 안 1분봉 수."""

    code: str
    ts: datetime
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    minutes: int = 5
