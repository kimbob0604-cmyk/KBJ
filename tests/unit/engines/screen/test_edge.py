"""cis 신호 우위(kbj.engines.screen.edge) — 손으로 셀 수 있는 합성 일봉."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.engines.screen.backtest import OhlcSeries, Signal
from kbj.engines.screen.edge import HORIZONS, baseline, edge_row, forward_of

D0 = date(2026, 1, 1)


def _s(code: str, closes: list[float], *, highs: list[float] | None = None) -> OhlcSeries:
    """시가 = 전날 종가(첫날은 종가), 고가 = 종가 +1%(또는 highs), 저가 = 종가 −1%."""
    n = len(closes)
    opens = [closes[0], *closes[:-1]]
    hi = highs or [c * 1.01 for c in closes]
    return OhlcSeries(
        code=code, dates=tuple(D0 + timedelta(days=i) for i in range(n)),
        open=tuple(opens), high=tuple(hi), low=tuple(c * 0.99 for c in closes),
        close=tuple(closes), volume=(1,) * n, turnover=(1,) * n,
    )  # fmt: skip


def test_앞날_수익률은_다음_날_시가부터() -> None:
    closes = [100.0, 110.0, *(110.0 + k for k in range(1, 25))]
    s = _s("000100", closes)
    sig = {D0: [Signal(D0, s.code, 4.0, 100.0, 101.0, 99.0)]}
    (f,) = forward_of({s.code: s}, sig)
    # 진입 = 1일째 시가 100(전날 종가) → 1일째 종가 110 = +10%
    assert f.rets[0] == pytest.approx(10.0)
    assert f.rets[HORIZONS.index(5)] == pytest.approx((closes[5] / 100 - 1) * 100)
    assert f.mfe == pytest.approx((max(s.high[1:21]) / 100 - 1) * 100)
    assert f.mae == pytest.approx(8.9)  # 진입일 저가 110 × 0.99 = 108.9


def test_기간_끝이면_그_n일만_뺀다() -> None:
    s = _s("000100", [100.0, 101.0, 102.0, 103.0])
    (f,) = forward_of({s.code: s}, {D0: [Signal(D0, s.code, 4.0, 100.0)]})
    assert f.rets[0] is not None and f.rets[1:] == (None, None, None)
    row = edge_row("x", [f])
    assert row.n == 1 and row.mean[1] is None and row.median_last is None


def test_확인_진입은_신호일_고가를_넘을_때만() -> None:
    closes = [100.0] * 30
    s = _s("000100", closes, highs=[100.5] * 30)
    sig = {D0: [Signal(D0, s.code, 4.0, 100.0, high=101.0)]}
    assert forward_of({s.code: s}, sig, entry="confirm") == []
    s2 = _s("000100", closes, highs=[102.0] * 30)
    (f,) = forward_of({s2.code: s2}, sig, entry="confirm")
    assert f.rets[0] == pytest.approx((100 / 101 - 1) * 100)  # 신호일 고가 101 에 체결


def test_기준선은_모든_날_평균() -> None:
    s = _s("000100", [100.0 * 1.01**k for k in range(40)])  # 매일 +1%
    row = baseline({s.code: s}, [s.code, "없는코드"], start=D0)
    assert row.n == 39
    assert row.mean[0] == pytest.approx(1.0)  # 시가 = 전날 종가 → 1일 = +1%
    assert row.mean[1] == pytest.approx((1.01**5 - 1) * 100)
    assert row.up_pct[0] == 100.0
