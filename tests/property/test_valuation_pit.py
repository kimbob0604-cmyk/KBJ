"""밸류에이션 PIT·분할 속성 시험(hypothesis) — docs/p4_design.md §8.3 'test_valuation_pit', D-P4-11.

① 미래 참조 0: 날짜 t 의 PIT 재무·배수는 접수일 > t 인 분기를 지우거나 바꿔도 같다.
② 분할 불변: 주식 수 × k·가격 ÷ k 이면 배수(총액 ÷ 총액)는 같고, 주당 값(EPS·BPS)은 그날 주식 수로
   1/k 가 된다(분할 전 보고 EPS 가 남아 있어도).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.fin_rows import QuarterRow
from kbj.engines.consensus.forward import ntm
from kbj.engines.financials.period import fiscal_quarter
from kbj.engines.valuation.multiples import Facts, company_mktcap
from kbj.engines.valuation.pit import DayInput, PitBook, value_day
from kbj.engines.valuation.scenario import inputs_from
from tests.unit.engines.valuation.val_rows import CAPS, CODE, PREF, estimate, quarter, snap

QUARTERS = [(y, q) for y in (2023, 2024, 2025) for q in (1, 2, 3, 4)]


@st.composite
def books(draw: st.DrawFn) -> list[QuarterRow]:
    rows: list[QuarterRow] = []
    for y, q in QUARTERS:
        if draw(st.integers(0, 9)) == 0:
            continue  # 가끔 빠진 분기
        end = fiscal_quarter(y, q, 12).end
        lag = draw(st.integers(20, 100))
        fs: Literal["CFS", "OFS"] = draw(st.sampled_from(["CFS", "CFS", "OFS"]))
        rows.append(
            quarter(
                y,
                q,
                rcept=end + timedelta(days=lag),
                fs=fs,
                revenue=draw(st.integers(-10, 5_000)),
                ni_owner=draw(st.integers(-500, 800)),
                op=draw(st.integers(-300, 900)),
                d_and_a=draw(st.integers(0, 200)),
                equity_owner=draw(st.integers(-1_000, 9_000)),
                net_debt=draw(st.integers(-2_000, 4_000)),
                minority=draw(st.one_of(st.none(), st.integers(0, 300))),
                eps=draw(st.one_of(st.none(), st.floats(-50, 80, allow_nan=False))),
            )
        )
    return rows


def _mutate(rows: list[QuarterRow], t: date, drop: bool) -> list[QuarterRow]:
    out: list[QuarterRow] = []
    for r in rows:
        if r.rcept_dt is not None and r.rcept_dt > t:
            if drop:
                continue
            r = replace(
                r,
                revenue=(r.revenue or 0) * 3 + 7,
                ni_owner=-123,
                equity_owner=1,
                net_debt=None,
                eps_basic=None,
            )
        out.append(r)
    return out


@settings(max_examples=60, deadline=None)
@given(books(), st.integers(0, 4 * 365), st.booleans())
def test_미래_공시를_바꾸거나_지워도_과거_값은_같다(
    rows: list[QuarterRow], offset: int, drop: bool
) -> None:
    t = date(2023, 1, 1) + timedelta(days=offset)
    a = PitBook(rows).at(t)
    b = PitBook(_mutate(rows, t, drop)).at(t)
    assert (a.ttm, a.balance, a.first_seen, a.digest, a.known_through) == (
        b.ttm,
        b.balance,
        b.first_seen,
        b.digest,
        b.known_through,
    )
    cap = company_mktcap({CODE: snap(CODE, t, 1_000.0, 50)}, (CODE,), t)
    inp = DayInput(t, cap, 1_000.0, 50)
    ra = value_day(CODE, inp, a, Facts(kind="common"), caps=CAPS)
    rb = value_day(CODE, inp, b, Facts(kind="common"), caps=CAPS)
    assert ra == rb


@settings(max_examples=80, deadline=None)
@given(
    st.floats(100, 500_000, allow_nan=False),
    st.integers(1_000, 10**9),
    st.floats(50, 400_000, allow_nan=False),
    st.integers(100, 10**8),
    st.sampled_from([2, 5, 10, 50]),
)
def test_분할_앞뒤_배수는_같다(p: float, n: int, pp: float, pn: int, k: int) -> None:
    t = date(2026, 10, 26)
    rows = [quarter(2025, q, rcept=date(2026, 3, 20)) for q in (1, 2, 3, 4)]
    view = PitBook(rows).at(t)

    def caps(mult: int) -> DayInput:
        s = {CODE: snap(CODE, t, p / mult, n * mult), PREF: snap(PREF, t, pp / mult, pn * mult)}
        for c in s:  # 시총 칸 없이 종가 × 상장주식수로(분할 앞뒤 같은 총액)
            s[c] = replace(s[c], mktcap=None)
        cap = company_mktcap(s, (CODE, PREF), t)
        return DayInput(t, cap, p / mult, n * mult)

    before = value_day(CODE, caps(1), view, Facts(), caps=CAPS)
    after = value_day(CODE, caps(k), view, Facts(), caps=CAPS)
    for m in ("per", "pbr", "psr", "ev_ebitda"):
        x, y = getattr(before, m), getattr(after, m)
        assert (x is None) == (y is None), m
        if x is not None and y is not None:
            assert abs(x - y) <= 1e-9 * max(1.0, abs(x)), m


@settings(max_examples=60, deadline=None)
@given(st.integers(1_000, 10**7), st.sampled_from([2, 5, 10]), st.floats(1.0, 5_000.0))
def test_분할_뒤_주당_값은_그날_주식_수로(n: int, k: int, eps: float) -> None:
    t = date(2026, 11, 13)
    view = PitBook(
        [
            quarter(2025, q, rcept=date(2026, 3, 20), ni_owner=round(eps * n / 4), eps=eps / 4)
            for q in (1, 2, 3, 4)
        ]
    ).at(t)
    est = ntm(
        [
            estimate(CODE, t, "202612", eps=eps, ni_owner=round(eps * n)),
            estimate(CODE, t, "202712", eps=eps, ni_owner=round(eps * n)),
        ],
        t,
    )
    pre = inputs_from(CODE, t, view, est, [], price=100.0, krx_common_shares=n)
    post = inputs_from(CODE, t, view, est, [], price=100.0 / k, krx_common_shares=n * k)
    assert pre.fy1_eps is not None and post.fy1_eps is not None
    # 지배순익은 원 정수(반올림) — 다시 센 주당 값 × k 와 보고 EPS 의 차이는 반올림 몫뿐:
    # FY1 은 한 번(≤ 0.5 ÷ n), TTM 은 분기 넷의 합(≤ 4 × 0.5 ÷ n)
    assert abs(post.fy1_eps * k - pre.fy1_eps) <= 0.5 / n + 1e-9 * pre.fy1_eps
    assert pre.ttm_eps is not None and post.ttm_eps is not None
    assert abs(post.ttm_eps * k - pre.ttm_eps) <= 2.0 / n + 1e-9 * abs(pre.ttm_eps)
    assert pre.bps is not None and post.bps is not None
    assert abs(post.bps * k - pre.bps) <= 1e-9 * abs(pre.bps) + 1e-9
