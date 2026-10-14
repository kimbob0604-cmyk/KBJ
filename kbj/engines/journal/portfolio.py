"""매매 기록 → 포지션·손익(docs/p8_design.md §4.7, ADR 0033 — 이동평균 단가).

규칙(돈은 Decimal 원 — float 로 바꾸지 않는다):
- 매수: 보유 원가 += 가격 × 수량 + 수수료 + 세금, 수량 += 수량. 평균 단가 = 보유 원가 ÷ 수량
  (매수 비용을 원가에 넣는다 [확인 필요 — SD 는 비용을 따로 뺐다]).
- 매도: 실현 손익 = (가격 − 평균 단가) × 수량 − 수수료 − 세금. 보유 원가는 평균 단가 × 남은 수량.
  보유 수량보다 많이 팔면 `NegativeHolding`(쓰기 모듈이 422 로 돌려준다 — 공매도 기록 없음).
- 보유일: 그 종목 보유가 0 에서 시작된 첫 매수일 → 매도일(매도 때), 또는 → 기준일(보유 중).
- 매매 순서: (매매일, id) 오름차순(같은 날은 넣은 순서) — 저장소 `trades()` 순서와 같다.
- 평가 손익: (최신 종가 − 평균 단가) × 수량. 종가가 없으면 None(지어내지 않는다).
- 월별: 실현 = 그 달 매도의 실현 합, 평가 변화 = 월말 평가 손익 − 전월말 평가 손익(보유 종목 중
  하나라도
  월말 종가가 없으면 None), 수익률 = (실현 + 평가 변화) ÷ (월초 보유 원가 + 그 달 매수 원가) × 100
  [확인 필요 — 현금 원장 없음]. 분모가 0 이면 None.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal, Protocol

__all__ = [
    "Book",
    "Lot",
    "Month",
    "NegativeHolding",
    "Realized",
    "TradeLike",
    "close_on",
    "monthly",
    "replay",
    "to_won",
    "unrealized",
]

ZERO: Final = Decimal(0)
HUNDRED: Final = Decimal(100)


class TradeLike(Protocol):
    """`kbj.core.rules_rows.TradeRow` 와 같은 칸(엔진은 이 칸만 읽는다)."""

    @property
    def id(self) -> int | None: ...
    @property
    def code(self) -> str: ...
    @property
    def side(self) -> Literal["buy", "sell"] | str: ...
    @property
    def trade_date(self) -> date: ...
    @property
    def price(self) -> Decimal: ...
    @property
    def qty(self) -> int: ...
    @property
    def fee(self) -> Decimal: ...
    @property
    def tax(self) -> Decimal: ...


class NegativeHolding(ValueError):
    """보유 수량보다 많이 판다(코드·날짜만 — 수량·가격은 문구에 넣지 않는다)."""

    def __init__(self, code: str, day: date) -> None:
        super().__init__(f"{code} {day.isoformat()} 매도 수량이 보유 수량보다 많다")
        self.code = code
        self.day = day


@dataclass
class Lot:
    """한 종목의 보유(수량·보유 원가·보유 시작일)와 누적 실현 손익."""

    qty: int = 0
    cost: Decimal = ZERO
    opened_on: date | None = None
    realized: Decimal = ZERO

    @property
    def avg_cost(self) -> Decimal | None:
        return None if self.qty == 0 else self.cost / self.qty


@dataclass(frozen=True)
class Realized:
    code: str
    day: date
    qty: int
    pnl: Decimal
    hold_days: int | None


@dataclass
class Book:
    lots: dict[str, Lot] = field(default_factory=dict[str, Lot])
    events: list[Realized] = field(default_factory=list[Realized])
    bought: dict[str, Decimal] = field(default_factory=dict[str, Decimal])  # 'YYYY-MM' → 매수 원가

    @property
    def realized_total(self) -> Decimal:
        return sum((lot.realized for lot in self.lots.values()), ZERO)

    def holdings(self) -> dict[str, Lot]:
        return {c: lot for c, lot in self.lots.items() if lot.qty > 0}


def _ordered(trades: Iterable[TradeLike]) -> list[TradeLike]:
    return sorted(trades, key=lambda t: (t.trade_date, t.id or 0))


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def replay(trades: Iterable[TradeLike], *, upto: date | None = None) -> Book:
    """매매를 순서대로 적용한다(upto 가 있으면 그날까지). 음수 보유는 `NegativeHolding`."""
    book = Book()
    for t in _ordered(trades):
        if upto is not None and t.trade_date > upto:
            break
        lot = book.lots.setdefault(t.code, Lot())
        if t.side == "buy":
            add = t.price * t.qty + t.fee + t.tax
            if lot.qty == 0:
                lot.opened_on = t.trade_date
            lot.qty += t.qty
            lot.cost += add
            m = _month(t.trade_date)
            book.bought[m] = book.bought.get(m, ZERO) + add
            continue
        if t.qty > lot.qty:
            raise NegativeHolding(t.code, t.trade_date)
        avg = lot.cost / lot.qty
        pnl = (t.price - avg) * t.qty - t.fee - t.tax
        hold = (t.trade_date - lot.opened_on).days if lot.opened_on is not None else None
        lot.realized += pnl
        lot.qty -= t.qty
        lot.cost = ZERO if lot.qty == 0 else avg * lot.qty
        if lot.qty == 0:
            lot.opened_on = None
        book.events.append(Realized(t.code, t.trade_date, t.qty, pnl, hold))
    return book


def close_on(series: Sequence[tuple[date, float]], day: date) -> tuple[date, float] | None:
    """오름차순 (날짜, 종가) 에서 day 이하 마지막 값(없으면 None)."""
    i = bisect_right([d for d, _ in series], day)
    return None if i == 0 else series[i - 1]


def unrealized(lot: Lot, close: float | None) -> Decimal | None:
    if lot.qty == 0:
        return ZERO
    if close is None or lot.avg_cost is None:
        return None
    return (Decimal(str(close)) - lot.avg_cost) * lot.qty


def to_won(x: Decimal) -> int:
    """원 단위 반올림(0.5 는 올림 — 표시용)."""
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Month:
    month: str
    realized: Decimal
    unrealized_change: Decimal | None
    ret_pct: float | None


def _month_end(y: int, m: int) -> date:
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return nxt - timedelta(days=1)


def _u_at(
    book: Book, closes: Mapping[str, Sequence[tuple[date, float]]], day: date
) -> Decimal | None:
    total = ZERO
    for code, lot in book.holdings().items():
        got = close_on(closes.get(code, ()), day)
        u = unrealized(lot, None if got is None else got[1])
        if u is None:
            return None
        total += u
    return total


def monthly(
    trades: Sequence[TradeLike],
    closes: Mapping[str, Sequence[tuple[date, float]]],
    *,
    asof: date,
) -> list[Month]:
    """첫 매매 달부터 기준일 달까지 월별 실현·평가 변화·수익률(오래된 달 먼저)."""
    ordered = _ordered(t for t in trades if t.trade_date <= asof)
    if not ordered:
        return []
    first = ordered[0].trade_date
    full = replay(ordered)
    out: list[Month] = []
    y, m = first.year, first.month
    prev_u: Decimal | None = ZERO
    prev_cost = ZERO
    while (y, m) <= (asof.year, asof.month):
        end = min(_month_end(y, m), asof)
        book = replay(ordered, upto=end)
        key = f"{y:04d}-{m:02d}"
        realized = sum((e.pnl for e in full.events if _month(e.day) == key), ZERO)
        u = _u_at(book, closes, end)
        change = None if u is None or prev_u is None else u - prev_u
        base = prev_cost + full.bought.get(key, ZERO)
        ret = None if change is None or base == 0 else float((realized + change) / base * HUNDRED)
        out.append(Month(key, realized, change, ret))
        prev_u = u
        prev_cost = sum((lot.cost for lot in book.holdings().values()), ZERO)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
