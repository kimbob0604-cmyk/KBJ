"""회계 기간·정기보고서 제목·PIT TTM 창 — docs/p4_design.md §4.5, docs/metrics.md §9."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from kbj.core.fin_rows import QuarterRow
from kbj.engines.financials.period import (
    fiscal_period,
    fiscal_quarter,
    report_of_title,
    ttm_window,
)


@pytest.mark.parametrize(
    ("acc_mt", "q", "start", "end", "label"),
    [
        (12, 1, date(2025, 1, 1), date(2025, 3, 31), "2025 1Q"),
        (12, 4, date(2025, 10, 1), date(2025, 12, 31), "2025 4Q"),
        (3, 1, date(2025, 4, 1), date(2025, 6, 30), "FY25 1Q (25.04~25.06)"),
        (3, 4, date(2026, 1, 1), date(2026, 3, 31), "FY25 4Q (26.01~26.03)"),
        (6, 2, date(2025, 10, 1), date(2025, 12, 31), "FY25 2Q (25.10~25.12)"),
        (9, 3, date(2026, 4, 1), date(2026, 6, 30), "FY25 3Q (26.04~26.06)"),
    ],
)
def test_결산월별_분기_기간(acc_mt: int, q: int, start: date, end: date, label: str) -> None:
    p = fiscal_quarter(2025, q, acc_mt)
    assert (p.start, p.end, p.label) == (start, end, label)


def test_보고서_코드로() -> None:
    assert fiscal_period(2026, "11014", 12).end == date(2026, 9, 30)
    assert fiscal_period(2026, "11012", 3).end == date(2026, 9, 30)  # 3월 결산 반기
    assert fiscal_period(2026, "11011", None).end == date(2026, 12, 31)  # 모름 → 12월(추정)


@pytest.mark.parametrize(
    ("title", "acc_mt", "want"),
    [
        ("분기보고서 (2026.09)", 12, (2026, "11014")),
        ("반기보고서 (2026.06)", 12, (2026, "11012")),
        ("[기재정정]사업보고서 (2025.12)", 12, (2025, "11011")),
        ("반기보고서 (2026.09)", 3, (2026, "11012")),  # 3월 결산 — 회계연도 시작 해
        ("사업보고서 (2027.03)", 3, (2026, "11011")),
        ("분기보고서 (2026.12)", 3, (2026, "11014")),
        ("분기보고서 (2026.06)", 3, (2026, "11013")),
        ("사업보고서 (2026.09)", 12, None),  # 이름과 분기가 어긋남
        ("분기보고서 (2026.08)", 12, None),  # 분기 끝 달이 아님
        ("감사보고서", 12, None),
    ],
)
def test_제목에서_사업연도와_보고서(title: str, acc_mt: int, want: tuple[int, str] | None) -> None:
    assert report_of_title(title, acc_mt) == want


def _q(year: int, q: int, rcept: date | None, fs: str = "CFS") -> QuarterRow:
    p = fiscal_quarter(year, q, 12)
    return QuarterRow("99000001", year, q, fs, p.start, p.end, rcept, "full", revenue=q)  # type: ignore[arg-type]


def test_ttm_은_t_까지_공시된_연속_4분기() -> None:
    rows = [_q(2025, 1, date(2025, 5, 15)), _q(2025, 2, date(2025, 8, 14)),
            _q(2025, 3, date(2025, 11, 14)), _q(2025, 4, date(2026, 3, 20)),
            _q(2026, 1, date(2026, 5, 15))]  # fmt: skip
    assert ttm_window(rows, date(2026, 3, 19)) is None  # 4Q 전 — 3개뿐
    win = ttm_window(rows, date(2026, 3, 20))
    assert win is not None and [(r.fiscal_year, r.fiscal_q) for r in win] == [
        (2025, 1), (2025, 2), (2025, 3), (2025, 4)]  # fmt: skip
    win2 = ttm_window(rows, date(2026, 6, 1))
    assert win2 is not None and win2[0].fiscal_q == 2 and win2[-1].fiscal_year == 2026


def test_ttm_미래_분기를_바꿔도_과거_답은_같다() -> None:
    rows = [_q(2025, k, date(2025, 3 * k + 2, 14)) for k in (1, 2, 3)] + [
        _q(2025, 4, date(2026, 3, 20))]  # fmt: skip
    t = date(2026, 4, 1)
    before = ttm_window(rows, t)
    future = [*rows, _q(2026, 1, date(2026, 5, 15))]
    changed = [*rows[:-1], replace(rows[-1], revenue=999), _q(2026, 1, date(2026, 5, 15))]
    assert ttm_window(future, t) == before
    assert ttm_window(changed, date(2026, 3, 19)) is None


def test_ttm_은_기준을_섞지_않는다() -> None:
    rows = [_q(2025, 1, date(2025, 5, 15), "OFS"), _q(2025, 2, date(2025, 8, 14)),
            _q(2025, 3, date(2025, 11, 14)), _q(2025, 4, date(2026, 3, 20))]  # fmt: skip
    assert ttm_window(rows, date(2026, 4, 1)) is None
    rows.append(_q(2025, 1, date(2025, 5, 15), "CFS"))
    win = ttm_window(rows, date(2026, 4, 1))
    assert win is not None and {r.fs_div for r in win} == {"CFS"}
