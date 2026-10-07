"""캘린더 태거 — 시각 → (귀속 거래일, 세션). GEXLAB `services/runtime.py:tagger_for`:105 승격.

health 기록·로그에 거래일·세션을 붙일 때 쓴다. 판정은 `kbj.core.calendar.session_tag` 하나에
맡긴다(장 전 준비는 곧 열릴 세션, 장 밖은 (None, None) — GX poller 와 같다).
"""

from __future__ import annotations

from datetime import date, datetime

from kbj.core.calendar import TradingCalendar, session_tag
from kbj.services.runtime.health import Tagger

__all__ = ["tagger_for"]


def tagger_for(cal: TradingCalendar) -> Tagger:
    """시각 → (거래일, 세션). 장 전 준비는 곧 열릴 세션, 장 밖은 (None, None) — poller 와 같다."""

    def tag(t: datetime) -> tuple[date | None, str | None]:
        got = session_tag(t, cal)
        return (None, None) if got is None else got

    return tag
