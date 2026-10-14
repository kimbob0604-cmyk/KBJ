"""프레임(§4.2-1): anchor·결측·부분 봉·장중 수급·분할 의심·입력 지문."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.core.quality import Quality
from kbj.engines.rules.frame import build_frame
from tests.unit.engines.rules.rule_rows import ANCHOR, CODE, bars, days, flow, frame, slot


def test_anchor_and_upto_cut() -> None:
    ds = days(10)
    bs = bars([100.0 + i for i in range(10)], ds)
    f = build_frame(CODE, bs, upto=ds[7], anchor=ds[2], split_ratio=0.31)
    assert f.dates == tuple(ds[2:8])
    assert f.close[0] == 102.0


def test_calendar_gap_is_halted() -> None:
    ds = days(5)
    bs = [b for i, b in enumerate(bars([100.0] * 5, ds)) if i != 2]
    f = build_frame(CODE, bs, upto=ds[-1], anchor=ANCHOR, split_ratio=0.31, calendar=ds)
    assert f.n == 5 and f.close[2] is None and f.halted[2]
    assert f.skip_reason(2) == "halted" and f.skip_reason(3) is None


def test_partial_bar_appended_and_future_bars_dropped() -> None:
    ds = days(6)
    bs = bars([100.0] * 6, ds)
    f = build_frame(
        CODE, bs, upto=ds[3], anchor=ANCHOR, split_ratio=0.31, partial=slot(ds[4], 110.0)
    )
    assert f.dates[-1] == ds[4] and f.close[-1] == 110.0 and f.n == 5
    assert f.partial and not f.live_flow
    assert f.quality[-1] is Quality.ESTIMATED
    with pytest.raises(ValueError):
        build_frame(CODE, bs, upto=ds[4], anchor=ANCHOR, split_ratio=0.31, partial=slot(ds[4], 1.0))


def test_live_flow_today() -> None:
    ds = days(4)
    fl = {d: flow(d, 10) for d in ds[:3]}
    live = flow(ds[3], 5, q=Quality.ESTIMATED)
    f = build_frame(
        CODE,
        bars([100.0] * 3, ds[:3]),
        upto=ds[2],
        anchor=ANCHOR,
        split_ratio=0.31,
        flows=fl,
        partial=slot(ds[3], 101.0),
        partial_flow=live,
    )
    assert f.live_flow
    assert f.series("foreign_streak", {"side": "buy"})[-1] == 4.0
    assert f.quality_at(3, flows=True) is Quality.ESTIMATED


def test_streak_sell_side_and_sum() -> None:
    ds = days(5)
    fl = {d: flow(d, v) for d, v in zip(ds, [5, -1, -2, -3, 4], strict=True)}
    f = build_frame(
        CODE, bars([100.0] * 5, ds), upto=ds[-1], anchor=ANCHOR, split_ratio=0.31, flows=fl
    )
    assert f.series("foreign_streak", {"side": "sell"})[3] == 3.0
    assert f.series("foreign_streak", {"side": "buy"})[4] == 1.0
    assert f.series("foreign_sum", {"n": 2}) == [None, 4.0, -3.0, -5.0, 1.0]
    # 그날 수급 행이 없으면 None(0 이 아니다)
    g = build_frame(
        CODE,
        bars([100.0] * 5, ds),
        upto=ds[-1],
        anchor=ANCHOR,
        split_ratio=0.31,
        flows={ds[0]: flow(ds[0], 1)},
    )
    assert g.series("foreign_streak", {"side": "buy"})[1] is None


def test_split_suspect_point_in_time() -> None:
    closes = [100.0] * 5 + [40.0] * 5  # 칸 5 에서 −60%
    f = frame(closes, split_window=3)
    assert f.split_idx == (5,)
    assert [f.skip_reason(i) for i in range(10)] == [None] * 5 + ["split_suspect"] * 3 + [None] * 2
    g = frame([100.0] * 10, split_dates=[days(10)[7]], split_window=2)
    assert g.skip_reason(7) == "split_suspect" and g.skip_reason(6) is None


def test_input_fp_changes_with_window_values_only() -> None:
    a = frame([100.0, 101.0, 102.0, 103.0], fp_cells=2)
    b = frame([999.0, 101.0, 102.0, 103.0], fp_cells=2)
    assert a.input_fp(3) == b.input_fp(3)
    assert a.input_fp(1) != b.input_fp(1)
    assert len(a.input_fp(3)) == 16


def test_series_cache_and_bad_inputs() -> None:
    f = frame([100.0] * 30)
    assert f.series("sma", {"n": 5}) is f.series("sma", {"n": 5})
    with pytest.raises(ValueError):
        f.series("nope", {})
    with pytest.raises(ValueError):
        build_frame("000660", bars([1.0]), upto=date(2027, 1, 1), anchor=ANCHOR, split_ratio=0.31)
