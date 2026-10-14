"""토큰 거절 변형 — KIS 가 `EGW00123` 을 준다(설계 §10.5, §3.4 거절 폭주 가드).

창 2026-10-06 13:00 ~ 14:30 KST(빈 Redis — 13:00 에 첫 발급).

1. 14:00 scheduler 의 첫 KIS 조회(`flows.intraday` 가집계)가 한 번 거절된다 → 읽는 쪽은
   신고만(`kis:token:rejected`), auth 가 다음 step(14:00:30)에 **한 번** 다시 발급한다(warning
   `token_rejected`). 거절당한 14:00 1차 시도들은 실패로 기록되고, 슬롯 안 재시도가 있는
   `flows.intraday` 는 새 토큰으로 회복한다. `rules.intraday`(재시도 0)는 같은 회차
   `flows.intraday` 를 무른 의존 `wait_min: 5` 로 기다리므로(docs/p8_design.md — 장중 수급 라이브)
   그 회복 뒤 14:01 에 새 토큰으로 돌아 ok 다.
2. 14:05~14:11 scheduler(P5 매분 F2 작업이 14:05:00 에 먼저)·reader(P9 전 legacy)의 조회가 계속
   거절된다(새 토큰 발급 10분 안) → auth 는
   **다시 발급하지 않고** critical `token_rejected_after_issue` 한 번. 두 프로세스가 거듭
   신고해도 추가 발급 0. 그 뒤 scheduler 리더는 그 토큰을 쓰지 않으므로 장중 회차가
   실패한다(14:10 `flows.intraday` 가 재시도까지 실패한 뒤 그것을 기다린 `rules.intraday` 도
   실패) — 사람이 볼 critical 이 남는다.

마지막 시험은 지난 토큰을 아직 메모리에 쥔 다른 프로세스의 늦은 거절 신고가 현재 토큰의 신고를
덮어쓰지 않는지 본다(처음에는 `xfail strict` 로 고정했던 결함 — 최종 점검에서 `RedisTokenCache.
report_rejected` 가 캐시의 값이 이미 다른 토큰이면 쓰지 않게 고쳤다).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest

from kbj.services.auth.issuer import TOKEN_PATH
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions

pytestmark = pytest.mark.sim


def _once(sim: SimDay) -> None:
    sim.kis.inject("EGW00123", 1, start=at(10, 6, 14), consumers=["scheduler"])


def _storm(sim: SimDay) -> None:
    sim.kis.inject(
        "EGW00123",
        1000,
        start=at(10, 6, 14, 5),
        until=at(10, 6, 14, 11),
        consumers=["scheduler", "reader"],
    )


def _reader_quote(sim: SimDay) -> None:
    # 다른 프로세스(읽기 전용 — P9 전 legacy 브리지)도 새 토큰으로 거절당하고 신고한다
    sim.reader_quote()


@pytest.fixture(scope="module")
def rejected() -> SimDay:
    sim = SimDay(
        SimOptions(
            start=at(10, 6, 13),
            hours=1.5,
            ws=False,
            reader_calls=False,
            hooks=(
                (at(10, 6, 13, 59), _once),
                (at(10, 6, 14, 4), _storm),
                (at(10, 6, 14, 5, 10), _reader_quote),
            ),
        )
    )
    try:
        return sim.run()
    finally:
        sim.close()


def _hms(sim: SimDay) -> list[str]:
    return [p.at.astimezone(KST).strftime("%H:%M:%S") for p in sim.kis.posts(TOKEN_PATH)]


def test_first_rejection_reissues_exactly_once(rejected: SimDay) -> None:
    assert _hms(rejected) == ["13:00:00", "14:00:30"]  # 신고 뒤 다음 auth step 에 한 번
    warn = rejected.auth_health.of("token_rejected")
    assert len(warn) == 1 and warn[0].severity == "warning"
    assert "scheduler" in warn[0].detail


def test_rejection_storm_after_issue_is_closed_without_reissue(rejected: SimDay) -> None:
    crit = rejected.auth_health.of("token_rejected_after_issue")
    assert [(e.severity, e.at.astimezone(KST).strftime("%H:%M")) for e in crit] == [
        ("critical", "14:05")
    ]
    assert rejected.kis.token_posts == 2  # 두 프로세스가 거듭 거절당해도 추가 발급 0
    first, second = rejected.kis.tokens()
    storm = [c for c in rejected.kis.gets() if c.msg_cd == "EGW00123"]
    assert [(c.consumer, c.bearer) for c in storm] == [
        ("scheduler", first.value),  # 14:00 한 번
        # P5: 장중 투자자 1분(flows.market_minute — 켬, ADR 0019·D-P5-21)은 scheduler 가 매분
        # 부른다 → 폭주 첫 거절은 14:05:00 scheduler. 그 뒤 scheduler 리더는 이 토큰을 다시 쓰지
        # 않으므로(14:10 장중 회차는 TokenUnavailable) 남은 거절은 reader 뿐
        ("scheduler", second.value),  # 14:05:00
        ("reader", second.value),  # 14:05:10 훅
    ]
    assert [s for _, s, _ in rejected.reader_results] == [503]  # 토큰 없음(TokenUnavailable)


def test_only_auth_issues(rejected: SimDay) -> None:
    by = Counter(c.consumer for c in rejected.kis.calls if c.method == "POST")
    assert by == {"auth": 3}  # 접근토큰 2 + 접속키 1


def test_failed_slots_are_recorded_and_keys_not_duplicated(rejected: SimDay) -> None:
    final = rejected.final_runs()
    assert final[("flows.intraday", "2026-10-06T13:50")].status == "ok"
    # 14:00 거절은 flows.intraday 1차 시도가 받는다. flows.intraday 는 P3 부터 슬롯 안 재시도
    # (20·40초 — D-P3-14)라 auth 가 14:00:30 에 다시 발급한 토큰으로 같은 슬롯에 회복하고,
    # 재시도 0 인 rules.intraday 는 그 회차 flows.intraday 를 무른 의존(wait_min 5 — 장중 수급
    # 라이브)으로 기다렸다가 새 토큰으로 돈다 — 거절을 삼킨 것이 아니라 거절 뒤에 처음 돌았다
    flows_ok = final[("flows.intraday", "2026-10-06T14:00")]
    rules_1400 = final[("rules.intraday", "2026-10-06T14:00")]
    assert flows_ok.status == "ok" and rules_1400.status == "ok"
    assert rules_1400.attempt == 1 and rules_1400.started_at is not None
    assert flows_ok.started_at is not None and rules_1400.started_at >= flows_ok.started_at
    assert rules_1400.started_at >= at(10, 6, 14, 0, 30)  # 재발급 뒤
    key = ("flows.intraday", "2026-10-06T14:00", 1)
    first_try = [r for r in rejected.runs.records.values() if (r.job, r.as_of, r.attempt) == key]
    assert [r.status for r in first_try] == ["failed"]  # 거절은 기록됐다
    for slot in ("14:10", "14:20"):  # 14:20 은 거절한 토큰을 다시 쓰지 않아 실패
        assert final[("flows.intraday", f"2026-10-06T{slot}")].status == "failed", slot
        # 기다린 rules.intraday 도 거절한 토큰을 쓰지 않아 실패(삼키지 않는다)
        assert final[("rules.intraday", f"2026-10-06T{slot}")].status == "failed", slot
    assert max(rejected.done_keys().values()) == 1
    assert rejected.claims.refused == []
    alerts = {r.subject for r in rejected.notify_log.of("ops.job_failed")}
    assert {"rules.intraday:2026-10-06T14:10", "flows.intraday:2026-10-06T14:10"} <= alerts


def test_stale_token_report_does_not_mask_current_rejection(
    simulate: Callable[[SimOptions], SimDay],
) -> None:
    """reader 프로세스(P9 전 legacy 브리지)가 13:30 에 첫 토큰을 메모리에 쥔 채, 14:00:30 재발급
    뒤 14:40(발급 40분 뒤 — 가드 밖)에 scheduler(새 토큰)와 reader(지난 토큰)가 함께 거절당한다.
    auth 는 새 토큰을 다시 발급하거나 적어도 알려야 한다."""

    def storm(sim: SimDay) -> None:
        sim.kis.inject(
            "EGW00123",
            1000,
            start=at(10, 6, 14, 40),
            until=at(10, 6, 14, 41),
            consumers=["scheduler", "reader"],
        )

    sim = simulate(
        SimOptions(
            start=at(10, 6, 13),
            hours=1.75,
            ws=False,
            reader_calls=False,
            hooks=(
                (at(10, 6, 13, 30), _reader_quote),  # reader 리더가 첫 토큰을 메모리에
                (at(10, 6, 13, 59), _once),
                (at(10, 6, 14, 39), storm),
                (at(10, 6, 14, 40, 10), _reader_quote),  # scheduler(14:40:00) 신고 뒤
            ),
        )
    )
    reissued = len(sim.kis.posts(TOKEN_PATH)) >= 3
    alerted = any(
        e.kind in ("token_rejected", "token_rejected_after_issue") and e.at >= at(10, 6, 14, 40)
        for e in sim.auth_health.events
    )
    assert reissued or alerted
