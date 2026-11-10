"""시장폭(docs/metrics.md §6.2, docs/p3_design.md §4.4).

- 상승·보합·하락·미지: ET `board/engine/aggregate.py:breadth` 를 그대로 쓴다(import — 두 벌 금지).
  값이 없는 종목(등락률 None·invalid 행)은 **미지(unknown)** — 보합으로 넣지 않는다.
- 20일선 위 비율(%): 종가 > 최근 20영업일 종가 SMA20(오늘 포함)인 종목 ÷ SMA20 을 계산할 수 있는
  종목 × 100. 20봉 미만(창 안 종가가 20개 미만) 종목은 분모에서 뺀다.
- 시장별 N일선 위 비율(N = 20·60·120 — 2026-11-10 첫 화면, `ma_breadth`): 같은 규칙을 시장마다.
  원장 창(60영업일)이 120일선보다 짧아 지난 날 종가는 일봉 이력(`history`)으로 채운다 — 원장
  영업일이면 원장, 아니면 일봉(invalid·값 없음 뺌). 분자·분모 규칙은 20일선 위 비율과 같은 함수
  (`_above_ma`).
- 신고가 수·비율: 그날 보드 **종가 기준** 52주 이상 라벨(hist·w52) 수 ÷ 보드 유니버스 × 100.
- 상한가·하한가: 등락률 ≥ limit_move_pct / ≤ −limit_move_pct(`market.limit_move_pct` 29.5 [추정]).
- 대상: 코스피·코스닥 주식(`STOCK_KINDS`). 신저가는 KR 보드에 없어 P3 에 없다.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import Bar, Label
from kbj.engines.board.aggregate import breadth as board_breadth
from kbj.engines.flows.ledger import (
    MAIN_MARKETS,
    STOCK_KINDS,
    Ledger,
    LedgerRow,
    source_label,
    worst_quality,
)

__all__ = [
    "MA_DAYS",
    "MA_HISTORY_DAYS",
    "MA_WINDOWS",
    "NEWHIGH_KINDS",
    "Breadth",
    "MaShare",
    "MarketMaBreadth",
    "ma_breadth",
    "market_breadth",
]

MA_DAYS: Final = 20
MA_WINDOWS: Final[tuple[int, ...]] = (20, 60, 120)  # 시장별 N일선(metrics §6.2 — 첫 화면)
MA_HISTORY_DAYS: Final = max(MA_WINDOWS) + 1  # 일봉 이력 창(120일 + 원장에 아직 없는 날 하루)
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
    above, base = _above_ma(rows, window, day, MA_DAYS, _ledger_close(ledger))

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


# ── N일선 위 비율(공용 규칙) ──────────────────────────────────────────────────────────────


def _ledger_close(ledger: Ledger) -> Callable[[str, date], float | None]:
    def close(code: str, d: date) -> float | None:
        x = ledger.get(code, d)
        return x.close if x is not None and x.usable else None

    return close


def _above_ma(
    rows: Iterable[LedgerRow],
    window: Sequence[date],
    day: date,
    n: int,
    close_on: Callable[[str, date], float | None],
) -> tuple[int, int]:
    """(N일선 위 종목 수, 분모). 창이 그날로 끝나지 않거나 N날이 안 되면 (0, 0). 창 안 종가가 N개가
    안 되는 종목·그날 행을 쓸 수 없는 종목은 분모 밖(metrics §6.2)."""
    above = base = 0
    if len(window) < n or window[-1] != day:
        return 0, 0
    for r in rows:
        if not r.usable or r.close is None:
            continue
        closes = [c for d in window if (c := close_on(r.code, d)) is not None]
        if len(closes) < n:
            continue
        base += 1
        if r.close > sum(closes) / n:
            above += 1
    return above, base


@dataclass(frozen=True)
class MaShare:
    ma: int  # N(영업일)
    above: int
    base: int  # N일 평균을 낼 수 있는 종목
    pct: float | None  # above ÷ base × 100(분모 0 이면 None)


@dataclass(frozen=True)
class MarketMaBreadth:
    market: str  # KOSPI · KOSDAQ
    date: date
    shares: tuple[MaShare, ...]  # MA_WINDOWS 순
    quality: Quality | None  # 그날 쓴 원장 행 중 가장 나쁜 것
    source: str
    notes: tuple[str, ...] = ()


def ma_breadth(
    ledger: Ledger,
    day: date,
    history: Mapping[str, Sequence[Bar]],
    *,
    windows: Sequence[int] = MA_WINDOWS,
    markets: Sequence[str] = MAIN_MARKETS,
    kinds: Collection[str] = STOCK_KINDS,
) -> list[MarketMaBreadth]:
    """시장별 N일선 위 비율(metrics §6.2). `history` = 일봉 이력 {code: 오름차순}(원장 창 밖 날을
    채운다 — `repos.market.series`). 영업일 축 = 원장 영업일 ∪ 이력 날짜(그날 이하)."""
    in_ledger = set(ledger.trading_days)
    by_code: dict[str, dict[date, float]] = {}
    hist_days: set[date] = set()
    for code, bars in history.items():
        got: dict[date, float] = {}
        for b in bars:
            if b.date > day:
                continue
            hist_days.add(b.date)
            if b.date not in in_ledger and b.close is not None and b.quality.usable:
                got[b.date] = b.close
        if got:
            by_code[code] = got
    axis = sorted(d for d in in_ledger | hist_days if d <= day)
    lclose = _ledger_close(ledger)

    def close(code: str, d: date) -> float | None:
        if d in in_ledger:
            return lclose(code, d)
        return by_code.get(code, {}).get(d)

    out: list[MarketMaBreadth] = []
    for m in markets:
        rows = [r for r in ledger.day(day) if r.market == m and r.kind in kinds]
        shares: list[MaShare] = []
        notes: list[str] = []
        for n in windows:
            above, base = _above_ma(rows, axis[-n:], day, n, close)
            shares.append(MaShare(n, above, base, above / base * 100 if base else None))
            if len(axis) < n:
                notes.append(f"{n}일선 — 영업일이 {len(axis)}일뿐이다(일봉 이력 부족)")
        used = [r for r in rows if r.usable]
        srcs = [p for r in used for s in r.sources.values() for p in s.split("+")]
        out.append(
            MarketMaBreadth(
                market=m,
                date=day,
                shares=tuple(shares),
                quality=worst_quality(r.quality for r in used),
                source=source_label(srcs),
                notes=tuple(notes),
            )
        )
    return out
