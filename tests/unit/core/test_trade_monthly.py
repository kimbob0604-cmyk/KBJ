"""월별 수출입 추이 — 류 쌓기·전월비·전년동월비·분기 QoQ·YoY(docs/metrics.md §29a)."""

from __future__ import annotations

import pytest

from kbj.core.trade_monthly import OTHER, monthly_stack, prev_ym, quarter_of
from kbj.core.trade_rows import ChapterMonth


def _rows(months: list[str], per: dict[str, int], grow: float = 0.0) -> list[ChapterMonth]:
    out = []
    for i, ym in enumerate(months):
        for ch, v in per.items():
            out.append(ChapterMonth(ym, ch, int(v * (1 + grow) ** i), int(v / 2)))
    return out


def _yms(start: str, n: int) -> list[str]:
    out, ym = [], start
    for _ in range(n):
        out.append(ym)
        ym = prev_ym(ym, -1)
    return out


def test_prev_ym_quarter_of() -> None:
    assert prev_ym("2026-01") == "2025-12" and prev_ym("2026-03", 12) == "2025-03"
    assert prev_ym("2025-12", -1) == "2026-01"
    assert quarter_of("2026-09") == "2026Q3" and quarter_of("2026-10") == "2026Q4"


def test_top_chapters_and_other_sum_to_total() -> None:
    rows = _rows(_yms("2025-01", 12), {"85": 500, "87": 200, "84": 100, "27": 50, "01": 5})
    st = monthly_stack(rows, flow="exp", top=3, months=12)
    assert st.chapters == ("85", "87", "84", OTHER)
    p = st.months[-1]
    assert p.parts[OTHER] == 55 and sum(p.parts.values()) == p.total == 855


def test_mom_yoy_and_quarters() -> None:
    rows = _rows(_yms("2024-01", 24), {"85": 1000}, grow=0.01)
    st = monthly_stack(rows, flow="exp", months=24)
    last = st.months[-1]
    assert last.mom == pytest.approx(0.01, abs=2e-3)
    assert last.yoy == pytest.approx(1.01**12 - 1, abs=5e-3)
    assert [q.q for q in st.quarters] == [f"{y}Q{n}" for y in (2024, 2025) for n in (1, 2, 3, 4)]
    q = st.quarters[-1]
    assert q.qoq == pytest.approx(1.01**3 - 1, abs=5e-3)
    assert q.yoy == pytest.approx(1.01**12 - 1, abs=5e-3)
    assert st.quarters[0].qoq is None and st.quarters[3].yoy is None  # 비교 분기 없음


def test_partial_quarter_and_missing_month_are_not_zero() -> None:
    yms = [y for y in _yms("2025-01", 8) if y != "2025-05"]  # 5월 빠짐, 7·8월만 있는 3분기
    st = monthly_stack(_rows(yms, {"85": 100}), flow="imp", months=12)
    assert [q.q for q in st.quarters] == ["2025Q1"]  # 2분기(5월 빠짐)·3분기(두 달)는 없음
    june = next(p for p in st.months if p.ym == "2025-06")
    assert june.mom is None  # 5월이 없다 — 0 으로 보지 않는다
    assert st.flow == "imp" and june.total == 50


def test_empty_and_args() -> None:
    assert monthly_stack([], flow="exp").months == ()
    with pytest.raises(ValueError):
        monthly_stack([], flow="exp", top=0)
