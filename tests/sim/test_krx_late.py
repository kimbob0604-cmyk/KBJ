"""KRX 공표 지연 변형 — 10-02 분이 08:00 이 아니라 08:20 에 나온다(설계 §10.5).

창 2026-10-06 07:50 ~ 10:30 KST. `krx.daily`(08:05, 10분마다 10:00 까지)와 등록부
`gex.krx_derivatives`(P9 — 옛 GX KRX 파생 단계)가 08:05·08:15 에 빈 응답을 받고 08:25 에 받는다.
성공 수집은 키마다 1회, 일 예산은
3 × 엔드포인트 수(빈 응답도 센다 — kbj/data/budget.py). P7 `krx.vkospi`(08:10, 10분마다 —
운영 등록부에서는 꺼짐 #P7-1, 하네스는 꺼진 작업도 시뮬레이션 처리기로 돌린다 — 하네스 머리말)는
08:10 빈 응답·08:20 성공으로 2번.
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
            reader_calls=False,
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


# P7 krx.vkospi(파생상품지수 일별 — 13번째 엔드포인트). 운영에서는 꺼짐(#P7-1), 하네스는 돈다
VKOSPI = "/idx/drvprod_dd_trd"


def test_each_endpoint_requested_three_times_and_succeeds_once(late: SimDay) -> None:
    req = Counter(late.krx.requested())
    assert len(req) == 13
    assert {n for (ep, _), n in req.items() if ep != VKOSPI} == {3}
    assert [n for (ep, _), n in req.items() if ep == VKOSPI] == [2]  # 08:10 빈 응답·08:20 성공
    hits = [s.at.astimezone(KST).strftime("%H:%M") for s in late.krx.seen if s.endpoint != VKOSPI]
    assert sorted(set(hits)) == ["08:05", "08:15", "08:25"]
    vk = [s.at.astimezone(KST).strftime("%H:%M") for s in late.krx.seen if s.endpoint == VKOSPI]
    assert vk == ["08:10", "08:20"]
    ok = Counter((s.endpoint, s.bas_dd) for s in late.krx.successes())
    assert len(ok) == 13 and set(ok.values()) == {1}


def test_keys_collected_once_and_budget_counts_empty_answers(late: SimDay) -> None:
    krx_keys = Counter(k for k in late.done_keys() if k.source == "KRX")
    assert len(krx_keys) == 13 and set(krx_keys.values()) == {1}  # + krx.vkospi(위 VKOSPI)
    assert late.claims.refused == []
    assert late.budget_used("krx", date(2026, 10, 6)) == 3 * 12 + 2


def test_gx_derivatives_follow_the_same_rule(late: SimDay) -> None:
    """P9 WRAP: GX KRX 파생은 등록부 gex.krx_derivatives(runner kbj) — krx.daily 와 같은 재시도."""
    runs = late.runs_of("gex.krx_derivatives")
    assert [
        (r.started_at.astimezone(KST).strftime("%H:%M"), r.attempt, r.status)
        for r in runs
        if r.status != "running" and r.started_at is not None
    ] == [
        ("08:05", 1, "failed"),  # 공표 전(not_ready) — 재시도 예약
        ("08:15", 2, "failed"),
        ("08:25", 3, "ok"),
    ]
    assert [r.detail.get("result") for r in runs if r.status == "failed"] == ["not_ready"] * 2
