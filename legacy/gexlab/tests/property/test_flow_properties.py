"""플로우 지표 속성 (docs/metrics.md §6.1~§6.7).

- HIRO-lite: 종목 하나의 누적 매수·매도가 늘기만 하면(사이에 역행 틱이 끼어도) 고객 델타 흐름 =
  (마지막 − 첫 누적 매수 − (마지막 − 첫 누적 매도)) × Δ × m × F, 역행 틱 수만큼 reversals
- 대량 체결 p99(nearest-rank): 관측된 값이고, 그 값 이하가 99% 이상, 그 값 미만은 99% 미만
- PCR: 0 이상, OI·거래량을 같은 배수로 늘려도 같다, 콜·풋을 바꾸면 역수
- 맥스페인: 고른 행사가는 후보 중 하나이고 그 pain 은 모든 후보의 §6.6 식 값 이하(누적합 =
  식), 모든 행사가·F 를 같은 폭으로 옮기면 결과도 그만큼 옮겨진다
- OI 증감: 증감 합 = 마지막 − 처음, 이상치는 '줄어듦 → 다음에 90% 이상 복구' 짝으로만, 그런 짝은
  모두 이상치
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from core.gex import OPTION_MULTIPLIER
from core.metrics.flow import (
    BLOCK_QUANTILE,
    OI_RECOVERY,
    HiroState,
    HiroTick,
    OiSnapshot,
    hiro_step,
    max_pain,
    nearest_rank,
    oi_change_cells,
    pain_at,
    pcr,
)

STRIKES = tuple(Decimal("1050") + Decimal("2.5") * i for i in range(41))

legs = st.lists(
    st.tuples(st.sampled_from(("C", "P")), st.integers(0, 50_000), st.integers(0, 50_000)),
    max_size=30,
)
books = st.dictionaries(st.sampled_from(STRIKES), st.integers(0, 20_000), max_size=20)


@given(legs, st.integers(1, 50))
def test_pcr_is_non_negative_and_scale_free(ls: list[tuple[str, int, int]], k: int) -> None:
    r = pcr(ls)
    scaled = pcr([(cp, o * k, v * k) for cp, o, v in ls])
    for a, b in ((r.oi, scaled.oi), (r.volume, scaled.volume)):
        assert (a.value is None) == (b.value is None)
        if a.value is not None and b.value is not None:
            assert a.value >= 0 and abs(a.value - b.value) <= 1e-12 * max(1.0, a.value)


@given(legs)
def test_swapping_calls_and_puts_inverts_the_ratio(ls: list[tuple[str, int, int]]) -> None:
    r = pcr(ls)
    swapped = pcr([("P" if cp == "C" else "C", o, v) for cp, o, v in ls])
    if r.oi.value and swapped.oi.value:
        assert abs(r.oi.value * swapped.oi.value - 1) <= 1e-12


@given(books, books, st.floats(1060.0, 1140.0))
def test_max_pain_is_the_minimum_of_the_formula(
    calls: dict[Decimal, int], puts: dict[Decimal, int], F: float
) -> None:
    oi = [(k, "C", n) for k, n in calls.items()] + [(k, "P", n) for k, n in puts.items()]
    r = max_pain(oi, STRIKES, forward=F)
    if not any(calls.values()) and not any(puts.values()):
        assert r.strike is None
        return
    assert r.strike is not None and r.strike in STRIKES and r.pain is not None
    pains = {k: pain_at(k, calls, puts) for k in STRIKES}
    assert r.pain == pains[r.strike] == min(pains.values())
    assert r.tied == sum(1 for p in pains.values() if p == r.pain)


@given(books, books, st.floats(1060.0, 1140.0), st.integers(-8, 8))
def test_shifting_every_strike_and_f_shifts_the_result(
    calls: dict[Decimal, int], puts: dict[Decimal, int], F: float, steps: int
) -> None:
    d = Decimal("2.5") * steps
    oi = [(k, "C", n) for k, n in calls.items()] + [(k, "P", n) for k, n in puts.items()]
    moved = [(k + d, cp, n) for k, cp, n in oi]
    a = max_pain(oi, STRIKES, forward=F)
    b = max_pain(moved, [k + d for k in STRIKES], forward=F + float(d))
    if a.strike is None:
        assert b.strike is None
        return
    assert b.pain == a.pain and b.tied == a.tied
    if a.tied == 1:
        assert b.strike == a.strike + d


T0 = datetime(2026, 10, 13, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))


@given(st.lists(st.integers(0, 10_000), min_size=1, max_size=40))
def test_oi_changes_add_up_and_outliers_are_dip_recovery_pairs(ois: list[int]) -> None:
    cells = oi_change_cells([OiSnapshot(T0 + timedelta(seconds=i), n) for i, n in enumerate(ois)])
    changes = [c.change for c in cells]
    assert changes[0] is None and all(c is not None for c in changes[1:])
    assert sum(c for c in changes[1:] if c is not None) == ois[-1] - ois[0]
    pairs: set[int] = set()
    for i in range(1, len(ois) - 1):
        dip, rec = ois[i] - ois[i - 1], ois[i + 1] - ois[i]
        if dip < 0 and rec >= OI_RECOVERY * -dip:
            pairs |= {i, i + 1}
    assert {i for i, c in enumerate(cells) if c.outlier} == pairs


@given(
    st.lists(st.tuples(st.integers(0, 50), st.integers(0, 50), st.booleans()), max_size=30),
    st.floats(-1.0, 1.0),
    st.floats(1000.0, 1200.0),
)
def test_hiro_flow_of_one_option_is_its_net_signed_volume_times_delta_notional(
    steps: list[tuple[int, int, bool]], delta: float, F: float
) -> None:
    def mk(i: int, b: int, s: int) -> HiroTick:
        at = T0 + timedelta(seconds=i)
        return HiroTick("C1", date(2026, 10, 13), "day", at, b, s, delta, F)

    buy, sell = 100, 100
    state, _ = hiro_step(HiroState(), mk(0, buy, sell))
    reversals = 0
    for i, (db, ds, bad) in enumerate(steps, start=1):
        if bad:  # 역행 틱(누적 매수가 줄었다) — 버려진다
            reversals += 1
            state, ev = hiro_step(state, mk(i, buy - 1, sell + ds))
            assert ev == "reversal"
            continue
        buy, sell = buy + db, sell + ds
        state, ev = hiro_step(state, mk(i, buy, sell))
        assert ev == "applied"
    net = (buy - 100) - (sell - 100)
    expected = net * delta * OPTION_MULTIPLIER * F
    assert math.isclose(state.customer_flow, expected, rel_tol=1e-9, abs_tol=1e-3)
    assert state.signed_qty == net and state.reversals == reversals
    assert state.dealer_hedge == -state.customer_flow


@given(st.lists(st.integers(1, 500), min_size=1, max_size=400))
def test_p99_nearest_rank_bounds_the_share_of_ticks(values: list[int]) -> None:
    p = nearest_rank(values)
    n = len(values)
    assert p in values
    assert sum(v <= p for v in values) >= BLOCK_QUANTILE * n - 1e-9
    assert sum(v < p for v in values) < BLOCK_QUANTILE * n
