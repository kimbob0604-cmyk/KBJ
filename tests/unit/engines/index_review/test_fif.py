"""유동비율 반올림·완충·FOL·DART 추정(metrics §24.2) — 경계값과 리뷰 날짜에 따른 규칙 선택.

2026-10-14: 완충 비교를 '≤' → '<'(GIMI 2026-08판 §3.1.7 — 변화가 단위 이상이면 반영),
완충 예외(비구성종목·FOL 로 인한 변경), FOL 0.1% 반올림·foreign room 계수(§3.1.2.6·§3.1.6.3)를
더했다."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.config.index_reviews import load_index_reviews
from kbj.engines.index_review.fif import (
    apply_fol,
    band_of,
    estimate_fif_raw,
    resolve_fif,
    room_factor,
    round_fif,
    round_fol,
)

CFG = load_index_reviews()
NEW = CFG.fif_rule_for(date(2026, 5, 13))
OLD = CFG.fif_rule_for(date(2026, 2, 11))


def test_rule_is_chosen_by_review_date() -> None:
    assert NEW.id == "msci_2026_05" and OLD.id == "msci_legacy"
    assert CFG.fif_rule_for(date(2026, 5, 1)).id == "msci_2026_05"  # 그날부터 새 규칙
    assert CFG.fif_rule_for(date(2026, 4, 30)).id == "msci_legacy"
    assert CFG.fif_rule_for(date(2025, 5, 13)).id == "msci_legacy"  # 사용자 요약의 2025-05 아님
    assert CFG.fif_rule_for(date(2026, 11, 11)).id == "msci_2026_05"


@pytest.mark.parametrize(
    ("raw", "step"),
    [(0.2501, 0.025), (0.25, 0.005), (0.0501, 0.005), (0.05, 0.001), (0.0001, 0.001)],
)
def test_new_rule_band_edges(raw: float, step: float) -> None:
    """경계값 자체(25%·5%)는 아래 띠(raw > above — 원문 "greater than 25%"; 5% 는 [확인 필요])."""
    assert band_of(raw, NEW).step == step


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        # 2.5% 반올림(반은 올림)
        (0.2624, 0.25), (0.2625, 0.275), (0.9874, 0.975), (0.9875, 1.0), (0.99, 1.0),
        (0.1234, 0.125), (0.1224, 0.12), (0.0512, 0.05), (0.0526, 0.055),  # 0.5%
        (0.0434, 0.043), (0.0435, 0.044), (0.0004, 0.001),  # 0.1%(가장 작은 단위 아래로 안 내림)
    ],
)  # fmt: skip
def test_new_rule_rounding(raw: float, want: float) -> None:
    got, how = round_fif(raw, NEW)
    assert how == "rounded" and got == pytest.approx(want, abs=1e-12)


@pytest.mark.parametrize(
    ("raw", "want"),
    [(0.1501, 0.2), (0.16, 0.2), (0.2, 0.2), (0.2001, 0.25), (0.15, 0.15), (0.144, 0.14),
     (0.145, 0.15), (0.97, 1.0)],
)  # fmt: skip
def test_old_rule_up_above_15_round_below(raw: float, want: float) -> None:
    got, _ = round_fif(raw, OLD)
    assert got == pytest.approx(want, abs=1e-12)


@pytest.mark.parametrize(
    ("raw", "prev", "kept"),
    [
        # 정확히 ±2.5%p — 반영(단위 이상이면 반영 — GIMI §3.1.7 '<'), 단위 바로 아래는 유지
        (0.30, 0.325, False), (0.30, 0.275, False), (0.30, 0.3249, True), (0.30, 0.2751, True),
        (0.10, 0.105, False), (0.10, 0.1049, True),  # 0.5%p 띠
        (0.03, 0.031, False), (0.03, 0.0309, True),  # 0.1%p 띠
    ],
)  # fmt: skip
def test_buffer_keeps_previous_within_band(raw: float, prev: float, kept: bool) -> None:
    got, how = round_fif(raw, NEW, prev)
    assert (how == "buffer") is kept
    if kept:
        assert got == prev


def test_old_rule_has_no_buffer() -> None:
    got, how = round_fif(0.30, OLD, 0.31)
    assert how == "rounded" and got == pytest.approx(0.30)


def test_invalid_raw_rejected() -> None:
    for bad in (0.0, -0.1, 1.2):
        with pytest.raises(ValueError):
            round_fif(bad, NEW)


def test_fol_caps_fif() -> None:
    assert apply_fol(0.6, 0.49) == (0.49, True)
    assert apply_fol(0.4, 0.49) == (0.4, False)
    assert apply_fol(0.4, None) == (0.4, False)
    fif, how = resolve_fif(0.7, None, NEW, 0.49)
    assert fif == 0.49 and how == "rounded+fol"


def test_fol_is_rounded_to_a_tenth_of_a_percent() -> None:
    step = CFG.fol.val("limit_step")
    assert step == 0.001
    assert round_fol(0.4904, step) == pytest.approx(0.490)
    assert round_fol(0.4905, step) == pytest.approx(0.491)  # 반은 올림
    assert round_fol(0.0001, step) == pytest.approx(0.001)  # 가장 작은 단위 아래로 안 내림
    fif, how = resolve_fif(0.7, None, NEW, 0.4906, fol_step=step)
    assert fif == pytest.approx(0.491) and how == "rounded+fol"


def test_buffer_exceptions_new_member_and_fol() -> None:
    # 기존 구성종목: |0.32 − 0.30| < 2.5%p → 직전 값 유지
    assert resolve_fif(0.32, 0.30, NEW, None, member=True) == (0.30, "buffer")
    # 비구성종목(신규 편입·세그먼트 이동)은 완충 없이 반올림 값
    assert resolve_fif(0.32, 0.30, NEW, None, member=False) == (pytest.approx(0.325), "rounded")
    # FOL 이 묶으면 FOL 로 인한 변경 — 완충(직전 0.50 유지) 대신 FOL 값
    assert resolve_fif(0.51, 0.50, NEW, None, member=True) == (0.50, "buffer")
    assert resolve_fif(0.51, 0.50, NEW, 0.49, member=True) == (0.49, "rounded+fol")


# FOL 0.49 에서 room = (0.49 − 보유) ÷ 0.49 — 경계를 십진으로 정확히 맞춘 보유 비율
HELD_ROOM_25 = 0.3675  # room 25%
HELD_ROOM_15 = 0.4165  # room 15%
HELD_ROOM_375 = 0.471625  # room 3.75%


@pytest.mark.parametrize(
    ("held", "member", "factor", "addable"),
    [
        (0.0, False, 1.0, True), (HELD_ROOM_25, False, 1.0, True),  # room ≥ 25% — 조정 없음
        (0.40, False, 0.5, True), (HELD_ROOM_15, False, 0.5, True),  # 15~25% — 0.5
        (0.45, False, 1.0, False),  # room < 15% — 신규 편입 불가
        (HELD_ROOM_25, True, 1.0, True),
        (0.48, True, 0.25, True),  # 기존 room < 3.75% — 0.25
        (HELD_ROOM_375, True, 1.0, True),  # 3.75% 정확히는 중간 단계(표 미확인 — 조정 안 함)
        (0.45, True, 1.0, True),  # 중간 단계 [확인 필요] — 조정 안 함(사유)
    ],
)  # fmt: skip
def test_foreign_room_factor(held: float, member: bool, factor: float, addable: bool) -> None:
    f, ok, why = room_factor(CFG.fol, 0.49, held, member=member)
    assert (f, ok) == (factor, addable)
    if member and f == 1.0 and held > HELD_ROOM_25:
        assert why is not None and "확인 필요" in why


def test_foreign_room_needs_limit_and_holding() -> None:
    assert room_factor(CFG.fol, None, 0.3, member=False) == (1.0, True, None)
    f, ok, why = room_factor(CFG.fol, 0.49, None, member=False)
    assert (f, ok) == (1.0, True) and why is not None and "미적용" in why


def test_resolve_without_raw_uses_prev_or_none() -> None:
    assert resolve_fif(None, 0.35, NEW, None) == (0.35, "prev")
    assert resolve_fif(None, None, NEW, None) == (None, None)  # 지어내지 않는다


def test_estimate_from_dart_holdings() -> None:
    holders = [("합성지주", 400), ("국민연금공단", 100), ("XX자산운용", 50)]
    assert estimate_fif_raw(1000, 100, holders, ("국민연금", "자산운용")) == pytest.approx(0.5)
    assert estimate_fif_raw(None, 0, [], ()) is None
    assert estimate_fif_raw(1000, 600, [("A", 500)], ()) is None  # 비유동 ≥ 발행 — 자료 불일치
