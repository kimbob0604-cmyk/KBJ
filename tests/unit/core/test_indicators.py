"""지표 정본(`kbj.core.indicators` — metrics §30) — 손계산 표 + board 원본 값 고정 표.

board 원본 값은 2026-10-14 에 legacy `board.engine.systems`(`sma_arr`·`ema_arr`·`rsi_arr`·`adx_arr`·
`turtle_n`)·`signals`(`pstdev`·`bb_width`·`true_range`)로 같은 입력(아래 `_series`)에서 뽑아
고정했다
(legacy 를 import 하지 않는다 — ADR 0007). 정지일이 없는 입력에서는 결측 규칙이 끼어들지 않으므로
board 값과 같아야 한다(D-P8-3 — 다르면 정본이 board 와 다르다는 뜻).
"""

from __future__ import annotations

import math

import pytest

from kbj.core import indicators as ind

N = 60


def _series() -> tuple[list[float], list[float], list[float]]:
    c = [100 + 10 * math.sin(i / 5) + i * 0.3 + (i % 7) * 0.8 for i in range(N)]
    h = [x + 1.5 + (i % 3) * 0.4 for i, x in enumerate(c)]
    lo = [x - 1.2 - (i % 4) * 0.3 for i, x in enumerate(c)]
    return h, lo, c


# board 원본 값(같은 입력) — {칸: 값}
BOARD = {
    "sma20": {19: 109.43952011681161, 27: 106.17250741172086, 40: 110.60041857344868,
              59: 115.12634792805868},
    "ema12": {19: 107.48114523648576, 27: 102.99981565646333, 40: 118.11595559872316,
              59: 111.79489459764658},
    "rsi14": {19: 53.80714737683019, 27: 58.30251762427123, 40: 76.0366428737006,
              59: 51.304687088196104},
    "adx14": {19: None, 27: 12.063331316736045, 40: 29.366011324372273, 59: 19.89256431861197},
    "atr14": {19: 4.175882831166237, 27: 4.104564285673223, 40: 4.213362324543774,
              59: 4.160467761044729},
    "bbw20": {19: 0.17358582695559188, 27: 0.21972741667713414, 40: 0.35022785620081714,
              59: 0.20472434748963933},
}  # fmt: skip


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def test_board_golden_values() -> None:
    h, lo, c = _series()
    got = {
        "sma20": ind.sma(c, 20),
        "ema12": ind.ema(c, 12),
        "rsi14": ind.rsi(c, 14),
        "adx14": ind.adx(h, lo, c, 14).adx,
        "atr14": ind.atr(h, lo, c, 14),
        "bbw20": ind.bollinger(c, 20, 2.0).width,
    }
    for name, table in BOARD.items():
        for i, want in table.items():
            assert _close(got[name][i], want), (name, i, got[name][i], want)


def test_output_lengths_equal_input() -> None:
    h, lo, c = _series()
    m = ind.macd(c)
    b = ind.bollinger(c)
    a = ind.adx(h, lo, c)
    outs = [
        ind.sma(c, 5), ind.ema(c, 5), ind.rsi(c), m.line, m.signal, m.hist, b.upper, b.pctb,
        a.pdi, ind.atr(h, lo, c), ind.vol_ratio(c), ind.high_n(h), ind.low_n(lo),
        ind.change_pct(c), ind.width_rank(b.width, 10), ind.true_range(h, lo, c),
    ]  # fmt: skip
    for out in outs:
        assert len(out) == N


def test_sma_hand() -> None:
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    # 창 안에 None 이 있으면 None
    assert ind.sma([1, None, 3, 4, 5], 2) == [None, None, None, 3.5, 4.5]


def test_ema_hand_and_seed() -> None:
    # 시드 = 첫 3칸 SMA = 2, α = 0.5 → 2 + 0.5(4 − 2) = 3, 3 + 0.5(5 − 3) = 4
    assert ind.ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_skips_missing() -> None:
    dense = ind.ema([1, 2, 3, 4, 5], 3)
    gap = ind.ema([1, 2, None, 3, 4, 5], 3)
    assert gap == [dense[0], dense[1], None, dense[2], dense[3], dense[4]]


def test_rsi_warmup_none_not_zero() -> None:
    out = ind.rsi([10, 11, 12, 11, 12, 13], 3)
    assert out[:3] == [None, None, None]
    # 첫 3 변화: +1 +1 −1 → g = 2/3, l = 1/3 → 100 − 100/(1 + 2) = 66.66…
    assert out[3] == pytest.approx(100 - 100 / 3)
    # 오르기만 하면 100(l = 0)
    assert ind.rsi([1, 2, 3, 4], 3)[3] == 100.0


