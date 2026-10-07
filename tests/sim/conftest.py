"""하루 운영 시뮬레이션 공통 — `SimDay` 를 만들고 끝나면 닫는다(tests/sim/harness.py).

- 모든 시험은 `@pytest.mark.sim`(pyproject 마커). CI 의 sim 잡이 `-m sim` 으로 돌린다.
- 실제 네트워크는 쓰지 않는다. 가짜 웹소켓 서버만 127.0.0.1 임의 포트에 뜬다(ws 를 켠 창).
- 벽시계에 기대지 않는다 — 시계는 `FakeClock` 하나(CLAUDE.md §4).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.sim.harness import SimDay, SimOptions

KST = ZoneInfo("Asia/Seoul")


def at(month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """2026 년 KST 시각."""
    return datetime(2026, month, day, hour, minute, second, tzinfo=KST)


@pytest.fixture
def simulate() -> Iterator[Callable[[SimOptions], SimDay]]:
    """옵션으로 시뮬레이션을 돌리고(run), 시험이 끝나면 닫는다."""
    made: list[SimDay] = []

    def run(opts: SimOptions) -> SimDay:
        sim = SimDay(opts)
        made.append(sim)
        return sim.run()

    yield run
    for sim in made:
        sim.close()
