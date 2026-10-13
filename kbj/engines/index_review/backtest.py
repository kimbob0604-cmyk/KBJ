"""백테스트 채점 — 과거 리뷰의 예측 대 실제(발표) 편입·편출(metrics §24.8).

실제 = 효력일에 유효한 구성종목 − 기준일 구성종목(편입), 그 반대(편출). 예측 = 엔진 판정 add·delete
(경계는 예측에 넣지 않고 따로 센다). 적중 = 예측 ∩ 실제, 놓침 = 실제 − 예측, 오탐 = 예측 − 실제.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["Score", "score"]


@dataclass(frozen=True)
class Score:
    review_id: str
    side: str  # add·delete
    hits: tuple[str, ...]
    misses: tuple[str, ...]
    false_pos: tuple[str, ...]
    borderline_actual: tuple[str, ...]  # 경계로 본 종목 가운데 실제로 바뀐 것

    @property
    def precision(self) -> float | None:
        n = len(self.hits) + len(self.false_pos)
        return None if n == 0 else len(self.hits) / n

    @property
    def recall(self) -> float | None:
        n = len(self.hits) + len(self.misses)
        return None if n == 0 else len(self.hits) / n

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "review": self.review_id, "side": self.side, "hits": list(self.hits),
            "misses": list(self.misses), "false_pos": list(self.false_pos),
            "borderline_actual": list(self.borderline_actual),
            "precision": self.precision, "recall": self.recall,
        }  # fmt: skip


def score(
    review_id: str,
    side: str,
    predicted: Collection[str],
    actual: Collection[str],
    borderline: Collection[str] = (),
) -> Score:
    p, a, b = set(predicted), set(actual), set(borderline)
    return Score(
        review_id, side, tuple(sorted(p & a)), tuple(sorted(a - p)), tuple(sorted(p - a)),
        tuple(sorted(b & a)),
    )  # fmt: skip
