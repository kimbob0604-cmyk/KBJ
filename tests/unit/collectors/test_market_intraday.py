"""`flows.intraday`·`market.intraday` 처리기 — 가짜 KIS + 메모리 저장소(묶음 C, 새 시험).

슬롯마다 이력 표 행 수 = 기대(결측 0), 모든 행 ts = 슬롯 시작·estimated, 실패한 키만 미완(슬롯 안
재시도가 그 키만 다시 — 중복 0), 일별 원장 오늘 행(kis.prelim)이 마지막 슬롯 값과 같다(§8.3 단언
②·⑤의 단위판).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.data.spec import DataKey
from kbj.services.collectors import market_intraday
from kbj.services.collectors._p3 import slot_ts
from tests.fakes.kis_server import RANK_ROWS, SECTORS_PER_MARKET, FakeKisServer
from tests.unit.collectors.p3_fakes import (
    ETF_CODES,
    Clock,
    DirectKis,
    ctx,
    kst,
    load_cfg,
    new_repos,
    seed_etfs,
)

DAY = date(2026, 10, 7)
PREV = date(2026, 10, 6)


def slot(h: int, m: int) -> str:
    return f"2026-10-07T{h:02d}:{m:02d}"


def flow_keys(as_of: str) -> list[DataKey]:
    return [
        DataKey("KIS", "inst_foreign_intraday", as_of, "KRX"),
        DataKey("KIS", "turnover_rank_intraday", as_of, "KRX"),
        DataKey("KIS", "etf_quote_intraday", as_of),
    ]


def market_keys(as_of: str) -> list[DataKey]:
    return [
        DataKey("KIS", "index_quote_intraday", as_of),
        DataKey("KIS", "sector_quote_intraday", as_of),
    ]


def setup(hook: Any = None) -> tuple[Any, FakeKisServer, Clock, DirectKis]:
    repos = new_repos()
    seed_etfs(repos, PREV)
    clock = Clock(kst(2026, 10, 7, 10, 0, 30))
    server = FakeKisServer(clock)
    return repos, server, clock, DirectKis(server, hook=hook)


def run_flows(repos: Any, kis: DirectKis, ks: list[DataKey], clock: Clock) -> Any:
    return market_intraday.flows(
        ctx("flows.intraday", ks[0].as_of, ks, now=clock(), repos=repos, kis=kis)
    )


def test_flows_slot_rows_complete_and_stamped_with_the_slot() -> None:
    repos, _server, clock, kis = setup()
    res = run_flows(repos, kis, flow_keys(slot(10, 0)), clock)
    assert res.status == "ok", res.detail
    ts = kst(2026, 10, 7, 10, 0)
    end = kst(2026, 10, 7, 10, 10)
    inv = repos.flows.intraday(ts, end)
    # 시장 2 × 종목 10 × 외국인·기관(두 목록이 같은 종목이면 (종목, 구분)마다 한 줄 — 합성 서버)
    assert len(inv) == 2 * RANK_ROWS * 2
    assert all(q.rank is not None for q in inv)  # 합성 서버는 두 목록에 같은 종목
    assert {(q.ts, q.quality, q.source) for q in inv} == {(ts, Quality.ESTIMATED, "kis.prelim")}
    ranks = repos.market.rank_rows(ts, end)
    assert len(ranks) == 2 * RANK_ROWS
    assert {r.market for r in ranks} == {"KOSPI", "KOSDAQ"}
    quotes = repos.etf.quotes(ts, end)
    assert sorted(q.code for q in quotes) == sorted(ETF_CODES)  # 감시 상위 N(50) ≥ 3
    snaps = repos.market.snapshots(DAY, source="kis.prelim")
    assert len(snaps) == 2 * RANK_ROWS
    assert all(s.turnover_is_estimate for s in snaps)
    # KIS 호출: 가집계 4 + 순위 2 + ETF 3, 우선순위 P2·대기 30초
    assert kis.count() == 4 + 2 + len(ETF_CODES)
    assert {(c[2], c[3]) for c in kis.calls} == {
        (market_intraday.PRIORITY, market_intraday.TIMEOUT_S)
    }


def test_market_slot_indices_and_sectors() -> None:
    repos, _server, clock, kis = setup()
    ks = market_keys(slot(10, 10))
    res = market_intraday.market(ctx("market.intraday", ks[0].as_of, ks, now=clock(), repos=repos,
                                     kis=kis))  # fmt: skip
    assert res.status == "ok", res.detail
    ts = kst(2026, 10, 7, 10, 10)
    end = kst(2026, 10, 7, 10, 20)
    idx = repos.market.index_quotes(ts, end)
    assert [q.code for q in idx] == sorted(load_cfg().market.intraday_indices)
    assert {q.name for q in idx} == set(load_cfg().market.intraday_indices.values())
    assert len(repos.market.sector_quotes(ts, end)) == 2 * SECTORS_PER_MARKET
    assert kis.count() == 3 + 2  # §3.7 슬롯당 5건


def test_failed_key_only_is_retried_within_the_slot_without_duplicates() -> None:
    """변형 (a) — FHPST02400000 HTTP 500 1회 → 그 키만 미완, 재시도로 완료, 중복 0."""
    repos, server, clock, kis = setup()
    server.inject("HTTP500", 1, tr_id="FHPST02400000")
    ks = flow_keys(slot(10, 20))
    res = run_flows(repos, kis, ks, clock)
    assert res.status == "failed"
    assert set(res.collected) == set(ks[:2])
    assert "etf_quote_intraday" in res.detail["reason"]
    clock.at = kst(2026, 10, 7, 10, 21)  # backoff 20초 뒤 — 슬롯 안
    res2 = run_flows(repos, kis, [ks[2]], clock)
    assert res2.status == "ok"
    ts = kst(2026, 10, 7, 10, 20)
    quotes = repos.etf.quotes(ts, kst(2026, 10, 7, 10, 30))
    assert len(quotes) == len(ETF_CODES)
    assert len(repos.flows.intraday(ts, kst(2026, 10, 7, 10, 30))) == 2 * RANK_ROWS * 2


def test_ledger_today_row_follows_the_last_slot() -> None:
    """§8.3 ⑤ — 일별 원장 오늘 행(kis.prelim)이 마지막 슬롯의 이력 값과 같다."""
    repos, _server, clock, kis = setup()
    for h, m in ((10, 0), (10, 10)):
        clock.at = kst(2026, 10, 7, h, m, 40)
        assert run_flows(repos, kis, flow_keys(slot(h, m)), clock).status == "ok"
    last = repos.flows.intraday(kst(2026, 10, 7, 10, 10), kst(2026, 10, 7, 10, 20))
    want = {}
    for q in last:  # 같은 종목이 두 목록에 나와도 처음 값(prelim_days 규칙)
        want.setdefault((q.code, q.investor), q.net_value)
    ledger = repos.flows.days(None, DAY, DAY)
    got = {(r.code, r.investor): r.net_value for rows in ledger.values() for r in rows}
    assert got == want
    assert {r.quality for rows in ledger.values() for r in rows} == {Quality.ESTIMATED}


def test_prelim_does_not_overwrite_a_confirmed_row() -> None:
    """마감 확정 뒤 늦게 온 장중 잠정은 덮지 않는다(저장소 규칙 — skipped 로 보인다)."""
    from kbj.core.rows import Investor, InvestorDay

    repos, _server, clock, kis = setup()
    run_flows(repos, kis, flow_keys(slot(10, 0)), clock)
    code = next(iter(repos.flows.days(None, DAY, DAY)))
    final = InvestorDay(code, DAY, Investor.FOREIGN, 1, 1, "kis", "KRX", Quality.OK)
    repos.flows.upsert_investor_days([final], revise=True, loaded_by="t", now=clock())
    clock.at = kst(2026, 10, 7, 10, 10, 5)
    res = run_flows(repos, kis, flow_keys(slot(10, 10)), clock)
    assert res.detail["inst_foreign_intraday"]["ledger_skipped"] >= 1
    best = {r.investor: r for r in repos.flows.days([code], DAY, DAY)[code]}
    assert best[Investor.FOREIGN].source == "kis"


def test_empty_inst_list_before_first_round_is_zero_rows_not_a_failure() -> None:
    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        return {**body, "output": []} if tr == "FHPTJ04400000" else body

    repos, _server, clock, kis = setup(hook)
    res = run_flows(repos, kis, flow_keys(slot(9, 0))[:1], clock)
    assert res.status == "ok"
    assert res.rows == 0


def test_no_etf_daily_means_no_watch_list_and_a_named_failure() -> None:
    repos = new_repos()
    clock = Clock(kst(2026, 10, 7, 10, 0, 30))
    kis = DirectKis(FakeKisServer(clock))
    res = run_flows(repos, kis, flow_keys(slot(10, 0))[2:], clock)
    assert res.status == "failed"
    assert "감시 목록" in res.detail["reason"]


def test_watch_list_is_top_n_by_net_asset() -> None:
    repos = new_repos()
    seed_etfs(repos, PREV, (100, 900, 500))
    assert market_intraday.watch_list(repos, DAY, 2) == ["995020", "995030"]


def test_slot_as_of_must_be_a_slot_start() -> None:
    assert slot_ts("2026-10-07T10:20") == kst(2026, 10, 7, 10, 20)
    with pytest.raises(ValueError):
        slot_ts("2026-10-07T10:25")


def test_no_keys_is_skipped() -> None:
    res = market_intraday.flows(ctx("flows.intraday", slot(10, 0), [], now=kst(2026, 10, 7, 10),
                                    repos=new_repos()))  # fmt: skip
    assert res.status == "skipped"


# ── 실행기와 함께 — 등록부 그대로의 슬롯 안 재시도(D-P3-14) ─────────────────────────────


def _registry_job(name: str) -> Any:
    import yaml

    from kbj.services.scheduler.registry import Registry
    from tests.unit.collectors.p3_fakes import ROOT

    data = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text(encoding="utf-8"))
    job = next(j for j in data["jobs"] if j["name"] == name)
    return Registry.parse({"version": 1, "jobs": [job]})


def test_runner_retries_the_failed_key_inside_the_slot_and_every_key_is_done_once() -> None:
    """등록부의 retry {max 2, 20·40초}·deadline 9분 그대로 — 10:00 슬롯 ETF 500 1회 → 슬롯 안
    완료, 데이터 키마다 done 1회(결측 0), 다른 키는 다시 잡지 않는다."""
    from datetime import timedelta

    from kbj.core.calendar import us_calendar
    from kbj.data.catalog import all_datasets
    from kbj.services.runtime.health import MemoryHealthSink
    from kbj.services.scheduler.claims import MemoryClaimStore, MemoryRunLog
    from kbj.services.scheduler.runner import JobRunner, inline_submit
    from tests.unit.collectors.p3_fakes import KR

    repos, server, clock, kis = setup()
    clock.at = kst(2026, 10, 7, 9, 59, 50)
    kis = DirectKis(server)  # 토큰을 이 시각에
    server.inject("HTTP500", 1, tr_id="FHPST02400000", start=kst(2026, 10, 7, 10, 0))
    claims = MemoryClaimStore()
    registry = _registry_job("flows.intraday")
    owner = registry.by_name("flows.intraday").owner
    runner = JobRunner(
        registry,
        {owner: market_intraday.flows},
        claims,
        KR,
        us_calendar(),
        runs=MemoryRunLog(),
        catalog=all_datasets(),
        health=MemoryHealthSink(),
        submit=inline_submit,
        resources={"repos": repos, "kis": kis, "markets": load_cfg(), "kr": KR},
    )
    events = []
    while clock.at < kst(2026, 10, 7, 10, 9):
        events += runner.tick(clock.at)
        clock.at += timedelta(seconds=10)
    kinds = [e.kind for e in events if e.as_of == "2026-10-07T10:00"]
    assert kinds.count("retry") == 1
    assert kinds[-1] == "ok"
    done = [k for k in claims.done_keys() if k.as_of == "2026-10-07T10:00"]
    assert len(done) == len(set(done))
    krx_done = {(k.dataset, k.venue) for k in done if k.venue in ("", "KRX")}
    assert krx_done == {
        ("inst_foreign_intraday", "KRX"),
        ("turnover_rank_intraday", "KRX"),
        ("etf_quote_intraday", ""),
    }
    ts = kst(2026, 10, 7, 10, 0)
    assert len(repos.etf.quotes(ts, ts + timedelta(minutes=10))) == len(ETF_CODES)
    # 재시도는 실패한 키만 — 가집계·순위는 슬롯에서 한 번씩만 불렀다
    assert kis.count("FHPTJ04400000") == 4
    assert kis.count("FHPST01710000") == 2
