"""체결·비용(metrics §30.4)."""

from __future__ import annotations

import pytest

from kbj.engines.backtest.costs import Costs, exit_after, net_return, next_open

C = Costs(fee_bps=1.5, sell_tax_pct=0.20, slippage_bps=0)


def test_net_return_hand() -> None:
    buy = 100 * (1 + 0.00015)
    sell = 110 * (1 - 0.00015 - 0.002)
    assert net_return(100, 110, C) == pytest.approx(sell / buy - 1)
    assert net_return(100, 100, Costs(0, 0, 0)) == 0.0
    with pytest.raises(ValueError):
        Costs(fee_bps=-1)


def test_next_open_skips_halted() -> None:
    assert next_open([10.0, None, 12.0], 0) == (2, 12.0)
    assert next_open([10.0], 0) is None


def test_exit_stop_first_when_both_hit() -> None:
    o = [100.0, 100.0]
    h = [100.0, 130.0]
    lo = [100.0, 80.0]
    c = [100.0, 100.0]
    e = exit_after(0, 100.0, o, h, lo, c, hold=5, stop_pct=-7, target_pct=15)
    assert e is not None and e.reason == "stop" and e.price == pytest.approx(93.0)


def test_exit_gap_open_and_hold_and_open_end() -> None:
    o = [100.0, 90.0, 100.0]
    e = exit_after(
        0,
        100.0,
        o,
        [100.0, 92.0, 101.0],
        [100.0, 88.0, 99.0],
        [100.0, 91.0, 100.0],
        hold=3,
        stop_pct=-7,
    )
    assert e is not None and e.reason == "stop" and e.price == 90.0
    e2 = exit_after(
        0,
        100.0,
        [100.0, 101.0, None, 103.0],
        [101.0, 102.0, None, 104.0],
        [99.0, 100.0, None, 102.0],
        [100.0, 101.0, None, 103.0],
        hold=3,
    )
    assert e2 is not None and e2.reason == "hold" and e2.index == 3 and e2.bars == 3
    e3 = exit_after(0, 100.0, [100.0], [100.0], [100.0], [100.0], hold=3)
    assert e3 is not None and e3.reason == "open_end"
