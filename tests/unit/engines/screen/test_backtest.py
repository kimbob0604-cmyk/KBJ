"""cis 순추세 백테스트 엔진(kbj.engines.screen.backtest) — 합성 일봉 시나리오."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.config.screen import load_screen
from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.engines.screen.backtest import (
    OhlcSeries,
    Signal,
    regime_series,
    run_backtest,
    series_from,
    signals_of,
    summarize,
)
from kbj.engines.screen.cis import StockMeta, screen_cis

CONF = load_screen()
CFG = CONF.cis
BT = CONF.cis_backtest.model_copy(update={"stop_mode": "pct", "entry_mode": "open"})
N = CFG.lookback_days
D0 = date(2026, 1, 1)
META = {"000100": StockMeta("가나", "KOSPI", "common", False)}

Ohlc = tuple[float, float, float, float]


def _series(path: list[Ohlc], *, spike_at: int = N, base_vol: int = 1_000_000) -> OhlcSeries:
    """앞 N 일은 10,000 박스(고가 10,200, 거래대금 100억), spike_at 날 거래량 5배, 그 뒤 path."""
    box: list[Ohlc] = [(10_000, 10_200, 9_800, 10_000)] * N
    bars = box + path
    vols = [base_vol * 5 if i == spike_at else base_vol for i in range(len(bars))]
    return OhlcSeries(
        code="000100",
        dates=tuple(D0 + timedelta(days=i) for i in range(len(bars))),
        open=tuple(b[0] for b in bars), high=tuple(b[1] for b in bars),
        low=tuple(b[2] for b in bars), close=tuple(b[3] for b in bars),
        volume=tuple(vols), turnover=tuple(int(b[3] * v) for b, v in zip(bars, vols, strict=True)),
    )  # fmt: skip


SPIKE: Ohlc = (10_100, 11_000, 10_050, 10_950)  # 박스 위·양봉·고가 근처


def _rise(step: float, n: int, low_gap: float = 50) -> list[Ohlc]:
    """11,000 에서 하루 step 씩 오르는 양봉 n 개."""
    return [
        (11_000 + step * k, 11_000 + step * (k + 1), 11_000 + step * k - low_gap,
         11_000 + step * (k + 1) - 50)
        for k in range(n)
    ]  # fmt: skip


def _run(s: OhlcSeries, **bt: object):
    atr_days = bt.get("atr_days", 5)
    assert isinstance(atr_days, int)
    sig = signals_of({s.code: s}, META, {}, CFG, start=s.dates[0], atr_days=atr_days)
    cfg_bt = BT.model_copy(update=bt)
    res = run_backtest({s.code: s}, sig, s.dates[N:], CFG, cfg_bt)
    return sig, res


def test_신호는_거래량_급증·돌파_날_하루() -> None:
    s = _series([SPIKE] + [(11_000, 11_100, 10_900, 11_000)] * 3)
    sig, _ = _run(s)
    assert list(sig) == [s.dates[N]]
    assert sig[s.dates[N]][0].vol_ratio == pytest.approx(5.0)


def test_추적_손절로_이익을_끝까지() -> None:
    up = _rise(300, 12)
    peak = up[-1][1]
    down = [(peak - 200, peak - 100, peak * 0.90, peak * 0.91)]  # 저가가 최고가 −8% 아래
    _, res = _run(_series([SPIKE, *up, *down]), add_frac=0.0, ma_exit_breaks=9)
    t = res.trades[0]
    assert t.reason == "trail" and t.entry_date == D0 + timedelta(days=N + 1)
    assert t.exit_px == pytest.approx(peak * (1 - CFG.plan.trail_pct / 100))
    assert t.pnl > 0 and t.r_mult > 1


def test_진입일_저가가_손절가면_그날_손절() -> None:
    _, res = _run(_series([SPIKE, (11_000, 11_050, 10_500, 10_600)]))
    t = res.trades[0]
    assert t.reason == "stop" and t.days == 1
    assert t.exit_px == pytest.approx(11_000 * (1 - CFG.plan.stop_pct / 100))
    assert t.ret_pct < -CFG.plan.stop_pct  # 비용까지 빠진다
    assert t.r_mult == pytest.approx(t.pnl / (t.qty * 11_000 * CFG.plan.stop_pct / 100))


def test_갭_추격은_건너뛴다() -> None:
    _, res = _run(_series([SPIKE, (12_000, 12_100, 11_900, 12_000)]))
    assert res.trades == () and res.skipped == {"갭 추격 금지": 1}


def test_오르지_못하면_시간_손절() -> None:
    flat = [(11_000, 11_050, 10_950, 11_000)] * 6
    _, res = _run(_series([SPIKE, *flat]))
    t = res.trades[0]
    assert t.reason == "time" and t.days == BT.time_stop_days


def test_불타기는_오를_때만_한_번() -> None:
    up = _rise(400, 6, low_gap=20)
    _, res = _run(_series([SPIKE, *up]))
    t = res.trades[0]
    assert t.adds == 1 and t.reason == "end"
    assert t.cost > t.qty * 11_000 * 0.5  # 불타기 몫까지 매수 총액에 들어간다


def test_요약_통계() -> None:
    up = _rise(300, 12)
    peak = up[-1][1]
    _, res = _run(_series([SPIKE, *up, (peak - 200, peak - 100, peak * 0.9, peak * 0.91)]))
    s = summarize(res, flat_band_pct=BT.flat_band_pct, max_positions=CFG.plan.max_positions)
    assert s.trades == 1 and s.wins == 1 and s.win_pct == 100.0
    assert s.total_return_pct > 0 and s.mdd_pct <= 0 and s.max_consec_losses == 0
    assert sum(s.reasons.values()) == 1 and s.by_year


def test_화면과_같은_판정() -> None:
    """같은 날 스크리너(screen_cis)가 통과로 본 종목 = 백테스트 신호."""
    s = _series([SPIKE])
    bars = [
        Bar(code=s.code, date=d, open=o, high=h, low=lo, close=c, volume=v, turnover=t,
            source="krx", venue="KRX", quality=Quality.OK)
        for d, o, h, lo, c, v, t in zip(s.dates, s.open, s.high, s.low, s.close, s.volume,
                                         s.turnover, strict=True)
    ]  # fmt: skip
    assert series_from(s.code, bars) == s
    day = s.dates[-1]
    shown = screen_cis({s.code: bars}, META, {}, CFG, day)
    sig = signals_of({s.code: s}, META, {}, CFG, start=day)
    assert [r.code for r in shown.rows if r.passed] == [x.code for x in sig[day]]


def test_시장_약세_날은_신호_없음() -> None:
    s = _series([SPIKE])
    reg = {"KOSPI": {s.dates[N]: False}}
    assert signals_of({s.code: s}, META, reg, CFG, start=s.dates[0]) == {}
    assert regime_series({}, CFG) == {"KOSPI": {}, "KOSDAQ": {}}


def test_보유_상한() -> None:
    s = _series([SPIKE, (11_000, 11_100, 10_900, 11_000)])
    sig = {s.dates[N]: [Signal(s.dates[N], s.code, 5.0, 10_950)]}
    res = run_backtest({s.code: s}, sig, s.dates[N:], CFG,
                       BT.model_copy(update={"capital_won": 1}))  # fmt: skip
    assert res.trades == () and res.skipped == {"현금·비중 부족": 1}


def test_신호일_저가_손절() -> None:
    _, res = _run(_series([SPIKE, (11_000, 11_050, 10_000, 10_100)]), stop_mode="signal_low")
    t = res.trades[0]
    assert t.reason == "stop" and t.exit_px == SPIKE[2]  # 신호일 저가 10,050


def test_ATR_손절은_이력이_모자라면_건너뛴다() -> None:
    _, res = _run(_series([SPIKE, (11_000, 11_100, 10_900, 11_000)]), stop_mode="atr", atr_days=99)
    assert res.trades == () and res.skipped == {"손절가 없음(ATR 이력 부족)": 1}
    _, res = _run(_series([SPIKE, (11_000, 11_100, 10_900, 11_000)]), stop_mode="atr")
    assert res.trades and res.trades[0].entry_px == 11_000


def test_확인_진입은_신호일_고가를_넘을_때만() -> None:
    _, res = _run(_series([SPIKE, (10_900, 10_990, 10_800, 10_950)]), entry_mode="confirm")
    assert res.trades == () and res.skipped == {"확인 실패(신호일 고가 못 넘음)": 1}
    _, res = _run(_series([SPIKE, (10_900, 11_300, 10_850, 11_250)]), entry_mode="confirm")
    assert res.trades[0].entry_px == SPIKE[1]  # 신호일 고가 11,000 에 체결


def test_신호일_종가_진입() -> None:
    _, res = _run(_series([SPIKE, (11_000, 11_100, 10_900, 11_000)]), entry_mode="close")
    t = res.trades[0]
    assert t.entry_px == SPIKE[3] and t.entry_date == D0 + timedelta(days=N)  # 신호일 종가 10,950
    assert t.days == 2  # 신호일 + 다음 날


def test_종가_진입_다음_날_시가가_손절_아래면_시가에_손절() -> None:
    _, res = _run(_series([SPIKE, (10_500, 10_600, 10_400, 10_500)]), entry_mode="close")
    t = res.trades[0]
    assert t.reason == "stop" and t.exit_px == 10_500  # 손절가 10,950 × 0.98 = 10,731 아래 시가


def test_시간·이평_청산_0_이면_끈다() -> None:
    flat = [(11_000, 11_050, 10_950, 11_000)] * 6
    _, res = _run(_series([SPIKE, *flat]), time_stop_days=0, ma_exit_breaks=0)
    assert res.trades[0].reason == "end"
