"""ETF 분할·병합 감지와 전날 행 보정(docs/metrics.md §8.2, §4 오류 1번 — docs/p3_design.md §4.3).

- 감지: Sₜ ÷ Sₜ₋₁ ≈ r **이고** NAVₜ₋₁ ÷ NAVₜ ≈ r (r = k 또는 1/k, k ∈ `config/markets.yaml`
  `etf.split_ratios`), 상대오차 `etf.split_tol`. 두 조건을 **함께** 본다 — 좌수만 10배로 늘고 NAV 가
  그대로면 큰 설정이지 분할이 아니다.
- 비율: ratio = 새 좌수 ÷ 옛 좌수(1:10 분할 = 10, 5:1 병합 = 0.2 — `kbj.core.rows.SplitEvent`).
- 보정(`adjust`): 전날 행을 Sₜ₋₁ × ratio, NAVₜ₋₁ ÷ ratio(종가도 ÷ ratio)로 바꾼다. 순자산은
  그대로(S×NAV 불변). 그 뒤 순유입·가격효과 공식을 그대로 쓴다(`kbj.engines.etf.flows`).
- 감지 결과는 `estimated` 다(`SplitCandidate.to_event` → origin=detected). 수동 표(origin=manual)가
  있으면 그것이 우선이고(`flows.window_flows`), 저장소도 manual 을 덮지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, SplitEvent

__all__ = [
    "DETECTED_SOURCE",
    "SPLIT_RATIOS",
    "SPLIT_TOL",
    "SplitCandidate",
    "adjust",
    "detect_split",
]

# 기본값은 config/markets.yaml `etf.split_tol`·`split_ratios` 와 같다 — 서비스는 설정값을 넘긴다
SPLIT_TOL: Final = 0.02
SPLIT_RATIOS: Final[tuple[int, ...]] = (2, 3, 4, 5, 10, 20, 50, 100)
DETECTED_SOURCE: Final = "engine:etf.splits"


@dataclass(frozen=True)
class SplitCandidate:
    """감지한 분할·병합 한 건. ratio = 새 좌수 ÷ 옛 좌수."""

    code: str
    effective_date: date
    ratio: float
    kind: Literal["split", "merge"]
    k: int  # 맞은 배수(병합이면 1/k 의 k)
    shares_ratio: float  # 관측 Sₜ ÷ Sₜ₋₁
    nav_ratio: float  # 관측 NAVₜ₋₁ ÷ NAVₜ

    def to_event(self, *, source: str = DETECTED_SOURCE) -> SplitEvent:
        word = "분할" if self.kind == "split" else "병합"
        what = f"1:{self.k}" if self.kind == "split" else f"{self.k}:1"
        note = (
            f"{what} {word} 감지 — 좌수 ×{self.shares_ratio:.4f}, NAV ÷{self.nav_ratio:.4f} "
            "(감지값 — 수동 표가 있으면 그것이 우선)"
        )
        return SplitEvent(
            code=self.code,
            effective_date=self.effective_date,
            ratio=self.ratio,
            origin="detected",
            note=note,
            source=source,
            quality=Quality.ESTIMATED,
        )


def _positive(d: EtfDay) -> tuple[int, float] | None:
    if d.list_shrs is None or d.nav is None or d.list_shrs <= 0 or d.nav <= 0:
        return None
    return d.list_shrs, d.nav


def detect_split(
    prev: EtfDay,
    cur: EtfDay,
    *,
    tol: float = SPLIT_TOL,
    ratios: Sequence[int] = SPLIT_RATIOS,
) -> SplitCandidate | None:
    """전날·오늘 행으로 분할·병합을 감지한다. 좌수·NAV 가 없거나 0 이하이면 None(판단 불가)."""
    if not 0 < tol < 0.5:
        raise ValueError("tol 은 0 과 0.5 사이")
    if prev.code != cur.code:
        raise ValueError(f"다른 ETF 의 행이다: {prev.code} ≠ {cur.code}")
    if prev.date >= cur.date:
        raise ValueError("prev 는 cur 보다 앞 날짜여야 한다")
    a, b = _positive(prev), _positive(cur)
    if a is None or b is None:
        return None
    s_ratio = b[0] / a[0]
    n_ratio = a[1] / b[1]
    best: tuple[float, float, int, Literal["split", "merge"]] | None = None
    for k in ratios:
        if k < 2:
            raise ValueError("배수는 2 이상")
        options: tuple[tuple[float, Literal["split", "merge"]], ...] = (
            (float(k), "split"),
            (1.0 / k, "merge"),
        )
        for r, kind in options:
            err = max(abs(s_ratio / r - 1.0), abs(n_ratio / r - 1.0))
            if err <= tol and (best is None or err < best[0]):
                best = (err, r, k, kind)
    if best is None:
        return None
    _, r, k, kind = best
    return SplitCandidate(cur.code, cur.date, r, kind, k, s_ratio, n_ratio)


def adjust(prev: EtfDay, ratio: float) -> EtfDay:
    """전날 행을 분할·병합 뒤 단위로 맞춘다: 좌수 × ratio(반올림 정수), NAV·종가 ÷ ratio."""
    if not ratio > 0:
        raise ValueError("ratio 는 0 보다 커야 한다")
    return replace(
        prev,
        list_shrs=None if prev.list_shrs is None else round(prev.list_shrs * ratio),
        nav=None if prev.nav is None else prev.nav / ratio,
        close=None if prev.close is None else prev.close / ratio,
    )
