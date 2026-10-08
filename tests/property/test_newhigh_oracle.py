"""엔진 라벨 ⊆ 단순 루프 라벨 — 독립 오라클 속성 시험(docs/p3_design.md §4.1 6단계·§8.1).

hypothesis 로 만든 일봉(양수 가격, 가끔 급등락·계단)에서 `kbj.engines.board.newhigh.evaluate` 의
갱신 라벨이 `tests/oracles/newhigh_loop.labels_by_loop`(엔진을 쓰지 않는 루프 — ET flowlab
`verify.check_newhigh` 이식)의 갱신 라벨에 포함되는지 본다. 역사적 신고가는 w52 를 넘었다는
필요조건만(스칼라는 직전 구간 최고가 이상이어야 정의상 맞다 — 그렇게 만든다).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.engines.board import newhigh as nh
from kbj.engines.board.config import BoardConfig
from tests.oracles.newhigh_loop import labels_by_loop

CFG = BoardConfig.load()
LOOKBACK: dict[str, int] = dict(CFG["newhigh"]["lookback"])
D0 = date(2024, 1, 1)


@st.composite
def series(draw: st.DrawFn) -> list[dict[str, Any]]:
    n = draw(st.integers(min_value=2, max_value=300))
    steps = draw(
        st.lists(
            st.floats(min_value=-0.12, max_value=0.12, allow_nan=False),
            min_size=n,
            max_size=n,
        )
    )
    jumps = draw(st.lists(st.integers(min_value=0, max_value=n - 1), max_size=2))
    up = draw(st.lists(st.floats(min_value=0.0, max_value=0.05), min_size=n, max_size=n))
    px = draw(st.floats(min_value=500.0, max_value=200000.0))
    rows: list[dict[str, Any]] = []
    for i in range(n):
        px = max(px * (1 + steps[i]), 100.0)
        if i in jumps:
            px = px * draw(st.sampled_from([0.2, 0.5, 2.0, 1.35]))  # 계단(분할 미반영)·급등락
        close = round(px, 0)
        high = round(close * (1 + up[i]), 0)
        rows.append(
            dict(
                asof=(D0 + timedelta(days=i)).isoformat(),
                open=close,
                high=high,
                low=round(close * 0.98, 0),
                close=close,
                volume=1000.0 + i,
            )
        )
    return rows


@settings(max_examples=150, deadline=None)
@given(rows=series(), hist_mult=st.floats(min_value=1.0, max_value=1.5), cleared=st.booleans())
def test_엔진_라벨은_루프_라벨에_포함된다(
    rows: list[dict[str, Any]], hist_mult: float, cleared: bool
) -> None:
    asof = rows[-1]["asof"]
    prior = rows[:-1]
    hist_ref = (
        {
            "high": max(r["high"] for r in prior) * hist_mult,
            "close": max(r["close"] for r in prior) * hist_mult,
        }
        if prior
        else None
    )
    ev = nh.evaluate(rows, asof, CFG, hist_ref=hist_ref, hist_days=1200, split_cleared=cleared)
    assert ev is not None
    loop = labels_by_loop(rows, asof, LOOKBACK)
    for basis in nh.BASES:
        hits = ev["basis"][basis]["hit"]
        label = ev["basis"][basis]["label"]
        for kind, on in hits.items():
            if not on:
                continue
            want = loop[basis].get(kind)
            if kind == "hist" and want is None:
                continue  # w52 창이 짧아 필요조건을 못 본다
            assert want is True, (basis, kind, asof, len(rows))
        # 라벨은 갱신한 것 중 최상위(상위가 하위를 포함 — 정의)
        if label is not None:
            assert hits[label]


def test_오라클은_정의대로_센다() -> None:
    days = [(D0 + timedelta(days=i)).isoformat() for i in range(62)]
    closes = [100.0] * 61 + [101.0]
    rows = [dict(asof=d, high=c, close=c) for d, c in zip(days, closes, strict=True)]
    got = labels_by_loop(rows, days[-1], {"d60": 60, "w52": 252})
    assert got["close"] == {"d60": True, "w52": None, "hist": None}
    # 같은 값은 갱신이 아니다(엄격 >)
    rows[-1] = dict(asof=days[-1], high=100.0, close=100.0)
    assert labels_by_loop(rows, days[-1], {"d60": 60})["close"]["d60"] is False
