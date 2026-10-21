"""실적 프리뷰(P10 단계 3 — `kbj.engines.trade.preview`): 회귀·진행 중 분기 채우기·컨센서스
잔여 분기 평균·판정 문턱·신뢰도·점 부족. 손으로 만든 값."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kbj.engines.trade.preview import PreviewCfg, fit_line, preview, quarter_months

D = Decimal


def _world(last_ym: str = "2026-08") -> tuple[dict[str, Decimal | None], dict[str, int | None]]:
    """매출 = 1000 + 2 × (분기 수출 × 환율 1000) 이 정확히 맞는 세계 — 2024Q1~2026Q2 공시."""
    monthly: dict[str, Decimal | None] = {}
    revenue: dict[str, int | None] = {}
    y, m = 2024, 1
    k = 0
    while f"{y:04d}-{m:02d}" <= last_ym:
        monthly[f"{y:04d}-{m:02d}"] = D(100 + k)
        k += 1
        m += 1
        if m == 13:
            y, m = y + 1, 1
    for q in (
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
        "2025Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
        "2026Q1",
        "2026Q2",
    ):
        x = sum(float(monthly[ym] or 0) for ym in quarter_months(q)) * 1000
        revenue[q] = int(1000 + 2 * x)
    return monthly, revenue


FX = {f"{y}-{m:02d}": 1000.0 for y in (2024, 2025, 2026) for m in range(1, 13)}


def test_fit_line() -> None:
    a, b, r2 = fit_line([1, 2, 3], [3, 5, 7]) or (0, 0, 0)
    assert (a, b, r2) == pytest.approx((1, 2, 1))
    assert fit_line([1], [1]) is None and fit_line([2, 2], [1, 3]) is None


def test_진행_중_분기는_평균으로_채우고_잔여_분기_컨센서스와_비교() -> None:
    monthly, revenue = _world("2026-08")  # 2026Q3 는 7·8월만
    # 2026 연간 컨센서스: 공시 Q1·Q2 + 남은 두 분기 각각 정답의 110%
    true_q3 = 1000 + 2 * ((130 + 131) / 2 * 3 * 1000)
    annual = {
        2026: int(revenue["2026Q1"] or 0) + int(revenue["2026Q2"] or 0) + int(true_q3 * 1.1) * 2
    }
    p = preview(monthly, revenue, fx=FX, annual_consensus=annual)
    assert p is not None and p.quarter == "2026Q3" and p.months_seen == 2 and not p.reported
    assert p.revenue_est == pytest.approx(true_q3, rel=1e-9)
    assert p.consensus == pytest.approx(true_q3 * 1.1, rel=1e-6)
    assert p.diff == pytest.approx(1 / 1.1 - 1, rel=1e-6) and p.verdict == "MISS"
    assert p.r2 == pytest.approx(1.0) and p.confidence == "높음" and p.points >= 6
    assert any("2개월만" in n for n in p.notes)


def test_문턱_안이면_IN_LINE_위면_BEAT() -> None:
    monthly, revenue = _world("2026-09")
    true_q3 = 1000 + 2 * ((130 + 131 + 132) * 1000)
    base = int(revenue["2026Q1"] or 0) + int(revenue["2026Q2"] or 0)
    p = preview(monthly, revenue, fx=FX, annual_consensus={2026: base + int(true_q3 * 1.02) * 2})
    assert p is not None and p.verdict == "IN LINE"
    p = preview(monthly, revenue, fx=FX, annual_consensus={2026: base + int(true_q3 * 0.9) * 2})
    assert p is not None and p.verdict == "BEAT"


def test_공시된_분기는_사후_대조_컨센서스_없음() -> None:
    monthly, revenue = _world("2026-06")
    p = preview(monthly, revenue, fx=FX, annual_consensus={2026: 10**12})
    assert (
        p is not None
        and p.quarter == "2026Q2"
        and p.reported
        and p.consensus is None
        and p.verdict is None
    )
    assert p.revenue_est == pytest.approx(revenue["2026Q2"], rel=1e-9)


def test_점_부족이면_추정하지_않는다() -> None:
    monthly, revenue = _world("2026-08")
    few = {q: v for q, v in revenue.items() if q >= "2025Q3"}
    p = preview(monthly, few, fx=FX, annual_consensus={}, cfg=PreviewCfg(min_points=6))
    assert p is not None and p.revenue_est is None and p.verdict is None and p.confidence == "낮음"
    assert any("회귀 점 부족" in n for n in p.notes)
    assert preview({}, revenue, fx=FX, annual_consensus={}) is None


def test_환율_없으면_메모() -> None:
    monthly, revenue = _world("2026-08")
    p = preview(monthly, revenue, fx={}, annual_consensus={})
    assert p is not None and any("환율" in n for n in p.notes)
