"""KRX 공표 지연 변형 — 10-02 분이 08:00 이 아니라 08:20 에 나온다(설계 §10.5).

창 2026-10-06 07:50 ~ 10:30 KST. `krx.daily`(08:05, 10분마다 10:00 까지)와 legacy GX 의 KRX 파생
단계가 08:05·08:15 에 빈 응답을 받고 08:25 에 받는다. 성공 수집은 키마다 1회, 일 예산은
3 × 엔드포인트 수(빈 응답도 센다 — kbj/data/budget.py).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, time

import pytest

from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions

pytestmark = pytest.mark.sim


@pytest.fixture(scope="module")
def late() -> SimDay:
    sim = SimDay(
        SimOptions(
            start=at(10, 6, 7, 50),
            hours=2 + 40 / 60,
            krx_publish_time=time(8, 20),
            ws=False,
            legacy_calls=False,
        )
    )
    try:
        return sim.run()
    finally:
        sim.close()


def test_krx_daily_retries_until_published(late: SimDay) -> None:
    runs = late.runs_of("krx.daily")
    assert [(r.attempt, r.status) for r in runs if r.status != "running"] == [
        (1, "failed"),  # 08:05 아직 공표 전(not_ready) — 재시도 예약
        (2, "failed"),  # 08:15
        (3, "ok"),  # 08:25
    ]
    assert [r.detail.get("result") for r in runs if r.status == "failed"] == ["not_ready"] * 2
    assert late.final_runs()[("krx.daily", "2026-10-02")].status == "ok"


def test_each_endpoint_requested_three_times_and_succeeds_once(late: SimDay) -> None:
    req = Counter(late.krx.requested())
    assert len(req) == 12 and set(req.values()) == {3}
    hits = [s.at.astimezone(KST).strftime("%H:%M") for s in late.krx.seen]
    assert sorted(set(hits)) == ["08:05", "08:15", "08:25"]
    ok = Counter((s.endpoint, s.bas_dd) for s in late.krx.successes())
    assert len(ok) == 12 and set(ok.values()) == {1}


def test_keys_collected_once_and_budget_counts_empty_answers(late: SimDay) -> None:
    krx_keys = Counter(k for k in late.done_keys() if k.source == "KRX")
    assert len(krx_keys) == 12 and set(krx_keys.values()) == {1}
    assert late.claims.refused == []
    assert late.budget_used("krx", date(2026, 10, 6)) == 3 * 12


def test_gx_derivatives_follow_the_same_rule(late: SimDay) -> None:
    gx = [
        (r.at.astimezone(KST).strftime("%H:%M"), r.status)
        for r in late.external_runs
        if r.job == "gex.krx_derivatives"
    ]
    assert gx == [
        ("08:05", "not_ready"),
        ("08:15", "not_ready"),
        ("08:25", "ok"),
    ]
