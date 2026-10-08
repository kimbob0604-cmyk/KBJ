"""시장 투자자 합계·종목 상세(docs/p3_design.md §5.2 InvestorTotals·StockFlowDetail)."""

from __future__ import annotations

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import INST7, Investor, InvestorDay
from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.flows.totals import market_investor_totals, stock_detail
from tests.fixtures.synthetic.ledger_gen import (
    corrupt_investor,
    drop_investor,
    generate,
    ledger_inputs,
)


@pytest.fixture(scope="module")
def m():
    return generate(31)


def test_시장_합계는_검산_0(m) -> None:
    t = market_investor_totals(m.market_investors)
    assert t is not None
    assert t.date == m.last_day
    assert t.check1_residual == 0 and t.check2_residual == 0
    assert set(t.by_investor) == {"foreign", "institution", "other_corp", "individual"}
    assert set(t.by_market) == {"KOSPI", "KOSDAQ"}
    assert t.inst7 is not None and set(t.inst7) == {i.value for i in INST7}
    assert t.quality is Quality.OK and t.source == "KIS"
    # 시장별 값의 합 = 합계
    for k, v in t.by_investor.items():
        assert v == sum(mk[k] for mk in t.by_market.values())  # type: ignore[misc]


def test_특정_날짜와_없는_날(m) -> None:
    d = m.trading_days[3]
    t = market_investor_totals(m.market_investors, d)
    assert t is not None and t.date == d
    assert market_investor_totals(m.market_investors, m.holidays[0]) is None
    assert market_investor_totals([]) is None


def test_기타법인이_없으면_검산_1_불가(m) -> None:
    t = market_investor_totals(drop_investor(m, Investor.OTHER_CORP).market_investors)
    assert t is not None
    assert t.by_investor["other_corp"] is None and t.check1_residual is None
    assert t.check2_residual == 0
    assert any("검산 ① 불가" in n for n in t.notes)


def test_한_시장이_빠지면_합계를_내지_않는다(m) -> None:
    rows = [r for r in m.market_investors if r.code == "KOSPI"]
    t = market_investor_totals(rows, markets=("KOSPI", "KOSDAQ"))
    assert t is not None
    assert all(v is None for v in t.by_investor.values())
    assert any("KOSDAQ" in n for n in t.notes)


def test_잠정보다_확정을_고른다(m) -> None:
    d = m.last_day
    extra = InvestorDay("KOSPI", d, Investor.FOREIGN, 1, None, "kis.prelim", "KRX",
                        Quality.ESTIMATED)  # fmt: skip
    a = market_investor_totals(m.market_investors)
    b = market_investor_totals([*m.market_investors, extra])
    assert a is not None and b is not None and a.by_investor == b.by_investor


def test_종목_상세(m) -> None:
    d = m.trading_days[-5]
    bad = corrupt_investor(m, "Q00000", d, Investor.OTHER_CORP, 1_000_000)
    led, _ = apply_checks(build_ledger(**ledger_inputs(bad)))  # type: ignore[arg-type]
    det = stock_detail(led, "Q00000", m.last_day, 20)
    assert len(det.days) == 20
    assert det.checks["c1_failed"] == 1
    assert det.cumulative == {
        w: led.window("Q00000", m.last_day, 20).sums_by_investor[w]
        for w in ("foreign", "inst", "other_corp", "indiv")
    }
    halted = stock_detail(led, "Q00001", m.trading_days[11], 2)
    assert [x.traded for x in halted.days] == [False, False]
    assert halted.checks["c1_unavailable"] == 2
    missing = stock_detail(led, "Q00006", m.last_day, 5)  # 신규 상장 — 앞 날은 행이 없다
    assert missing.days[0].quality is None
