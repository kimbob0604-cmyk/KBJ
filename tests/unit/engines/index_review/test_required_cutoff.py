"""문턱·괴리율·필요 상승률(역산)·컷오프 추정(metrics §24.3·§24.4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kbj.config.index_reviews import load_index_reviews
from kbj.engines.index_review.cutoff import coverage_cutoff, estimate_cutoff
from kbj.engines.index_review.required import BP, gap, meets, required_rise, threshold


def test_threshold_and_gap() -> None:
    thr = threshold(1.8, 1_000_000_000_000)
    assert thr == Decimal("1800000000000.0")
    assert gap(1_980_000_000_000, thr) == pytest.approx(0.10)
    assert gap(1_800_000_000_000, thr) == 0.0
    with pytest.raises(ValueError):
        gap(1, Decimal(0))


def test_meets_at_equality() -> None:
    thr_m, thr_f = threshold(1.8, 100), threshold(0.9, 100)
    assert meets(180, 0.5, thr_m, thr_f)  # 시총 = 문턱, 유동 = 90 = 문턱
    assert not meets(179, 0.6, thr_m, thr_f)
    assert not meets(180, 0.49, thr_m, thr_f)


def test_required_rise_picks_larger_basis_and_rounds_up_to_bp() -> None:
    thr_m, thr_f = threshold(1.8, 1_000), threshold(0.9, 1_000)
    r, basis = required_rise(1_500, 0.5, thr_m, thr_f)  # 시총 +20%, 유동 900/750 → +20%
    assert basis == "mcap" and r == Decimal("0.2")
    r, basis = required_rise(1_500, 0.4, thr_m, thr_f)  # 유동 900/600 → +50%
    assert basis == "ff" and r == Decimal("0.5")
    r, _ = required_rise(1_700, None, thr_m, None)  # 1800/1700 − 1 = 0.0588235… → 0.0589
    assert r == Decimal("0.0589")
    assert meets(1_700, None, thr_m, None, r) and not meets(1_700, None, thr_m, None, r - BP)
    r, _ = required_rise(3_600, 0.9, thr_m, thr_f)  # 이미 충족 — 음수(여유)
    assert r == Decimal("-0.5")


def test_coverage_cutoff() -> None:
    items = [("A", 100, Decimal(50)), ("B", 80, Decimal(40)), ("C", 30, Decimal(10))]
    assert coverage_cutoff(items, 0.5) == 100  # 50/100 ≥ 50%
    assert coverage_cutoff(items, 0.85) == 80  # 90/100
    assert coverage_cutoff(items, 0.95) == 30
    assert coverage_cutoff([("A", 0, Decimal(0))], 0.85) is None


def test_estimate_cutoff_band_is_ordered() -> None:
    rules = load_index_reviews().msci_rules("msci_korea")
    items = [(f"{i:06d}", 1000 - i * 7, Decimal(500 - i * 3)) for i in range(100)]
    c = estimate_cutoff(items, rules)
    assert c is not None and c.kind == "model"
    assert c.low_won is not None and c.high_won is not None
    assert c.low_won <= c.won <= c.high_won
