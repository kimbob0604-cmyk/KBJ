"""Redis 순간 장애 변형 — 2026-10-06 12:00:00 ~ 12:01:00 KST 동안 접속 실패(설계 §10.5).

창 11:30 ~ 12:30 KST(빈 Redis — 11:30 첫 발급). 기대:

- auth 는 발급하지 않는다(받아도 둘 곳이 없다) — warning `token_cache_error`, 추가 발급 0.
- 그 사이 돈 수집 작업(12:00 공시 피드·장중 수급·관심종목)과 워치독은 실패로 기록되고 쥔
  키를 놓는다. 재시도 규칙대로 다음 회차(12:01 공시·12:10 장중)가 받는다. 데이터 키 중복 0.
- 실패 알림은 대기열(Redis)에 못 넣으니 health `job_failed_notify_failed` 로 남는다
  (삼키지 않는다).
- 장애가 끝나면 세션 상태 키가 다시 채워진다.
"""

from __future__ import annotations

import json

import pytest

from kbj.services.auth.issuer import TOKEN_PATH
from kbj.store.redis_keys import SESSION_STATE
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions

pytestmark = pytest.mark.sim

DOWN = (at(10, 6, 12), at(10, 6, 12, 1))


@pytest.fixture(scope="module")
def blip() -> SimDay:
    sim = SimDay(
        SimOptions(start=at(10, 6, 11, 30), hours=1, redis_down=DOWN, ws=False, legacy_calls=False)
    )
    try:
        return sim.run()
    finally:
        sim.close()


def test_auth_does_not_issue_while_redis_is_down(blip: SimDay) -> None:
    posts = [p.at.astimezone(KST).strftime("%H:%M") for p in blip.kis.posts(TOKEN_PATH)]
    assert posts == ["11:30"]
    errs = blip.auth_health.of("token_cache_error")
    assert errs and all(DOWN[0] <= e.at < DOWN[1] for e in errs)
    assert not [
        s
        for s in blip.auth_statuses
        if DOWN[0] <= s.at < DOWN[1] and s["token"].action == "refreshed"
    ]


def test_jobs_during_the_blip_fail_and_release_keys(blip: SimDay) -> None:
    final = blip.final_runs()
    failed = {k for k, r in final.items() if r.status == "failed"}
    assert failed == {
        ("filings.dart_feed", "2026-10-06T12:00"),
        # flows.intraday 는 P3 부터 슬롯 안 재시도(20·40초 — D-P3-14)로 같은 슬롯에 회복한다
        ("rules.intraday", "2026-10-06T12:00"),
        ("ops.watchdog", "2026-10-06T12:00"),
    }
    released = {(r.job, r.key.as_of) for r in blip.claims.rows if r.status == "failed"}
    assert ("rules.intraday", "2026-10-06T12:00") in released
    assert final[("flows.intraday", "2026-10-06T12:00")].status == "ok"  # 재시도로 회복


def test_next_runs_recover_and_no_key_is_collected_twice(blip: SimDay) -> None:
    final = blip.final_runs()
    assert final[("filings.dart_feed", "2026-10-06T12:01")].status == "ok"
    assert final[("flows.intraday", "2026-10-06T12:10")].status == "ok"
    assert final[("ops.watchdog", "2026-10-06T11:30")].status == "ok"
    assert max(blip.done_keys().values()) == 1
    assert blip.claims.refused == []


def test_failures_are_not_swallowed(blip: SimDay) -> None:
    kinds = set(blip.health_kinds())
    assert {"job_failed", "job_failed_notify_failed"} <= kinds
    assert "outbox_redis_failed" in kinds  # notifier 도 장애를 알린다


def test_state_is_republished_after_the_blip(blip: SimDay) -> None:
    raw = blip.sched_redis.get(SESSION_STATE)
    assert isinstance(raw, bytes) and json.loads(raw)["state"] == "DAY"
