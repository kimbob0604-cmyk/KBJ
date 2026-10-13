"""겹쳐 보기 속성(metrics §28 — hypothesis, 묶음 MAP):

- 가중치 선형성: 모든 가중치를 a 배 하면 E 도 a 배, 줄 묶음 A∪B 의 E = E(A) + E(B).
- 분기 합 = 세 달 합(달 값이 다 있을 때).
- 상관은 [−1, 1] 이거나 None, NaN·Infinity 없음.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import ItemMonthRow, StockMapRow
from kbj.engines.trade.overlay import (
    Sources,
    export_index,
    lag_corr,
    quarter_of,
    quarter_sums,
    sources,
)

T = datetime(2026, 12, 11, 21, 0, tzinfo=KST)
MONTHS = [f"2026-{k:02d}" for k in range(1, 10)]
HS = ("85", "8542", "33", "3304")
VAL = st.integers(min_value=0, max_value=10**10)
W = st.sampled_from([0.05, 0.1, 0.2, 0.25, 0.3])


def _it(ym: str, hs: str, usd: int) -> ItemMonthRow:
    return ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs), exp_usd=Decimal(usd), exp_kg=None, imp_usd=Decimal(0),
        imp_kg=None, bal_usd=None, source="DATAGO:15101609", as_of=T, quality=Quality.OK,
    )  # fmt: skip


def _line(hs: str, w: float) -> StockMapRow:
    return StockMapRow(
        code="990001", hs=hs, flow="exp", region_id="", name="가상", weight=w, origin="public",
        reviewed=False, map_version="v", source="KBJ:trade_map", as_of=T, quality=Quality.OK,
        evidence=({"kind": "synthetic", "note": "x"},),
    )  # fmt: skip


VALUES = st.lists(VAL, min_size=len(MONTHS) * 2, max_size=len(MONTHS) * 2)


def _src(vals: list[int]) -> Sources:
    rows = [
        _it(ym, hs, vals[i * 2 + j])
        for i, ym in enumerate(MONTHS)
        for j, hs in enumerate(("85", "33"))
    ]
    return sources(rows, [], [], months=MONTHS)


@settings(max_examples=60, deadline=None)
@given(VALUES, W, W, st.sampled_from([1, 2, 3]))
def test_가중치_선형성(vals: list[int], wa: float, wb: float, a: int) -> None:
    src = _src(vals)
    ea, _, _ = export_index([_line("85", wa)], src)
    eb, _, _ = export_index([_line("33", wb)], src)
    eab, _, _ = export_index([_line("85", wa), _line("33", wb)], src)
    scaled, _, _ = export_index([_line("85", wa * a / 3), _line("33", wb * a / 3)], src)
    for ym in MONTHS:
        va, vb, vab = ea[ym][0], eb[ym][0], eab[ym][0]
        assert va is not None and vb is not None and vab is not None
        assert vab == va + vb
        sv = scaled[ym][0]
        assert sv is not None
        assert abs(float(sv) - float(vab) * a / 3) <= 1e-6 * max(1.0, float(vab))


@settings(max_examples=60, deadline=None)
@given(VALUES)
def test_분기_합은_세_달_합(vals: list[int]) -> None:
    e, _, _ = export_index([_line("85", 0.3), _line("33", 0.2)], _src(vals))
    qs = quarter_sums(e)
    for p, (v, _q) in qs.items():
        ms = [m for m in MONTHS if quarter_of(m) == p]
        assert len(ms) == 3 and v is not None
        assert v == sum((e[m][0] or Decimal(0) for m in ms), Decimal(0))


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.floats(min_value=-5, max_value=5, allow_nan=False), min_size=12, max_size=12),
    st.lists(st.floats(min_value=-5, max_value=5, allow_nan=False), min_size=12, max_size=12),
)
def test_상관은_범위_안(xs: list[float], ys: list[float]) -> None:
    qs = [f"{y}Q{q}" for y in (2024, 2025, 2026) for q in range(1, 5)]
    out, _ = lag_corr(
        dict(zip(qs, xs, strict=True)),
        dict(zip(qs, ys, strict=True)),
        lags=(0, 1, 2),
        min_quarters=8,
    )
    for v in out.values():
        assert v is None or (math.isfinite(v) and -1.0 <= v <= 1.0)
