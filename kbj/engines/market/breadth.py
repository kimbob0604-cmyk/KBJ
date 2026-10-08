"""시장폭(docs/metrics.md §6.2, docs/p3_design.md §4.4).

- 상승·보합·하락·미지: ET `board/engine/aggregate.py:breadth` 를 그대로 쓴다(import — 두 벌 금지).
  값이 없는 종목(등락률 None·invalid 행)은 **미지(unknown)** — 보합으로 넣지 않는다.
- 20일선 위 비율(%): 종가 > 최근 20영업일 종가 SMA20(오늘 포함)인 종목 ÷ SMA20 을 계산할 수 있는
  종목 × 100. 20봉 미만(창 안 종가가 20개 미만) 종목은 분모에서 뺀다.
- 신고가 수·비율: 그날 보드 **종가 기준** 52주 이상 라벨(hist·w52) 수 ÷ 보드 유니버스 × 100.
- 상한가·하한가: 등락률 ≥ limit_move_pct / ≤ −limit_move_pct(`market.limit_move_pct` 29.5 [추정]).
- 대상: 코스피·코스닥 주식(`STOCK_KINDS`). 신저가는 KR 보드에 없어 P3 에 없다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import Label
from kbj.engines.board.aggregate import breadth as board_breadth
from kbj.engines.flows.ledger import (
    MAIN_MARKETS,
    STOCK_KINDS,
    Ledger,
    source_label,
    worst_quality,
)

__all__ = ["MA_DAYS", "NEWHIGH_KINDS", "Breadth", "market_breadth"]

MA_DAYS: Final = 20
NEWHIGH_KINDS: Final[frozenset[str]] = frozenset({"hist", "w52"})  # 52주 이상(metrics §6.2)


@dataclass(frozen=True)
class Breadth:
    date: date
    up: int
    flat: int
    down: int
    unknown: int
    total: int  # 등락률을 아는 종목
    above_ma20_n: int
    ma20_base: int  # SMA20 을 계산할 수 있는 종목
    above_ma20_pct: float | None
    newhigh_n: int
    newhigh_pct: float | None
    limit_up: int
    limit_down: int
    quality: Quality | None
    source: str
    notes: tuple[str, ...] = field(default_factory=tuple[str, ...])


def market_breadth(
    ledger: Ledger,
    day: date,
    labels: Iterable[Label],
    *,
    limit_move_pct: float = 29.5,
    board_universe: int | None = None,
    markets: Collection[str] = MAIN_MARKETS,
    kinds: Collection[str] = STOCK_KINDS,
) -> Breadth:
    """그날 시장폭. `labels` 는 보드 라벨(prv_board.label) — 종가 기준·그날 것만 센다."""
    if limit_move_pct <= 0:
        raise ValueError("limit_move_pct 는 0 보다 커야 한다")
    rows = [r for r in ledger.day(day) if r.market in markets and r.kind in kinds]
    items = [{"chg_pct": r.chg_pct if r.usable else None} for r in rows]
    b = board_breadth(items)

    window = ledger.days_upto(day, MA_DAYS)
    above = base = 0
    if window and window[-1] == day:
        for r in rows:
            closes = [
                x.close
                for d in window
                if (x := ledger.get(r.code, d)) is not None and x.usable and x.close is not None
            ]
            if len(closes) < MA_DAYS or not r.usable or r.close is None:
                continue
            base += 1
            if r.close > sum(closes) / MA_DAYS:
                above += 1

    known = [r.chg_pct for r in rows if r.usable and r.chg_pct is not None]
    lim_up = sum(1 for v in known if v >= limit_move_pct)
    lim_dn = sum(1 for v in known if v <= -limit_move_pct)

    day_labels = [
        lb for lb in labels if lb.date == day and lb.basis == "close" and lb.kind in NEWHIGH_KINDS
    ]
    newhigh_codes = {lb.code for lb in day_labels}
    nh_pct = (
        len(newhigh_codes) / board_universe * 100
        if board_universe is not None and board_universe > 0
        else None
    )
    notes: list[str] = []
    if board_universe is None:
        notes.append("보드 유니버스 수를 모른다 — 신고가 비율 없음")
    n_invalid = sum(1 for r in rows if not r.usable)
    if n_invalid:
        notes.append(f"invalid {n_invalid}행은 미지로 셌다")
    used = [r for r in rows if r.usable]
    qs = [r.quality for r in used] + [lb.quality for lb in day_labels]
    return Breadth(
        date=day,
        up=b["up"],
        flat=b["flat"],
        down=b["down"],
        unknown=b["unknown"],
        total=b["total"],
        above_ma20_n=above,
        ma20_base=base,
        above_ma20_pct=above / base * 100 if base else None,
        newhigh_n=len(newhigh_codes),
        newhigh_pct=nh_pct,
        limit_up=lim_up,
        limit_down=lim_dn,
        quality=worst_quality(qs),
        source=source_label(
            [p for r in used for s in r.sources.values() for p in s.split("+")]
            + [lb.source for lb in day_labels]
        ),
        notes=tuple(notes),
    )
