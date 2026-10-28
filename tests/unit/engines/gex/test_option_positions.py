"""`kbj.engines.gex.option_positions` — 행사가 배분(키움 방식)·누적·OI 검산·벽·라벨·합성 델타
(metrics §34).

손계산 세계(`tests/fixtures/synthetic/opt_pos_world.py` — 합성)와 허용오차로 비교한다(CLAUDE §4 —
바이트 비교 금지). 속성: 배분 합 + 미배분 = 원 누적 수량(hypothesis). 시계는 쓰지 않는다
(입력 시각만).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.gex.option_positions import (
    MIN_JUDGE,
    TOP_N,
    DayAlloc,
    FlowPoint,
    Quote,
    StrikeDay,
    allocate_day,
    build_positions,
    ewy_level,
    judge,
    pick_two,
    split,
)
from tests.fixtures.synthetic.opt_pos_world import (
    HAND_DAY,
    HAND_NET,
    HAND_POINTS,
    HAND_QUOTES,
    WORLD_DAYS,
    at,
    world_rows,
)

CAL = TradingCalendar.default()
OPEN = at(9, 0)
TOL = 1e-6


def _alloc(points: Sequence[FlowPoint], quotes: Sequence[Quote]) -> DayAlloc:
    got = allocate_day({"frgn": points}, quotes, mrkt_cls="", cp="C", open_at=OPEN, cal=CAL)
    return got["frgn"]


def _net(a: DayAlloc) -> dict[float, float]:
    return {s.strike: s.net for s in a.strikes}


# ── 손계산 ──────────────────────────────────────────────────────────────────────────────


def test_hand_world_matches_hand_calculation() -> None:
    a = _alloc(HAND_POINTS, HAND_QUOTES)
    got = _net(a)
    assert set(got) == set(HAND_NET)
    for k, v in HAND_NET.items():
        assert math.isclose(got[k], v, abs_tol=TOL), (k, got[k], v)
    assert {s.expiry for s in a.strikes} == {"202611"}
    assert a.total_buy == 200 and a.total_sell == 70  # 역행 행(150)은 기준을 옮기지 않았다
    assert math.isclose(a.allocated_buy, 200, abs_tol=TOL)
    assert math.isclose(a.allocated_sell, 70, abs_tol=TOL)
    assert a.reverted == 1 and a.gap_minutes == 2  # 09:00·09:02
    assert a.unallocated_buy == 0 and a.single_qty == 0
    assert any("빠진 분 2" in r for r in a.reasons) and any("역행" in r for r in a.reasons)


def test_two_to_one_and_tie_split() -> None:
    two = pick_two([(860, 7.0), (870, 4.0), (850, 12.0)], 6.0, "C")
    assert two == [(860, 7.0), (870, 4.0)]
    assert split(90.0, two, 6.0) == [(860, 60.0), (870, 30.0)]
    tie = pick_two([(880, 2.0), (890, 1.0), (870, 4.0)], 1.5, "C")
    assert split(40.0, tie, 1.5) == [(880, 20.0), (890, 20.0)]
    assert split(7.0, [(900, 3.0)], 2.0) == [(900, 7.0)]
    assert split(7.0, [], 2.0) == []


def test_same_price_otm_prefers_atm_side() -> None:
    # 콜 외가격 0.01 이 여럿 — 낮은 행사가(ATM 쪽) 둘, 거리 같아 1:1
    calls = [(1000, 0.01), (1010, 0.01), (990, 0.01), (980, 0.5)]
    assert pick_two(calls, 0.01, "C") == [(990, 0.01), (1000, 0.01)]
    puts = [(700, 0.01), (710, 0.01), (690, 0.01)]
    assert pick_two(puts, 0.01, "P") == [(710, 0.01), (700, 0.01)]


def test_zero_value_rounds_to_cheapest_and_is_imprecise() -> None:
    pts = [FlowPoint(at(9, 1), 3, 0, 0, 0)]  # 대금 백만원 반올림 0
    a = _alloc(pts, HAND_QUOTES)
    assert set(_net(a)) == {890, 880}  # p = 0 → 가장 싼 둘
    assert a.imprecise_qty == 3


def test_no_quote_is_unallocated_not_dropped() -> None:
    pts = [FlowPoint(at(9, 1), 10, 15, 0, 0)]
    a = _alloc(pts, [])
    assert a.strikes == () and a.unallocated_buy == 10 and a.total_buy == 10
    assert any("미배분" in r for r in a.reasons)


def test_stale_quote_is_not_a_candidate() -> None:
    q = [
        Quote(at(9, 0, 30), "", "202611", 870, "C", 4.0),
        Quote(at(9, 6), "", "202611", 860, "C", 7.0),
    ]
    a = _alloc(
        [FlowPoint(at(9, 6), 10, 10, 0, 0)], q
    )  # 09:06 슬롯 — 870 은 5분 넘게 묵음, 860 은 t 이후
    assert a.unallocated_buy == 10
    b = _alloc([FlowPoint(at(9, 5), 10, 10, 0, 0)], q)  # 09:05 — 870 은 [09:00, 09:05) 안
    assert _net(b) == {870: 10.0} and b.single_qty == 10


def test_invalid_rows_skip_without_losing_volume() -> None:
    pts = [
        FlowPoint(at(9, 1), 30, 30, 0, 0),
        FlowPoint(at(9, 2), 60, 60, 0, 0, Quality.INVALID),
        FlowPoint(at(9, 3), 90, 90, 0, 0),
    ]
    a = _alloc(pts, HAND_QUOTES)
    assert math.isclose(a.allocated_buy, 90, abs_tol=TOL) and a.skipped == 1


def test_expiry_day_after_1520_moves_to_next_series() -> None:
    d = date(2026, 11, 12)  # 11월 둘째 목요일 — 202611 최종거래일
    t1 = datetime(2026, 11, 12, 15, 0, tzinfo=KST)
    t2 = datetime(2026, 11, 12, 15, 30, tzinfo=KST)
    b1, b2 = t1 - timedelta(seconds=30), t2 - timedelta(seconds=30)
    q = [
        Quote(b1, "", "202611", 900, "C", 2.0),
        Quote(b1, "", "202612", 900, "C", 8.0),
        Quote(b2, "", "202611", 900, "C", 0.5),
        Quote(b2, "", "202612", 900, "C", 8.0),
    ]
    pts = [FlowPoint(t1, 10, 5, 0, 0), FlowPoint(t2, 20, 25, 0, 0)]
    open_at = datetime(2026, 11, 12, 9, 0, tzinfo=KST)
    a = allocate_day({"frgn": pts}, q, mrkt_cls="", cp="C", open_at=open_at, cal=CAL)["frgn"]
    by = {(s.expiry, s.strike): s.buy for s in a.strikes}
    assert by == {("202611", 900): 10.0, ("202612", 900): 10.0}
    assert CAL.is_trading_day(d)


def test_multiple_investors_share_one_ladder() -> None:
    res = allocate_day({"frgn": list(HAND_POINTS), "prsn": [FlowPoint(at(9, 1), 9, 9, 0, 0)]},
                       HAND_QUOTES, mrkt_cls="", cp="C", open_at=OPEN, cal=CAL)  # fmt: skip
    assert set(res) == {"frgn", "prsn"}
    assert math.isclose(res["prsn"].allocated_buy, 9, abs_tol=TOL)


# ── 속성 ────────────────────────────────────────────────────────────────────────────────

_steps = st.lists(
    st.tuples(st.integers(0, 400), st.integers(0, 3000), st.integers(0, 400), st.integers(0, 3000)),
    min_size=1,
    max_size=30,
)


@settings(max_examples=200, deadline=None)
@given(steps=_steps, drop=st.sets(st.integers(0, 29), max_size=10))
def test_allocation_sums_to_source_quantity(
    steps: list[tuple[int, int, int, int]], drop: set[int]
) -> None:
    pts: list[FlowPoint] = []
    cum = [0, 0, 0, 0]
    for i, s in enumerate(steps):
        cum = [c + x for c, x in zip(cum, s, strict=True)]
        if i in drop:  # 누락 분 — 다음 행이 이어받는다
            continue
        pts.append(FlowPoint(at(9, 1) + timedelta(minutes=i), cum[0], cum[1], cum[2], cum[3]))
    a = _alloc(pts, HAND_QUOTES)
    last = pts[-1] if pts else None
    want_b = last.buy_qty if last else 0
    want_s = last.sell_qty if last else 0
    assert math.isclose(a.allocated_buy + a.unallocated_buy, want_b or 0, abs_tol=TOL)
    assert math.isclose(a.allocated_sell + a.unallocated_sell, want_s or 0, abs_tol=TOL)
    assert all(s.buy >= -TOL and s.sell >= -TOL for s in a.strikes)


# ── 누적·검산·벽·라벨·합성 ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def board():  # type: ignore[no-untyped-def]
    rows, stats, oi = world_rows(CAL)
    return build_positions(rows, stats, day=WORLD_DAYS[-1], investor="frgn", krx_oi=oi, cal=CAL)


def test_holdings_accumulate_per_series_with_start_date(board) -> None:  # type: ignore[no-untyped-def]
    m = next(s for s in board.series if s.mrkt_cls == "")
    assert m.accum_from == WORLD_DAYS[0] and m.days_missing == 0 and m.days_used == 5
    c = {(s.strike, s.cp): s for s in m.strikes}
    assert c[(900.0, "C")].holding == -4900 and c[(900.0, "C")].prev_holding == -4700
    assert c[(950.0, "C")].holding == 0 and c[(950.0, "C")].label == "cleared"
    assert c[(850.0, "P")].holding == -4000
    w = next(s for s in board.series if s.mrkt_cls == "WKI")
    assert w.accum_from == date(2026, 11, 2)  # 상장 뒤 첫 저장일


def test_oi_exceed_is_invalid_and_excluded(board) -> None:  # type: ignore[no-untyped-def]
    m = next(s for s in board.series if s.mrkt_cls == "")
    k = next(s for s in m.strikes if (s.strike, s.cp) == (1000.0, "C"))
    assert (
        k.holding == 300 and k.oi == 100 and k.quality is Quality.INVALID and k.oi_source == "krx"
    )
    assert m.invalid == 1
    assert {s.quality for s in m.strikes} == {Quality.ESTIMATED, Quality.INVALID}
    wk = next(s for s in board.series if s.mrkt_cls == "WKI")
    assert {s.oi_source for s in wk.strikes} == {"board"}  # KRX 없으면 전광판


def test_walls_and_prev_walls(board) -> None:  # type: ignore[no-untyped-def]
    m = next(s for s in board.series if s.mrkt_cls == "")
    assert m.call_wall is not None and m.call_wall.strike == 900.0 and m.call_wall.holding == -4900
    assert m.put_wall is not None and m.put_wall.strike == 850.0
    assert m.prev_call_wall is not None and m.prev_call_wall.strike == 900.0


def test_top_changes_and_levels(board) -> None:  # type: ignore[no-untyped-def]
    assert len(board.top_changes) == TOP_N
    first = board.top_changes[:2]
    assert {(t.strike, t.change, t.label) for t in first} == {
        (950.0, 4000, "cleared"),
        (975.0, 4000, "cleared"),
    }
    kinds = [lv.kind for lv in board.levels]
    assert "futures" in kinds and "call_wall" in kinds
    assert board.levels == tuple(sorted(board.levels, key=lambda lv: (-lv.level, lv.kind)))
    fut = next(lv for lv in board.levels if lv.kind == "futures")
    assert fut.level == 884.0 and fut.distance_pct == 0 and fut.ewy is None


def test_synthetic_delta(board) -> None:  # type: ignore[no-untyped-def]
    rows, _stats, oi = world_rows(CAL)
    s = board.synthetic
    assert s.futures == -3000 * 4 + 670 and s.futures_from == WORLD_DAYS[0]
    # 옵션 부분 손계산: 살아 있는 시리즈 전부, OI 초과 행 제외, 보유 × 마지막 날 델타
    hold: dict[tuple[str, float, str], float] = {}
    delta: dict[tuple[str, float, str], float] = {}
    for r in rows:
        k = (r.expiry, r.strike, r.cp)
        hold[k] = hold.get(k, 0.0) + r.net
        if r.delta is not None:
            delta[k] = r.delta
    want = sum(h * delta[k] for k, h in hold.items() if k != ("202611", 1000.0, "C"))
    assert math.isclose(s.options, want, abs_tol=1e-3)
    assert s.total is not None and math.isclose(s.total, s.futures + s.options, abs_tol=1e-3)
    assert s.missing_delta == 0
    assert oi


def test_expired_series_disappear() -> None:
    rows = [StrikeDay(date(2026, 11, 5), "WKI", "261101", 900, "C", 10, 0)]
    stats: list = []  # type: ignore[type-arg]
    b = build_positions(rows, stats, day=date(2026, 11, 5), investor="frgn", krx_oi={}, cal=CAL)
    assert b.series == ()  # 만기일 마감 뒤 소멸
    b2 = build_positions(rows, stats, day=date(2026, 11, 4), investor="frgn", krx_oi={}, cal=CAL)
    assert b2.series == ()  # 그날 이하 행만 — 11-05 행은 11-04 보드에 없다


def test_missing_days_are_counted() -> None:
    rows, stats, oi = world_rows(CAL)
    stats = [s for s in stats if s.trade_date != WORLD_DAYS[2]]
    b = build_positions(rows, stats, day=WORLD_DAYS[-1], investor="frgn", krx_oi=oi, cal=CAL)
    m = next(s for s in b.series if s.mrkt_cls == "")
    assert m.days_missing == 1 and any("빠진 날 1" in r for r in m.reasons)


@pytest.mark.parametrize(
    ("prev", "now", "label"),
    [
        (-100, -50, "small"),
        (-100, -MIN_JUDGE, "new"),
        (-1000, 400, "flipped"),
        (-1000, -200, "cleared"),
        (-1000, 100, "cleared"),  # 반대 부호지만 작다 → 청산
        (-1000, -201, "reduced"),
        (-1000, -700, "reduced"),
        (-1000, -701, "hold"),
        (-1000, -1300, "added"),
        (1000, 1299, "hold"),
    ],
)
def test_judge_boundaries(prev: float, now: float, label: str) -> None:
    assert judge(prev, now) == label


def test_ewy_level_formula() -> None:
    assert ewy_level(900, None, 880) is None
    assert math.isclose(ewy_level(900, 150.0, 880.0) or 0, 900 * 150 / 880)
    assert HAND_DAY.weekday() == 1
