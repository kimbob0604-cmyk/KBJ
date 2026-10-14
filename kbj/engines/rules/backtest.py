"""규칙 백테스트 — 같은 프레임·평가기·쿨다운으로 시점을 하나씩 넘긴다(P8 묶음 BKT, docs/p8_design.md
§4.6·D-P8-6·D-P8-13, metrics §30.4).

순수 계산(저장소·어댑터 없음 — 계약 ⑨). 실시간(`rules.intraday`·`rules.close`)과 **같은 함수**를
부른다: `FrameInputs` → `build_frame` → `evaluate(rule, frame, i)` → `CooldownBook`. 별도 '벡터화
판' 은 없다.

- 모드 `close`: 거래일 d 마다(ts = `live.close_eval_ts(d)` — 실시간 마감 신호와 같은 평가 시각,
  쿨다운·일치 감시 키가 같다) 확정 프레임의 칸 d 를 평가. 신호 → **d+1 의 첫 유효 시가 체결**
  (board 미래참조 규칙), 진입 칸 장중 고저도 손절·익절에 노출된다.
- 모드 `intraday_replay`: 저장된 슬롯(`watch_quote_slot`)마다 전 거래일까지의 확정 일봉 + 그 슬롯
  부분 봉(+ 그 회차 가집계 — 없으면 수급 조건 '판단 불가', 사용자 결정 2026-10-13)으로 프레임을
  다시 만들어 마지막 칸을 평가. 신호 → 슬롯 현재가 체결, 손절·익절은 다음 거래일부터(그날 슬롯 뒤
  고저는 모른다 — 보수적으로 보지 않는다).
- `both`: 두 모드의 사건을 시각 순으로 섞어 **하나의** 쿨다운 상태기로(장중·마감이 같은 쿨다운).
- 청산·비용·거래 통계는 `kbj.engines.backtest`(공용 — costs·stats). 거래는 신호마다 하나(동시
  보유 허용), 곡선은 청산일 순 거래 단위 복리(표시용 — MDD 와 같은 정의).
- 신호 성과(1·3·5·10·20일)는 `outcome.signal_outcomes`(실제 알림 성과와 같은 함수)로 — '백테스트
  기대' 칸.
- 시점 규칙(속성 시험 `test_backtest_props.py`): 칸 d 의 판정·쿨다운은 d 이후 봉에 무관하다
  (평가기는 칸 ≤ d 만 읽고, 상태기는 ts 이전 신호만 본다). 청산 값은 당연히 진입 뒤 봉을 읽는다.
- 표본 = 원장 보유 구간(상장폐지 종목도 원장에 있으면 포함 — 부르는 쪽이 PIT 유니버스로 고른다).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar
from kbj.core.rules_rows import QuoteSlot
from kbj.core.time import KST
from kbj.engines.backtest.costs import Costs, exit_after, net_return, next_open
from kbj.engines.backtest.stats import TradeResult, TradeStats, trade_stats
from kbj.engines.flows.ledger import LedgerRow, worst_quality
from kbj.engines.rules.cooldown import CooldownBook, PriorSignal
from kbj.engines.rules.dsl import Rule
from kbj.engines.rules.evaluate import Verdict, evaluate
from kbj.engines.rules.frame import DEFAULT_FP_CELLS, DEFAULT_SPLIT_WINDOW, Frame, build_frame
from kbj.engines.rules.live import close_eval_ts
from kbj.engines.rules.outcome import OutcomeCalc, signal_outcomes
from kbj.engines.rules.stats import StatsCellCalc, stats_cells

__all__ = [
    "BacktestOut",
    "BacktestTimeout",
    "BtSignal",
    "BtTrade",
    "CodeData",
    "EvalMode",
    "FrameInputs",
    "TradeParams",
    "modes_of",
    "run_backtest",
]

EvalMode = Literal["close", "intraday_replay"]


def modes_of(rule: Rule) -> tuple[EvalMode, ...]:
    """규칙 모드 → 백테스트 모드(`both` 는 둘 다 — 같은 상태기)."""
    if rule.mode == "close":
        return ("close",)
    if rule.mode == "intraday":
        return ("intraday_replay",)
    return ("close", "intraday_replay")


@dataclass(frozen=True)
class FrameInputs:
    """종목 하나의 프레임 입력 — 실시간·백테스트가 `build_frame` 에 같은 인자를 넘기게 묶는다."""

    code: str
    bars: Sequence[Bar]
    anchor: date
    split_ratio: float
    flows: Mapping[date, LedgerRow] = field(default_factory=dict[date, LedgerRow])
    calendar: Sequence[date] | None = None
    split_dates: Collection[date] = ()
    new_high: Mapping[date, frozenset[str]] | None = None
    hold_since: date | None = None
    split_window: int = DEFAULT_SPLIT_WINDOW
    fp_cells: int = DEFAULT_FP_CELLS

    def close_frame(self, upto: date) -> Frame:
        """upto 까지 확정 일봉 프레임(마감 평가 — 칸 d 를 읽는다)."""
        return build_frame(
            self.code, self.bars, upto=upto, anchor=self.anchor, split_ratio=self.split_ratio,
            flows=self.flows, calendar=self.calendar, split_dates=self.split_dates,
            new_high=self.new_high, hold_since=self.hold_since, split_window=self.split_window,
            fp_cells=self.fp_cells,
        )  # fmt: skip

    def prev_day(self, d: date) -> date | None:
        """d 직전 거래일(달력이 있으면 달력, 없으면 일봉 날짜)."""
        src = self.calendar if self.calendar is not None else [b.date for b in self.bars]
        prev = [x for x in src if x < d]
        return max(prev) if prev else None

    def intraday_frame(self, slot: QuoteSlot, live_flow: LedgerRow | None) -> Frame | None:
        """전 거래일까지 확정 일봉 + 슬롯 부분 봉(+ 가집계) 프레임. 전 거래일이 없으면 None."""
        prev = self.prev_day(slot.slot_ts.astimezone(KST).date())
        if prev is None:
            return None
        return build_frame(
            self.code, self.bars, upto=prev, anchor=self.anchor, split_ratio=self.split_ratio,
            flows=self.flows, partial=slot, partial_flow=live_flow, calendar=self.calendar,
            split_dates=self.split_dates, new_high=self.new_high, hold_since=self.hold_since,
            split_window=self.split_window, fp_cells=self.fp_cells,
        )  # fmt: skip


@dataclass(frozen=True)
class CodeData:
    """백테스트 한 종목의 입력. `slots`·`live_flows` 는 장중 재생용(슬롯 시각 → 가집계 잠정 행)."""

    inputs: FrameInputs
    slots: Sequence[QuoteSlot] = ()
    live_flows: Mapping[datetime, LedgerRow] = field(default_factory=dict[datetime, LedgerRow])
    index_bars: Mapping[date, IndexBar] | None = None  # 시장 지수(성과 초과 — 없으면 None)
    listed: bool = True

    @property
    def code(self) -> str:
        return self.inputs.code


@dataclass(frozen=True)
class TradeParams:
    hold: int = 5
    stop_pct: float | None = None
    target_pct: float | None = None
    costs: Costs = field(default_factory=Costs)

    def __post_init__(self) -> None:
        if self.hold < 1:
            raise ValueError("hold 는 1 이상")
        if self.stop_pct is not None and self.stop_pct >= 0:
            raise ValueError("stop_pct 는 음수(%)")
        if self.target_pct is not None and self.target_pct <= 0:
            raise ValueError("target_pct 는 양수(%)")


@dataclass(frozen=True)
class BtSignal:
    """백테스트 신호(규칙이 참인 시점). status = dry(허락) · cooldown(막힘)."""

    code: str
    trade_date: date
    eval_ts: datetime
    mode: EvalMode
    entry_price: float | None
    status: Literal["dry", "cooldown"]
    input_fp: str
    quality: Quality
    outcomes: tuple[OutcomeCalc, ...] = ()

    @property
    def rets(self) -> dict[str, float | None]:
        return {f"h{o.horizon}": o.ret for o in self.outcomes}


@dataclass(frozen=True)
class BtTrade:
    code: str
    signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    reason: Literal["stop", "target", "hold", "open_end"]
    ret_pct: float  # 비용 반영 순수익(%)
    bars: int


@dataclass(frozen=True)
class BacktestOut:
    signals: tuple[BtSignal, ...]
    trades: tuple[BtTrade, ...]
    stats: TradeStats
    outcome: tuple[StatsCellCalc, ...]
    curve: tuple[tuple[date, float], ...]
    evaluated: int  # 평가한 (종목, 시점) 수
    skipped: Mapping[str, int]  # 평가 못 한 칸·판단 불가 사유별 수
    quality: Quality


class BacktestTimeout(RuntimeError):
    """`should_stop` 이 참 — 즉석 백테스트 시간 상한(부르는 쪽이 503 으로)."""


@dataclass(frozen=True)
class _Event:
    ts: datetime
    code: str
    mode: EvalMode
    day: date
    slot: QuoteSlot | None = None


def _events(
    data: Mapping[str, CodeData],
    frames: Mapping[str, Frame],
    modes: Sequence[EvalMode],
    start: date,
    end: date,
) -> list[_Event]:
    ev: list[_Event] = []
    for code, cd in data.items():
        if "close" in modes:
            fr = frames[code]
            for d in fr.dates:
                if start <= d <= end:
                    ev.append(_Event(close_eval_ts(d), code, "close", d))
        if "intraday_replay" in modes:
            for s in cd.slots:
                d = s.slot_ts.astimezone(KST).date()
                if start <= d <= end:
                    ev.append(_Event(s.slot_ts, code, "intraday_replay", d, s))
    ev.sort(key=lambda e: (e.ts, e.code))
    return ev


def _verdict(
    rule: Rule, ev: _Event, cd: CodeData, frame: Frame
) -> tuple[Verdict | None, Frame | None]:
    if ev.mode == "close":
        i = frame.index_of(ev.day)
        return (None, None) if i is None else (evaluate(rule, frame, i), frame)
    if ev.slot is None:  # pragma: no cover — _events 가 채운다
        raise ValueError("장중 사건에 슬롯이 없다")
    fr = cd.inputs.intraday_frame(ev.slot, cd.live_flows.get(ev.slot.slot_ts))
    if fr is None:
        return None, None
    return evaluate(rule, fr, fr.last), fr


def _trade(sig: BtSignal, ev: _Event, frame: Frame, params: TradeParams) -> BtTrade | None:
    i = frame.index_of(ev.day)
    if i is None:
        return None
    if ev.mode == "close":
        nxt = next_open(frame.open, i)
        if nxt is None:
            return None
        k, px = nxt
        check_entry = True
    else:
        if sig.entry_price is None:
            return None
        k, px, check_entry = i, sig.entry_price, False
    ex = exit_after(
        k, px, frame.open, frame.high, frame.low, frame.close, hold=params.hold,
        stop_pct=params.stop_pct, target_pct=params.target_pct, check_entry_bar=check_entry,
    )  # fmt: skip
    if ex is None:
        return None
    ret = net_return(px, ex.price, params.costs) * 100
    return BtTrade(sig.code, ev.day, frame.dates[k], px, frame.dates[ex.index], ex.price,
                   ex.reason, ret, max(1, ex.bars))  # fmt: skip


def _outcomes(
    sig: BtSignal, frame: Frame, cd: CodeData, horizons: Sequence[int], split_ratio: float
) -> tuple[OutcomeCalc, ...]:
    i = frame.index_of(sig.trade_date)
    if i is None or sig.entry_price is None or not horizons:
        return ()
    bars = {b.date: b for b in cd.inputs.bars if b.date >= sig.trade_date}
    got = signal_outcomes(
        entry_price=sig.entry_price, days=frame.dates[i:], bars=bars, index_bars=cd.index_bars,
        horizons=horizons, upto=frame.dates[-1], split_ratio=split_ratio, listed=cd.listed,
    )  # fmt: skip
    return tuple(got)


def run_backtest(
    rule: Rule,
    data: Mapping[str, CodeData],
    *,
    rule_id: str,
    start: date,
    end: date,
    modes: Sequence[EvalMode],
    cooldown_min: int,
    params: TradeParams | None = None,
    horizons: Sequence[int] = (),
    min_samples: int = 20,
    prior: Sequence[PriorSignal] = (),
    should_stop: Callable[[], bool] | None = None,
) -> BacktestOut:
    """규칙 하나를 [start, end] 에 돌린다. `data` = 종목 → 입력(확정 일봉은 end 뒤까지 있어도 된다 —
    청산·성과용, 판정은 칸 ≤ d 만 읽는다). `prior` = start 전 신호(쿨다운 이어받기 — 보통 비움)."""
    if start > end:
        raise ValueError("start > end")
    if not modes:
        raise ValueError("modes 가 비었다")
    tp = params or TradeParams()
    frames: dict[str, Frame] = {}
    for code, cd in data.items():
        if cd.code != code:
            raise ValueError(f"입력 키와 종목이 다르다: {code}")
        last = max((b.date for b in cd.inputs.bars), default=end)
        frames[code] = cd.inputs.close_frame(max(last, end))
    book = CooldownBook(prior)
    signals: list[BtSignal] = []
    trades: list[BtTrade] = []
    skipped: dict[str, int] = {}
    qualities: list[Quality] = []
    evaluated = 0
    for ev in _events(data, frames, modes, start, end):
        if should_stop is not None and should_stop():
            raise BacktestTimeout("백테스트 시간 상한")
        cd = data[ev.code]
        v, fr = _verdict(rule, ev, cd, frames[ev.code])
        if v is None or fr is None:
            continue
        evaluated += 1
        if v.state in ("skipped", "unknown"):
            why = v.skipped_reason or v.state
            skipped[why] = skipped.get(why, 0) + 1
            continue
        if not v.ok:
            continue
        d = book.step(rule_id=rule_id, code=ev.code, ts=ev.ts, cooldown_min=cooldown_min)
        if ev.mode == "close":
            entry = fr.close[v.index]
        else:
            entry = ev.slot.price if ev.slot is not None else None
        sig = BtSignal(ev.code, ev.day, ev.ts, ev.mode, entry, "dry" if d.allowed else "cooldown",
                       v.input_fp, v.quality)  # fmt: skip
        if d.allowed:
            full = frames[ev.code]
            outs = _outcomes(sig, full, cd, horizons, cd.inputs.split_ratio)
            sig = BtSignal(sig.code, sig.trade_date, sig.eval_ts, sig.mode, sig.entry_price,
                           sig.status, sig.input_fp, sig.quality, outs)  # fmt: skip
            t = _trade(sig, ev, full, tp)
            if t is not None:
                trades.append(t)
        signals.append(sig)
        qualities.append(v.quality)
    trades.sort(key=lambda t: (t.exit_date, t.code, t.signal_date))
    st = trade_stats(
        [TradeResult(t.ret_pct, t.bars, t.reason, t.reason == "target") for t in trades]
    )
    curve: list[tuple[date, float]] = []
    eq = 1.0
    for t in trades:
        eq *= 1 + t.ret_pct / 100
        curve.append((t.exit_date, eq))
    outs_all = [o for s in signals if s.status == "dry" for o in s.outcomes]
    cells = stats_cells(outs_all, horizons, min_samples=min_samples) if horizons else []
    q = worst_quality(qualities) or Quality.OK
    return BacktestOut(tuple(signals), tuple(trades), st, tuple(cells), tuple(curve), evaluated,
                       skipped, q)  # fmt: skip
