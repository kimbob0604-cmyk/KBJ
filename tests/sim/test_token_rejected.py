"""토큰 거절 변형 — KIS 가 `EGW00123` 을 준다(설계 §10.5, §3.4 거절 폭주 가드).

창 2026-10-06 13:00 ~ 14:30 KST(빈 Redis — 13:00 에 첫 발급).

1. 14:00 scheduler 의 첫 KIS 조회가 한 번 거절된다 → 읽는 쪽은 신고만(`kis:token:rejected`),
   auth 가 다음 step(14:00:30)에 **한 번** 다시 발급한다(warning `token_rejected`). 그
   회차(14:00 장중 작업)는 실패로 기록된다(재시도 0 — 거절한 토큰을 그 리더는 다시 쓰지
   않는다).
2. 14:05~14:11 scheduler·legacy 의 조회가 계속 거절된다(새 토큰 발급 10분 안) → auth 는
   **다시 발급하지 않고** critical `token_rejected_after_issue` 한 번. 두 프로세스가 거듭
   신고해도 추가 발급 0. 그 뒤 scheduler 리더는 그 토큰을 쓰지 않으므로 장중 회차가
   실패한다 — 사람이 볼 critical 이 남는다.

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
        consumers=["scheduler", "legacy"],
    )


def _legacy_quote(sim: SimDay) -> None:
    sim.legacy_quote()  # 다른 프로세스(브리지)도 새 토큰으로 거절당하고 신고한다


@pytest.fixture(scope="module")
def rejected() -> SimDay:
    sim = SimDay(
        SimOptions(
            start=at(10, 6, 13),
            hours=1.5,
            ws=False,
            legacy_calls=False,
            hooks=(
                (at(10, 6, 13, 59), _once),
                (at(10, 6, 14, 4), _storm),
                (at(10, 6, 14, 5, 10), _legacy_quote),
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
        ("legacy", second.value),  # 14:05:10
        ("scheduler", second.value),  # 14:10
    ]
    assert [s for _, s, _ in rejected.legacy_results] == [503]  # 브리지는 503 봉투(토큰 없음)


def test_only_auth_issues(rejected: SimDay) -> None:
    by = Counter(c.consumer for c in rejected.kis.calls if c.method == "POST")
    assert by == {"auth": 3}  # 접근토큰 2 + 접속키 1


def test_failed_slots_are_recorded_and_keys_not_duplicated(rejected: SimDay) -> None:
    final = rejected.final_runs()
    assert final[("flows.intraday", "2026-10-06T13:50")].status == "ok"
    for slot in ("14:00", "14:10", "14:20"):  # 14:20 은 거절한 토큰을 다시 쓰지 않아 실패
        assert final[("flows.intraday", f"2026-10-06T{slot}")].status == "failed", slot
    assert max(rejected.done_keys().values()) == 1
    assert rejected.claims.refused == []
    alerts = {r.subject for r in rejected.notify_log.of("ops.job_failed")}
    assert "flows.intraday:2026-10-06T14:00" in alerts


def test_stale_token_report_does_not_mask_current_rejection(
    simulate: Callable[[SimOptions], SimDay],
) -> None:
    """legacy 브리지가 13:30 에 첫 토큰을 메모리에 쥔 채, 14:00:30 재발급 뒤 14:40(발급 40분 뒤 —
    가드 밖)에 scheduler(새 토큰)와 legacy(지난 토큰)가 함께 거절당한다. auth 는 새 토큰을 다시
    발급하거나 적어도 알려야 한다."""

    def storm(sim: SimDay) -> None:
        sim.kis.inject(
            "EGW00123",
            1000,
            start=at(10, 6, 14, 40),
            until=at(10, 6, 14, 41),
            consumers=["scheduler", "legacy"],
        )

    sim = simulate(
        SimOptions(
            start=at(10, 6, 13),
            hours=1.75,
            ws=False,
            legacy_calls=False,
            hooks=(
                (at(10, 6, 13, 30), _legacy_quote),  # legacy 리더가 첫 토큰을 메모리에
                (at(10, 6, 13, 59), _once),
                (at(10, 6, 14, 39), storm),
                (at(10, 6, 14, 40, 10), _legacy_quote),  # scheduler(14:40:00) 신고 뒤
            ),
        )
    )
    reissued = len(sim.kis.posts(TOKEN_PATH)) >= 3
    alerted = any(
        e.kind in ("token_rejected", "token_rejected_after_issue") and e.at >= at(10, 6, 14, 40)
        for e in sim.auth_health.events
    )
    assert reissued or alerted
