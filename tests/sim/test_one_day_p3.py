"""하루 운영 확장 — P2 24시간 창에 P3 실제 처리기를 붙인다(docs/p3_design.md §8.3, 묶음 S).

창: 2026-10-07(수) 05:00 ~ 10-08(목) 09:30 KST(28.5시간 — 다음 날 08:05 KRX 확정·08:40 보드
확정까지).
켜진 P3 작업 — `krx.daily`·`market.close_collect`·`flows.intraday`·`market.intraday`·`board.daily`·
`board.confirm`·`etf.collect`(가짜 운용사 9곳)·`public.export` — 은 실제 처리기, 저장소는 메모리.
나머지 작업은 P2 시뮬레이션 처리기 그대로(같은 KIS 버킷·같은 선점 장부).

단언(§8.3)
- 중복 수집 0(선점 장부의 모든 키가 done 1회, 충돌 0), KIS 어떤 1초에도 ≤ 4, 발급은 auth 에서만.
- P3 작업이 기대한 날짜마다 ok, 그 실행이 잡은 데이터 키 수 = 등록부 collects 를 펼친 수
  (하드코딩 없이).
- 원장 quality 전이: 장중 `kis.prelim`(estimated) → 마감 `kis`(ok, 차이는 investor_revision) →
  다음 날 `krx`(ok). KRX 대조에서 어긋난 KIS 행은 invalid + `prv_market.eod_reconcile` 행.
- 보드 artifact 5종: 16:20 잠정(estimated) → 다음 날 08:40 확정(ok, confirm 기록).
- 검산 ③·분할 감지 기록이 `krx.daily` 끝 단계에서 돈다(메인이 본 열린 항목 — record_flow_checks).
- 공개 산출물: calendar·events·manifest, 로그인 등급 출처 이름 0(`check_tree`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime

import pytest

from kbj.core.quality import Quality
from kbj.data.catalog import keys_for
from kbj.services.public_export.manifest import check_tree
from kbj.services.scheduler.registry import Registry
from tests.sim.conftest import at
from tests.sim.harness import ROOT, SimDay, SimOptions

pytestmark = pytest.mark.sim

START = at(10, 7, 5)
HOURS = 28.5
D6, D7, D8 = date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)
CODE = "990010"  # 가짜 KIS(장중 가집계·순위)와 가짜 KRX 에 둘 다 있는 종목
REG = Registry.load(ROOT / "config" / "jobs.yaml")
ARTIFACTS = ("universe_meta", "newhigh", "sectors", "events", "rankings")

Shot = dict[str, object]


class Watch:
    """시각마다 원장·보드 상태를 찍어 둔다(hooks)."""

    def __init__(self) -> None:
        self.shots: dict[str, Shot] = {}

    def at(self, name: str, when: datetime) -> tuple[datetime, Callable[[SimDay], None]]:
        def take(sim: SimDay) -> None:
            snaps, _ = sim.repos.market.snapshot(D7)
            snap = snaps.get(CODE)
            inv = sim.repos.flows.days([CODE], D7, D7).get(CODE, [])
            newhigh = sim.repos.board.artifact(D7, "newhigh")
            self.shots[name] = {
                "snap": None if snap is None else (snap.source, snap.quality),
                "inv": sorted({(r.source, r.quality) for r in inv}),
                "board": None if newhigh is None else (newhigh.source, newhigh.quality),
            }

        return when, take


def _issuers_next_day(sim: SimDay) -> None:
    sim.issuers.latest = D8  # 가짜 운용사가 10-08 공시를 낸다


@pytest.fixture(scope="module")
def watch() -> Watch:
    return Watch()


@pytest.fixture(scope="module")
def day(watch: Watch) -> Iterator[SimDay]:
    hooks = (
        watch.at("intraday", at(10, 7, 15, 30)),
        watch.at("close", at(10, 7, 16, 10)),
        watch.at("board_daily", at(10, 7, 16, 30)),
        (at(10, 8, 0, 0), _issuers_next_day),
        watch.at("krx_next_day", at(10, 8, 8, 30)),
        watch.at("confirmed", at(10, 8, 9, 0)),
    )
    sim = SimDay(SimOptions(start=START, hours=HOURS, p3=True, hooks=hooks))
    try:
        yield sim.run()
    finally:
        sim.close()


# ── 중복 0·한도·발급 ─────────────────────────────────────────────────────────────────────
def test_no_duplicate_collection_and_every_claim_done(day: SimDay) -> None:
    done = day.done_keys()
    assert done and max(done.values()) == 1
    # 선점 거절은 '이미 받은 키'를 다시 잡으려 한 것뿐이다(중복 수집을 막은 기록 — 10-07·10-08 연속
    # 발화하는 macro.monthly, 야간·주간 세션이 같은 거래일 마스터를 잡는 GX). P3 작업의 거절은 0
    p3 = set(EXPECTED_AS_OF) | {"flows.intraday", "market.intraday"}
    for key, job in day.claims.refused:
        assert job not in p3, (key, job)
        assert done[key] == 1, (key, job)
    # 창 끝(10-08 09:30)에 진행 중인 키(GX 마스터·장중 슬롯 등)는 claimed 로 남을 수 있다 —
    # 실패만 없으면 된다
    assert [r for r in day.claims.rows if r.status == "failed"] == []
    assert [r for r in day.claims.rows if r.job in p3 and r.status != "done"] == []
    owners = day.jobs_per_dataset()
    assert {ds: jobs for ds, jobs in owners.items() if len(jobs) > 1} == {}


def test_kis_limit_and_only_auth_issues(day: SimDay) -> None:
    assert day.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in day.kis.posts()} == {"auth"}
    assert all(c.status == 200 for c in day.kis.gets())


# ── P3 작업이 날짜마다 돌았다 ─────────────────────────────────────────────────────────────
EXPECTED_AS_OF = {
    "krx.daily": {"2026-10-06", "2026-10-07"},  # 10-07·10-08 08:05 — 전 거래일
    "board.confirm": {"2026-10-06", "2026-10-07"},
    "market.close_collect": {"2026-10-07"},
    "board.daily": {"2026-10-07"},
    "etf.collect": {"2026-10-07", "2026-10-08"},
    "public.export": {"2026-10-07", "2026-10-08"},
}


def test_p3_jobs_ran_ok_and_claimed_what_the_registry_says(day: SimDay) -> None:
    final = day.final_runs()
    done = day.done_keys()
    for job, want in EXPECTED_AS_OF.items():
        got = {a for (j, a), r in final.items() if j == job}
        assert got == want, job
        for as_of in want:
            r = final[(job, as_of)]
            assert r.status == "ok", (job, as_of, dict(r.detail))
            spec = REG.by_name(job)
            keys = [
                k
                for c in spec.collects
                for k in keys_for(day.catalog[c.dataset_id], as_of, c.venues or None)
            ]
            assert all(done[k] == 1 for k in keys), (job, as_of)
            assert int(r.detail.get("collected", 0)) == len(keys), (job, as_of)
    # 장중 두 작업: 그날 정규장 슬롯 전부(09:00~15:30, 양 끝 포함 40)
    for job in ("flows.intraday", "market.intraday"):
        ok = [
            a
            for (j, a), r in final.items()
            if j == job and r.status == "ok" and a.startswith("2026-10-07")
        ]
        assert len(ok) == 40 and min(ok).endswith("T09:00") and max(ok).endswith("T15:30"), job


# ── 원장 quality 전이 ─────────────────────────────────────────────────────────────────────
def test_ledger_quality_goes_estimated_ok_then_krx(day: SimDay, watch: Watch) -> None:
    s = watch.shots
    assert s["intraday"]["snap"] == ("kis.prelim", Quality.ESTIMATED)
    assert s["intraday"]["inv"] and {q for _, q in s["intraday"]["inv"]} == {Quality.ESTIMATED}  # type: ignore[union-attr]
    assert s["close"]["snap"] == ("kis", Quality.OK)
    assert {src for src, _ in s["close"]["inv"]} == {"kis"}  # type: ignore[union-attr]
    assert s["krx_next_day"]["snap"] == ("krx", Quality.OK)
    # 마감 수집이 장중 잠정 행을 덮으며 차이를 남겼다(같은 트랜잭션 — D-P3-7)
    revs = day.repos.flows.revisions(D7)
    assert revs and all(r.final_source == "kis" for r in revs)


def test_krx_reconcile_rows_and_invalid_kis_rows(day: SimDay) -> None:
    rows = day.repos.market.reconcile(D7)
    assert rows
    checked = {r.code for r in rows}
    kis = {s.code: s for s in day.repos.market.snapshots(D7, source="kis")}
    assert checked <= set(kis)
    for r in rows:
        if r.verdict == "mismatch":
            assert (
                kis[r.code].quality == Quality.INVALID
            )  # 어긋난 KIS 행은 invalid(조용히 두지 않음)
    detail = day.final_runs()[("krx.daily", "2026-10-07")].detail
    assert detail["reconcile"]["checked"] == len(checked)  # type: ignore[index]


def test_etf_flow_checks_run_at_the_end_of_krx_daily(day: SimDay) -> None:
    detail = day.final_runs()[("krx.daily", "2026-10-07")].detail
    checks = detail["etf_checks"]
    assert checks["flows"] > 0  # type: ignore[index]  # 10-06·10-07 ETF 일별 → 그날 흐름
    assert checks["c3_failed"] == len(day.repos.flows.checks(D7, "etf"))  # type: ignore[index]


# ── 보드 ─────────────────────────────────────────────────────────────────────────────────
def test_board_estimated_then_confirmed(day: SimDay, watch: Watch) -> None:
    assert watch.shots["board_daily"]["board"] is not None
    _, q = watch.shots["board_daily"]["board"]  # type: ignore[misc]
    assert q == Quality.ESTIMATED
    for name in ARTIFACTS:
        a = day.repos.board.artifact(D7, name)
        assert a is not None, name
        assert a.quality == Quality.OK and "krx" in a.source, name
    newhigh = day.repos.board.artifact(D7, "newhigh")
    assert newhigh is not None and "confirm" in newhigh.payload
    assert newhigh.payload["confirm"]["previous_quality"] == "estimated"
    assert day.repos.board.artifact(D6, "newhigh") is not None  # 10-07 08:40 이 10-06 을 확정


# ── ETF·공개 ─────────────────────────────────────────────────────────────────────────────
def test_etf_holdings_and_changes(day: SimDay) -> None:
    assert day.repos.etf.funds()
    assert day.repos.etf.changes(D8)  # 10-07 → 10-08 공시 사이 변동
    meta = day.repos.etf.meta()
    assert meta and all(m.etf_type is not None for m in meta.values())  # krx.daily 가 새 ETF 분류


def test_public_export_files_are_public_only(day: SimDay) -> None:
    names = sorted(p.name for p in day.public_out.iterdir())
    assert names == ["calendar.json", "events.json", "manifest.json"]
    assert check_tree(day.public_out) == []
