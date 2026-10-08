"""장중 1시간 무결측 — PLAN §8 P3 완료 기준(docs/p3_design.md §0.3·§8.3, 묶음 S).

창: 2026-10-07(수) 09:55 ~ 11:05 KST. `flows.intraday`·`market.intraday` 는 **실제 처리기**
(`kbj.services.collectors.market_intraday`)가 가짜 KIS(같은 시계·앱키 리미터·auth 토큰)와 메모리
저장소로 돈다. 다른 켜진 작업·legacy GX 흉내도 같은 창에서 같은 KIS 버킷을 쓴다(P2 하네스).

단언(§8.3)
① 키 — 슬롯 10:00~10:50(6개) × 등록부 collects 를 펼친 데이터 키가 **각각 done 1회**, 끝 상태가
  done 이 아닌 키 0.
② 행 — 슬롯마다 지수·업종·순위·가집계·감시 ETF 행 수 = 기대(설정·가짜 서버 상수에서 계산), 모든 행
  ts = 슬롯 시작, quality estimated. 모자란 슬롯 0.
③ 시간 — 그 슬롯의 마지막 KIS 호출이 슬롯 + 9분(`deadline_min`) 전.
④ 한도·토큰 — 어떤 1초 창에도 KIS ≤ 4건, 발급은 auth 에서만(auth 밖 POST 0).
⑤ 원장 — 일별 원장 오늘 행(kis.prelim, estimated)이 슬롯마다 갱신되고, 그 시점 마지막 슬롯의 이력
  표 값과 같다.
변형 — (a) 10:20 FHPST02400000 HTTP 500 1회, (b) 10:30 EGW00201 3연속, (c) 10:40 Redis 5초 끊김,
(d) 10:31 scheduler 재기동 — 넷 다 결측 0·중복 0.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta

import pytest

from kbj.core.quality import Quality
from kbj.data.private.kis.investors import INST_FOREIGN_WHO
from kbj.services.collectors.market_intraday import INVESTOR_MARKETS
from kbj.services.scheduler.registry import Registry
from tests.fakes.kis_server import RANK_ROWS
from tests.sim.conftest import KST, at
from tests.sim.harness import ROOT, SimDay, SimOptions
from tests.sim.p3 import N_SEED_ETFS, expected_rows, intraday_keys, seed_watch_etfs, slots

pytestmark = pytest.mark.sim

DAY = date(2026, 10, 7)
PREV = date(2026, 10, 6)
START = at(10, 7, 9, 55)
HOURS = 70 / 60  # 09:55 ~ 11:05
SLOTS = slots(at(10, 7, 10, 0), at(10, 7, 10, 50))
REG = Registry.load(ROOT / "config" / "jobs.yaml")
P3_TRS = ("FHPTJ04400000", "FHPST01710000", "FHPST02400000", "FHPUP02100000", "FHPUP02140000")


class Ledger:
    """슬롯 끝(+9분)마다 원장 오늘 행과 그 슬롯 이력 표를 찍어 둔다(단언 ⑤)."""

    def __init__(self) -> None:
        self.shots: list[tuple[datetime, dict[tuple[str, str], tuple[int | None, str, str]],
                               dict[tuple[str, str], int | None]]] = []  # fmt: skip

    def hook(self, slot: datetime) -> tuple[datetime, Callable[[SimDay], None]]:
        def take(sim: SimDay) -> None:
            days = sim.repos.flows.days(None, DAY, DAY)
            ledger = {
                (r.code, str(r.investor)): (r.net_value, r.source, str(r.quality))
                for rows in days.values()
                for r in rows
            }
            hist = {
                (q.code, str(q.investor)): q.net_value
                for q in sim.repos.flows.intraday(slot, slot + timedelta(minutes=1))
            }
            self.shots.append((slot, ledger, hist))

        return slot + timedelta(minutes=9), take


def _opts(
    ledger: Ledger | None = None,
    *,
    more: Callable[[SimDay], None] | None = None,
    redis_down: tuple[datetime, datetime] | None = None,
    restarts: tuple[datetime, ...] = (),
) -> SimOptions:
    hooks = tuple(ledger.hook(at(10, 7, 10, m)) for m in range(0, 60, 10)) if ledger else ()
    prepare = (seed_watch_etfs(PREV),) + ((more,) if more is not None else ())
    return SimOptions(
        start=START,
        hours=HOURS,
        p3=True,
        prepare=prepare,
        hooks=hooks,
        redis_down=redis_down,
        restarts=restarts,
    )


@pytest.fixture(scope="module")
def ledger() -> Ledger:
    return Ledger()


@pytest.fixture(scope="module")
def hour(ledger: Ledger) -> Iterator[SimDay]:
    sim = SimDay(_opts(ledger))
    try:
        yield sim.run()
    finally:
        sim.close()


def _ts(label: str) -> datetime:
    return datetime.strptime(label, "%Y-%m-%dT%H:%M").replace(tzinfo=KST)


def assert_no_gap(sim: SimDay) -> None:
    """단언 ①②③ — 변형 시험도 같은 것을 본다."""
    want_rows = expected_rows(sim, N_SEED_ETFS)
    n_inv = (
        RANK_ROWS * len(INVESTOR_MARKETS) * len(INST_FOREIGN_WHO) * len(sim.markets().kis.venues)
    )
    done = sim.done_keys()
    lo, hi = at(10, 7, 9, 0), at(10, 7, 12, 0)
    idx = Counter(q.ts for q in sim.repos.market.index_quotes(lo, hi))
    sec = Counter(q.ts for q in sim.repos.market.sector_quotes(lo, hi))
    rank = Counter(q.ts for q in sim.repos.market.rank_rows(lo, hi))
    inv = Counter(q.ts for q in sim.repos.flows.intraday(lo, hi))
    etf = Counter(q.ts for q in sim.repos.etf.quotes(lo, hi))
    gets = [c for c in sim.kis.gets("scheduler") if c.tr_id in P3_TRS and c.status == 200]
    for label in SLOTS:
        keys = intraday_keys(REG, sim, label)
        assert len(keys) == 5, keys  # 장중 데이터셋 5개(지수·업종·가집계·순위·ETF)
        for k, job in keys.items():
            assert done[k] == 1, (label, k.label())
            row = sim.claims.live(k)
            assert row is not None and row.status == "done" and row.job == job, (label, k.label())
        ts = _ts(label)
        got = (idx[ts], sec[ts], rank[ts], inv[ts], etf[ts])
        want = (want_rows.index, want_rows.sector, want_rows.rank, n_inv, want_rows.etf_quote)
        assert got == want, (label, got, want)
        last = max(c.at for c in gets if ts <= c.at < ts + timedelta(minutes=10))
        assert last < ts + timedelta(minutes=9), (label, last)  # 단언 ③ deadline_min
    # 슬롯마다 두 작업의 끝 실행 기록이 ok
    final = sim.final_runs()
    for label in SLOTS:
        for job in ("flows.intraday", "market.intraday"):
            assert final[(job, label)].status == "ok", (job, label, final[(job, label)].detail)
    assert sim.claims.refused == []
    assert max(done.values()) == 1  # 중복 수집 0(장중 밖 작업 포함)


# ── 기본 창 ──────────────────────────────────────────────────────────────────────────────
def test_every_slot_key_done_once_and_rows_complete(hour: SimDay) -> None:
    assert_no_gap(hour)


def test_rows_are_stamped_with_the_slot_and_estimated(hour: SimDay) -> None:
    lo, hi = at(10, 7, 10, 0), at(10, 7, 11, 0)
    rows = [
        *hour.repos.market.index_quotes(lo, hi),
        *hour.repos.market.sector_quotes(lo, hi),
        *hour.repos.market.rank_rows(lo, hi),
        *hour.repos.flows.intraday(lo, hi),
        *hour.repos.etf.quotes(lo, hi),
    ]
    assert rows
    labels = set(SLOTS)
    for r in rows:
        assert r.ts.astimezone(KST).strftime("%Y-%m-%dT%H:%M") in labels
        assert r.quality == Quality.ESTIMATED
    # 장중 원천 이름은 소문자 한 낱말(원장 순위 — 묶음 C·M 규약)
    assert {r.source for r in hour.repos.flows.intraday(lo, hi)} == {"kis.prelim"}


def test_kis_limit_and_tokens_only_from_auth(hour: SimDay) -> None:
    assert hour.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in hour.kis.posts()} <= {"auth"}
    assert hour.kis.token_posts <= 1  # 창 안에서 auth 가 처음 한 번
    gets = hour.kis.gets()
    assert gets and all(c.status == 200 for c in gets)


def test_ledger_today_rows_follow_the_latest_slot(hour: SimDay, ledger: Ledger) -> None:
    assert [s for s, _, _ in ledger.shots] == [_ts(x) for x in SLOTS]
    prev: dict[tuple[str, str], tuple[int | None, str, str]] | None = None
    changed = 0
    for slot, book, hist in ledger.shots:
        assert hist, slot
        for key, value in hist.items():
            net, source, quality = book[key]
            assert (net, source, quality) == (value, "kis.prelim", str(Quality.ESTIMATED)), key
        if prev is not None:
            changed += sum(1 for k, v in book.items() if prev.get(k) != v)
        prev = book
    assert (
        changed > 0
    )  # 슬롯마다 값이 바뀐다(가짜 서버 시드에 슬롯 포함) — 덮어쓰기가 실제로 일어남


def test_other_jobs_in_the_window_still_run(hour: SimDay) -> None:
    """P3 처리기가 KIS 버킷을 나눠 써도 같은 창의 P2 작업·GX 흉내가 밀리지 않는다."""
    final = hour.final_runs()
    assert all(r.status == "ok" for r in final.values()), {
        k: r.detail for k, r in final.items() if r.status != "ok"
    }
    assert {r.status for r in hour.external_runs if r.job == "gex.poller"} == {"ok"}


# ── 변형(§8.3) ───────────────────────────────────────────────────────────────────────────
def _http500(sim: SimDay) -> None:
    sim.kis.inject("HTTP500", 1, start=at(10, 7, 10, 20), tr_id="FHPST02400000",
                   consumers=["scheduler"])  # fmt: skip


def _egw_burst(sim: SimDay) -> None:
    sim.kis.inject("EGW00201", 3, start=at(10, 7, 10, 30), consumers=["scheduler"])


VARIANTS: dict[str, SimOptions] = {
    "a_http500_once": _opts(more=_http500),
    "b_egw00201_x3": _opts(more=_egw_burst),
    "c_redis_blip_5s": _opts(redis_down=(at(10, 7, 10, 40), at(10, 7, 10, 40, 5))),
    "d_scheduler_restart": _opts(restarts=(at(10, 7, 10, 31),)),
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variants_keep_the_hour_gap_free(name: str) -> None:
    sim = SimDay(VARIANTS[name])
    try:
        sim.run()
        assert_no_gap(sim)
        assert sim.kis.max_in_window(1.0) <= 4
        assert {c.consumer for c in sim.kis.posts()} <= {"auth"}
        if name == "a_http500_once":
            hit = [c for c in sim.kis.calls if c.msg_cd == "HTTP500"]
            assert len(hit) == 1 and hit[0].at >= at(10, 7, 10, 20)
        if name == "b_egw00201_x3":
            assert sum(1 for c in sim.kis.calls if c.msg_cd == "EGW00201") == 3
        if name == "d_scheduler_restart":
            assert sim.restarted_at == [at(10, 7, 10, 31)]
            # 10:30 슬롯이 재기동을 넘겨도 키는 한 번만(중복 0)
            label = "2026-10-07T10:30"
            for k in intraday_keys(REG, sim, label):
                assert sim.done_keys()[k] == 1
    finally:
        sim.close()
