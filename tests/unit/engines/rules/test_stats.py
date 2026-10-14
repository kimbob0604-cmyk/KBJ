"""알림 후 성과 통계(metrics §30.4) — 손계산."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kbj.engines.rules.stats import histogram, stats_cells


@dataclass(frozen=True)
class _O:
    horizon: int
    ret: float | None
    excess: float | None = None
    mfe: float | None = None
    mae: float | None = None


def test_cells_hand_calc() -> None:
    rows = [
        _O(1, 0.02, 0.01, 0.03, -0.01),
        _O(1, -0.01, -0.02, 0.01, -0.02),
        _O(1, 0.03, None, 0.05, -0.00),
        _O(1, None),  # 정지 — 뺀 수
        _O(5, 0.10, 0.05, 0.12, -0.03),
        _O(7, 0.5),  # 모르는 기간은 무시
    ]
    c1, c5, c20 = stats_cells(rows, (20, 1, 5), min_samples=3)
    assert (c1.horizon, c5.horizon, c20.horizon) == (1, 5, 20)
    assert c1.n == 3 and c1.excluded == 1
    assert c1.win_rate == pytest.approx(200 / 3)
    assert c1.mean == pytest.approx((2 - 1 + 3) / 3)
    assert c1.median == pytest.approx(2.0)
    assert c1.q25 == pytest.approx(0.5) and c1.q75 == pytest.approx(2.5)
    assert c1.excess_mean == pytest.approx((1 - 2) / 2)
    assert c1.mfe_median == pytest.approx(3.0) and c1.mae_median == pytest.approx(-1.0)
    assert c1.small is False
    assert c5.n == 1 and c5.small is True and c5.mean == pytest.approx(10.0)
    assert c20.n == 0 and c20.mean is None and c20.win_rate is None and c20.small


def test_histogram() -> None:
    assert histogram([]) == []
    bins = histogram([-3.0, -0.5, 0.0, 1.9, 4.0])
    assert bins[0].lo == -4.0 and bins[-1].hi == 4.0
    assert [b.n for b in bins] == [1, 1, 2, 1]  # 4.0 은 마지막 구간(포함)
    assert sum(b.n for b in bins) == 5
    wide = histogram([-100.0, 100.0])
    assert len(wide) <= 20 and sum(b.n for b in wide) == 2
    one = histogram([2.0, 2.0])
    assert len(one) == 1 and one[0].n == 2
    with pytest.raises(ValueError):
        histogram([1.0], step=0)
