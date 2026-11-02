"""`gex.option_positions` 작업 — 원천 행 → 하루 배분 저장(metrics §34.1, ADR 0044).

메모리 저장소(`repos.opt_pos` 원천 칸에 합성 손계산 세계를 넣는다 — KIS·KRX 원자료 아님).
손계산 배분이 그대로 저장되고(허용오차), 그날 행 교체로 멱등이며, 델타는 자체(option_iv) → KIS 순,
선물 순매수·근월물 가격이 묶음 하루 행에 들어간다. 원천이 없으면 not_ready, 한 묶음의 실패는 다른
묶음을 막지 않는다.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.engine import option_positions as job
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos, memory_repos
from kbj.store.repos.opt_pos import ChainClose, ChainQuote, FlowRow
from tests.fixtures.synthetic.opt_pos_world import HAND_DAY, HAND_NET, HAND_POINTS, HAND_QUOTES, at

CAL = TradingCalendar.default()
NOW = datetime(2026, 11, 3, 16, 5, tzinfo=KST)


def _ctx(repos: MemoryRepos, as_of: str = "2026-11-03") -> JobContext:
    return JobContext(job="gex.option_positions", as_of=as_of, run_id="t", attempt=1, now=NOW,
                      keys=(), resources={"repos": repos, "kr": CAL})  # fmt: skip


def _fill(repos: MemoryRepos) -> None:
    src = repos.opt_pos
    for p in HAND_POINTS:
        src.flows.append(FlowRow(p.ts, "K2I", "OC01", "frgn", p.buy_qty, p.buy_value,
                                 p.sell_qty, p.sell_value, None, "ok"))  # fmt: skip
    src.flows += [
        FlowRow(at(15, 40), "K2I", "F001", "frgn", 900, None, 1000, None, -100, "ok"),
        FlowRow(at(15, 55), "K2I", "F001", "frgn", 1900, None, 1230, None, 670, "ok"),
    ]
    src.quotes += [ChainQuote(q.ts, q.mrkt_cls, q.expiry, q.strike, q.cp, q.last)
                   for q in HAND_QUOTES]  # fmt: skip
    src.closes[HAND_DAY] = [
        ChainClose("", "202611", 860.0, "C", 7.0, 5000, 0.61, 0.6),
        ChainClose("", "202611", 870.0, "C", 5.0, 4000, 0.5, None),
    ]
    src.fut_price[HAND_DAY] = 884.0


def test_job_stores_hand_allocation_and_futures_row() -> None:
    repos = memory_repos()
    _fill(repos)
    res = job.daily(_ctx(repos))
    assert res.status == "ok", res.detail
    rows = {r.strike: r for r in repos.opt_pos.strikes if r.investor == "frgn"}
    assert set(rows) == set(HAND_NET)
    for k, v in HAND_NET.items():
        assert math.isclose(rows[k].buy_qty - rows[k].sell_qty, v, abs_tol=1e-6)
    assert (rows[860.0].delta, rows[860.0].delta_source) == (0.6, "self")
    assert (rows[870.0].delta, rows[870.0].delta_source) == (0.5, "kis")
    assert rows[870.0].board_oi == 4000 and rows[850.0].delta is None
    days = {(d.market_code, d.sector_code): d for d in repos.opt_pos.days}
    oc = days[("K2I", "OC01")]
    assert (oc.minutes, oc.gap_minutes, oc.total_buy, oc.reverted) == (4, 2, 200, 1)
    fut = days[("K2I", "F001")]
    assert fut.futures_net == 670 and fut.fut_price == 884.0 and fut.reasons == ()
    assert oc.computed_at == NOW


def test_rerun_replaces_the_day() -> None:
    repos = memory_repos()
    _fill(repos)
    job.daily(_ctx(repos))
    n = (len(repos.opt_pos.strikes), len(repos.opt_pos.days))
    job.daily(_ctx(repos))
    assert (len(repos.opt_pos.strikes), len(repos.opt_pos.days)) == n


def test_futures_before_1545_is_flagged() -> None:
    repos = memory_repos()
    _fill(repos)
    repos.opt_pos.flows = [f for f in repos.opt_pos.flows if f.ts != at(15, 55)]
    job.daily(_ctx(repos))
    fut = next(d for d in repos.opt_pos.days if d.sector_code == "F001")
    assert fut.futures_net == -100 and "15:40" in fut.reasons[0]


def test_no_source_is_not_ready() -> None:
    res = job.daily(_ctx(memory_repos()))
    assert res.status == "not_ready" and res.rows is None


def test_one_pair_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = memory_repos()
    _fill(repos)
    repos.opt_pos.flows.append(FlowRow(at(9, 1), "K2I", "OP01", "frgn", 5, 5, 0, 0, None, "ok"))
    real = job.allocate_day

    def boom(points: Any, quotes: Any, **kw: Any) -> Any:
        if kw["cp"] == "P":
            raise RuntimeError("합성 실패")
        return real(points, quotes, **kw)

    monkeypatch.setattr(job, "allocate_day", boom)
    res = job.daily(_ctx(repos))
    assert res.status == "failed" and "K2I:OP01" in res.detail["pair_errors"]
    assert repos.opt_pos.strikes  # 콜 묶음은 썼다
