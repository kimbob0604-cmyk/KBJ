"""백테스트 속성(D-P8-13, metrics §30.4 — hypothesis, 합성 입력).

- 미래참조 없음: 칸 k 뒤 봉을 아무렇게나 바꿔도 칸 ≤ k 의 신호(시점·상태·진입가·입력 지문)가 같다.
- 비용 0 이면 체결 대칭: a→b 와 b→a 왕복의 순수익 곱이 1, 거래 수익 = 청산가/진입가 − 1.
- 가격 척도 불변: 모든 가격을 같은 배수로 키워도(비율 지표 규칙) 신호·거래 수익이 같다.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from kbj.engines.backtest.costs import Costs, net_return
from kbj.engines.rules.backtest import BacktestOut, CodeData, FrameInputs, TradeParams, run_backtest
from tests.unit.engines.rules.rule_rows import ANCHOR, CODE, bars, days, rule

ZERO = Costs(0, 0, 0)
N = 40
DS = days(N)
RULES: list[dict[str, object]] = [
    {"all": [{"ind": "change_pct", "op": ">", "value": 1.0}]},
    {"any": [{"ind": "rsi", "p": {"n": 3}, "op": "<", "value": 30},
             {"ind": "sma", "p": {"n": 3}, "op": "cross_above",
              "ref": {"ind": "sma", "p": {"n": 8}}}]},
    {"all": [{"ind": "bb", "p": {"n": 5, "k": 2.0}, "field": "pctb", "op": ">", "value": 0.8}]},
]  # fmt: skip
closes = st.lists(st.floats(min_value=80.0, max_value=120.0), min_size=N, max_size=N)


def _run(cs: list[float], which: int, *, hold: int = 3, cooldown: int = 1440) -> BacktestOut:
    fi = FrameInputs(CODE, bars(list(cs), DS), anchor=ANCHOR, split_ratio=0.31, calendar=DS)
    return run_backtest(
        rule(RULES[which]), {CODE: CodeData(fi)}, rule_id="r_000000000001", start=DS[0],
        end=DS[-1], modes=("close",), cooldown_min=cooldown,
        params=TradeParams(hold=hold, costs=ZERO), horizons=(1, 3),
    )  # fmt: skip


@settings(max_examples=60, deadline=None)
@given(closes, closes, st.integers(min_value=5, max_value=N - 2), st.integers(0, len(RULES) - 1))
def test_no_lookahead(a: list[float], b: list[float], k: int, which: int) -> None:
    changed = a[: k + 1] + b[k + 1 :]
    x, y = _run(a, which), _run(changed, which)
    key = lambda s: (s.trade_date, s.status, s.entry_price, s.input_fp)  # noqa: E731
    assert [key(s) for s in x.signals if s.trade_date <= DS[k]] == [
        key(s) for s in y.signals if s.trade_date <= DS[k]
    ]


@settings(max_examples=60, deadline=None)
@given(st.floats(1.0, 1e6), st.floats(1.0, 1e6))
def test_zero_cost_round_trip_symmetry(p: float, q: float) -> None:
    there = net_return(p, q, ZERO)
    back = net_return(q, p, ZERO)
    assert abs((1 + there) * (1 + back) - 1) < 1e-9
    assert abs(there - (q / p - 1)) < 1e-12


@settings(max_examples=40, deadline=None)
@given(closes, st.integers(0, len(RULES) - 1), st.floats(min_value=0.5, max_value=4.0))
def test_zero_cost_trade_return_and_scale(cs: list[float], which: int, m: float) -> None:
    base = _run(cs, which)
    for t in base.trades:
        assert abs(t.ret_pct - (t.exit_price / t.entry_price - 1) * 100) < 1e-9
    scaled = _run([c * m for c in cs], which)
    assume(len(base.signals) == len(scaled.signals))  # 부동소수 경계(임계값 동률)는 건너뛴다
    assert [(s.trade_date, s.status) for s in base.signals] == [
        (s.trade_date, s.status) for s in scaled.signals
    ]
    for t0, t1 in zip(base.trades, scaled.trades, strict=True):
        assert abs(t0.ret_pct - t1.ret_pct) < 1e-6
