"""컷오프 추정 — 시장 커버리지 방식(metrics §24.4).

MSCI 는 컷오프(Interim Market Size-Segment Cutoff)를 공표하지 않는다. 방법론의 커버리지 방식으로
추정한다:
전체 시총 내림차순으로 유동시총을 누적해 전체 유동시총의 coverage(설정 — 85%) 에 처음 닿는 종목의
**전체
시총**. 구간: coverage_high(더 넓게 덮음 → 더 낮은 컷오프)·coverage_low.

성질(속성 시험): 모든 시총을 k 배 하면 컷오프도 k 배(동차), coverage 가 오르면 컷오프는 오르지
않는다,
종목 하나의 시총이 오르면 그 종목의 '컷오프 이상' 여부는 나빠지지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Context, Decimal, localcontext
from typing import Final

from kbj.config.index_reviews import MsciRules
from kbj.engines.index_review.types import Cutoff

__all__ = ["coverage_cutoff", "estimate_cutoff"]

_CTX: Final = Context(prec=50)


def coverage_cutoff(items: Sequence[tuple[str, int, Decimal]], target: float) -> int | None:
    """items = (코드, 전체 시총, 유동시총). 유동시총이 0 이하인 것은 뺀다. 비면 None."""
    if not 0 < target < 1:
        raise ValueError("coverage 는 0~1")
    rows = sorted((x for x in items if x[1] > 0 and x[2] > 0), key=lambda x: (-x[1], x[0]))
    if not rows:
        return None
    with localcontext(_CTX):
        total = sum((x[2] for x in rows), Decimal(0))
        goal = Decimal(str(target)) * total
        cum = Decimal(0)
        for _, mcap, ff in rows:
            cum += ff
            if cum >= goal:
                return mcap
    return rows[-1][1]


def estimate_cutoff(items: Sequence[tuple[str, int, Decimal]], rules: MsciRules) -> Cutoff | None:
    mid = coverage_cutoff(items, rules.val("coverage"))
    if mid is None:
        return None
    return Cutoff(
        won=mid,
        low_won=coverage_cutoff(items, rules.val("coverage_high")),
        high_won=coverage_cutoff(items, rules.val("coverage_low")),
        method=f"coverage {rules.val('coverage'):.0%}",
        kind="model",
    )
