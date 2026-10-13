"""수출 속보 속성(metrics §25 — hypothesis): 임의 10일 잠정치에서

- 구간 단조: 누계(1~10 ≤ 1~20 ≤ 1~말일)로 만든 자료면 `monotonic_breaks` 가 비고, 한 칸을
  줄이면 그 칸이 잡힌다.
- 합 = 전체 오차 0: 열 1~n 이 전체(열 00)를 나눠 가지면 Δ 기여도 합 = 1(분모가 0 이 아닐 때 — 1e-9).
- YoY 는 정의 그대로(`v / v₋₁₂ − 1`), 분모 0·없음은 None(0 으로 채우지 않는다).
- 칩은 가장 최근 (ym, seg) 를 고른다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import TenDayRow, prev_ym
from kbj.engines.trade.flash import flash_chip, monotonic_breaks, segments

T0 = datetime(2026, 11, 11, 11, 0, tzinfo=KST)
SPAN = {1: "01~10", 2: "01~20", 3: "01~30"}


def _row(ym: str, seg: int, col: int, usd: int) -> TenDayRow:
    return TenDayRow(
        kind="exp_item",
        ym=ym,
        seg=seg,
        col_no=col,
        name="전체" if col == 0 else f"품목{col}",
        usd=Decimal(usd),
        first_usd=Decimal(usd),
        first_seen_at=T0,
        span_text=SPAN[seg],
        source="DATAGO:15157908",
        as_of=T0,
        quality=Quality.ESTIMATED,
    )


def _parts(total: int, weights: list[int]) -> list[int]:
    s = sum(weights)
    out = [total * w // s for w in weights]
    out[-1] += total - sum(out)  # 나머지는 마지막 열 — 합 = 전체 정확히
    return out


amounts = st.integers(min_value=0, max_value=10**12)
weights = st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=10)


@settings(max_examples=150, deadline=None)
@given(
    totals=st.lists(st.integers(min_value=0, max_value=10**11), min_size=3, max_size=3),
    w_now=weights,
    prev=st.lists(st.integers(min_value=0, max_value=10**11), min_size=3, max_size=3),
    data=st.data(),
)
def test_누계_단조_그리고_기여도_합(
    totals: list[int], w_now: list[int], prev: list[int], data: st.DataObject
) -> None:
    cur = sorted(totals)  # 누계 — 단조
    old = sorted(prev)
    w_old = data.draw(st.lists(st.integers(1, 100), min_size=len(w_now), max_size=len(w_now)))
    rows: list[TenDayRow] = []
    for ym, tots, ws in (("2026-11", cur, w_now), ("2025-11", old, w_old)):
        for seg, t in zip((1, 2, 3), tots, strict=True):
            rows.append(_row(ym, seg, 0, t))
            rows += [_row(ym, seg, i + 1, v) for i, v in enumerate(_parts(t, ws))]
    # 열마다 누계가 단조가 아닐 수 있으니(비율이 구간마다 같아 단조) — 전체 열만 확인
    assert [b for b in monotonic_breaks(rows) if b[3] == 0] == []
    for s in segments(rows, kind="exp_item", months=1):
        c0 = s.cells[0]
        d0 = (c0.usd or 0) - old[s.seg - 1]
        parts = [c.contribution for c in s.cells[1:]]
        if d0 != 0:
            assert all(p is not None for p in parts)
            assert abs(sum(p for p in parts if p is not None) - 1.0) < 1e-9
        else:
            assert all(p is None for p in parts)
        if old[s.seg - 1] == 0:
            assert c0.yoy is None
        else:
            assert c0.yoy is not None
            assert abs(c0.yoy - ((c0.usd or 0) / old[s.seg - 1] - 1)) < 1e-12


@settings(max_examples=100, deadline=None)
@given(a=amounts, b=amounts, seg=st.sampled_from((2, 3)))
def test_한_칸을_줄이면_잡힌다(a: int, b: int, seg: int) -> None:
    lo, hi = sorted((a, b))
    rows = [_row("2026-11", seg - 1, 0, hi), _row("2026-11", seg, 0, lo)]
    breaks = monotonic_breaks(rows)
    assert breaks == ([("exp_item", "2026-11", seg, 0)] if lo < hi else [])


@settings(max_examples=100, deadline=None)
@given(
    keys=st.lists(
        st.tuples(st.integers(0, 30), st.sampled_from((1, 2, 3))),
        min_size=1,
        max_size=20,
        unique=True,
    )
)
def test_칩은_가장_최근_구간(keys: list[tuple[int, int]]) -> None:
    rows = [_row(prev_ym("2026-11", m), seg, 0, 1000 + m) for m, seg in keys]
    chip = flash_chip(rows)
    assert chip is not None
    want = max((prev_ym("2026-11", m), seg) for m, seg in keys)
    assert (chip.ym, chip.seg) == want
