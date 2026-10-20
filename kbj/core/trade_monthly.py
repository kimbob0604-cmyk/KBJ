"""월별 수출입 추이 — HS2(류) 쌓기·전월비·전년동월비·분기 QoQ·YoY(docs/metrics.md §29a).

순수 계산(I/O 없음). 입력은 (월, 류, 수출, 수입) 합 — 저장소 `TradeRepo.chapter_monthly` 가 HS10
행만 더해 만든다(§26 가장 깊은 행 규칙). 달이 없으면 0 이 아니라 그 달이 없는 것(비교값 None).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from kbj.core.trade_rows import ChapterMonth

__all__ = [
    "OTHER",
    "ChapterMonth",
    "MonthPoint",
    "Quarter",
    "Stack",
    "monthly_stack",
    "prev_ym",
    "quarter_of",
]

Flow = Literal["exp", "imp"]
OTHER: Final = "other"  # 쌓기의 '기타'(상위 류 밖)
TOP_WINDOW: Final = 12  # 상위 류를 고르는 최근 개월 수


@dataclass(frozen=True)
class MonthPoint:
    ym: str
    total: int
    parts: Mapping[str, int]  # 류(또는 OTHER) → 달러
    mom: float | None  # 전월비(비율 — 0.05 = +5%)
    yoy: float | None  # 전년동월비


@dataclass(frozen=True)
class Quarter:
    q: str  # YYYYQn
    total: int
    qoq: float | None
    yoy: float | None


@dataclass(frozen=True)
class Stack:
    flow: Flow
    chapters: tuple[str, ...]  # 쌓는 순서(큰 것부터) — 마지막이 OTHER
    months: tuple[MonthPoint, ...]
    quarters: tuple[Quarter, ...]  # 세 달이 다 있는 달력 분기만


def prev_ym(ym: str, n: int = 1) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    k = y * 12 + (m - 1) - n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def quarter_of(ym: str) -> str:
    return f"{ym[:4]}Q{(int(ym[5:7]) - 1) // 3 + 1}"


def _rate(cur: int, base: int | None) -> float | None:
    return None if base is None or base <= 0 else cur / base - 1


def monthly_stack(
    rows: Iterable[ChapterMonth], *, flow: Flow, top: int = 6, months: int = 36
) -> Stack:
    """월별 합계·상위 류 쌓기·비교율. months = 화면에 둘 최근 달 수(비교는 그 앞 달도 쓴다)."""
    if top < 1 or months < 1:
        raise ValueError("top·months 는 1 이상")
    by_month: dict[str, dict[str, int]] = {}
    for r in rows:
        v = r.exp_usd if flow == "exp" else r.imp_usd
        by_month.setdefault(r.ym, {})
        by_month[r.ym][r.chapter] = by_month[r.ym].get(r.chapter, 0) + int(v)
    yms = sorted(by_month)
    if not yms:
        return Stack(flow, (OTHER,), (), ())
    totals = {ym: sum(parts.values()) for ym, parts in by_month.items()}
    recent = yms[-TOP_WINDOW:]
    score: dict[str, int] = {}
    for ym in recent:
        for ch, v in by_month[ym].items():
            score[ch] = score.get(ch, 0) + v
    tops = tuple(sorted(score, key=lambda c: (-score[c], c))[:top])
    shown = yms[-months:]
    points: list[MonthPoint] = []
    for ym in shown:
        parts = {c: by_month[ym].get(c, 0) for c in tops}
        parts[OTHER] = totals[ym] - sum(parts.values())
        points.append(
            MonthPoint(
                ym=ym,
                total=totals[ym],
                parts=parts,
                mom=_rate(totals[ym], totals.get(prev_ym(ym))),
                yoy=_rate(totals[ym], totals.get(prev_ym(ym, 12))),
            )
        )
    q_tot: dict[str, int] = {}
    q_n: dict[str, int] = {}
    for ym in yms:
        q = quarter_of(ym)
        q_tot[q] = q_tot.get(q, 0) + totals[ym]
        q_n[q] = q_n.get(q, 0) + 1
    full = [q for q in sorted(q_tot) if q_n[q] == 3]
    quarters = [
        Quarter(q, q_tot[q], _rate(q_tot[q], _q_base(q, 1, q_tot, q_n)),
                _rate(q_tot[q], _q_base(q, 4, q_tot, q_n)))
        for q in full
        if quarter_of(shown[0]) <= q
    ]  # fmt: skip
    return Stack(flow, (*tops, OTHER), tuple(points), tuple(quarters))


def _q_base(q: str, back: int, q_tot: Mapping[str, int], q_n: Mapping[str, int]) -> int | None:
    y, n = int(q[:4]), int(q[5])
    k = y * 4 + (n - 1) - back
    key = f"{k // 4}Q{k % 4 + 1}"
    return q_tot[key] if q_n.get(key) == 3 else None
