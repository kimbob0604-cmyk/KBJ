"""상대 시총·연도별 수익률 엔진(합성 값)."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.engines.market.relcap import (
    INDICES,
    annual_returns,
    daily_symbols,
    pair_series,
    pair_view,
)

D1, D2, D3 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)
A = (("AAA", None, "USD"),)
B = (("BBB.KS", "000001", "KRW"),)


def test_ratio_uses_price_path_and_fx() -> None:
    hist = {"AAA": [(D1, 10.0), (D2, 20.0)], "BBB.KS": [(D1, 1000.0), (D3, 1000.0)]}
    caps = {"AAA": 200.0, "BBB.KS": 100_000.0}
    fx = {"KRW": [(D1, 1000.0), (D3, 1000.0)]}
    s, err = pair_series(A, B, hist, caps, fx)
    assert err is None
    # D1: A = 200 × 10/20 = 100$, B = 100,000원 / 1000 = 100$ → 100%
    # D3: A 는 휴장(직전 D2 = 200$), B = 100$ → 200%
    assert s == [(D1, 100.0), (D2, 200.0), (D3, 200.0)]


def test_missing_inputs_give_reason() -> None:
    hist = {"AAA": [(D1, 10.0)], "BBB.KS": [(D1, 1.0)]}
    assert pair_series(A, B, hist, {"AAA": 1.0}, {"KRW": [(D1, 1.0)]}) == (
        [],
        "BBB.KS 현재 시총 없음",
    )
    assert pair_series(A, B, hist, {"AAA": 1.0, "BBB.KS": 1.0}, {}) == ([], "KRW 환율 없음")
    assert pair_series(A, B, {}, {}, {})[1] == "AAA 일봉 없음"


def test_pair_view_range_position_and_since() -> None:
    hist = {
        "AAA": [(D1, 10.0), (D2, 20.0), (D3, 15.0)],
        "BBB.KS": [(D1, 1.0), (D2, 1.0), (D3, 1.0)],
    }
    caps = {"AAA": 150.0, "BBB.KS": 100_000.0}
    fx = {"KRW": [(D1, 1000.0)]}
    v = pair_view("A/B", A, B, hist, caps, fx, since=D2)
    assert [d for d, _ in v.series] == [D2, D3]
    assert v.high == 200.0 and v.low == 150.0 and v.latest == 150.0 and v.pct_of_range == 0.0
    assert v.legs == (("AAA",), ("BBB.KS",)) and v.error is None


def test_annual_returns_year_end_and_ytd() -> None:
    monthly = {
        "^GSPC": [(date(2024, 12, 1), 100.0), (date(2025, 6, 1), 90.0), (date(2025, 12, 1), 110.0),
                  (date(2026, 10, 1), 121.0)],
    }  # fmt: skip
    years, rows = annual_returns(monthly)
    assert years == [2026, 2025]
    sp = rows[0]
    assert sp.returns == {2025: 10.0, 2026: 10.0} and sp.asof == date(2026, 10, 1)
    assert len(rows) == len(INDICES) and rows[1].error == "월봉 없음"


def test_daily_symbols() -> None:
    s = daily_symbols()
    assert {"000660.KS", "005935.KS", "MU", "6981.T", "KRW=X", "JPY=X"} <= set(s)
    assert list(s) == sorted(s) and "^GSPC" not in s


def test_rounding() -> None:
    s, _ = pair_series(A, A, {"AAA": [(D1, 3.0)]}, {"AAA": 7.0}, {})
    assert s == [(D1, pytest.approx(100.0))]