def test_macd_hand() -> None:
    c = [float(i) for i in range(1, 11)]
    m = ind.macd(c, fast=2, slow=3, signal=2)
    # EMA2 시드 칸1 = 1.5, EMA3 시드 칸2 = 2; 선형 증가에서 EMA_n(t) = t − (n−1)/2 → line = 0.5
    assert m.line[:2] == [None, None]
    assert m.line[2] == pytest.approx(0.5)
    assert m.signal[3] == pytest.approx(0.5)  # line 의 첫 유효 칸(2)부터 2칸 SMA 시드
    assert m.hist[3] == pytest.approx(0.0)
    with pytest.raises(ValueError):
        ind.macd(c, fast=5, slow=3)


def test_bollinger_hand() -> None:
    c = [1.0, 2.0, 3.0]
    b = ind.bollinger(c, n=3, k=2.0)
    sd = math.sqrt(2 / 3)
    assert b.mid[2] == pytest.approx(2.0)
    assert b.upper[2] == pytest.approx(2 + 2 * sd)
    assert b.lower[2] == pytest.approx(2 - 2 * sd)
    assert b.pctb[2] == pytest.approx((3 - (2 - 2 * sd)) / (4 * sd))
    assert b.width[2] == pytest.approx(4 * sd / 2)
    # σ = 0 이면 %B None
    flat = ind.bollinger([5.0, 5.0, 5.0], n=3)
    assert flat.pctb[2] is None and flat.width[2] == 0.0


def test_width_rank_and_pct_rank() -> None:
    assert ind.pct_rank([1.0, 2.0, 3.0], 1.0) == 0.0
    assert ind.pct_rank([1.0, 2.0, 3.0], 3.0) == 100.0
    assert ind.pct_rank([2.0, 2.0], 2.0) == 50.0
    assert ind.pct_rank([7.0], 7.0) == 50.0
    w = [3.0, 2.0, 1.0, 4.0]
    assert ind.width_rank(w, 3) == [None, None, 0.0, 100.0]


def test_true_range_and_atr_hand() -> None:
    h = [10.0, 12.0, 11.0, 13.0]
    lo = [9.0, 10.0, 9.0, 11.0]
    c = [9.5, 11.0, 10.0, 12.5]
    assert ind.true_range(h, lo, c) == [1.0, 2.5, 2.0, 3.0]
    # 시드 = TR[1..2] 평균 = 2.25(칸 2), (2.25·1 + 3)/2 = 2.625
    assert ind.atr(h, lo, c, 2) == [None, None, 2.25, 2.625]


def test_adx_bounds_and_short_input() -> None:
    h, lo, c = _series()
    a = ind.adx(h, lo, c, 14)
    vals = [v for v in a.adx if v is not None]
    assert vals and all(0 <= v <= 100 for v in vals)
    assert a.pdi[13] is None and a.pdi[14] is not None
    assert all(v is None for v in ind.adx(h[:28], lo[:28], c[:28], 14).adx)


def test_vol_ratio_high_low_exclude_today() -> None:
    v = [10.0, 10.0, 30.0]
    assert ind.vol_ratio(v, 2) == [None, None, 3.0]
    assert ind.vol_ratio([0.0, 0.0, 5.0], 2)[2] is None  # 평균 0
    h = [1.0, 5.0, 3.0, 9.0]
    assert ind.high_n(h, 2) == [None, None, 5.0, 5.0]
    assert ind.low_n(h, 2) == [None, None, 1.0, 3.0]


def test_change_pct_uses_last_valid_close() -> None:
    out = ind.change_pct([100.0, None, 110.0, 99.0])
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(10.0)
    assert out[3] == pytest.approx(-10.0)


def test_trail_from_peak() -> None:
    h = [10.0, 12.0, 11.0, 9.0]
    c = [9.0, 11.0, 10.0, 9.0]
    assert ind.trail_from_peak(h, c, None) == [None] * 4
    out = ind.trail_from_peak(h, c, 1)
    assert out[0] is None
    assert out[1] == pytest.approx((11 / 12 - 1) * 100)
    assert out[3] == pytest.approx((9 / 12 - 1) * 100)


def test_bad_args() -> None:
    with pytest.raises(ValueError):
        ind.sma([1.0], 0)
    with pytest.raises(ValueError):
        ind.true_range([1.0], [1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        ind.bollinger([1.0], 3, 0)
