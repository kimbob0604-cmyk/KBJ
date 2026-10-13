"""오버행 상태·요약·추정 보호예수(kbj.engines.filings.overhang) — docs/metrics.md §12, 설계 §4.3.

합성 세계 #14(CB 3·BW 1·제3자배정 유상증자 — 리픽싱 2회·일부 청구)의 손계산
(`p4_world.expect_overhang`·`p4_expect.yaml hand.overhang_994130`)과 같은지, 그리고 정정·연결 실패·
파싱 실패·EB·유상증자 상장 전후 같은 경계를 본다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.core.fin_rows import (
    Instrument,
    OverhangAdjust,
    OverhangEvent,
    OverhangState,
    ShareCount,
)
from kbj.core.quality import Quality
from kbj.engines.filings import overhang as oh
from tests.unit.engines.filings.filings_world import world

EXPECT = Path(__file__).resolve().parents[4] / "tests/fixtures/synthetic/p4_expect.yaml"
CORP = "99400001"
T = date(2026, 11, 16)


def _rc(n: int, day: date = date(2025, 1, 2)) -> str:
    return f"{day:%Y%m%d}{n:06d}"


def cb(n: int = 1, **kw: Any) -> OverhangEvent:
    base: dict[str, Any] = dict(
        rcept_no=_rc(n), corp_code=CORP, rcept_dt=date(2025, 1, 2), instrument=Instrument.CB,
        face_value=10_000_000_000, price=10_000.0, shares=1_000_000, refix_floor=7_000.0,
        exercise_start=date(2026, 1, 2), exercise_end=date(2027, 12, 2),
        maturity=date(2028, 1, 2), listing_date=None, alloc_method="사모", parse_source="api",
        seq=str(n),
    )  # fmt: skip
    base.update(kw)
    return OverhangEvent(**base)


def adj(n: int, target: str | None, kind: str, *, day: date = date(2026, 3, 2),
        **kw: Any) -> OverhangAdjust:  # fmt: skip
    base: dict[str, Any] = dict(
        rcept_no=_rc(n, day), corp_code=CORP, rcept_dt=day, target_rcept_no=target, kind=kind,
        new_price=None, amount=None, shares=None, effective_date=day,
    )  # fmt: skip
    base.update(kw)
    return OverhangAdjust(**base)


def shares(n: int = 50_000_000, period_end: date = date(2026, 9, 30)) -> ShareCount:
    return ShareCount(CORP, period_end, _rc(900, period_end + timedelta(days=45)), n, None, 0,
                      None, rcept_dt=period_end + timedelta(days=45))  # fmt: skip


def one(states: list[OverhangState], rcept_no: str) -> OverhangState:
    return next(s for s in states if s.rcept_no == rcept_no)


# ── 합성 세계 #14 — 손계산과 같다 ────────────────────────────────────────────────────────


def test_world_case_14_matches_hand_calculation() -> None:
    w = world()
    corp = w.by_no(14).company_corp
    evs = [e for e in w.overhang_events if e.corp_code == corp]
    adjs = [a for a in w.overhang_adjusts if a.corp_code == corp]
    t = w.today
    states = oh.state_at(evs, adjs, t, lockup_months=12)
    exp = w.expect_overhang()
    assert {s.rcept_no: s.potential_shares for s in states} == exp["per_event"]
    sc = max(
        (x for x in w.share_counts if x.corp_code == corp and (x.rcept_dt or x.period_end) <= t),
        key=lambda x: (x.period_end, x.rcept_no),
    )
    s = oh.summary(states, sc, t, events=evs)
    hand = yaml.safe_load(EXPECT.read_text(encoding="utf-8"))["hand"]["overhang_994130"]
    assert s.potential_new_shares == exp["potential_new_shares"] == hand["potential_new_shares"]
    assert s.exercisable_shares == exp["exercisable_shares"] == hand["exercisable_shares"]
    assert s.denominator == hand["denominator"] and s.denom_source == oh.DENOM_SOURCE
    assert s.ratio_pct == pytest.approx(hand["ratio_pct"], abs=5e-5)
    assert s.exercisable_ratio_pct == pytest.approx(hand["exercisable_ratio_pct"], abs=5e-5)
    assert s.exchange_shares == 0 and s.quality is Quality.OK
    # 리픽싱 2회 뒤 CB2 가격 = 7,200(오늘 적용), 제3자배정 유증은 상장(07-15) 뒤라 0 + 추정 해제일
    cb2 = one(states, w.rcept("e14.cb2"))
    assert cb2.price == 7_200.0 and cb2.remaining_face == 15_000_000_000
    paid = one(states, w.rcept("e14.paid"))
    assert (paid.active, paid.potential_shares) == (False, 0)
    assert paid.lockup_end_est == date(2027, 7, 15)
    assert oh.LOCKUP_BASIS in paid.notes


def test_world_case_14_is_point_in_time() -> None:
    """어제 기준이면 오늘(11-16) 접수된 리픽싱·BW 행사는 반영되지 않는다(PIT)."""
    w = world()
    corp = w.by_no(14).company_corp
    evs = [e for e in w.overhang_events if e.corp_code == corp]
    adjs = [a for a in w.overhang_adjusts if a.corp_code == corp]
    y = w.today - timedelta(days=1)
    states = oh.state_at(evs, adjs, y, lockup_months=12)
    assert one(states, w.rcept("e14.cb2")).price == 8_500.0
    assert one(states, w.rcept("e14.bw1")).remaining_face == 8_000_000_000
    assert {s.rcept_no: s.potential_shares for s in states} == w.expect_overhang(y)["per_event"]


# ── 경계·규칙 ──────────────────────────────────────────────────────────────────────────


def test_active_window_and_exercisable() -> None:
    ev = cb()
    before_start = oh.state_at([ev], [], date(2025, 6, 1), lockup_months=12)[0]
    assert before_start.active and not before_start.exercisable
    assert before_start.potential_shares == 1_000_000
    on_end = oh.state_at([ev], [], date(2027, 12, 2), lockup_months=12)[0]
    assert on_end.active and on_end.exercisable  # 청구기간 끝날 그날까지 포함
    after = oh.state_at([ev], [], date(2027, 12, 3), lockup_months=12)[0]
    assert (after.active, after.exercisable, after.potential_shares) == (False, False, 0)
    # 결정 공시 접수 전(t < rcept_dt)에는 상태가 없다
    assert oh.state_at([ev], [], date(2025, 1, 1), lockup_months=12) == []


def test_exercise_and_redemption_reduce_remaining_and_full_claim_deactivates() -> None:
    ev = cb()
    a1 = adj(1, ev.rcept_no, "exercise", amount=3_000_000_000, shares=300_000)
    a2 = adj(2, ev.rcept_no, "redemption", amount=2_000_000_000, day=date(2026, 5, 2))
    s = oh.state_at([ev], [a1, a2], T, lockup_months=12)[0]
    assert s.remaining_face == 5_000_000_000 and s.potential_shares == 500_000
    full = adj(3, ev.rcept_no, "exercise", amount=5_000_000_000, day=date(2026, 6, 2))
    s2 = oh.state_at([ev], [a1, a2, full], T, lockup_months=12)[0]
    assert (s2.remaining_face, s2.active, s2.potential_shares) == (0, False, 0)


def test_effective_date_in_future_is_not_applied_yet() -> None:
    ev = cb()
    later = adj(1, ev.rcept_no, "price_adjust", new_price=8_000.0, day=date(2026, 11, 10),
                effective_date=date(2026, 11, 20))  # fmt: skip
    assert oh.state_at([ev], [later], T, lockup_months=12)[0].price == 10_000.0
    assert oh.state_at([ev], [later], date(2026, 11, 20), lockup_months=12)[0].price == 8_000.0


def test_last_price_adjust_wins_and_divides_with_floor() -> None:
    ev = cb(face_value=10_000_000_000)
    p1 = adj(1, ev.rcept_no, "price_adjust", new_price=9_000.0, day=date(2026, 2, 2))
    p2 = adj(2, ev.rcept_no, "price_adjust", new_price=7_777.0, day=date(2026, 4, 2))
    s = oh.state_at([ev], [p2, p1], T, lockup_months=12)[0]
    assert s.price == 7_777.0 and s.potential_shares == 10_000_000_000 // 7_777


def test_decimal_division_does_not_lose_a_share_to_binary_float() -> None:
    # 0.1 단위 가격 — 정확히 나누어떨어지면 버림 오차로 1주가 빠지지 않는다
    assert oh.potential_shares(1_234_560_000, 1234.56) == 1_000_000
    assert oh.potential_shares(10, 3.0) == 3
    with pytest.raises(ValueError):
        oh.potential_shares(10, 0.0)


def test_invalid_or_unlinked_adjusts_are_ignored() -> None:
    ev = cb()
    bad = adj(1, None, "exercise", amount=9_000_000_000, quality=Quality.INVALID,
              note="대상 결정 공시를 찾지 못했다")  # fmt: skip
    s = oh.state_at([ev], [bad], T, lockup_months=12)[0]
    assert s.remaining_face == 10_000_000_000 and s.quality is Quality.OK


def test_unparsed_claim_marks_upper_bound_estimated() -> None:
    ev = cb()
    s = oh.state_at([ev], [], T, lockup_months=12, unparsed={_rc(7): Instrument.CB})[0]
    assert s.quality is Quality.ESTIMATED and any("청구분 미반영" in n for n in s.notes)
    assert s.remaining_face == 10_000_000_000  # 권면 상한 그대로
    other = oh.state_at([ev], [], T, lockup_months=12, unparsed={_rc(7): Instrument.BW})[0]
    assert other.quality is Quality.OK  # 다른 상품의 파싱 실패는 걸리지 않는다
    unknown = oh.state_at([ev], [], T, lockup_months=12, unparsed={_rc(7): None})[0]
    assert unknown.quality is Quality.ESTIMATED


def test_eb_is_exchange_shares_not_new_shares() -> None:
    eb = cb(2, instrument=Instrument.EB, face_value=6_000_000_000, price=20_000.0)
    c = cb(1)
    states = oh.state_at([c, eb], [], T, lockup_months=12)
    s = oh.summary(states, shares(), T)
    assert s.exchange_shares == 300_000
    assert s.potential_new_shares == 1_000_000  # CB 만
    assert s.ratio_pct == pytest.approx(2.0)


def test_paid_in_counts_only_until_listing_and_lockup_estimate() -> None:
    paid = cb(5, instrument=Instrument.PAID_IN, face_value=None, price=5_000.0, shares=2_000_000,
              exercise_start=None, exercise_end=None, maturity=None, seq=None,
              listing_date=date(2026, 12, 1), alloc_method="제3자배정증자")  # fmt: skip
    before = oh.state_at([paid], [], T, lockup_months=12)[0]
    assert (before.active, before.exercisable, before.potential_shares) == (True, False, 2_000_000)
    assert before.lockup_end_est == date(2027, 12, 1) and before.quality is Quality.OK
    listed = oh.state_at([paid], [], date(2026, 12, 1), lockup_months=12)[0]
    assert (listed.active, listed.potential_shares) == (False, 0)
    s = oh.summary([before], shares(), T)
    assert s.potential_new_shares == 2_000_000 and s.exercisable_shares == 0
    est = oh.lockup_estimates([paid], 12)
    assert len(est) == 1 and est[0].quality is Quality.ESTIMATED
    assert est[0].lockup_end_est == date(2027, 12, 1) and est[0].basis == oh.LOCKUP_BASIS
    public = replace(paid, alloc_method="주주배정후 실권주 일반공모")
    assert oh.lockup_estimates([public], 12) == []
    assert oh.state_at([public], [], T, lockup_months=12)[0].lockup_end_est is None


def test_paid_in_without_listing_date_is_excluded_and_flagged() -> None:
    paid = cb(5, instrument=Instrument.PAID_IN, face_value=None, price=5_000.0, shares=2_000_000,
              exercise_start=None, exercise_end=None, maturity=None, seq=None,
              listing_date=None, alloc_method=None)  # fmt: skip
    st = oh.state_at([paid], [], T, lockup_months=12)
    assert st[0].potential_shares is None and st[0].quality is Quality.ESTIMATED
    s = oh.summary(st, shares(), T)
    assert s.potential_new_shares == 0 and s.quality is Quality.ESTIMATED
    assert any("모르는 결정 1건" in n for n in s.notes)


def test_bonus_issue_is_not_overhang() -> None:
    bonus = cb(6, instrument=Instrument.BONUS, face_value=None, price=None, shares=10_000_000,
               exercise_start=None, exercise_end=None, maturity=None, seq=None,
               listing_date=date(2026, 12, 1))  # fmt: skip
    assert oh.state_at([bonus], [], T, lockup_months=12) == []


def test_missing_face_or_price_falls_back_to_decision_shares_estimated() -> None:
    no_face = cb(face_value=None)
    s = oh.state_at([no_face], [], T, lockup_months=12)[0]
    assert s.potential_shares == 1_000_000 and s.quality is Quality.ESTIMATED
    no_price = cb(price=None)
    s2 = oh.state_at([no_price], [], T, lockup_months=12)[0]
    assert s2.potential_shares == 1_000_000 and s2.quality is Quality.ESTIMATED
    nothing = cb(price=None, shares=None)
    s3 = oh.state_at([nothing], [], T, lockup_months=12)[0]
    assert s3.potential_shares is None and s3.active
    summ = oh.summary([s3], shares(), T)
    assert summ.potential_new_shares == 0 and summ.quality is Quality.ESTIMATED


def test_summary_without_denominator_has_no_ratio() -> None:
    st = oh.state_at([cb()], [], T, lockup_months=12)
    s = oh.summary(st, None, T)
    assert (s.denominator, s.denom_source, s.ratio_pct, s.exercisable_ratio_pct) == (
        None, None, None, None,
    )  # fmt: skip
    assert any("주식총수" in n for n in s.notes)
    zero = ShareCount(CORP, date(2026, 9, 30), _rc(901), 0, None, None, None)
    assert oh.summary(st, zero, T).denominator is None  # 0 으로 나누지 않는다


def test_summary_denominator_quality_carries_into_the_ratio() -> None:
    st = oh.state_at([cb()], [], T, lockup_months=12)
    assert oh.summary(st, shares(), T).quality is Quality.OK
    bad = replace(shares(), quality=Quality.INVALID)
    s = oh.summary(st, bad, T)  # invalid 분모는 쓰지 않는다
    assert (s.denominator, s.ratio_pct) == (None, None)
    assert s.potential_new_shares == 1_000_000  # 잠재 주식(분자)은 그대로
    est = oh.summary(st, replace(shares(), quality=Quality.ESTIMATED), T)
    assert est.ratio_pct == pytest.approx(2.0) and est.quality is Quality.ESTIMATED
    assert any("분모(주식총수) 품질 estimated" in n for n in est.notes)


def test_summary_notes_new_listing_after_denominator_date() -> None:
    paid = cb(5, instrument=Instrument.PAID_IN, face_value=None, shares=1_000, seq=None,
              exercise_start=None, exercise_end=None, maturity=None,
              listing_date=date(2026, 10, 20), alloc_method="일반공모")  # fmt: skip
    st = oh.state_at([cb(), paid], [], T, lockup_months=12)
    s = oh.summary(st, shares(period_end=date(2026, 9, 30)), T, events=[paid])
    assert any("뒤 신주 상장 1건" in n for n in s.notes)


def test_summary_needs_corp_when_empty_and_rejects_mixed() -> None:
    with pytest.raises(ValueError):
        oh.summary([], None, T)
    empty = oh.summary([], shares(), T, corp_code=CORP)
    assert (empty.potential_new_shares, empty.ratio_pct) == (0, 0.0)
    other = replace(oh.state_at([cb()], [], T, lockup_months=12)[0], corp_code="99400002")
    with pytest.raises(ValueError, match="다른 회사"):
        oh.summary([other], shares(), T, corp_code=CORP)


# ── 정정 대체 ──────────────────────────────────────────────────────────────────────────


def test_corrected_decision_with_same_seq_replaces_and_adjusts_follow() -> None:
    old = cb(1, face_value=10_000_000_000)
    fixed = cb(1, rcept_no=_rc(50, date(2025, 1, 9)), rcept_dt=date(2025, 1, 9),
               face_value=12_000_000_000)  # fmt: skip
    claim = adj(1, old.rcept_no, "exercise", amount=2_000_000_000)  # 옛 접수번호를 가리킨다
    states = oh.state_at([old, fixed], [claim], T, lockup_months=12,
                         corrections={fixed.rcept_no})  # fmt: skip
    assert [s.rcept_no for s in states] == [fixed.rcept_no]
    assert states[0].remaining_face == 10_000_000_000  # 12e9 − 2e9(옛 대상 청구가 옮겨 왔다)


def test_corrected_paid_in_without_seq_replaces_previous_of_same_instrument() -> None:
    def paid(n: int, d: date, n_sh: int) -> OverhangEvent:
        return cb(n, rcept_no=_rc(n, d), rcept_dt=d, instrument=Instrument.PAID_IN,
                  face_value=None, shares=n_sh, seq=None, exercise_start=None,
                  exercise_end=None, maturity=None, listing_date=date(2026, 12, 20))  # fmt: skip

    a, b = paid(1, date(2026, 10, 1), 1_000), paid(2, date(2026, 10, 5), 1_200)
    kept, alias = oh.dedupe_events([a, b], {b.rcept_no})
    assert [e.rcept_no for e in kept] == [b.rcept_no] and alias == {a.rcept_no: b.rcept_no}
    kept2, _ = oh.dedupe_events([a, b])  # 정정이 아니면 둘 다 다른 증자
    assert len(kept2) == 2


def test_corrected_adjust_replaces_previous_same_target_and_kind() -> None:
    ev = cb()
    first = adj(1, ev.rcept_no, "exercise", amount=3_000_000_000, day=date(2026, 3, 2))
    fix = adj(2, ev.rcept_no, "exercise", amount=2_500_000_000, day=date(2026, 3, 5))
    other = adj(3, ev.rcept_no, "price_adjust", new_price=9_000.0, day=date(2026, 3, 1))
    got = oh.dedupe_adjusts([first, fix, other], {fix.rcept_no})
    assert {a.rcept_no for a in got} == {fix.rcept_no, other.rcept_no}
    s = oh.state_at([ev], [first, fix, other], T, lockup_months=12, corrections={fix.rcept_no})[0]
    assert s.remaining_face == 7_500_000_000


# ── 로그인 덧붙임(읽을 때) ─────────────────────────────────────────────────────────────


def test_login_helpers() -> None:
    s = oh.state_at([cb()], [], T, lockup_months=12)[0]
    assert oh.itm(s, 10_000.0) is True and oh.itm(s, 9_999.0) is False
    assert oh.itm(s, None) is None
    assert oh.overhang_value(s, 12_345.0) == 1_000_000 * 12_345
    assert oh.overhang_value(replace(s, active=False, exercisable=False), 1.0) == 0
    summ = oh.summary([s], shares(), T)
    assert oh.login_ratio(summ, 40_000_000) == pytest.approx(2.5)
    assert oh.login_ratio(summ, None) is None and oh.login_ratio(summ, 0) is None


def test_event_horizon() -> None:
    assert oh.event_horizon(cb(), 12) == date(2027, 12, 2)
    paid = cb(5, instrument=Instrument.PAID_IN, listing_date=date(2026, 7, 15),
              alloc_method="제3자배정", seq=None)  # fmt: skip
    assert oh.event_horizon(paid, 12) == date(2027, 7, 15)
    assert oh.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
