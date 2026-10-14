"""KBJ P7(묶음 ENG): engine 시험 입력 fixture 는 KBJ `tests/unit/engines/gex/engine_inputs.py` 로
옮겼다(엔진 시험과 함께). 남은 legacy 시험(shadow_report)이 쓰는 스냅샷 변환만 둔다 — 변환 본문은
`kbj.engines.gex.snapshot_cycle`(정본 하나)이고 여기는 시험 캘린더만 붙인다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from kbj.core.calendar import TradingCalendar
from kbj.engines.gex.series import SeriesKey
from kbj.engines.gex.snapshot_cycle import SnapshotCycle
from kbj.engines.gex.snapshot_cycle import snapshot_cycle as _snapshot_cycle

CAL = TradingCalendar.default()


def snapshot_cycle(
    path: Path,
    *,
    shift: timedelta | None = None,
    fresh_since: datetime | None = None,
    strikes: Mapping[SeriesKey, Sequence[Decimal]] | None = None,
) -> SnapshotCycle:
    """`kbj.engines.gex.snapshot_cycle` 을 시험 캘린더(`CAL`)로 — 검증 리포트와 같은 변환."""
    return _snapshot_cycle(path, shift=shift, fresh_since=fresh_since, strikes=strikes, cal=CAL)
