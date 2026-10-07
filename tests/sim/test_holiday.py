"""휴장일 변형 — 24시간 창 2026-10-05(월, 대체공휴일) 05:00 ~ 10-06 05:00 KST(설계 §10.5).

기대: 거래일 작업 0회, 아침·마감 브리핑 0건(간밤 미국 = 일요일 → `after_us_session` 거짓),
야간장 없음, KIS 조회 0. 토큰 발급은 auth 에서만 — 05:00 첫 발급과 다음 날 04:00 갱신(어떤
23시간 창에도 1회 이하).
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest

from kbj.services.auth.issuer import TOKEN_PATH
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions

pytestmark = pytest.mark.sim


@pytest.fixture(scope="module")
def holiday() -> SimDay:
    sim = SimDay(SimOptions(start=at(10, 5, 5), hours=24))
    try:
        return sim.run()
    finally:
        sim.close()


def test_no_trading_day_job_runs(holiday: SimDay) -> None:
    final = holiday.final_runs()
    trading = {j.name for j in holiday.registry.jobs if j.schedule.when == "trading_day"}
    assert [k for k in final if k[0] in trading] == []
    for job in ("brief.morning", "brief.closing", "guru.research", "macro.morning"):
        assert [k for k in final if k[0] == job] == [], job  # K∨U·U 조건 모두 거짓
    assert holiday.notify_log.rows == []
    assert holiday.tg.calls == []


def test_always_jobs_still_run(holiday: SimDay) -> None:
    final = holiday.final_runs()
    ran = Counter(job for job, _ in final)
    for job in ("ops.nightly", "filings.corp_code", "macro.monthly", "earnings.backfill"):
        assert ran[job] == 1, job
    assert {r.status for r in final.values()} == {"ok"}


def test_no_session_and_no_kis_reads(holiday: SimDay) -> None:
    assert [(e["state"], e["trade_date"]) for e in holiday.session_events] == [("IDLE", None)]
    assert holiday.kis.gets() == []  # 장이 없다 — poller·수집·legacy 모두 쉬었다
    assert holiday.krx.seen == []
    assert holiday.external_runs == [] and holiday.ws_results == []


def test_token_issued_only_by_auth_twice_23_hours_apart(holiday: SimDay) -> None:
    posts = holiday.kis.posts(TOKEN_PATH)
    assert [(p.at.astimezone(KST).strftime("%m-%d %H:%M"), p.consumer) for p in posts] == [
        ("10-05 05:00", "auth"),
        ("10-06 04:00", "auth"),
    ]
    assert posts[1].at - posts[0].at >= timedelta(hours=23)
    assert {c.consumer for c in holiday.kis.posts()} == {"auth"}
