"""하루 통합 시험(tests/integration/test_full_day.py)의 틀 — 컨테이너 없이.

- 실제 시간 상한(`Watchdog`·`poll_until`): 동기 `PollerService.run` 은 이벤트 루프를 막아
  `asyncio.wait_for` 가 깨지 못한다. 수집기가 가짜 시계를 밀지 못한 채 오류를 내면 run 은 1초 쉬고
  다시 돌 뿐 until 에 닿지 않는다 — 상한에서 poller 의 stop 을 세워 돌려받고 실패해야 시험이
  멈추지 않고 스택(down -v)도 남지 않는다
- engine 연속성을 보는 창(`_open_parts`): LIVE 창 중 세션이 열린 부분, 알림을 잃는 창은 자정뿐
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.calendar import TradingCalendar
from data.kis.ratelimit import LocalRateLimiter
from services.poller.collector import Collector
from services.poller.service import PollerService
from services.poller.sink import InMemorySink
from tests.fakes.kis_server import FakeClock, FakeKisServer, default_chain, make_client
from tests.integration.test_full_day import Watchdog, _open_parts, poll_until

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()


def _poller(start: datetime) -> tuple[FakeClock, Collector, PollerService]:
    clock = FakeClock(start)
    kis = make_client(FakeKisServer(clock, default_chain()), LocalRateLimiter(clock=clock))
    collector = Collector(kis, InMemorySink(), calendar=CAL, clock=clock)
    return clock, collector, PollerService(collector)


def test_the_watchdog_stops_a_poller_that_cannot_advance_the_fake_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = datetime(2026, 9, 28, 8, 45, tzinfo=KST)
    clock, collector, poller = _poller(t0)

    def boom(_end: datetime) -> None:
        raise RuntimeError("regression")  # 시계를 밀기 전에 — run 은 1초 쉬고 다시 돈다

    monkeypatch.setattr(collector, "run_until", boom)
    stop = threading.Event()
    dog = Watchdog(stop, 0.2)
    outcome: list[BaseException | None] = []

    def walk() -> None:
        try:
            with dog:
                poll_until(poller, stop, t0 + timedelta(seconds=1))
            outcome.append(None)
        except BaseException as e:
            outcome.append(e)

    started = time.monotonic()
    th = threading.Thread(target=walk, daemon=True)  # 틀이 고장이면 여기서 멈춘다 — join 상한
    th.start()
    th.join(10)
    assert not th.is_alive(), "상한이 지나도 poller 가 돌아오지 않았다"
    assert time.monotonic() - started < 5  # 1초 쉬기도 stop 으로 깬다
    assert len(outcome) == 1 and isinstance(outcome[0], TimeoutError), outcome
    assert dog.fired and clock.now() == t0


def test_the_watchdog_stays_quiet_when_the_walk_finishes_in_time() -> None:
    t0 = datetime(2026, 9, 28, 16, 30, tzinfo=KST)  # POST_DAY — 호출 없이 시계만 민다
    clock, _collector, poller = _poller(t0)
    stop = threading.Event()
    with Watchdog(stop, 60.0) as dog:
        assert dog.armed
        poll_until(poller, stop, t0 + timedelta(seconds=30))
    assert not dog.armed  # 끝내면 거둔다 — 나중에 울려 다른 일을 멈추지 않는다
    assert not dog.fired and not stop.is_set()
    assert clock.now() >= t0 + timedelta(seconds=30)


def test_the_engine_is_checked_on_the_open_session_parts_of_the_live_windows() -> None:
    parts = [(a.astimezone(KST), b.astimezone(KST), lose) for a, b, lose in _open_parts()]
    assert [(f"{a:%d %H:%M}", f"{b:%d %H:%M}", lose) for a, b, lose in parts] == [
        ("28 08:45", "28 08:52", False),  # 개장 — PRE_DAY 부분은 뺀다
        ("28 15:17", "28 15:24", False),  # 15:20 만기 전환
        ("28 15:43", "28 15:45", False),  # 주간 마감까지
        ("28 18:00", "28 18:08", False),  # 야간 개장
        ("28 23:58", "29 00:03", True),  # 자정 — chain.ready 를 잃는다(따라잡기만)
        ("29 05:57", "29 06:00", False),  # 야간 마감까지
    ]
