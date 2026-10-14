"""통계(metrics §30.4) — board `backtest.stats` 같은 단언 + 분위수·MDD·기술 통계."""

from __future__ import annotations

import pytest

from kbj.engines.backtest.stats import TradeResult, describe, max_drawdown, quantile, trade_stats


def test_board_stats_same_assertions() -> None:
    """board `tests/test_backtest.py::TestStats::test_stats` 와 같은 입력·같은 단언."""
    ts = [TradeResult(ret_pct=r, bars=5, reason="stop") for r in (-8, -8, 20, 4)]
    st = trade_stats(ts)
    assert st.n == 4
    assert st.win_rate == 50.0
    assert st.mean == 2.0
    assert st.profit_factor == 1.5
    assert st.reasons == {"stop": 4}


def test_empty_and_no_loss() -> None:
    assert trade_stats([]).n == 0 and trade_stats([]).mean is None
    assert trade_stats([TradeResult(5, 2, "hold")]).profit_factor is None


def test_quantile_and_describe() -> None:
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.25) == pytest.approx(1.75)
    d = describe([0.1, -0.2, 0.3], min_samples=20)
    assert d.n == 3 and d.small and d.win_rate == pytest.approx(200 / 3)
    assert d.median == 0.1
    assert describe([]).mean is None


def test_mdd() -> None:
    assert max_drawdown([10, -50, 20]) == pytest.approx(-0.5)
    assert max_drawdown([1, 2]) == 0.0
