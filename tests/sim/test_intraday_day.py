"""장중 하루 판 — 정규장 전체 슬롯 무결측(docs/p3_design.md §8.3 '하루 판', 묶음 S). 느림 표시.

- 평일 2026-10-07(수): 09:00~15:30, 10분 슬롯(등록부 equity
  `{start: open, end: close, every_min: 10}` — 양 끝 포함 40개).
- 수능일 2026-11-19(목): 지연 개장(`late_open` — 10:00~16:30). 슬롯은 캘린더의 그날 정규장 경계를
  따른다(등록부가 시각을 하드코딩하지 않는다).

CI 의 sim 잡은 `-m "sim and not slow"` 로 이 파일을 건너뛴다. 로컬·수동은 `-m slow`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.services.scheduler.registry import Registry
from tests.sim.conftest import KST
from tests.sim.harness import ROOT, SimDay, SimOptions
from tests.sim.p3 import N_SEED_ETFS, expected_rows, intraday_keys, seed_watch_etfs, slots

pytestmark = [pytest.mark.sim, pytest.mark.slow]

REG = Registry.load(ROOT / "config" / "jobs.yaml")
KR = TradingCalendar.default()


def _run(day: date) -> SimDay:
    open_, close = KR.equity_bounds(day)
    start = open_ - timedelta(minutes=5)
    hours = (close - start).total_seconds() / 3600 + 10 / 60
    sim = SimDay(
        SimOptions(
            start=start,
            hours=hours,
            p3=True,
            prepare=(seed_watch_etfs(KR.prev_trading_day(day)),),
            legacy_calls=False,
        )
    )
    return sim.run()


@pytest.fixture(
    scope="module", params=[date(2026, 10, 7), date(2026, 11, 19)], ids=["평일", "수능일"]
)
def whole_day(request: pytest.FixtureRequest) -> Iterator[tuple[date, SimDay]]:
    day: date = request.param
    sim = _run(day)
    try:
        yield day, sim
    finally:
        sim.close()


def _ts(label: str) -> datetime:
    return datetime.strptime(label, "%Y-%m-%dT%H:%M").replace(tzinfo=KST)


def test_slots_follow_the_session_and_none_is_missing(whole_day: tuple[date, SimDay]) -> None:
    day, sim = whole_day
    open_, close = KR.equity_bounds(day)
    want = slots(open_, close)
    assert len(want) == 40
    if day == date(2026, 11, 19):
        assert want[0].endswith("T10:00") and want[-1].endswith("T16:30")  # 수능일 지연 개장
    else:
        assert want[0].endswith("T09:00") and want[-1].endswith("T15:30")
    final = sim.final_runs()
    for job in ("flows.intraday", "market.intraday"):
        got = sorted(a for (j, a), r in final.items() if j == job and r.status == "ok")
        assert got == want, job
    done = sim.done_keys()
    for label in want:
        for k in intraday_keys(REG, sim, label):
            assert done[k] == 1, (label, k.label())
    assert max(done.values()) == 1
    rows = expected_rows(sim, N_SEED_ETFS)
    lo, hi = open_ - timedelta(hours=1), close + timedelta(hours=1)
    per_slot = {_ts(x): 0 for x in want}
    for q in sim.repos.etf.quotes(lo, hi):
        per_slot[q.ts] += 1
    assert set(per_slot.values()) == {rows.etf_quote}
    assert sim.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in sim.kis.posts()} <= {"auth"}
