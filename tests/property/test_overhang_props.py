"""오버행 상태 속성 시험(hypothesis) — docs/p4_design.md §8.3 'test_overhang_props', metrics §12.

엔진(`kbj.engines.filings.overhang`)과 따로 쓴 산수(오라클)로 본다.

- 잔여 권면 ≥ 0, 잠재 주식 ≥ 0, 행사 가능 ⇒ 활성.
- 활성 구간: 결정일 ≤ t ≤ min(청구기간 끝, 만기) 이고 잔여 > 0.
- 잠재 주식 = ⌊잔여 ÷ 마지막 조정 가격(적용일 ≤ t)⌋ — 전환가 조정 뒤 잠재 주식 = 잔여 ÷ 새 가격.
- 유상증자는 상장일 뒤 0, 그 전에는 신주 수. EB 는 신주 합(오버행 비율 분자)에 없다.
- PIT: 접수일 > t 인 조정을 더해도 t 의 상태는 그대로.
- 청구(행사)를 하나 더하면 잠재 주식은 늘지 않는다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from fractions import Fraction
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.fin_rows import Instrument, OverhangAdjust, OverhangEvent, ShareCount
from kbj.engines.filings import overhang as oh

CORP = "99400001"
D0 = date(2024, 1, 1)
DAYS = 1500


def _d(n: int) -> date:
    return D0 + timedelta(days=n)


@st.composite
def bond(draw: st.DrawFn, n: int = 1) -> OverhangEvent:
    inst = draw(st.sampled_from([Instrument.CB, Instrument.BW, Instrument.EB]))
    start = draw(st.integers(0, 400))
    ex_s = start + draw(st.integers(0, 400))
    ex_e = ex_s + draw(st.integers(0, 600))
    mat = ex_e + draw(st.integers(0, 60))
    face = draw(st.integers(1, 2_000)) * 10_000_000
    price = float(draw(st.integers(500, 50_000)))
    return OverhangEvent(
        rcept_no=f"{_d(start):%Y%m%d}{n:06d}", corp_code=CORP, rcept_dt=_d(start),
        instrument=inst, face_value=face, price=price, shares=math.floor(face / price),
        refix_floor=None, exercise_start=_d(ex_s), exercise_end=_d(ex_e), maturity=_d(mat),
        listing_date=None, alloc_method="사모", parse_source="api", seq=str(n),
    )  # fmt: skip


@st.composite
def adjusts(draw: st.DrawFn, ev: OverhangEvent) -> list[OverhangAdjust]:
    out: list[OverhangAdjust] = []
    k = draw(st.integers(0, 6))
    base = (ev.rcept_dt - D0).days
    for i in range(k):
        day = base + draw(st.integers(0, 900))
        lag = draw(st.integers(0, 20))
        kind: Literal["exercise", "redemption", "price_adjust"] = draw(
            st.sampled_from(["exercise", "redemption", "price_adjust"])
        )
        price = float(draw(st.integers(300, 60_000))) if kind == "price_adjust" else None
        amt = None if kind == "price_adjust" else draw(st.integers(0, 400)) * 10_000_000
        out.append(
            OverhangAdjust(
                rcept_no=f"{_d(day):%Y%m%d}{500 + i:06d}", corp_code=CORP, rcept_dt=_d(day),
                target_rcept_no=ev.rcept_no, kind=kind, new_price=price, amount=amt,
                shares=None, effective_date=_d(day + lag),
            )
        )  # fmt: skip
    return out


def _oracle(ev: OverhangEvent, adjs: list[OverhangAdjust], t: date) -> tuple[int, float, bool, int]:
    """(잔여, 가격, 활성, 잠재) — 엔진과 따로 쓴 정의(metrics §12)."""
    used = [a for a in adjs if a.rcept_dt <= t and (a.effective_date or a.rcept_dt) <= t]
    assert ev.face_value is not None and ev.price is not None
    rem = max(ev.face_value - sum(a.amount or 0 for a in used if a.kind != "price_adjust"), 0)
    prices = sorted(
        (a for a in used if a.kind == "price_adjust"),
        key=lambda a: (a.effective_date or a.rcept_dt, a.rcept_no),
    )
    price = prices[-1].new_price if prices else ev.price
    assert price is not None
    ends = [x for x in (ev.exercise_end, ev.maturity) if x is not None]
    active = ev.rcept_dt <= t <= min(ends) and rem > 0
    pot = math.floor(Fraction(rem) / Fraction(str(price))) if active else 0
    return rem, price, active, pot


@settings(max_examples=150, deadline=None)
@given(data=st.data())
def test_bond_state_matches_the_oracle(data: st.DataObject) -> None:
    ev = data.draw(bond())
    adjs = data.draw(adjusts(ev))
    t = _d(data.draw(st.integers(0, DAYS)))
    states = oh.state_at([ev], adjs, t, lockup_months=12)
    if t < ev.rcept_dt:
        assert states == []
        return
    (s,) = states
    rem, price, active, pot = _oracle(ev, adjs, t)
    assert s.remaining_face is not None and s.remaining_face == rem >= 0
    assert s.price == price
    assert s.active is active
    assert s.potential_shares == pot and pot >= 0
    if s.exercisable:
        assert s.active and ev.exercise_start is not None and ev.exercise_start <= t


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_future_filed_adjusts_do_not_change_the_past(data: st.DataObject) -> None:
    ev = data.draw(bond())
    adjs = data.draw(adjusts(ev))
    t = _d(data.draw(st.integers((ev.rcept_dt - D0).days, DAYS)))
    known = [a for a in adjs if a.rcept_dt <= t]
    assert oh.state_at([ev], adjs, t, lockup_months=12) == oh.state_at(
        [ev], known, t, lockup_months=12
    )


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_one_more_exercise_never_increases_potential(data: st.DataObject) -> None:
    ev = data.draw(bond())
    adjs = data.draw(adjusts(ev))
    t = _d(data.draw(st.integers((ev.rcept_dt - D0).days, DAYS)))
    amt = data.draw(st.integers(1, 300)) * 10_000_000
    extra = OverhangAdjust(
        rcept_no=f"{ev.rcept_dt:%Y%m%d}999999", corp_code=CORP, rcept_dt=ev.rcept_dt,
        target_rcept_no=ev.rcept_no, kind="exercise", new_price=None, amount=amt, shares=None,
        effective_date=ev.rcept_dt,
    )  # fmt: skip
    before = oh.state_at([ev], adjs, t, lockup_months=12)[0]
    after = oh.state_at([ev], [*adjs, extra], t, lockup_months=12)[0]
    assert before.potential_shares is not None and after.potential_shares is not None
    assert after.potential_shares <= before.potential_shares
    assert after.remaining_face is not None and before.remaining_face is not None
    assert after.remaining_face <= before.remaining_face


@settings(max_examples=100, deadline=None)
@given(
    decided=st.integers(0, 600),
    to_listing=st.integers(1, 200),
    t_off=st.integers(-50, 400),
    n_shares=st.integers(1, 10_000_000),
)
def test_paid_in_counts_only_before_listing(
    decided: int, to_listing: int, t_off: int, n_shares: int
) -> None:
    listing = _d(decided + to_listing)
    ev = OverhangEvent(
        rcept_no=f"{_d(decided):%Y%m%d}000001", corp_code=CORP, rcept_dt=_d(decided),
        instrument=Instrument.PAID_IN, face_value=None, price=5_000.0, shares=n_shares,
        refix_floor=None, exercise_start=None, exercise_end=None, maturity=None,
        listing_date=listing, alloc_method="제3자배정", parse_source="api",
    )  # fmt: skip
    t = _d(decided + t_off)
    states = oh.state_at([ev], [], t, lockup_months=12)
    if t < ev.rcept_dt:
        assert states == []
        return
    (s,) = states
    assert s.potential_shares == (n_shares if t < listing else 0)
    assert s.active is (t < listing) and not s.exercisable
    assert s.lockup_end_est is not None and s.lockup_end_est > listing


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_summary_sums_new_shares_and_keeps_eb_apart(data: st.DataObject) -> None:
    evs = [data.draw(bond(n)) for n in range(1, data.draw(st.integers(1, 4)) + 1)]
    t = _d(data.draw(st.integers(0, DAYS)))
    states = oh.state_at(evs, [], t, lockup_months=12)
    den = data.draw(st.integers(1, 10**9))
    sc = ShareCount(CORP, date(2023, 12, 31), "20240315000001", den, None, None, None)
    s = oh.summary(states, sc, t, corp_code=CORP)
    new = sum(x.potential_shares or 0 for x in states
              if x.active and x.instrument is not Instrument.EB)  # fmt: skip
    eb = sum(x.potential_shares or 0 for x in states if x.active and x.instrument is Instrument.EB)
    ex = sum(x.potential_shares or 0 for x in states
             if x.exercisable and x.instrument is not Instrument.EB)  # fmt: skip
    assert (s.potential_new_shares, s.exchange_shares, s.exercisable_shares) == (new, eb, ex)
    assert s.exercisable_shares <= s.potential_new_shares
    assert s.ratio_pct is not None and math.isclose(s.ratio_pct, new / den * 100)
