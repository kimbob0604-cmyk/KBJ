"""`python -m kbj.services.engine.verify --program`(P5 F2 — probe_results #42) — 합성 세계를 메모리
저장소에 올려 검산 ①~④ 건수만 낸다(값·종목코드 없음). 어긋나면 종료 코드 1."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import date

from kbj.core.quality import Quality
from kbj.core.rows import Investor, InvestorDay
from kbj.services.engine import verify as V
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p5_world import CLOCK, TODAY, build_world
from tests.unit.api.web_fixtures_p5.program import f2_markets


def _repos(*, drift: bool = False):
    w = build_world()
    repos = memory_repos()
    for pid in ("kospi", "kosdaq", "k200_fut", "k200_call", "k200_put"):
        repos.flows.put_investor_minutes(w.minutes(pid), loaded_by="t")
    days = list(w.program_days)
    if drift:
        days = [
            replace(r, net_value=(r.net_value or 0) + 1)
            if r.trade_date == TODAY and r.market == "Q" and (r.kind, r.side) == ("whol", "smtn")
            else r
            for r in days
        ]
    repos.program.put_market_days(days, loaded_by="t", received_at=CLOCK)
    repos.program.put_market_minutes(list(w.program_intraday), loaded_by="t", received_at=CLOCK)
    daily = w.investor_daily_won()
    repos.flows.upsert_market_days(
        [
            InvestorDay("0001", TODAY, inv, daily[("kospi", k)], None, "kis", "KRX", Quality.OK)
            for k, inv in (("prsn", Investor.INDIVIDUAL), ("frgn", Investor.FOREIGN))
        ],
        loaded_by="t",
        received_at=CLOCK,
    )
    return repos


def run(repos: MemoryRepos, day: date = TODAY) -> tuple[int, dict[str, object]]:
    out = io.StringIO()
    code = V.main(
        ["--program", "--date", day.isoformat()],
        repos=repos,
        now=CLOCK,
        out=out,
        markets=f2_markets(),
    )
    return code, json.loads(out.getvalue())


def test_synthetic_ledger_has_zero_differences() -> None:
    code, body = run(_repos())
    assert code == 0
    (rep,) = body["program_reports"]  # type: ignore[misc]
    kospi = rep["minute"]["kospi"]
    assert kospi["c1"] == {"ok": 391, "failed": 0, "na": 0}
    assert kospi["c3"]["checked"] == 2 and kospi["c3"]["failed"] == 0
    assert rep["minute"]["k200_fut"]["c1"]["status"] == "n/a"
    assert rep["program"]["K:KRX"]["daily"]["failed"] == 0
    assert rep["program"]["Q:KRX"]["intraday"]["groups"] == 391
    text = json.dumps(body, ensure_ascii=False)
    assert "997" not in text  # 종목코드 없음(합성 코드 997xxx)


def test_drift_is_counted_and_exit_code_1() -> None:
    code, body = run(_repos(drift=True))
    assert code == 1
    (rep,) = body["program_reports"]  # type: ignore[misc]
    assert rep["program"]["Q:KRX"]["daily"]["failed"] == 2
    assert rep["program"]["K:KRX"]["daily"]["failed"] == 0
