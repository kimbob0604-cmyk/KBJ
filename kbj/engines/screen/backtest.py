"""cis 순추세 백테스트(순수 계산) — 스크리너(`cis.judge`)와 같은 판정으로 신호를 내고
일봉으로 굴린다.

규칙(config/screen.yaml `cis.plan`·`cis_backtest` — 값은 출발점, 백테스트로 다시 정한다)
- 신호: D일 종가로 통과(`judge(...).passed`) — 거래량 배수 큰 순으로 D+1 시가 진입. 이미 들고
  있는 종목·동시 보유 상한(`max_positions`)·갭(시가 > D 종가 × (1 + `gap_skip_pct`))은
  건너뛴다(센다).
- 수량: 위험 = 전날 평가액 × `risk_pct`, 1주 위험 = 진입가 × `stop_pct` → 수량 = 위험 ÷ 1주 위험
  (내림), 한 종목 비중 `max_weight_pct`·현금 안으로 줄인다.
- 손절: 진입가 −`stop_pct`. 고가가 +`breakeven_at_pct` 를 넘으면 본전으로, 보유 중 최고가
  −`trail_pct` 가 더 높으면 추적 손절로 올린다(내리지 않는다). 시가가 손절 아래면 시가, 장중 저가가
  닿으면 손절가 체결. **같은 날 손절과 새 고가가 다 나오면 손절이 먼저**(일봉이라 순서를 모른다 —
  보수적).
- 그 밖의 청산(종가): `ma_exit_days` 일선 아래 마감이 `ma_exit_breaks` 번째(cis '두 번째 하락에서
  판다'), `time_stop_days` 거래일 안에 종가가 +`time_stop_gain_pct` 를 한 번도 못 넘음. 기간 끝은
  마지막 종가.
- 불타기(한 번): 종가가 +`add_at_pct` 이상이고 보유 중 종가 신고가면 다음 날 시가에 처음 수량 ×
  `add_frac`(시가가 손절 위일 때만). 물타기는 없다.
- 비용: `kbj.engines.backtest.costs`(수수료 양쪽·매도세·슬리피지 양쪽).

미래 참조 없음: 신호는 D일까지 값만, 체결은 D+1 시가부터. 대상 종류·시장은 지금 정보로 고른다
(상장폐지 종목이 저장소에 없으면 결과가 좋게 치우친다 — 호출하는 쪽이 표시).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from math import floor
from typing import Final, Literal

from kbj.config.screen import CisBacktestCfg, CisCfg
from kbj.core.rows import Bar, IndexBar
from kbj.engines.backtest.costs import Costs, buy_cost, sell_proceeds
from kbj.engines.screen.cis import MARKETS, CisRow, Day, StockMeta, eligible, judge

__all__ = [
    "REASONS",
    "BacktestResult",
    "OhlcSeries",
    "Signal",
    "Summary",
    "Trade",
    "regime_series",
    "run_backtest",
    "series_from",
    "signals_of",
    "summarize",
]

Reason = Literal["stop", "breakeven", "trail", "ma", "time", "end"]
REASONS: Final[Mapping[str, str]] = {
    "stop": "손절", "breakeven": "본전 손절", "trail": "추적 손절", "ma": "이평 두 번째 이탈",
    "time": "시간 손절", "end": "기간 끝",
}  # fmt: skip


@dataclass(frozen=True)
class OhlcSeries:
    """한 종목 일봉(가격이 다 있는 날만, 오름차순)."""

    code: str
    dates: tuple[date, ...]
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]
    volume: tuple[int, ...]
    turnover: tuple[int, ...]


def series_from(code: str, bars: Sequence[Bar]) -> OhlcSeries | None:
    rows = [
        (b.date, b.open, b.high, b.low, b.close, b.volume, b.turnover)
        for b in sorted(bars, key=lambda b: b.date)
        if b.open and b.high and b.low and b.close and b.volume is not None
        and b.turnover is not None
    ]  # fmt: skip
    if not rows:
        return None
    d, o, h, lo, c, v, t = zip(*rows, strict=True)
    return OhlcSeries(code, d, o, h, lo, c, v, t)  # pyright: ignore[reportArgumentType]


def regime_series(
    index_bars: Mapping[str, Sequence[IndexBar]], cfg: CisCfg
) -> dict[str, dict[date, bool]]:
    """시장 → {날짜: 지수 종가 > n일선}. 후보 지수 코드 중 봉이 가장 많은 것."""
    out: dict[str, dict[date, bool]] = {}
    n = cfg.regime_ma
    for m in MARKETS:
        cands = [
            [b for b in sorted(index_bars.get(c, ()), key=lambda b: b.date) if b.close is not None]
            for c in cfg.index.get(m, ())
        ]
        seq = max(cands, key=len, default=[])
        closes = [float(b.close) for b in seq if b.close is not None]
        out[m] = {
            seq[i].date: closes[i] > sum(closes[i - n + 1 : i + 1]) / n
            for i in range(n - 1, len(seq))
        }
    return out


@dataclass(frozen=True)
class Signal:
    date: date
    code: str
    vol_ratio: float
    close: float
    high: float = 0.0  # 신호일 고가(확인 진입 문턱)
    low: float = 0.0  # 신호일 저가(신호일 저가 손절)
    atr: float | None = None  # 신호일까지 ATR(`atr_days`)


def _atr(s: OhlcSeries, i: int, n: int) -> float | None:
    """i 일까지 n 일 평균 진폭(true range) — 모자라면 None."""
    if i < n:
        return None
    tr = [
        max(s.high[k] - s.low[k], abs(s.high[k] - s.close[k - 1]), abs(s.low[k] - s.close[k - 1]))
        for k in range(i - n + 1, i + 1)
    ]
    return sum(tr) / n


def signals_of(
    series: Mapping[str, OhlcSeries],
    meta: Mapping[str, StockMeta],
    regimes: Mapping[str, Mapping[date, bool]],
    cfg: CisCfg,
    *,
    start: date,
    atr_days: int = 14,
) -> dict[date, list[Signal]]:
    """날짜 → 통과 신호(거래량 배수 큰 순). 판정은 스크리너와 같은 `judge`."""
    out: dict[date, list[Signal]] = defaultdict(list)
    n = cfg.lookback_days
    for code in sorted(series):
        m = meta.get(code)
        if m is None or eligible(code, m) is not None:
            continue
        s = series[code]
        pv, pt = [0], [0]
        for v, t in zip(s.volume, s.turnover, strict=True):
            pv.append(pv[-1] + v)
            pt.append(pt[-1] + t)
        reg = regimes.get(str(m.market), {})
        for i in range(n, len(s.dates)):
            if s.dates[i] < start:
                continue
            vol_avg = (pv[i] - pv[i - n]) / n
            if not vol_avg:
                continue
            d = Day(
                close=s.close[i], open=s.open[i], high=s.high[i], low=s.low[i],
                volume=s.volume[i], turnover=s.turnover[i], prev_close=s.close[i - 1],
                vol_avg=vol_avg, avg_turnover=(pt[i] - pt[i - n]) / n,
                box_high=max(s.high[i - n : i]),
            )  # fmt: skip
            row = judge(code, m, d, reg.get(s.dates[i]), cfg)
            if isinstance(row, CisRow) and row.passed:
                out[s.dates[i]].append(
                    Signal(s.dates[i], code, row.vol_ratio, s.close[i], s.high[i], s.low[i],
                           _atr(s, i, atr_days))
                )  # fmt: skip
    for day in out:
        out[day].sort(key=lambda x: (-x.vol_ratio, x.code))
    return dict(out)


@dataclass(frozen=True)
class Trade:
    code: str
    entry_date: date
    exit_date: date
    entry_px: float
    exit_px: float
    qty: int  # 불타기 포함 최종 수량
    adds: int
    cost: float  # 매수 총액(비용 포함, 원)
    pnl: float  # 원(비용 반영)
    ret_pct: float  # pnl ÷ 매수 총액 × 100
    r_mult: float  # pnl ÷ 처음 위험 금액
    days: int  # 보유 거래일(진입일 포함)
    reason: Reason


@dataclass
class _Pos:
    code: str
    entry_date: date
    entry_px: float
    qty: int
    init_qty: int
    cost: float
    risk: float
    stop: float
    kind: Reason
    high: float
    max_close: float
    breaks: int = 0
    days: int = 0
    adds: int = 0
    pending_add: int = 0


@dataclass(frozen=True)
class BacktestResult:
    start: date
    end: date
    capital: float
    equity: tuple[tuple[date, float], ...]
    trades: tuple[Trade, ...]
    signals: int
    skipped: Mapping[str, int] = field(default_factory=dict[str, int])


def _costs(bt: CisBacktestCfg) -> Costs:
    c = bt.costs
    return Costs(fee_bps=c.fee_bps, sell_tax_pct=c.sell_tax_pct, slippage_bps=c.slippage_bps)


def _first_stop(px: float, sig: Signal, stop_pct: float, bt: CisBacktestCfg) -> float | None:
    """최초 손절가 — 진입가 아래여야 한다(신호일 저가가 진입가 이상이면 % 손절로)."""
    pct = px * (1 - stop_pct / 100)
    if bt.stop_mode == "signal_low":
        return sig.low if 0 < sig.low < px else pct
    if bt.stop_mode == "atr":
        if sig.atr is None:
            return None
        stop = px - sig.atr * bt.atr_mult
        return stop if 0 < stop < px else None
    return pct


def run_backtest(
    series: Mapping[str, OhlcSeries],
    signals: Mapping[date, Sequence[Signal]],
    days: Sequence[date],
    cfg: CisCfg,
    bt: CisBacktestCfg,
) -> BacktestResult:
    """`days`(오름차순 거래일)를 하루씩 굴린다. 신호 날짜 d 의 진입은 days 에서 d 다음 날 시가."""
    if not days:
        raise ValueError("거래일이 없다")
    plan, costs = cfg.plan, _costs(bt)
    idx: dict[str, dict[date, int]] = {}

    def at(code: str, d: date) -> int | None:
        if code not in idx:
            idx[code] = {x: i for i, x in enumerate(series[code].dates)}
        return idx[code].get(d)

    cash = float(bt.capital_won)
    equity_prev = cash
    held: dict[str, _Pos] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    curve: list[tuple[date, float]] = []
    skipped: Counter[str] = Counter()
    n_signals = 0
    pending: Sequence[Signal] = ()

    def close_out(p: _Pos, d: date, px: float, reason: Reason) -> None:
        nonlocal cash
        proceeds = p.qty * sell_proceeds(px, costs)
        cash += proceeds
        pnl = proceeds - p.cost
        trades.append(
            Trade(code=p.code, entry_date=p.entry_date, exit_date=d, entry_px=p.entry_px,
                  exit_px=px, qty=p.qty, adds=p.adds, cost=p.cost, pnl=pnl,
                  ret_pct=pnl / p.cost * 100, r_mult=pnl / p.risk if p.risk else 0.0,
                  days=p.days, reason=reason)
        )  # fmt: skip
        del held[p.code]

    for d in days:
        s_cache: dict[str, OhlcSeries] = {}
        # 1) 들고 있는 종목 — 시가: 불타기 체결, 갭 하락 손절
        for p in list(held.values()):
            i = at(p.code, d)
            if i is None:
                continue  # 그날 거래 없음(정지)
            s = s_cache.setdefault(p.code, series[p.code])
            o = s.open[i]
            if p.pending_add and o > p.stop:
                add_cost = p.pending_add * buy_cost(o, costs)
                if add_cost <= cash:
                    cash -= add_cost
                    p.qty += p.pending_add
                    p.cost += add_cost
                    p.adds += 1
            p.pending_add = 0
            if o <= p.stop:
                close_out(p, d, o, p.kind)
        # 2) 어제 신호 — 시가 진입
        for sig in pending:
            if sig.code in held:
                continue
            if len(held) >= plan.max_positions:
                skipped["보유 상한"] += 1
                continue
            i = at(sig.code, d)
            if i is None:
                skipped["다음 날 거래 없음"] += 1
                continue
            s = s_cache.setdefault(sig.code, series[sig.code])
            o = s.open[i]
            if o > sig.close * (1 + plan.gap_skip_pct / 100):
                skipped["갭 추격 금지"] += 1
                continue
            px = o
            if bt.entry_mode == "confirm":
                if s.high[i] < sig.high:
                    skipped["확인 실패(신호일 고가 못 넘음)"] += 1
                    continue
                px = max(o, sig.high)
            stop = _first_stop(px, sig, plan.stop_pct, bt)
            if stop is None:
                skipped["손절가 없음(ATR 이력 부족)"] += 1
                continue
            per = px - stop
            qty = floor(equity_prev * plan.risk_pct / 100 / per)
            qty = min(qty, floor(equity_prev * bt.max_weight_pct / 100 / px))
            qty = min(qty, floor(cash / buy_cost(px, costs)))
            if qty < 1:
                skipped["현금·비중 부족"] += 1
                continue
            cost = qty * buy_cost(px, costs)
            cash -= cost
            held[sig.code] = _Pos(code=sig.code, entry_date=d, entry_px=px, qty=qty, init_qty=qty,
                                  cost=cost, risk=qty * per, stop=stop, kind="stop", high=px,
                                  max_close=px)  # fmt: skip
        # 3) 장중 저가 손절 → 4) 종가 판정·손절 올리기·불타기 예약
        for p in list(held.values()):
            i = at(p.code, d)
            if i is None:
                continue
            s = s_cache.setdefault(p.code, series[p.code])
            p.days += 1
            if s.low[i] <= p.stop:
                close_out(p, d, p.stop, p.kind)
                continue
            c = s.close[i]
            last_close[p.code] = c
            p.high = max(p.high, s.high[i])
            k = bt.ma_exit_days
            if i + 1 >= k and c < sum(s.close[i - k + 1 : i + 1]) / k:
                p.breaks += 1
                if p.breaks >= bt.ma_exit_breaks:
                    close_out(p, d, c, "ma")
                    continue
            new_high = c > p.max_close
            p.max_close = max(p.max_close, c)
            gain_seen = p.max_close >= p.entry_px * (1 + bt.time_stop_gain_pct / 100)
            if p.days >= bt.time_stop_days and not gain_seen:
                close_out(p, d, c, "time")
                continue
            if p.high >= p.entry_px * (1 + bt.breakeven_at_pct / 100) and p.stop < p.entry_px:
                p.stop, p.kind = p.entry_px, "breakeven"
            trail = p.high * (1 - plan.trail_pct / 100)
            if trail > p.stop:
                p.stop, p.kind = trail, "trail"
            if (
                bt.add_frac > 0 and p.adds == 0 and new_high
                and c >= p.entry_px * (1 + plan.add_at_pct / 100)
            ):  # fmt: skip
                p.pending_add = max(1, floor(p.init_qty * bt.add_frac))
        # 5) 평가액
        equity_prev = cash + sum(p.qty * last_close.get(p.code, p.entry_px) for p in held.values())
        curve.append((d, equity_prev))
        pending = signals.get(d, ())
        n_signals += len(pending)
    end = days[-1]
    for p in list(held.values()):
        close_out(p, end, last_close.get(p.code, p.entry_px), "end")
    if curve:
        curve[-1] = (end, cash)
    return BacktestResult(
        start=days[0], end=end, capital=float(bt.capital_won), equity=tuple(curve),
        trades=tuple(trades), signals=n_signals, skipped=dict(skipped),
    )  # fmt: skip


@dataclass(frozen=True)
class Summary:
    trades: int
    wins: int
    losses: int
    flats: int
    win_pct: float
    loss_pct: float
    flat_pct: float
    avg_win_pct: float | None
    avg_loss_pct: float | None
    payoff: float | None  # 평균 이익 ÷ |평균 손실|
    expectancy_pct: float | None  # 거래당 평균 수익률
    profit_factor: float | None  # 이익 합 ÷ |손실 합|
    avg_r: float | None
    total_return_pct: float
    cagr_pct: float | None
    mdd_pct: float  # 평가액 기준 최대 낙폭(음수)
    max_consec_losses: int
    avg_days: float | None
    exposure: float  # 평균 보유 종목 수 ÷ 동시 보유 상한
    reasons: Mapping[str, int]
    by_year: Mapping[int, float]  # 연도별 평가액 수익률(%)


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarize(res: BacktestResult, *, flat_band_pct: float, max_positions: int) -> Summary:
    rets = [t.ret_pct for t in res.trades]
    wins = [r for r in rets if r >= flat_band_pct]
    losses = [r for r in rets if r <= -flat_band_pct]
    n = len(rets)
    gain = sum(t.pnl for t in res.trades if t.pnl > 0)
    loss = -sum(t.pnl for t in res.trades if t.pnl < 0)
    streak = worst = 0
    for t in sorted(res.trades, key=lambda t: (t.exit_date, t.code)):
        streak = streak + 1 if t.ret_pct <= -flat_band_pct else 0
        worst = max(worst, streak)
    peak, mdd = res.capital, 0.0
    for _, v in res.equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    final = res.equity[-1][1] if res.equity else res.capital
    span = (res.end - res.start).days
    cagr = ((final / res.capital) ** (365 / span) - 1) * 100 if span > 0 and final > 0 else None
    by_year: dict[int, float] = {}
    prev = res.capital
    for y in sorted({d.year for d, _ in res.equity}):
        last = [v for d, v in res.equity if d.year == y][-1]
        by_year[y] = (last / prev - 1) * 100
        prev = last
    held_days = sum(t.days for t in res.trades)
    return Summary(
        trades=n, wins=len(wins), losses=len(losses), flats=n - len(wins) - len(losses),
        win_pct=len(wins) / n * 100 if n else 0.0, loss_pct=len(losses) / n * 100 if n else 0.0,
        flat_pct=(n - len(wins) - len(losses)) / n * 100 if n else 0.0,
        avg_win_pct=_mean(wins), avg_loss_pct=_mean(losses),
        payoff=(_mean(wins) or 0) / abs(_mean(losses) or 1) if wins and losses else None,
        expectancy_pct=_mean(rets), profit_factor=gain / loss if loss else None,
        avg_r=_mean([t.r_mult for t in res.trades]),
        total_return_pct=(final / res.capital - 1) * 100, cagr_pct=cagr, mdd_pct=mdd * 100,
        max_consec_losses=worst, avg_days=_mean([float(t.days) for t in res.trades]),
        exposure=held_days / (len(res.equity) * max_positions) if res.equity else 0.0,
        reasons=dict(Counter(REASONS[t.reason] for t in res.trades)), by_year=by_year,
    )  # fmt: skip
