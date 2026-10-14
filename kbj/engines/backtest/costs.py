"""체결·비용(metrics §30.4 — D-P8-13).

- 비용: 매수 = 가격 × (1 + 수수료 + 슬리피지), 매도 = 가격 × (1 − 수수료 − 슬리피지 − 거래세).
  순수익률 =
  매도 순액 / 매수 총액 − 1. 값은 `config/rules.yaml costs`(수수료 bp·매도세 %·슬리피지 bp — 거래세
  [확인 필요]).
- 청산(`exit_after`): 진입 칸 다음 칸부터 보유 N 칸까지 장중 low/high 로 손절·익절을 보고(같은 칸에
  둘 다
  닿으면 **손절 먼저** — 보수적), 닿지 않으면 N 번째 칸 종가. 손절·익절 가격에 갭으로 넘어 열면 시가
  체결. 정지 칸(가격 None)은 건너뛴다(체결 없음 — 다음 유효 칸). 진입 칸 이후의 값만 읽는다.
- 진입(`next_open`): 마감 신호 d → d+1 의 첫 유효 시가(board `backtest.py` 미래참조 규칙).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = ["Costs", "Exit", "buy_cost", "exit_after", "net_return", "next_open", "sell_proceeds"]

ExitReason = Literal["stop", "target", "hold", "open_end"]


@dataclass(frozen=True)
class Costs:
    fee_bps: float = 1.5
    sell_tax_pct: float = 0.20
    slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.fee_bps < 0 or self.sell_tax_pct < 0 or self.slippage_bps < 0:
            raise ValueError("비용은 0 이상")

    @property
    def fee(self) -> float:
        return self.fee_bps / 10_000

    @property
    def slip(self) -> float:
        return self.slippage_bps / 10_000

    @property
    def tax(self) -> float:
        return self.sell_tax_pct / 100


def buy_cost(price: float, costs: Costs) -> float:
    """1주 매수 총액(가격 + 수수료 + 슬리피지)."""
    if price <= 0:
        raise ValueError("가격은 0 보다 커야 한다")
    return price * (1 + costs.fee + costs.slip)


def sell_proceeds(price: float, costs: Costs) -> float:
    """1주 매도 순액(가격 − 수수료 − 슬리피지 − 거래세)."""
    if price <= 0:
        raise ValueError("가격은 0 보다 커야 한다")
    return price * (1 - costs.fee - costs.slip - costs.tax)


def net_return(entry: float, exit_px: float, costs: Costs) -> float:
    """비용 반영 순수익률(비율 — 0.05 = 5%)."""
    return sell_proceeds(exit_px, costs) / buy_cost(entry, costs) - 1


def next_open(opens: Sequence[float | None], i: int) -> tuple[int, float] | None:
    """칸 i 다음의 첫 유효 시가(칸, 가격). 없으면 None(체결 없음)."""
    for k in range(i + 1, len(opens)):
        o = opens[k]
        if o is not None and o > 0:
            return k, o
    return None


@dataclass(frozen=True)
class Exit:
    index: int
    price: float
    reason: ExitReason
    bars: int  # 진입 칸부터 청산 칸까지 유효 칸 수(진입 칸 포함)


def exit_after(
    entry_index: int,
    entry_price: float,
    opens: Sequence[float | None],
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
    *,
    hold: int,
    stop_pct: float | None = None,
    target_pct: float | None = None,
    check_entry_bar: bool = False,
) -> Exit | None:
    """보유 `hold` 유효 칸(진입 칸 포함) 동안 손절·익절 → 아니면 마지막 칸 종가. 자료가 모자라면
    마지막 유효 칸 종가로 `open_end`. 진입 칸 이후 유효 칸이 없고 진입 칸 종가도 없으면 None.

    stop_pct·target_pct 는 % (예 −7 · 15). check_entry_bar=True 면 진입 칸 자체의 고저도 본다(시가
    진입은
    그 칸 장중 움직임에 노출된다 — BKT 가 정한다)."""
    if hold < 1:
        raise ValueError("hold 는 1 이상")
    if entry_price <= 0:
        raise ValueError("진입가는 0 보다 커야 한다")
    stop = None if stop_pct is None else entry_price * (1 + stop_pct / 100)
    target = None if target_pct is None else entry_price * (1 + target_pct / 100)
    m = len(closes)
    bars = 0
    last_valid: tuple[int, float] | None = None
    for k in range(entry_index, m):
        c = closes[k]
        if c is None:
            continue
        bars += 1
        last_valid = (k, c)
        if k > entry_index or check_entry_bar:
            o, h, lo = opens[k], highs[k], lows[k]
            if stop is not None and lo is not None and lo <= stop:
                px = o if (o is not None and k > entry_index and o < stop) else stop
                return Exit(k, px, "stop", bars)
            if target is not None and h is not None and h >= target:
                px = o if (o is not None and k > entry_index and o > target) else target
                return Exit(k, px, "target", bars)
        if bars >= hold:
            return Exit(k, c, "hold", bars)
    if last_valid is None:
        return None
    return Exit(last_valid[0], last_valid[1], "open_end", bars)
