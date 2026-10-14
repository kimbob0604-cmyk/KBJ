"""metrics §D0(백분위·다섯 칸·상태 점수)·§D1(σ 세 칸) — 손계산·경계."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kbj.config.diag import load_diag
from kbj.core.quality import Quality
from kbj.engines.diagnosis.bands import (
    pct_rank,
    pct_state,
    sigma_state,
    state_score,
    worst,
)

D = Decimal
BANDS = (20, 40, 60, 80)


def test_d0_pct_rank_hand_computed() -> None:
    w = [D(v) for v in (1, 2, 3, 4, 5)]
    # 평균 순위 r → (r − 1) ÷ (n − 1) × 100
    assert pct_rank(w, D(1)) == D(0)
    assert pct_rank(w, D(3)) == D(50)
    assert pct_rank(w, D(5)) == D(100)
    # 동점은 평균 순위: [1, 2, 2, 3] 의 2 → r = 1 + (2+1)/2 = 2.5 → 1.5/3 × 100 = 50
    assert pct_rank([D(1), D(2), D(2), D(3)], D(2)) == D(50)
    # 셋 중 둘째 둘이 같으면 r = 2.5 → 1.5/2 = 75
    assert pct_rank([D(1), D(7), D(7)], D(7)) == D(75)
    assert pct_rank([D(9)], D(9)) == D(50)
    # 소수 둘째 반올림: 7개 중 둘째 → 1/6 × 100 = 16.666… → 16.67
    assert pct_rank([D(v) for v in range(7)], D(1)) == D("16.67")


def test_d0_pct_rank_requires_today_in_window() -> None:
    with pytest.raises(ValueError, match="그날 포함"):
        pct_rank([D(1), D(2)], D(3))
    with pytest.raises(ValueError, match="빈 창"):
        pct_rank([], D(1))


@pytest.mark.parametrize(
    ("p", "state"),
    [
        ("0", "extreme_low"), ("19.99", "extreme_low"), ("20", "low"), ("39.99", "low"),
        ("40", "normal"), ("59.99", "normal"), ("60", "high"), ("79.99", "high"),
        ("80", "extreme_high"), ("100", "extreme_high"),
    ],
)  # fmt: skip
def test_d0_five_bands_edges(p: str, state: str) -> None:
    assert pct_state(D(p), BANDS) == state


def test_d0_bands_match_config() -> None:
    assert tuple(load_diag().bands) == BANDS


def test_d1_sigma_bands_hand_computed() -> None:
    # 창 [0, 0, 0, 0, 10]: 평균 2, 모분산 (4·4 + 64)/5 = 16 → σ 4. x = 10 → z = 2 → 넓음
    w = [D(0), D(0), D(0), D(0), D(10)]
    assert sigma_state(w, D(10), 1.0) == ("wide", D("2.00"))
    # x = 0 → z = −0.5 → 보통
    assert sigma_state(w, D(0), 1.0) == ("normal", D("-0.50"))
    # 경계: z = −1 정확히 → 좁음(≤ −1σ)
    w2 = [D(-1), D(1)]  # 평균 0, σ 1
    assert sigma_state(w2, D(-1), 1.0) == ("narrow", D("-1.00"))
    assert sigma_state(w2, D(1), 1.0) == ("wide", D("1.00"))
    # 표준편차 0 → 보통
    assert sigma_state([D(3), D(3)], D(3), 1.0) == ("normal", D(0))


@pytest.mark.parametrize(
    ("state", "risk_on", "score"),
    [
        ("extreme_high", "high", 1), ("high", "high", 1), ("normal", "high", 0),
        ("low", "high", -1), ("extreme_low", "high", -1),
        ("extreme_high", "low", -1), ("low", "low", 1),
        ("narrow", "low", 1), ("wide", "low", -1), ("normal", "low", 0),
        ("extreme_high", "none", 0), ("extreme_low", "none", 0),
        (None, "high", None),
    ],
)  # fmt: skip
def test_d0_state_score_table(state: str | None, risk_on: str, score: int | None) -> None:
    assert state_score(state, risk_on) == score  # type: ignore[arg-type]


def test_worst_quality_order() -> None:
    assert worst([]) is None
    assert worst([Quality.OK, Quality.STALE]) is Quality.STALE
    assert worst([Quality.STALE, Quality.ESTIMATED]) is Quality.ESTIMATED
    assert worst([Quality.ESTIMATED, Quality.INVALID, Quality.OK]) is Quality.INVALID
