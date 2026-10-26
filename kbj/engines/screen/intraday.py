"""cis 30분봉 백테스트(순수 계산) — 연습게임 규칙: 거래량 실린 박스 돌파에 사고, −2% 손절·추적 손절,
불타기 한 번, 물타기 없음(config/screen.yaml `cis30`).

흐름
- 감시: 일봉 신호일 D(`signals_of` — 스크리너와 같은 판정) 다음 거래일부터 `entry_days` 동안 그
  종목을 본다.
- 신호(30분봉 마감): 종가 > 직전 `box_bars` 봉 고가 최고, 양봉, 거래량 ≥ 직전 봉 평균 × `vol_mult`.
  09:00 봉(`skip_first_bar`)·그날 마지막 봉(다음 봉이 다음 날이라)은 사지 않는다. 직전 봉이 앞
  거래일보다
  오래되면(분봉이 끊긴 종목) 보지 않는다. 한 종목은 하루 한 번만 산다.
- 체결: 신호 다음 5분봉 시가. 수량 = 전날 평가액 × `risk_pct` ÷ (진입가 − 손절가), 비중·현금 안으로.
- 손절·청산(5분봉마다): 시가가 손절가 아래면 시가, 저가가 닿으면 손절가 — **같은 봉에서 손절이 새
  고가보다
  먼저**(보수적). 고가가 +`breakeven_at_pct` 를 넘으면 본전으로, 최고가 −`trail_pct` 가 더 높으면
  올린다.
  `overnight` 이 아니면 그날 마지막 종가, 보유 `max_hold_days` 거래일째 종가에 판다. 분봉이 더
  없으면
  마지막 종가(기간 끝).
- 불타기(한 번): 30분봉 종가 ≥ 진입가 × (1 + `add_at_pct`) 이고 보유 중 30분봉 종가 신고가면 다음
  5분봉 시가에 처음 수량 × `add_frac`(시가가 손절 위일 때만).
- 비용: `kbj.engines.backtest.costs`.

미래 참조 없음: 일봉 신호는 D 종가까지, 30분봉 신호는 그 봉 마감까지 값만 쓰고 다음 봉 시가에 산다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import floor

from kbj.config.screen import Cis30Cfg
from kbj.core.intraday_rows import IntraBar
from kbj.core.time import KST
from kbj.engines.backtest.costs import Costs, buy_cost, sell_proceeds
from kbj.engines.screen.backtest import BacktestResult, Reason, Signal, Trade

__all__ = ["Bar30", "Watch", "bars30", "fetch_pairs", "run_intraday", "watch_days"]

SESSION_MIN = 390  # 09:00 ~ 15:30


@dataclass(frozen=True)
class Watch:
    code: str
    signal_date: date
    vol_ratio: float


def fetch_pairs(
    signals: Mapping[date, Sequence[Signal]], days: Sequence[date], window: int
) -> list[tuple[str, date]]:
    """분봉을 받을 (종목, 거래일) — 신호일 D ~ D+window(오래된 날 먼저)."""
    idx = {d: i for i, d in enumerate(days)}
    out: set[tuple[str, date]] = set()
    for d, sigs in signals.items():
        i = idx.get(d)
        if i is None:
            continue
        for k in range(window + 1):
            if i + k < len(days):
                out.update((s.code, days[i + k]) for s in sigs)
    return sorted(out, key=lambda p: (p[1], p[0]))


def watch_days(
    signals: Mapping[date, Sequence[Signal]], days: Sequence[date], entry_days: int
) -> dict[date, list[Watch]]:
    """거래일 → 그날 감시 종목(D+1 ~ D+entry_days, 같은 종목은 최근 신호, 거래량 배수 큰 순)."""
    idx = {d: i for i, d in enumerate(days)}
    best: dict[date, dict[str, Watch]] = defaultdict(dict)
    for d in sorted(signals):
        i = idx.get(d)
        if i is None:
            continue
        for k in range(1, entry_days + 1):
            if i + k >= len(days):
                break
            for s in signals[d]:
                best[days[i + k]][s.code] = Watch(s.code, d, s.vol_ratio)
    return {d: sorted(ws.values(), key=lambda w: (-w.vol_ratio, w.code)) for d, ws in best.items()}


@dataclass(frozen=True)
class Bar30:
    day: date
    slot: int  # 0 = 09:00 봉
    last_slot: bool  # 그날 마지막 봉(15:30 동시호가 포함)
    first: int  # 5분봉 위치(처음)
    end: int  # 5분봉 위치(마지막)
    open: float
    high: float
    low: float
    close: float
    volume: int


def _slot(b: IntraBar, minutes: int) -> int:
    t = b.ts.astimezone(KST)
    m = (t.hour - 9) * 60 + t.minute
    return min(max(m, 0) // minutes, (SESSION_MIN - 1) // minutes)


def bars30(b5: Sequence[IntraBar], minutes: int = 30) -> list[Bar30]:
    """5분봉(시각 순) → n분봉. 15:30 동시호가 봉은 그날 마지막 봉에 넣는다."""
    out: list[Bar30] = []
    last = (SESSION_MIN - 1) // minutes
    i = 0
    while i < len(b5):
        day, slot = b5[i].trade_date, _slot(b5[i], minutes)
        j = i
        while j + 1 < len(b5) and b5[j + 1].trade_date == day and _slot(b5[j + 1], minutes) == slot:
            j += 1
        grp = b5[i : j + 1]
        out.append(
            Bar30(day, slot, slot == last, i, j, grp[0].open, max(b.high for b in grp),
                  min(b.low for b in grp), grp[-1].close, sum(b.volume for b in grp))
        )  # fmt: skip
        i = j + 1
    return out


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
    days: int = 0
    adds: int = 0


@dataclass
class _Book:
    code: str
    b5: Sequence[IntraBar]
    b30: list[Bar30]
    at30: dict[int, int]  # 5분봉 위치 → 그 봉이 끝나는 30분봉 위치
    by_day: dict[date, tuple[int, int]]  # 거래일 → 5분봉 [처음, 끝]


def _book(code: str, b5: Sequence[IntraBar], minutes: int) -> _Book:
    b30 = bars30(b5, minutes)
    by_day: dict[date, tuple[int, int]] = {}
    for k, b in enumerate(b5):
        lo, _ = by_day.get(b.trade_date, (k, k))
        by_day[b.trade_date] = (lo, k)
    return _Book(code, b5, b30, {x.end: j for j, x in enumerate(b30)}, by_day)


def _costs(cfg: Cis30Cfg) -> Costs:
    c = cfg.costs
    return Costs(fee_bps=c.fee_bps, sell_tax_pct=c.sell_tax_pct, slippage_bps=c.slippage_bps)


def _breakout(bk: _Book, j: int, prev_day: date | None, cfg: Cis30Cfg) -> bool:
    bar = bk.b30[j]
    n = cfg.box_bars
    if j < n or bar.last_slot or (cfg.skip_first_bar and bar.slot == 0):
        return False
    prior = bk.b30[j - n : j]
    if prev_day is None or prior[0].day < prev_day:
        return False  # 분봉이 끊겼다 — 박스를 모른다
    avg_v = sum(b.volume for b in prior) / n
    return (
        bar.close > max(b.high for b in prior)
        and bar.close > bar.open
        and avg_v > 0
        and bar.volume >= avg_v * cfg.vol_mult
    )


def run_intraday(
    bars5: Mapping[str, Sequence[IntraBar]],
    watch: Mapping[date, Sequence[Watch]],
    days: Sequence[date],
    cfg: Cis30Cfg,
    *,
    calendar: Sequence[date] | None = None,
) -> BacktestResult:
    """`days`(오름차순 거래일)를 하루씩, 그날 5분봉을 시각 순으로 굴린다.

    calendar = 앞 거래일을 찾을 전체 거래일(없으면 days) — 박스 직전 봉이 앞 거래일보다
    오래됐는지 본다.
    """
    if not days:
        raise ValueError("거래일이 없다")
    costs = _costs(cfg)
    books = {c: _book(c, b, cfg.bar_minutes) for c, b in bars5.items() if b}
    cash = float(cfg.capital_won)
    equity_prev = cash
    held: dict[str, _Pos] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    curve: list[tuple[date, float]] = []
    skipped: Counter[str] = Counter()
    n_signals = 0
    cal = sorted(calendar or days)
    prev_of = {d: cal[i - 1] for i, d in enumerate(cal) if i}

    def close_out(p: _Pos, d: date, px: float, reason: Reason, *, counted: bool = False) -> None:
        """counted = 그날 보유일을 이미 셌다(장 마감 뒤 청산)."""
        nonlocal cash
        proceeds = p.qty * sell_proceeds(px, costs)
        cash += proceeds
        pnl = proceeds - p.cost
        trades.append(
            Trade(code=p.code, entry_date=p.entry_date, exit_date=d, entry_px=p.entry_px,
                  exit_px=px, qty=p.qty, adds=p.adds, cost=p.cost, pnl=pnl,
                  ret_pct=pnl / p.cost * 100, r_mult=pnl / p.risk if p.risk else 0.0,
                  days=p.days if counted else p.days + 1, reason=reason)
        )  # fmt: skip
        del held[p.code]

    for d in days:
        prev_day = prev_of.get(d)
        watched = {w.code: w for w in watch.get(d, ()) if w.code in books}
        missing = sum(1 for w in watch.get(d, ()) if w.code not in books)
        if missing:
            skipped["분봉 없음(감시 종목)"] += missing
        active = [c for c in {*held, *watched} if d in books[c].by_day]
        events = sorted(
            (books[c].b5[k].ts, c, k)
            for c in active
            for k in range(books[c].by_day[d][0], books[c].by_day[d][1] + 1)
        )
        pending: dict[str, tuple[str, float]] = {}  # 종목 → ("entry", 돌파 봉 저가) | ("add", 수량)
        bought: set[str] = set()
        for _, code, k in events:
            bk, bar = books[code], books[code].b5[k]
            todo = pending.pop(code, None)
            p = held.get(code)
            if todo is not None and todo[0] == "add" and p is not None and bar.open > p.stop:
                add_cost = int(todo[1]) * buy_cost(bar.open, costs)
                if add_cost <= cash:
                    cash -= add_cost
                    p.qty += int(todo[1])
                    p.cost += add_cost
                    p.adds += 1
            elif todo is not None and todo[0] == "entry" and p is None:
                if len(held) >= cfg.max_positions:
                    skipped["보유 상한"] += 1
                else:
                    px = bar.open
                    pct = px * (1 - cfg.stop_pct / 100)
                    stop = todo[1] if cfg.stop_mode == "bar_low" and 0 < todo[1] < px else pct
                    per = px - stop
                    qty = floor(equity_prev * cfg.risk_pct / 100 / per)
                    qty = min(qty, floor(equity_prev * cfg.max_weight_pct / 100 / px))
                    qty = min(qty, floor(cash / buy_cost(px, costs)))
                    if qty < 1:
                        skipped["현금·비중 부족"] += 1
                    else:
                        cost = qty * buy_cost(px, costs)
                        cash -= cost
                        p = held[code] = _Pos(code, d, px, qty, qty, cost, qty * per, stop, "stop",
                                              px, px)  # fmt: skip
                        bought.add(code)
            if p is not None:
                if bar.open <= p.stop:
                    close_out(p, d, bar.open, p.kind)
                    p = None
                elif bar.low <= p.stop:
                    close_out(p, d, p.stop, p.kind)
                    p = None
                else:
                    p.high = max(p.high, bar.high)
                    if p.high >= p.entry_px * (1 + cfg.breakeven_at_pct / 100) and (
                        p.stop < p.entry_px
                    ):
                        p.stop, p.kind = p.entry_px, "breakeven"
                    trail = p.high * (1 - cfg.trail_pct / 100)
                    if trail > p.stop:
                        p.stop, p.kind = trail, "trail"
            last_close[code] = bar.close
            j = bk.at30.get(k)
            if j is None:
                continue  # 30분봉이 아직 끝나지 않았다
            b30 = bk.b30[j]
            if p is not None:
                new_high = b30.close > p.max_close
                p.max_close = max(p.max_close, b30.close)
                if (
                    cfg.add_frac > 0 and p.adds == 0 and new_high and not b30.last_slot
                    and b30.close >= p.entry_px * (1 + cfg.add_at_pct / 100)
                ):  # fmt: skip
                    pending[code] = ("add", float(max(1, floor(p.init_qty * cfg.add_frac))))
            elif code in watched and code not in bought and _breakout(bk, j, prev_day, cfg):
                n_signals += 1
                pending[code] = ("entry", b30.low)
        # 그날 끝 — 보유일·당일 청산·보유 기한·분봉 끝
        for p in list(held.values()):
            bk = books[p.code]
            if d in bk.by_day:
                p.days += 1
                c = bk.b5[bk.by_day[d][1]].close
                if not cfg.overnight:
                    close_out(p, d, c, "eod", counted=True)
                elif p.days >= cfg.max_hold_days:
                    close_out(p, d, c, "hold", counted=True)
            elif max(bk.by_day) < d:
                close_out(p, d, last_close.get(p.code, p.entry_px), "end", counted=True)
        equity_prev = cash + sum(p.qty * last_close.get(p.code, p.entry_px) for p in held.values())
        curve.append((d, equity_prev))
    end = days[-1]
    for p in list(held.values()):
        close_out(p, end, last_close.get(p.code, p.entry_px), "end", counted=True)
    if curve:
        curve[-1] = (end, cash)
    return BacktestResult(
        start=days[0], end=end, capital=float(cfg.capital_won), equity=tuple(curve),
        trades=tuple(trades), signals=n_signals, skipped=dict(skipped),
    )  # fmt: skip
