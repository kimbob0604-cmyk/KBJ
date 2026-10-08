"""검산 ①②③ 속성 시험 — 합성 원장에서 잔차 0, 깨진 행만 invalid(docs/p3_design.md §0.3·§8.2).

- 임의 시드의 합성 원장(`tests/fixtures/synthetic/ledger_gen.py`)에서 ① 4구분 합 = 0, ② 7구분 합 =
  기관이 모든 행에서 잔차 0(검산 불가는 거래정지일의 '투자자별 행 없음'뿐).
- 기간 합(1·5·20일) = 일별 합.
- 임의의 행 하나를 깨면 **그 행만** invalid 이고 기간 집계에서 빠진다.
- ③ 순자산 변화 = 순유입 + 가격효과(분할·병합은 비율로 맞춘 뒤): 계산 순자산(S×NAV)으로
  잔차 0(부동소수 허용오차), 보고 순자산(round(S×NAV))과는 허용오차 0.005원 × (Sₜ + Sₜ₋₁) + 1원
  (공표 단위 — 합성은 원) 안. 생성 과정의 참값(ΔS × NAVₜ)과 공식이 같다. ETF 엔진
  (`kbj.engines.etf.flows` — 묶음 E3)이 있으면 그 결과도 참값과 대조한다.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import pairwise
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kbj.core.rows import GROUP4, INST7, EtfDay, Investor
from kbj.engines.flows.checks import apply_checks, check1, check2
from kbj.engines.flows.ledger import build_ledger, sum_known
from tests.fixtures.synthetic.ledger_gen import (
    SyntheticMarket,
    corrupt_investor,
    generate,
    ledger_inputs,
)

SEEDS = st.integers(min_value=0, max_value=2**31 - 1)
SETTINGS = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _ledger(m: SyntheticMarket):
    return apply_checks(build_ledger(**ledger_inputs(m)))  # type: ignore[arg-type]


@SETTINGS
@given(seed=SEEDS, n_days=st.integers(min_value=25, max_value=40))
def test_검산_1_2_잔차_0(seed: int, n_days: int) -> None:
    m = generate(seed, days=n_days)
    led, failures = _ledger(m)
    assert failures == []
    assert led.checks is not None and led.checks.failed == {}
    for r in led.rows.values():
        assert r.usable
        c1, c2 = check1(r), check2(r)
        if c1 is None:  # 거래정지일만 — 투자자 행이 하나도 없다
            assert not r.traded and all(r.value(w) is None for w in ("foreign", "inst"))
        else:
            assert c1 == 0
            assert c2 == 0


@SETTINGS
@given(seed=SEEDS, data=st.data())
def test_기간_합은_일별_합(seed: int, data: st.DataObject) -> None:
    m = generate(seed)
    led, _ = _ledger(m)
    code = data.draw(st.sampled_from([s.code for s in m.stocks]))
    n = data.draw(st.sampled_from([1, 5, 20]))
    end = data.draw(st.sampled_from(list(m.trading_days[n:])))
    agg = led.window(code, end, n)
    rows = [led.get(code, d) for d in led.days_upto(end, n)]
    used = [r for r in rows if r is not None and r.usable]
    assert agg.sum_turnover == sum_known(r.turnover for r in used)
    for w in ("foreign", "inst", "other_corp", "indiv"):
        assert agg.sums_by_investor[w] == sum_known(r.value(w) for r in used)
    assert len(agg.dates) == min(n, len([d for d in m.trading_days if d <= end]))


@SETTINGS
@given(seed=SEEDS, data=st.data())
def test_깨진_행_하나만_invalid(seed: int, data: st.DataObject) -> None:
    m = generate(seed)
    kis = [r for r in m.investors if r.source == "kis" and r.investor in (*GROUP4, *INST7)]
    target = data.draw(st.sampled_from(kis))
    delta = data.draw(st.integers(min_value=-(10**12), max_value=10**12).filter(lambda x: x != 0))
    bad = corrupt_investor(m, target.code, target.date, target.investor, delta)
    led, failures = _ledger(bad)
    assert {(f.code, f.date) for f in failures} == {(target.code, target.date)}
    want = {"c2"} if target.investor in INST7 else {"c1"}
    if target.investor is Investor.INSTITUTION:
        want = {"c1", "c2"}
    assert {f.check_id for f in failures} == want
    assert [k for k, r in led.rows.items() if not r.usable] == [(target.code, target.date)]
    end = led.days_upto(m.last_day, 30)
    agg = led.window(target.code, m.last_day, len(end))
    assert agg.n_invalid == 1


# ── 검산 ③ ─────────────────────────────────────────────────────────────────────────────


def _pairs(m: SyntheticMarket) -> list[tuple[EtfDay, EtfDay, float]]:
    by: dict[str, list[EtfDay]] = defaultdict(list)
    for e in m.etf_days:
        by[e.code].append(e)
    ratio: dict[tuple[str, date], float] = {
        (ev.code, ev.effective_date): ev.ratio for ev in m.split_events
    }
    out: list[tuple[EtfDay, EtfDay, float]] = []
    for code, rows in by.items():
        rows.sort(key=lambda e: e.date)
        for prev, cur in pairwise(rows):
            out.append((prev, cur, ratio.get((code, cur.date), 1.0)))
    return out


@SETTINGS
@given(seed=SEEDS)
def test_검산_3_잔차_0(seed: int) -> None:
    m = generate(seed)
    for prev, cur, r in _pairs(m):
        assert prev.list_shrs is not None and cur.list_shrs is not None
        assert prev.nav is not None and cur.nav is not None
        s0, s1 = prev.list_shrs * r, cur.list_shrs
        nav0, nav1 = prev.nav / r, cur.nav
        inflow = (s1 - s0) * nav1
        price = s0 * (nav1 - nav0)
        computed_chg = s1 * nav1 - prev.list_shrs * prev.nav
        assert computed_chg == pytest.approx(inflow + price, rel=1e-12, abs=1e-2)
        assert prev.net_asset is not None and cur.net_asset is not None
        reported_chg = cur.net_asset - prev.net_asset
        tol = 0.005 * (cur.list_shrs + prev.list_shrs) + 1
        assert abs(reported_chg - (inflow + price)) <= tol
        truth = m.etf_truth[(cur.code, cur.date)]
        assert truth.inflow == pytest.approx(inflow, rel=1e-9, abs=1e-3)
        assert truth.price_effect == pytest.approx(price, rel=1e-9, abs=1e-3)
        if r != 1.0:
            assert truth.status == "split_adjusted" and truth.inflow == 0


def test_검산_3_ETF_엔진과_참값() -> None:
    """E3 의 `daily_flow(prev, cur, split)` 이 있으면 합성 참값과 대조한다(없으면 건너뛴다 — E3 묶음
    완료 전. S 가 통합 때 이 시험이 실제로 도는지 확인한다)."""
    flows: Any = pytest.importorskip("kbj.engines.etf.flows")
    m = generate(20261007)
    events = {(ev.code, ev.effective_date): ev for ev in m.split_events}
    by: dict[str, list[EtfDay]] = defaultdict(list)
    for e in m.etf_days:
        by[e.code].append(e)
    for code, rows in by.items():
        rows.sort(key=lambda e: e.date)
        first = flows.daily_flow(None, rows[0], None)
        assert first.status == "new" and first.net_inflow is None
        for prev, cur in pairwise(rows):
            f = flows.daily_flow(prev, cur, events.get((code, cur.date)))
            t = m.etf_truth[(code, cur.date)]
            assert f.status == t.status
            assert f.net_inflow == pytest.approx(t.inflow, rel=1e-9, abs=1.0)
            assert f.price_effect == pytest.approx(t.price_effect, rel=1e-9, abs=1.0)
