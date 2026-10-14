"""평가기 속성(metrics §30.3 — hypothesis).

- 드모르간: 값이 다 있는 칸에서 not all(A, B) == any(¬A, ¬B)(¬ 는 연산자 반대 — `<`↔`>=`, `>`↔`<=`).
- 조건 순서 무관: 잎 순서를 바꿔도 판정이 같다.
- 미래참조 없음: 칸 i 이후 값을 바꿔도 칸 ≤ i 의 판정·근거 값·입력 지문이 같다(룩어헤드 없음).
- 쿨다운 상태기도 미래 신호를 보지 않는다.
"""

from __future__ import annotations

import random
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.time import KST
from kbj.engines.rules.cooldown import CooldownBook
from kbj.engines.rules.evaluate import evaluate
from tests.unit.engines.rules.rule_rows import frame, rule

prices = st.lists(st.floats(min_value=90.0, max_value=110.0), min_size=12, max_size=40)
NEG = {"<": ">=", ">=": "<", ">": "<=", "<=": ">"}
LEAVES = [
    ("rsi", {"n": 3}, None, 50.0),
    ("change_pct", {}, None, 0.0),
    ("sma", {"n": 3}, None, 100.0),
    ("bb", {"n": 5, "k": 2.0}, "pctb", 0.5),
]


def _leaf(k: int, op: str) -> dict[str, object]:
    name, p, fld, v = LEAVES[k]
    out: dict[str, object] = {"ind": name, "p": p, "op": op, "value": v}
    if fld:
        out["field"] = fld
    return out


@settings(max_examples=80, deadline=None)
@given(prices, st.sampled_from(list(NEG)), st.sampled_from(list(NEG)))
def test_de_morgan(c: list[float], op_a: str, op_b: str) -> None:
    f = frame(c)
    a, b = _leaf(0, op_a), _leaf(1, op_b)
    pos = rule({"all": [a, b]})
    neg = rule({"any": [_leaf(0, NEG[op_a]), _leaf(1, NEG[op_b])]})
    for i in range(6, f.n):
        va, vn = evaluate(pos, f, i), evaluate(neg, f, i)
        if va.state == "skipped":
            continue
        if all(x.value is not None for x in va.conds) and va.conds[0].value != 50.0:
            if va.conds[1].value != 0.0:
                assert va.ok == (not vn.ok)


@settings(max_examples=60, deadline=None)
@given(prices, st.randoms(use_true_random=False))
def test_order_independent(c: list[float], rnd: random.Random) -> None:
    f = frame(c)
    leaves = [_leaf(k, rnd.choice(list(NEG))) for k in range(len(LEAVES))]
    shuffled = leaves[:]
    rnd.shuffle(shuffled)
    for key in ("all", "any"):
        r1, r2 = rule({key: leaves}), rule({key: shuffled})
        for i in range(f.n):
            assert evaluate(r1, f, i).ok == evaluate(r2, f, i).ok


@settings(max_examples=60, deadline=None)
@given(prices, st.data())
def test_no_lookahead(c: list[float], data: st.DataObject) -> None:
    i = data.draw(st.integers(min_value=0, max_value=len(c) - 1))
    tail = data.draw(
        st.lists(
            st.floats(min_value=90.0, max_value=110.0),
            min_size=len(c) - i - 1,
            max_size=len(c) - i - 1,
        )
    )
    f1, f2 = frame(c), frame(c[: i + 1] + tail)
    r = rule(
        {
            "all": [
                _leaf(0, "<"),
                {"ind": "close", "op": "cross_above", "ref": {"ind": "sma", "p": {"n": 3}}},
                {
                    "ind": "macd",
                    "p": {"fast": 2, "slow": 4, "signal": 2},
                    "op": "turns_positive",
                    "field": "hist",
                },
            ]
        }
    )
    for j in range(i + 1):
        v1, v2 = evaluate(r, f1, j), evaluate(r, f2, j)
        assert (v1.ok, v1.state, v1.input_fp) == (v2.ok, v2.state, v2.input_fp)
        assert [x.value for x in v1.conds] == [x.value for x in v2.conds]


@settings(max_examples=40, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=3000), min_size=1, max_size=20), st.data())
def test_cooldown_ignores_future(minutes: list[int], data: st.DataObject) -> None:
    from datetime import datetime

    t0 = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
    ts = sorted(t0 + timedelta(minutes=m) for m in set(minutes))
    k = data.draw(st.integers(min_value=0, max_value=len(ts) - 1))
    full = CooldownBook()
    got_full = [full.step(rule_id="r_a", code="005930", ts=t, cooldown_min=60).allowed for t in ts]
    part = CooldownBook()
    got_part = [
        part.step(rule_id="r_a", code="005930", ts=t, cooldown_min=60).allowed for t in ts[: k + 1]
    ]
    assert got_full[: k + 1] == got_part
