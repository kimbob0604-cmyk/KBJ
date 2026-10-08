"""ET `monitor/flow/tests/test_analyze.py` 중 원장·검산·매수일수 시험 8건 승격 + 오라클 대조.

docs/p3_design.md §1.4(checks 행)·§1.11: "monitor/flow test_analyze 중 약 8(오라클)". 원본 시험의
뜻은 그대로 두고 import 경로만 kbj 로 바꿨다(함수 모양이 다른 곳은 같은 규칙을 kbj 함수로 확인한다).
나머지 analyze 시험(워크북 표·기여율·누적 차트)은 `flows.report`(P5)까지 legacy 에 남는다.

오라클: ET `monitor/flow/analyze.py` 의 `_sum`·`buy_days`·`reconcile` 의 '전체 순매수 0' 부분을
**그대로 옮겨 적은** 단순 루프(kbj 엔진을 쓰지 않는다 — kbj 는 legacy 를 import 하지 않는다,
계약 ②). 합성 원장에서 kbj `Ledger.window`·`buy_days`·`check1` 이 오라클과 같은지 본다.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.engines.flows.checks import apply_checks, check1
from kbj.engines.flows.ledger import build_ledger, sum_known
from kbj.engines.flows.streak import buy_days
from kbj.engines.flows.totals import stock_detail
from tests.fixtures.synthetic.ledger_gen import generate, ledger_inputs
from tests.unit.engines.flows.flow_rows import days, ledger_of, lrow

# ── 오라클(ET monitor/flow/analyze.py 원문 그대로 — 함수 본문은 고치지 않았다) ───────────────


def _oracle_sum(daily: dict[Any, dict[str, Any]], dates: list[Any], who: str):
    """구간 합. 값이 없는 날은 건너뛴다(0 으로 세지 않는다)."""
    tot, n = 0.0, 0
    for d in dates:
        v = (daily.get(d) or {}).get(who)
        if v is not None:
            tot += v
            n += 1
    return (tot if n else None), n


def _oracle_buy_days(daily: dict[Any, dict[str, Any]], dates: list[Any], who: str) -> int:
    """순매수한 날의 수. 보합(0)은 매수로 세지 않는다."""
    return sum(
        1 for d in dates if (daily.get(d) or {}).get(who) is not None and (daily[d][who]) > 0
    )


# ── 원본 시험(이름 그대로, import 경로만 kbj) ───────────────────────────────────────────────

D = days(3)


class Test매수일수:
    def test_보합은_매수로_세지_않는다(self) -> None:
        rows = [lrow("A00001", D[i], foreign=v) for i, v in enumerate([10, 0, -5])]
        assert buy_days(rows, "foreign") == 1

    def test_값이_없는_날은_건너뛴다(self) -> None:
        rows = [lrow("A00001", D[0], foreign=10), lrow("A00001", D[1], foreign=None)]
        assert buy_days(rows, "foreign") == 1


class Test단위:
    def test_None은_0이_아니다(self) -> None:
        """0 으로 바꾸면 '값이 없다' 가 '순매수 0원' 이 된다."""
        assert sum_known([None]) is None
        agg = ledger_of([lrow("A00001", D[0], foreign=None)]).window("A00001", D[0], 1)
        assert agg.sums_by_investor["foreign"] is None


class Test검산:
    def test_어긋나면_실패로_표시한다(self) -> None:
        row = lrow("A00001", D[0], foreign=-100, inst=999, other_corp=0, indiv=0)
        led, failures = apply_checks(ledger_of([row]))
        assert [(f.check_id, f.residual) for f in failures] == [("c1", 899)]
        assert not led.rows[("A00001", D[0])].usable

    def test_기간합계가_없으면_통과로_치지_않는다(self) -> None:
        row = lrow("A00001", D[0], foreign=1, inst=None, other_corp=None, indiv=None)
        led, failures = apply_checks(ledger_of([row]))
        assert failures == []
        assert led.checks is not None
        assert "c1" not in led.checks.checked  # 하지 않은 대조를 '통과' 로 세지 않는다
        assert led.checks.unavailable["c1"] == 1


class Test골든대조:
    """원본은 합성 워크북 골든 — 여기서는 합성 원장 생성기(시드 고정)가 그 자리다."""

    m = generate(20260818)
    led, failures = apply_checks(build_ledger(**ledger_inputs(m)))  # type: ignore[arg-type]

    def test_검산이_전부_통과한다(self) -> None:
        assert self.failures == []
        assert self.led.checks is not None and self.led.checks.failed == {}

    def test_누적_마지막값이_기간합계와_같다(self) -> None:
        end = self.m.last_day
        det = stock_detail(self.led, "Q00000", end, 20)
        dates = list(self.led.days_upto(end, 20))
        daily = {
            d: {"foreign": r.foreign, "inst": r.inst}
            for d in dates
            if (r := self.led.get("Q00000", d)) is not None
        }
        for who in ("foreign", "inst"):
            want, _ = _oracle_sum(daily, dates, who)
            assert det.cumulative[who] == want

    def test_일별_막대는_거래일수와_같다(self) -> None:
        det = stock_detail(self.led, "Q00001", self.m.last_day, 20)
        assert len(det.days) == 20


# ── 오라클 대조(속성) ─────────────────────────────────────────────────────────────────────


@settings(max_examples=15, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000), n=st.sampled_from([1, 5, 20]))
def test_kbj_집계가_오라클과_같다(seed: int, n: int) -> None:
    m = generate(seed)
    led = build_ledger(**ledger_inputs(m))  # type: ignore[arg-type]
    end = m.last_day
    dates = list(led.days_upto(end, n))
    for code in ("Q00000", "Q00001", "Q00002", "Q00005", "Q00010", "Q00014"):
        daily = {
            d: {"foreign": r.foreign, "inst": r.inst, "indiv": r.indiv}
            for d in dates
            if (r := led.get(code, d)) is not None
        }
        agg = led.window(code, end, n)
        rows = [led.get(code, d) for d in dates]
        for who in ("foreign", "inst", "indiv"):
            want, _ = _oracle_sum(daily, dates, who)
            assert agg.sums_by_investor[who] == want
            assert buy_days(rows, who) == _oracle_buy_days(daily, dates, who)  # type: ignore[arg-type]
        for r in rows:
            if r is not None and r.foreign is not None:
                # ET reconcile '순매수 금액 전체' — 4구분 합은 0
                assert check1(r) == 0
