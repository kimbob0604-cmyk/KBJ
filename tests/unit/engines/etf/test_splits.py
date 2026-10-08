"""분할·병합 감지(docs/metrics.md §8.2)."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay
from kbj.engines.etf.splits import SPLIT_RATIOS, adjust, detect_split

A, B = date(2026, 10, 1), date(2026, 10, 2)


def row(d: date, s: int | None, nav: float | None, code: str = "E1") -> EtfDay:
    return EtfDay(code, d, "합성", nav, nav, s, None, None, None, None, None, "krx", "KRX",
                  Quality.OK)  # fmt: skip


@pytest.mark.parametrize("k", SPLIT_RATIOS)
def test_every_ratio_split_and_merge(k: int) -> None:
    s, nav = 1_000_000, 50_000.0
    c = detect_split(row(A, s, nav), row(B, s * k, nav / k))
    assert c is not None and c.kind == "split" and c.ratio == k and c.k == k
    m = detect_split(row(A, s * k, nav / k), row(B, s, nav))
    assert m is not None and m.kind == "merge" and m.ratio == pytest.approx(1 / k)


def test_needs_both_shares_and_nav() -> None:
    assert detect_split(row(A, 1_000, 100.0), row(B, 10_000, 100.0)) is None  # 큰 설정
    assert detect_split(row(A, 1_000, 100.0), row(B, 1_000, 10.0)) is None  # NAV 급락만
    assert detect_split(row(A, 1_000, 100.0), row(B, 1_010, 100.5)) is None  # 보통 날


def test_tolerance_two_percent() -> None:
    near = detect_split(row(A, 1_000_000, 10_000.0), row(B, 10_150_000, 1_015.0))  # 1.5%
    assert near is not None and near.ratio == 10
    far = detect_split(row(A, 1_000_000, 10_000.0), row(B, 10_300_000, 1_000.0))  # 3%
    assert far is None
    loose = detect_split(row(A, 1_000_000, 10_000.0), row(B, 10_300_000, 1_000.0), tol=0.05)
    assert loose is not None


def test_missing_or_zero_inputs_are_undecidable() -> None:
    assert detect_split(row(A, None, 100.0), row(B, 1_000, 10.0)) is None
    assert detect_split(row(A, 1_000, 0.0), row(B, 1_000, 10.0)) is None


def test_bad_arguments() -> None:
    with pytest.raises(ValueError):
        detect_split(row(B, 1, 1.0), row(A, 1, 1.0))
    with pytest.raises(ValueError):
        detect_split(row(A, 1, 1.0), row(B, 1, 1.0, code="E2"))
    with pytest.raises(ValueError):
        detect_split(row(A, 1, 1.0), row(B, 2, 0.5), ratios=(1,))
    with pytest.raises(ValueError):
        detect_split(row(A, 1, 1.0), row(B, 2, 0.5), tol=0.0)


def test_adjust_and_event() -> None:
    p = adjust(row(A, 1_000_003, 10_000.0), 10.0)
    assert p.list_shrs == 10_000_030 and p.nav == pytest.approx(1_000.0)
    c = detect_split(row(A, 1_000_000, 10_000.0), row(B, 10_000_000, 1_000.0))
    assert c is not None
    ev = c.to_event()
    assert (ev.origin, ev.quality, ev.ratio, ev.effective_date) == (
        "detected",
        Quality.ESTIMATED,
        10.0,
        B,
    )
    assert "1:10 분할" in ev.note and ev.source
    with pytest.raises(ValueError):
        adjust(row(A, 1, 1.0), 0)
