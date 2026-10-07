"""플로우 지표 (core/metrics/flow.py — docs/metrics.md §6.1~§6.7).

- HIRO-lite(§6.1): signed_qty = Δbuy − Δsell, 고객 델타 흐름 Σ signed × Δ × m × F, 딜러 = 부호
  반전(콜 매수 주도 → 딜러 −), 첫 틱은 기준만, 역행 틱은 버리고(기준 그대로) 센다, 누적 없음·Δ 없음,
  세션 전환·리셋(끊김·시퀀스 공백)은 누적·기준을 비우고 사유·시각을 남긴다, 늘 estimated, 시퀀스
  공백 판정
- PCR(§6.5): OI·거래량 기준 put ÷ call, 분모 0 → null(품질 ok), 모르는 값은 빼고 estimated
- 맥스페인(§6.6): 손계산 값, 동률 → F 에 가까운 쪽 → 낮은 쪽, F 없이 동률 → 낮은 쪽·estimated,
  OI 전부 0 → null, 후보는 상장 행사가(OI 없는 행사가도 후보), 누적합 = 식 그대로
- OI 증감(§6.7): 첫 스냅샷 None, 증감, 줄었다가 다음에 90% 이상 복구 → 두 칸 이상치(경계 90%
  포함·89% 아님·넘친 복구·0 증감도 다음 스냅샷), 겹치지 않는 짝, 한 칸씩(`oi_step`) = 접기
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

import pytest

from core.gex import OPTION_MULTIPLIER
from core.metrics.flow import (
    BLOCK_DAYS,
    DEALER_WARN_DAYS,
    HiroState,
    HiroTick,
    OiCell,
    OiSnapshot,
    block_thresholds,
    dealer_check,
    dealer_warning,
    hiro_reset,
    hiro_step,
    is_block,
    is_seq_gap,
    max_pain,
    mismatch_streak,
    moneyness_bucket,
    nearest_rank,
    oi_change_cells,
    oi_step,
    pain_at,
    pcr,
)

K = Decimal
KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 10, 13, 9, 0, tzinfo=KST)


# ── PCR (§6.5) ──


def test_pcr_is_put_over_call_for_oi_and_volume() -> None:
    r = pcr([("C", 100, 40), ("C", 300, 10), ("P", 250, 30), ("P", 50, 0)])
    assert (r.oi.put, r.oi.call, r.oi.value) == (300, 400, 0.75)
    assert (r.volume.put, r.volume.call, r.volume.value) == (30, 50, 0.6)
    assert r.oi.quality == r.volume.quality == "ok" and r.oi.reasons == ()


def test_pcr_denominator_zero_is_null_not_a_quality_problem() -> None:
    r = pcr([("P", 10, 5), ("C", 0, 0)])
    assert r.oi.value is None and r.volume.value is None
    assert r.oi.quality == r.volume.quality == "ok"
    empty = pcr([])
    assert empty.oi.value is None and empty.oi.quality == "ok"
    # 풋이 0 이면 0 — null 이 아니다
    assert pcr([("C", 10, 1)]).oi.value == 0.0


def test_unknown_values_are_left_out_and_mark_the_ratio_estimated() -> None:
    r = pcr([("C", 100, None), ("P", None, 20), ("P", 50, 10), ("C", 100, 40)])
    assert (r.oi.put, r.oi.call, r.oi.missing) == (50, 200, 1)
    assert r.oi.value == 0.25 and r.oi.quality == "estimated" and r.oi.reasons == ("oi_missing",)
    assert (r.volume.put, r.volume.call, r.volume.missing) == (30, 40, 1)
    assert r.volume.quality == "estimated" and r.volume.reasons == ("volume_missing",)
    # 거래량을 전부 모르면 분모 0 — null 이지만 estimated(모른다)
    unknown = pcr([("C", 1, None), ("P", 1, None)])
    assert unknown.volume.value is None and unknown.volume.quality == "estimated"


@pytest.mark.parametrize(
    "leg", [("X", 1, 1), ("C", -1, 1), ("C", 1, -2), ("C", True, 1), ("C", 1.5, 1)]
)
def test_pcr_refuses_bad_legs(leg: tuple[object, object, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        pcr([leg])  # type: ignore[list-item]


# ── 맥스페인 (§6.6) ──


def test_max_pain_hand_computed() -> None:
    """콜 OI 1100:10·1105:20, 풋 OI 1095:30·1100:5 — 후보 1095·1100·1105.

    pain(1095) = 풋 1100 5계약 × 5 = 25 (K 아래 콜 없음)
    pain(1100) = 0 (K 아래 콜·K 위 풋 없음) → 최소
    pain(1105) = 콜 1100 10계약 × 5 = 50 (K 위 풋 없음)
    """
    oi = [(K("1100"), "C", 10), (K("1105"), "C", 20), (K("1095"), "P", 30), (K("1100"), "P", 5)]
    r = max_pain(oi)
    assert r.strike == K("1100") and r.pain == 0 and r.tied == 1 and r.candidates == 3
    assert r.quality == "ok" and r.pain_won == 0.0
    calls = {K("1100"): 10, K("1105"): 20}
    puts = {K("1095"): 30, K("1100"): 5}
    assert pain_at(K("1095"), calls, puts) == 25
    assert pain_at(K("1105"), calls, puts) == 50


def test_max_pain_value_in_won() -> None:
    r = max_pain([(K("1100"), "C", 3), (K("1110"), "P", 1)], strikes=[K("1105")])
    # 후보 하나 — pain = 콜 3×5 + 풋 1×5 = 20 계약·pt
    assert r.strike == K("1105") and r.pain == 20 and r.pain_won == 20 * OPTION_MULTIPLIER


def test_ties_go_to_the_strike_nearest_f_then_the_lower_one() -> None:
    # 콜 1100:1, 풋 1105:1 → pain(1100) = 5, pain(1102.5) = 2.5 + 2.5, pain(1105) = 5 — 셋 동률
    oi = [(K("1100"), "C", 1), (K("1105"), "P", 1)]
    strikes = [K("1100"), K("1102.5"), K("1105")]
    assert max_pain(oi, strikes, forward=1104.0).strike == K("1105")
    assert max_pain(oi, strikes, forward=1101.0).strike == K("1100")
    near = max_pain(oi, strikes, forward=1102.4)
    assert near.strike == K("1102.5") and near.tied == 3 and near.quality == "ok"
    # F 와의 거리도 같으면 낮은 쪽
    two = [K("1100"), K("1105")]
    assert max_pain(oi, two, forward=1102.5).strike == K("1100")


def test_a_tie_without_a_forward_takes_the_lower_strike_and_is_estimated() -> None:
    oi = [(K("1100"), "C", 1), (K("1105"), "P", 1)]
    r = max_pain(oi, [K("1100"), K("1105")])
    assert r.strike == K("1100") and r.quality == "estimated"
    assert r.reasons == ("tie_without_forward",)
    # 동률이 아니면 F 없이도 ok
    assert max_pain([(K("1100"), "C", 1)], [K("1100"), K("1105")]).quality == "ok"


def test_all_zero_oi_or_no_candidates_is_null() -> None:
    zero = max_pain([(K("1100"), "C", 0), (K("1100"), "P", 0)])
    assert zero.strike is None and zero.pain is None and zero.pain_won is None
    assert zero.quality == "ok" and zero.candidates == 1
    assert max_pain([]).strike is None
    assert max_pain([(K("1100"), "C", 5)], strikes=[]).strike is None


def test_listed_strikes_without_oi_rows_are_candidates() -> None:
    """상장 행사가(마스터)가 후보 — OI 행이 없는 행사가도 최소일 수 있다."""
    oi = [(K("1090"), "C", 10), (K("1110"), "P", 10)]
    # OI 가 온 행사가만이면 1090(=풋 20×10=200)·1110(=콜 20×10=200) 동률
    assert max_pain(oi, forward=1100.0).tied == 2
    # 상장 1100 은 pain = 콜 10×10 + 풋 10×10 = 200 — 셋 동률, F 에 가장 가까운 1100
    r = max_pain(oi, [K("1090"), K("1100"), K("1110")], forward=1101.0)
    assert r.strike == K("1100") and r.tied == 3


def test_max_pain_refuses_duplicates_and_bad_values() -> None:
    with pytest.raises(ValueError):
        max_pain([(K("1100"), "C", 1), (K("1100"), "C", 2)])
    with pytest.raises(ValueError):
        max_pain([(K("0"), "C", 1)])
    with pytest.raises(ValueError):
        max_pain([(K("1100"), "C", -1)])
    with pytest.raises(TypeError):
        max_pain([(1100.0, "C", 1)])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        max_pain([(K("1100"), "C", None)])  # type: ignore[list-item]


# ── OI 증감 (§6.7) ──


def snaps(*ois: int) -> list[OiSnapshot]:
    return [OiSnapshot(T0 + timedelta(seconds=30 * i), n) for i, n in enumerate(ois)]


def test_changes_are_differences_from_the_previous_snapshot() -> None:
    cells = oi_change_cells(snaps(100, 120, 110, 110))
    assert [c.change for c in cells] == [None, 20, -10, 0]
    assert [c.prev_oi for c in cells] == [None, 100, 120, 110]
    assert cells[0].prev_ts is None and cells[1].prev_ts == T0
    assert not any(c.outlier for c in cells)


@pytest.mark.parametrize(
    ("ois", "flags"),
    [
        ((100, 50, 95), [False, True, True]),  # 줄어든 50 의 90% = 45 이상 복구 → 두 칸
        ((100, 50, 94), [False, False, False]),  # 44/50 = 88% — 아님
        ((1000, 900, 990), [False, True, True]),  # 경계 정확히 90%
        ((1000, 900, 989), [False, False, False]),  # 89% — 아님
        ((100, 50, 130), [False, True, True]),  # 넘친 복구도
        ((100, 50, 50, 100), [False, False, False, False]),  # 다음 스냅샷은 0 증감 — 복구 아님
        ((100, 150, 100), [False, False, False]),  # 늘었다가 줄어듦은 아니다
        ((100, 50, 100, 50, 100), [False, True, True, True, True]),  # 겹치지 않는 두 짝
        ((100, 60, 20, 60), [False, False, True, True]),  # 두 번째 줄어듦 40 → 40 복구
    ],
)
def test_a_dip_recovered_by_ninety_percent_quarantines_both_cells(
    ois: tuple[int, ...], flags: list[bool]
) -> None:
    assert [c.outlier for c in oi_change_cells(snaps(*ois))] == flags


def test_stepping_one_snapshot_at_a_time_is_the_fold() -> None:
    ss = snaps(100, 50, 95, 95, 80, 79)
    first, none = oi_step(None, ss[0])
    assert none is None and first.change is None
    dip, none = oi_step(first, ss[1])
    assert none is None and dip.change == -50 and not dip.outlier
    rec, prev = oi_step(dip, ss[2])
    assert rec.outlier and prev == OiCell(ss[1].ts, 50, ss[0].ts, 100, -50, True)
    cells = oi_change_cells(ss)
    assert cells[1] == prev and cells[2] == rec
    assert [c.outlier for c in cells] == [False, True, True, False, False, False]


def test_recovery_share_is_a_parameter() -> None:
    assert oi_change_cells(snaps(100, 50, 60), recovery=Decimal("0.2"))[2].outlier
    with pytest.raises(ValueError):
        oi_step(None, snaps(1)[0], recovery=Decimal(0))


def test_snapshots_must_move_forward_and_be_aware_non_negative() -> None:
    a, b = snaps(1, 2)
    with pytest.raises(ValueError):
        oi_change_cells([b, a])
    with pytest.raises(ValueError):
        oi_change_cells([a, a])
    with pytest.raises(ValueError):
        OiSnapshot(datetime(2026, 10, 13, 9, 0), 1)  # noqa: DTZ001
    with pytest.raises(ValueError):
        OiSnapshot(T0, -1)


# ── HIRO-lite (§6.1) ──

TUE = date(2026, 10, 13)
M = OPTION_MULTIPLIER


def tick(
    code: str,
    buy: int | None,
    sell: int | None,
    *,
    delta: float | None = 0.5,
    F: float | None = 1100.0,
    s: int = 0,
    session: Literal["day", "night"] = "day",
    trade_date: date = TUE,
) -> HiroTick:
    return HiroTick(code, trade_date, session, T0 + timedelta(seconds=s), buy, sell, delta, F)


def run(ticks: list[HiroTick], state: HiroState | None = None) -> tuple[HiroState, list[str]]:
    st = state or HiroState()
    events: list[str] = []
    for t in ticks:
        st, ev = hiro_step(st, t)
        events.append(ev)
    return st, events


def test_signed_quantity_times_delta_notional_accumulates_the_customer_flow() -> None:
    st, events = run(
        [
            tick("C1", 10, 5, s=0),  # 기준
            tick("C1", 14, 5, s=1),  # 매수 주도 +4
            tick("P1", 0, 0, delta=-0.4, s=2),  # 기준
            tick("P1", 3, 0, delta=-0.4, s=3),  # 풋 매수 +3 → 고객 델타 −
            tick("C1", 14, 9, s=4),  # 매도 주도 −4
            tick("C1", 16, 10, s=5),  # +2 − 1 = +1
        ]
    )
    assert events == ["baseline", "applied", "baseline", "applied", "applied", "applied"]
    flow = (4 * 0.5 + 3 * -0.4 + -4 * 0.5 + 1 * 0.5) * M * 1100.0
    assert math.isclose(st.customer_flow, flow) and st.dealer_hedge == -st.customer_flow
    assert (st.signed_qty, st.ticks, st.reversals, st.unpriced_qty) == (4, 4, 0, 0)
    assert st.cums == {"C1": (16, 10), "P1": (3, 0)}
    assert st.last_ts == T0 + timedelta(seconds=5) and st.quality == "estimated"
    assert (st.trade_date, st.session, st.reset_reason, st.reset_at) == (TUE, "day", "start", T0)


def test_customer_call_buying_means_dealers_must_sell_delta() -> None:
    st, _ = run([tick("C1", 0, 0), tick("C1", 10, 0)])
    assert st.customer_flow > 0 and st.dealer_hedge < 0
    put, _ = run([tick("P1", 0, 0, delta=-0.3), tick("P1", 10, 0, delta=-0.3)])
    assert put.customer_flow < 0 and put.dealer_hedge > 0


def test_a_reversing_tick_is_dropped_and_the_baseline_kept() -> None:
    st, events = run(
        [
            tick("C1", 10, 10),
            tick("C1", 9, 12, s=1),
            tick("C1", 12, 10, s=2),
            tick("C1", 13, 9, s=3),
        ]
    )
    assert events == ["baseline", "reversal", "applied", "reversal"]
    assert st.reversals == 2 and st.cums["C1"] == (12, 10) and st.signed_qty == 2


def test_ticks_without_cumulative_fields_are_skipped() -> None:
    st, events = run([tick("C1", 10, 10), tick("C1", None, 12, s=1), tick("C1", 11, 10, s=2)])
    assert events == ["baseline", "no_cum", "applied"] and st.signed_qty == 1


def test_ticks_without_delta_or_forward_move_the_baseline_but_only_count_quantity() -> None:
    st, events = run(
        [
            tick("C1", 10, 10),
            tick("C1", 15, 11, delta=None, s=1),  # +4 — Δ 없음
            tick("C1", 16, 11, F=None, s=2),  # +1 — F 없음
            tick("C1", 17, 11, s=3),  # +1
        ]
    )
    assert events == ["baseline", "no_price", "no_price", "applied"]
    assert st.unpriced_qty == 5 and st.signed_qty == 1
    assert math.isclose(st.customer_flow, 0.5 * M * 1100.0)


def test_a_session_change_resets_and_the_first_tick_is_a_baseline_again() -> None:
    st, _ = run([tick("C1", 10, 0), tick("C1", 20, 0, s=1)])
    night = tick("C1", 25, 0, s=10_000, session="night", trade_date=TUE + timedelta(days=1))
    st2, ev = hiro_step(st, night)
    assert ev == "baseline" and st2.customer_flow == 0.0 and st2.signed_qty == 0
    assert (st2.trade_date, st2.session) == (TUE + timedelta(days=1), "night")
    assert (st2.reset_reason, st2.reset_at) == ("session_change", night.ts)
    assert st2.cums == {"C1": (25, 0)}


@pytest.mark.parametrize("reason", ["ws_disconnect", "seq_gap"])
def test_resets_clear_the_flow_and_baselines_but_keep_the_session(reason: str) -> None:
    st, _ = run([tick("C1", 10, 0), tick("C1", 20, 0, s=1)])
    at = T0 + timedelta(seconds=2)
    r = hiro_reset(st, reason, at)  # type: ignore[arg-type]
    assert (r.customer_flow, r.signed_qty, r.ticks, r.cums) == (0.0, 0, 0, {})
    assert (r.reset_reason, r.reset_at, r.trade_date, r.session) == (reason, at, TUE, "day")
    after, ev = hiro_step(r, tick("C1", 30, 0, s=3))
    assert ev == "baseline" and after.customer_flow == 0.0  # 끊긴 동안의 덩어리를 넣지 않는다
    with pytest.raises(ValueError):
        hiro_reset(st, "seq_gap", datetime(2026, 10, 13, 9, 0))  # noqa: DTZ001


def test_hiro_ticks_are_validated() -> None:
    with pytest.raises(ValueError):
        tick("C1", -1, 0)
    with pytest.raises(ValueError):
        tick("C1", 1, 0, delta=math.nan)
    with pytest.raises(ValueError):
        tick("C1", 1, 0, F=0.0)
    with pytest.raises(ValueError):
        HiroTick("C1", TUE, "day", datetime(2026, 10, 13, 9, 0), 1, 1)  # noqa: DTZ001


def test_sequence_gaps() -> None:
    assert not is_seq_gap(None, 17)
    assert not is_seq_gap(16, 17)
    assert is_seq_gap(16, 18) and is_seq_gap(16, 16) and is_seq_gap(16, 3)


# ── 딜러 가정 점검 (§6.3) ──


def test_dealer_check_is_consistent_when_securities_buy_calls_and_sell_puts() -> None:
    ok = dealer_check([120, -20, 5], [-300, 10, 0])
    assert (ok.call_net, ok.put_net, ok.consistent) == (105, -290, True)
    assert ok.quality == "ok" and ok.reasons == ()
    for calls, puts in (([0], [-1]), ([5], [0]), ([-5], [-5]), ([5], [5])):
        assert dealer_check(calls, puts).consistent is False, (calls, puts)


def test_missing_pairs_are_left_out_and_a_missing_side_gives_no_verdict() -> None:
    part = dealer_check([100, None, 20], [-50, -10, None])
    assert part.consistent is True and part.quality == "estimated"
    assert (part.call_net, part.put_net, part.reasons) == (120, -60, ("pairs_missing",))
    none = dealer_check([None, None], [-5])
    assert none.consistent is None and none.quality == "invalid" and none.call_net is None
    assert none.reasons == ("pairs_missing", "no_call_flow")
    empty = dealer_check([], [])
    assert empty.consistent is None and empty.reasons == ("no_call_flow", "no_put_flow")
    with pytest.raises(TypeError):
        dealer_check([True], [-1])  # type: ignore[list-item]


def test_mismatch_streak_counts_back_from_today_and_unknown_days_break_it() -> None:
    assert mismatch_streak([]) == 0
    assert mismatch_streak([False, False, True, False, False, False]) == 3
    assert mismatch_streak([False, False, None, False]) == 1
    assert mismatch_streak([False, True]) == 0 and mismatch_streak([False, None]) == 0
    assert DEALER_WARN_DAYS == 5
    assert not dealer_warning(4) and dealer_warning(5) and dealer_warning(9)
    assert dealer_warning(2, warn_days=2)
    with pytest.raises(ValueError):
        dealer_warning(1, warn_days=0)


# ── 대량 체결 (§6.4) ──


def test_moneyness_buckets_are_one_percent_wide_rounded_down() -> None:
    F = 1000.0
    assert moneyness_bucket(K("1000"), F) == 0
    assert moneyness_bucket(K("1009.9"), F) == 0
    assert moneyness_bucket(K("1010.1"), F) == 1
    assert moneyness_bucket(K("995"), F) == -1  # −0.5% → [−1%, 0%)
    assert moneyness_bucket(K("970"), F) == -3
    assert moneyness_bucket(1060.0, 1000.0, step=0.05) == 1  # 6% → [5%, 10%)
    assert moneyness_bucket(K("1030"), F) == 3 and moneyness_bucket(K("1029.99"), F) == 2
    for bad in ((K("1000"), 0.0), (K("0"), 1000.0), (K("1000"), math.inf)):
        with pytest.raises(ValueError):
            moneyness_bucket(*bad)


def test_p99_is_the_nearest_rank_observed_value() -> None:
    values = list(range(1, 101))  # 1..100
    assert nearest_rank(values) == 99  # ⌈0.99·100⌉ = 99 번째
    assert nearest_rank(list(range(1, 201))) == 198
    assert nearest_rank([7]) == 7 and nearest_rank([3, 1, 2], 0.5) == 2
    assert nearest_rank([5, 1], 1.0) == 5
    with pytest.raises(ValueError):
        nearest_rank([])
    with pytest.raises(ValueError):
        nearest_rank([1], 0.0)


def _days(n: int) -> list[date]:
    return [TUE - timedelta(days=i) for i in range(n, 0, -1)]


def test_block_detection_stays_off_until_twenty_trading_days_are_recorded() -> None:
    samples = [(d, "C", 0, q) for d in _days(BLOCK_DAYS - 1) for q in (1, 2, 3)]
    th = block_thresholds(samples)
    assert not th.active and th.table == {} and len(th.days) == BLOCK_DAYS - 1
    assert is_block(th, "C", 0, 10_000) is None
    more = [*samples, (TUE - timedelta(days=30), "C", 0, 1)]
    on = block_thresholds(more)
    assert on.active and len(on.days) == BLOCK_DAYS and on.days == tuple(sorted(on.days))


def test_thresholds_are_per_side_and_bucket_over_the_latest_twenty_days() -> None:
    days = _days(25)
    samples: list[tuple[date, str, int, int]] = []
    for i, d in enumerate(days):
        samples += [(d, "C", 0, q) for q in range(1, 6)]  # 콜 ATM 1~5
        samples.append((d, "P", -2, 100 if i < 5 else 10))  # 풋: 오래된 5일은 창 밖(100)
    th = block_thresholds(samples)
    assert th.active and th.days == tuple(days[-20:])
    assert th.table == {("C", 0): 5, ("P", -2): 10}
    assert th.samples == 20 * 6
    assert is_block(th, "C", 0, 5) is True and is_block(th, "C", 0, 4) is False  # 경계 포함
    assert is_block(th, "P", -2, 10) is True and is_block(th, "P", 0, 1_000) is None
    assert is_block(th, "C", 1, 1_000) is None  # 기록 없는 구간


def test_block_threshold_inputs_are_checked() -> None:
    with pytest.raises(ValueError):
        block_thresholds([(TUE, "X", 0, 1)])
    with pytest.raises(ValueError):
        block_thresholds([(TUE, "C", 0, -1)])
    with pytest.raises(ValueError):
        block_thresholds([], days=0)
