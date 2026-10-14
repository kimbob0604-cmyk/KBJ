"""프레임 — 종목 하나의 일봉·수급(+ 장중 부분 봉) → 지표 배열 묶음(P8 묶음 FND, docs/p8_design.md
§4.2).

실시간(`rules.intraday`·`rules.close`)과 백테스트가 **같은 생성기**를 부른다(D-P8-6). 순수 계산 —
원장은 부르는 쪽이 저장소에서 골라 넘긴다(`MarketRepo.series` 의 원장 우선순위 그대로).

- 칸 = 거래일. `calendar` 를 주면 그 날짜가 칸이고 일봉이 없는 날은 가격 None(거래정지 — `halted`).
  주지 않으면 일봉 날짜가 칸이다.
- 시작점(anchor, D-P8-7): `max(anchor, 종목 첫 봉)` 부터 `upto` 까지. 지표는 이 시작점에서 한 번에
  계산하고(`Frame.series` — 메모) 실시간은 마지막 칸·백테스트는 각 칸을 읽는다.
- 장중(`partial`): `upto` 는 전 거래일, 부분 봉(`QuoteSlot`)을 오늘 칸으로 붙인다. 오늘 이후 일봉은
  버린다(미래참조 없음). 오늘 수급 칸 = `partial_flow`(KIS 가집계 잠정 — 사용자 결정 2026-10-13).
  없으면 수급 지표의 오늘 값은 None 이고 평가기가 '판단 불가'(`no_live_flow`)로 둔다.
- 분할 의심(D-P8-8): 이웃한 유효 종가 쌍을 board `split_guard`(정본 — 같은 문턱)로 보고, `prv_board.
  split_check` 의심일(`split_dates`)을 더한다. 의심 칸 s 이후 `split_window` 칸 동안(그 칸 포함)은
  `skip_reason = split_suspect` — s 이전 칸에는 영향이 없다(시점 규칙).
- 입력 지문 `input_fp(i)` = 칸 i 까지 마지막 `fp_cells` 칸 OHLCV·수급 값의 sha256 앞 16자(일치 감시
  — D-P8-14).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final

from kbj.core import indicators as ind
from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.core.rules_rows import QuoteSlot
from kbj.core.time import KST
from kbj.engines.board.newhigh import split_guard
from kbj.engines.flows.ledger import LedgerRow, Who, worst_quality
from kbj.engines.flows.streak import streak

__all__ = [
    "DEFAULT_FP_CELLS",
    "DEFAULT_SPLIT_WINDOW",
    "Frame",
    "SeriesKey",
    "build_frame",
]

DEFAULT_FP_CELLS: Final = 260
DEFAULT_SPLIT_WINDOW: Final = 260

SeriesKey = tuple[str, tuple[tuple[str, int | float | str], ...], str | None]
Out = list[float | None]


@dataclass(frozen=True)
class Frame:
    """종목 하나의 평가 입력. 칸 i 의 지표는 `series(ind, p, field)[i]`."""

    code: str
    dates: tuple[date, ...]
    open: tuple[float | None, ...]
    high: tuple[float | None, ...]
    low: tuple[float | None, ...]
    close: tuple[float | None, ...]
    volume: tuple[float | None, ...]
    quality: tuple[Quality, ...]
    sources: tuple[str, ...]
    flow_rows: tuple[LedgerRow | None, ...]
    partial: bool  # 마지막 칸이 장중 부분 봉인가
    live_flow: bool  # 부분 봉 칸의 수급이 가집계로 채워졌나(partial 이 아니면 False)
    halted: tuple[bool, ...]
    split_idx: tuple[int, ...]
    split_window: int
    fp_cells: int
    new_high: Mapping[date, frozenset[str]] | None = None
    hold_start: int | None = None
    flags: tuple[str, ...] = ()  # 부분 봉 상태(상·하한가·VI 등 — 근거용)
    _cache: dict[SeriesKey, Out] = field(
        default_factory=dict[SeriesKey, Out], compare=False, repr=False
    )

    # ── 칸 ──
    @property
    def n(self) -> int:
        return len(self.dates)

    @property
    def last(self) -> int:
        return len(self.dates) - 1

    def index_of(self, d: date) -> int | None:
        lo, hi = 0, len(self.dates)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.dates[mid] < d:
                lo = mid + 1
            else:
                hi = mid
        return lo if lo < len(self.dates) and self.dates[lo] == d else None

    def skip_reason(self, i: int) -> str | None:
        """칸 i 를 평가하지 않는 사유(split_suspect · halted) — 없으면 None."""
        if any(s <= i < s + self.split_window for s in self.split_idx):
            return "split_suspect"
        if self.halted[i]:
            return "halted"
        return None

    def quality_at(self, i: int, *, flows: bool) -> Quality:
        """칸 i 가격(과 수급을 쓰면 수급 행)의 가장 나쁜 품질."""
        qs = [self.quality[i]]
        if flows:
            r = self.flow_rows[i]
            if r is not None:
                qs.append(r.quality)
        return worst_quality(qs) or Quality.INVALID

    def input_fp(self, i: int) -> str:
        lo = max(0, i - self.fp_cells + 1)
        cells: list[list[object]] = []
        for j in range(lo, i + 1):
            r = self.flow_rows[j]
            cells.append(
                [
                    self.dates[j].isoformat(),
                    self.open[j], self.high[j], self.low[j], self.close[j], self.volume[j],
                    None if r is None else r.foreign,
                    None if r is None else r.inst,
                ]
            )  # fmt: skip
        text = json.dumps(cells, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    # ── 지표 ──
    def series(
        self, name: str, p: Mapping[str, int | float | str] | None = None, fld: str | None = None
    ) -> Out:
        """지표 배열(칸 수만큼). 같은 (지표, 파라미터, 칸)은 한 번만 계산한다."""
        params = dict(p or {})
        key: SeriesKey = (name, tuple(sorted(params.items())), fld)
        hit = self._cache.get(key)
        if hit is None:
            hit = self._compute(name, params, fld)
            self._cache[key] = hit
        return hit

    def _num(self, params: Mapping[str, int | float | str], k: str) -> int:
        v = params[k]
        if isinstance(v, str):
            raise TypeError(f"파라미터 {k} 는 수여야 한다")
        return int(v)

    def _compute(self, name: str, params: Mapping[str, int | float | str], fld: str | None) -> Out:
        c, h, lo, v = list(self.close), list(self.high), list(self.low), list(self.volume)
        if name in ("close", "open", "high", "low"):
            return list(getattr(self, name))
        if name == "change_pct":
            return ind.change_pct(c)
        if name == "sma":
            return ind.sma(c, self._num(params, "n"))
        if name == "ema":
            return ind.ema(c, self._num(params, "n"))
        if name == "rsi":
            return ind.rsi(c, self._num(params, "n"))
        if name == "macd":
            m = ind.macd(
                c, self._num(params, "fast"), self._num(params, "slow"), self._num(params, "signal")
            )
            return {"line": m.line, "signal": m.signal, "hist": m.hist}[fld or "line"]
        if name in ("bb", "bb_width_rank"):
            k = params["k"]
            if isinstance(k, str):
                raise TypeError("파라미터 k 는 수여야 한다")
            b = ind.bollinger(c, self._num(params, "n"), float(k))
            if name == "bb_width_rank":
                return ind.width_rank(b.width, self._num(params, "lookback"))
            if fld is None:
                raise ValueError("bb 는 칸(field)이 필요하다")
            return {"upper": b.upper, "mid": b.mid, "lower": b.lower, "pctb": b.pctb,
                    "width": b.width}[fld]  # fmt: skip
        if name == "adx":
            a = ind.adx(h, lo, c, self._num(params, "n"))
            return {"adx": a.adx, "pdi": a.pdi, "mdi": a.mdi}[fld or "adx"]
        if name == "atr":
            return ind.atr(h, lo, c, self._num(params, "n"))
        if name == "vol_ratio":
            return ind.vol_ratio(v, self._num(params, "n"))
        if name == "high_n":
            return ind.high_n(h, self._num(params, "n"))
        if name == "low_n":
            return ind.low_n(lo, self._num(params, "n"))
        if name == "trail_from_peak":
            return ind.trail_from_peak(h, c, self.hold_start)
        if name == "new_high":
            axis = str(params.get("axis", "52w"))
            if self.new_high is None:
                return [None] * self.n
            nh = self.new_high
            return [1.0 if axis in nh.get(d, frozenset()) else 0.0 for d in self.dates]
        if name in ("foreign_streak", "inst_streak"):
            who: Who = "foreign" if name.startswith("foreign") else "inst"
            return self._streaks(who, str(params.get("side", "buy")))
        if name in ("foreign_sum", "inst_sum"):
            who2: Who = "foreign" if name.startswith("foreign") else "inst"
            return self._sums(who2, self._num(params, "n"))
        raise ValueError(f"모르는 지표: {name}")

    def _streaks(self, who: Who, side: str) -> Out:
        rows: list[LedgerRow | None] = list(self.flow_rows)
        if side == "sell":
            # 매도 연속 = 부호를 뒤집은 원장의 연속 순매수(정본 `streak` 를 그대로 쓴다)
            flipped: list[LedgerRow | None] = []
            for r in rows:
                val = None if r is None else r.value(who)
                flipped.append(
                    None if r is None else replace(r, **{who: None if val is None else -val})
                )
            rows = flipped
        out: Out = [None] * self.n
        for i in range(self.n):
            if rows[i] is None:
                continue
            out[i] = float(streak(rows[i::-1], who))
        return out

    def _sums(self, who: Who, n: int) -> Out:
        out: Out = [None] * self.n
        for i in range(n - 1, self.n):
            total = 0
            ok = True
            for r in self.flow_rows[i - n + 1 : i + 1]:
                val = None if r is None or not r.usable else r.value(who)
                if val is None:
                    ok = False
                    break
                total += val
            if ok:
                out[i] = float(total)
        return out


def _split_indices(
    dates: Sequence[date], close: Sequence[float | None], ratio: float, extra: Collection[date]
) -> tuple[int, ...]:
    hits: set[int] = set()
    prev: tuple[int, float] | None = None
    for i, c in enumerate(close):
        if c is None:
            continue
        if prev is not None:
            j, pc = prev
            pair = [
                {"asof": dates[j].isoformat(), "close": pc},
                {"asof": dates[i].isoformat(), "close": c},
            ]
            if split_guard(pair, ratio)[0]:
                hits.add(i)
        prev = (i, c)
    for d in extra:
        k = next((i for i, x in enumerate(dates) if x >= d), None)
        if k is not None:
            hits.add(k)
    return tuple(sorted(hits))


def build_frame(
    code: str,
    bars: Sequence[Bar],
    *,
    upto: date,
    anchor: date,
    split_ratio: float,
    flows: Mapping[date, LedgerRow] | None = None,
    partial: QuoteSlot | None = None,
    partial_flow: LedgerRow | None = None,
    calendar: Sequence[date] | None = None,
    split_dates: Collection[date] = (),
    new_high: Mapping[date, frozenset[str]] | None = None,
    hold_since: date | None = None,
    split_window: int = DEFAULT_SPLIT_WINDOW,
    fp_cells: int = DEFAULT_FP_CELLS,
) -> Frame:
    """프레임을 만든다. `bars` 는 그 종목의 일봉(날짜 오름차순, 날짜마다 한 행 — 원장이 고른 것)."""
    today: date | None = None
    if partial is not None:
        if partial.code != code:
            raise ValueError("부분 봉의 종목이 다르다")
        today = partial.slot_ts.astimezone(KST).date()
        if today <= upto:
            raise ValueError("장중 부분 봉의 날짜는 upto(전 거래일) 뒤여야 한다")
    by_date: dict[date, Bar] = {}
    for b in bars:
        if b.code != code:
            raise ValueError("다른 종목의 일봉이 섞였다")
        if b.date in by_date:
            raise ValueError(f"같은 날 일봉이 둘이다: {b.date}")
        if b.date <= upto:
            by_date[b.date] = b
    first = min(by_date) if by_date else None
    start = max(anchor, first) if first is not None else anchor
    if calendar is not None:
        days = [d for d in calendar if start <= d <= upto]
    else:
        days = sorted(d for d in by_date if d >= start)
    fl = flows or {}
    o: list[float | None] = []
    h: list[float | None] = []
    lo: list[float | None] = []
    c: list[float | None] = []
    v: list[float | None] = []
    q: list[Quality] = []
    src: list[str] = []
    fr: list[LedgerRow | None] = []
    halted: list[bool] = []
    for d in days:
        b = by_date.get(d)
        if b is None:
            for col in (o, h, lo, c, v):
                col.append(None)
            q.append(Quality.INVALID)
            src.append("")
        else:
            o.append(b.open)
            h.append(b.high)
            lo.append(b.low)
            c.append(b.close)
            v.append(None if b.volume is None else float(b.volume))
            q.append(b.quality)
            src.append(b.source)
        halted.append(b is None or b.close is None)
        fr.append(fl.get(d))
    flags: tuple[str, ...] = ()
    live_flow = False
    if partial is not None and today is not None:
        days.append(today)
        o.append(partial.open)
        h.append(partial.high)
        lo.append(partial.low)
        c.append(partial.price)
        v.append(None if partial.volume is None else float(partial.volume))
        q.append(partial.quality)
        src.append(partial.source)
        halted.append(partial.price is None or "halted" in partial.flags)
        if partial_flow is not None and partial_flow.date != today:
            raise ValueError("장중 수급 행의 날짜가 부분 봉과 다르다")
        fr.append(partial_flow)
        live_flow = partial_flow is not None
        flags = partial.flags
    hold_start: int | None = None
    if hold_since is not None:
        hold_start = next((i for i, d in enumerate(days) if d >= hold_since), None)
    return Frame(
        code=code,
        dates=tuple(days),
        open=tuple(o),
        high=tuple(h),
        low=tuple(lo),
        close=tuple(c),
        volume=tuple(v),
        quality=tuple(q),
        sources=tuple(src),
        flow_rows=tuple(fr),
        partial=partial is not None,
        live_flow=live_flow,
        halted=tuple(halted),
        split_idx=_split_indices(days, c, split_ratio, split_dates),
        split_window=split_window,
        fp_cells=fp_cells,
        new_high=new_high,
        hold_start=hold_start,
        flags=flags,
    )
