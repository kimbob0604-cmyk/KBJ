"""메모리 저장소 규칙(docs/p3_design.md §1.1) — 우선순위·덮어쓰기·차이 기록·manual 우선.

같은 시험 묶음(`repo_cases.CASES`)을 통합 시험 `tests/integration/test_repos_pg.py` 가 Postgres 로
돈다. 여기서는 메모리 구현으로 돌고, 메모리에만 있는 확인(Pg extra 모양 흉내 등)을 더한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Investor, InvestorDay
from kbj.store.repos import Repos, memory_repos
from kbj.store.repos.flows import StoredInvestorDay, plan_investor_upsert
from kbj.store.repos.memory import MemoryRepos
from tests.unit.store.repo_cases import CASE_IDS, CASES, snap

NOW = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
D = date(2026, 10, 6)


def as_repos(m: MemoryRepos) -> Repos:
    # pyright 가 메모리 구현이 Protocol(MarketRepo 등)을 만족하는지 여기서 확인한다
    return Repos(market=m.market, flows=m.flows, board=m.board, etf=m.etf)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_memory_repos_follow_the_rules(case: Callable[[Repos], None]) -> None:
    case(as_repos(memory_repos()))


def test_snapshot_extra_matches_pg_shape() -> None:
    m = memory_repos()
    m.market.upsert_snapshots([snap("000010", D, 1.0, flags=("halted",))], loaded_by="t")
    assert m.market.snapshot_extra(D, "000010", "kis", "KRX") == {
        "kind": "common",
        "status_flags": ["halted"],
    }
    m.market.set_snapshot_quality(
        D, "000010", source="kis", venue="KRX", quality=Quality.INVALID, note="거래대금 다름"
    )
    assert m.market.snapshot_extra(D, "000010", "kis", "KRX")["quality_note"] == "거래대금 다름"
    m.market.upsert_snapshots([snap("000010", D, 1.0, flags=None)], loaded_by="t")
    assert m.market.snapshot_extra(D, "000010", "kis", "KRX") == {"kind": "common"}


def _row(value: int, source: str, q: Quality) -> InvestorDay:
    return InvestorDay("000010", D, Investor.FOREIGN, value, None, source, "KRX", q)


def test_plan_picks_best_worse_row_and_counts_skips() -> None:
    """잠정 행이 둘(kis.prelim·kis estimated)이면 차이는 더 앞선 잠정(kis estimated)과 비교하고
    둘 다 지운다. 같은 호출 안 같은 키는 뒤의 것이 이긴다."""
    existing = [
        StoredInvestorDay(_row(10, "kis.prelim", Quality.ESTIMATED), NOW),
        StoredInvestorDay(_row(20, "kis", Quality.ESTIMATED), None),
    ]
    plan = plan_investor_upsert(
        [_row(1, "krx", Quality.OK), _row(30, "krx", Quality.OK)], existing, revise=True, now=NOW
    )
    assert [w.net_value for w in plan.write] == [30]
    assert {d.source for d in plan.delete} == {"kis.prelim", "kis"}
    (rev,) = plan.revisions
    assert (rev.est_value, rev.final_value, rev.diff, rev.est_ts) == (20, 30, 10, None)
    skipped = plan_investor_upsert(
        [_row(5, "kis.prelim", Quality.ESTIMATED)],
        [StoredInvestorDay(_row(20, "kis", Quality.OK), NOW)],
        revise=True,
        now=NOW,
    )
    assert (skipped.write, skipped.delete, skipped.skipped) == ((), (), 1)


def test_plan_diff_is_none_when_a_value_is_missing() -> None:
    est = InvestorDay("000010", D, Investor.FOREIGN, None, 7, "kis.prelim", "KRX", "estimated")  # type: ignore[arg-type]
    plan = plan_investor_upsert(
        [_row(5, "kis", Quality.OK)], [StoredInvestorDay(est, NOW)], revise=True, now=NOW
    )
    assert plan.revisions[0].diff is None and plan.revisions[0].est_value is None


def test_plan_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="naive"):
        plan_investor_upsert([], [], revise=True, now=datetime(2026, 10, 6))  # noqa: DTZ001


def test_repr_hides_connection_info() -> None:
    from kbj.store.repos import pg_repos

    def boom() -> object:  # 연결하지 않는다 — repr 만 본다
        raise AssertionError("연결하면 안 된다")

    repos = pg_repos(boom)  # type: ignore[arg-type]
    assert repr(repos.market) == "PgMarketRepo()" and "boom" not in repr(repos)
