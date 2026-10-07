"""scheduler 재기동 변형 — 15:50 과 16:45 에 scheduler 프로세스가 다시 뜬다(설계 §10.5).

창 2026-10-06 15:00 ~ 17:00 KST. 메모리(진행 중 시도·발화 창·토큰 리더)는 잃고
DB(`ops.job_run`·`ops.data_claim`)·Redis 는 남는다. 기대: 같은 (작업, as_of) 재실행 없음
(기록·선점이 막는다). 첫 tick 은 지금 분만 보므로 지난 cron 을 몰아 돌리지 않는다. 마감 요약
캐치업(20:30 까지)은 이미 보낸 날을 다시 보내지 않는다 — `brief.closing` 1건.
"""

from __future__ import annotations

from collections import Counter

import pytest

from tests.sim.conftest import at
from tests.sim.harness import SimDay, SimOptions

pytestmark = pytest.mark.sim


@pytest.fixture(scope="module")
def restarted() -> SimDay:
    sim = SimDay(
        SimOptions(
            start=at(10, 6, 15),
            hours=2,
            ws=False,
            legacy_calls=False,
            restarts=(at(10, 6, 15, 50), at(10, 6, 16, 45)),
        )
    )
    try:
        return sim.run()
    finally:
        sim.close()


def test_restarted_twice(restarted: SimDay) -> None:
    assert [t.strftime("%H:%M") for t in restarted.restarted_at] == ["06:50", "07:45"]  # UTC


def test_no_job_and_as_of_runs_twice(restarted: SimDay) -> None:
    starts = Counter((r.job, r.as_of) for r in restarted.runs.history if r.status == "running")
    assert starts and set(starts.values()) == {1}
    final = restarted.final_runs()
    assert {r.status for r in final.values()} == {"ok"}
    assert final[("market.close_collect", "2026-10-06")].attempt == 1


def test_closing_brief_once_despite_catch_up_window(restarted: SimDay) -> None:
    rows = restarted.notify_log.of("brief.closing")
    assert [r.status for r in rows] == ["suppressed"]
    tries = [r.status for r in restarted.runs.history if r.job == "brief.closing"]
    assert tries == ["running", "ok"]  # 한 시도


def test_keys_not_collected_twice_across_restarts(restarted: SimDay) -> None:
    assert max(restarted.done_keys().values()) == 1
    assert restarted.claims.refused == []


def test_state_republished_at_each_startup(restarted: SimDay) -> None:
    starts = [r for r in restarted.session_log.rows if r.detail.get("startup")]
    assert [r.state.value for r in starts if r.state] == ["DAY", "POST_DAY", "POST_DAY"]
    states = [e["state"] for e in restarted.session_events]
    dedup = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    assert dedup == ["DAY", "POST_DAY"]
