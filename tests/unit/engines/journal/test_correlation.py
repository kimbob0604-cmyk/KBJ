"""상관 엔진 — 피어슨·겹침 부족·빈 날 잇지 않기(docs/p8_design.md §4.7, ADR 0033)."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from kbj.engines.journal.correlation import (
    MAX_CODES,
    corr_matrix,
    daily_returns,
    pearson,
)

CAL = [date(2026, 1, 1) + timedelta(days=i) for i in range(130)]


def _walk(seed: int, n: int = 130) -> list[float]:
    r = random.Random(seed)  # noqa: S311 — 시험용 합성 값
    px = [100.0]
    for _ in range(n - 1):
        px.append(px[-1] * (1 + r.gauss(0, 0.02)))
    return px


def test_pearson_by_hand() -> None:
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None  # 분산 0
    assert pearson([1.0], [1.0]) is None


def test_daily_returns_do_not_bridge_gaps() -> None:
    cal = CAL[:4]
    closes = {cal[0]: 100.0, cal[1]: None, cal[2]: 110.0, cal[3]: 121.0}
    r = daily_returns(closes, cal)
    assert set(r) == {cal[3]}
    assert r[cal[3]] == pytest.approx(0.1)


def test_matrix_symmetric_diag_and_identical_series() -> None:
    a, b = _walk(1), _walk(2)
    closes = {
        "990001": dict(zip(CAL, a, strict=True)),
        "990002": dict(zip(CAL, b, strict=True)),
        "990003": dict(zip(CAL, [x * 2 for x in a], strict=True)),
    }
    m = corr_matrix(closes, CAL, ["990001", "990002", "990003"], window=60)
    assert m[0][0] == 1.0 and m[1][1] == 1.0
    assert m[0][2] == pytest.approx(1.0)
    assert m[0][1] == m[1][0]
    assert m[0][1] is not None and -1 <= m[0][1] <= 1
    # 손계산과 같다(창 = 마지막 61 날 → 수익률 60개)
    ra = [a[i] / a[i - 1] - 1 for i in range(70, 130)]
    rb = [b[i] / b[i - 1] - 1 for i in range(70, 130)]
    mx, my = sum(ra) / 60, sum(rb) / 60
    num = sum((x - mx) * (y - my) for x, y in zip(ra, rb, strict=True))
    den = math.sqrt(sum((x - mx) ** 2 for x in ra) * sum((y - my) ** 2 for y in rb))
    assert m[0][1] == pytest.approx(num / den, abs=1e-12)


def test_short_overlap_is_none() -> None:
    a = _walk(3)
    short = {d: v for i, (d, v) in enumerate(zip(CAL, a, strict=True)) if i >= 100}  # 29 수익률
    closes = {"990001": dict(zip(CAL, a, strict=True)), "990002": short, "990009": {}}
    m = corr_matrix(closes, CAL, ["990001", "990002", "990009"], window=120)
    assert m[0][1] is None and m[1][1] is None and m[2][2] is None
    assert m[0][0] == 1.0


def test_limits() -> None:
    with pytest.raises(ValueError):
        corr_matrix({}, CAL, [f"99{i:04d}" for i in range(MAX_CODES + 1)], window=60)
    with pytest.raises(ValueError):
        corr_matrix({}, CAL, [], window=1)
