"""엔진 입출력 자료형(순수 — kbj.core 만)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.index_rows import CutoffKind, RunStatus

__all__ = ["OUTCOME_ORDER", "Cutoff", "IndexResult", "Outcome", "StockIn", "StockOut"]

# none = 후보 아님(비구성종목이 문턱에서 멀다 — 저장하지 않는다)
Outcome = Literal["add", "delete", "borderline", "keep", "none"]
# 같은 종목에서 '좋아지는' 순서(시총이 오르면 이 순서로 나빠지지 않는다 — 속성 시험)
OUTCOME_ORDER: Final[dict[str, int]] = {
    "delete": 0, "none": 0, "borderline": 1, "keep": 2, "add": 2,
}  # fmt: skip


@dataclass(frozen=True)
class StockIn:
    """기준일 종목 한 줄.

    mktcap = 전체 시총(원), adv_won = 일평균 거래대금(원, 설정 창), fif_raw = 추정 유동비율(반올림
    전 —
    DART 지분 공시 등), fif_prev = 지수 운용사가 직전에 적용한 유동비율(구성종목 목록에 있으면 —
    완충 기준),
    member = 기준일 현 구성종목."""

    code: str
    name: str | None
    market: str | None
    kind: str | None
    sector: str | None
    listed_on: date | None
    mktcap: int | None
    adv_won: float | None
    fif_raw: float | None
    fif_prev: float | None
    fif_source: str | None
    flags: tuple[str, ...] | None
    member: bool


@dataclass(frozen=True)
class Cutoff:
    """컷오프(원). model = 엔진 추정(구간 low~high), user = 사용자가 넣은 증권사 추정치."""

    won: int
    low_won: int | None
    high_won: int | None
    method: str
    kind: CutoffKind


@dataclass(frozen=True)
class StockOut:
    """종목 판정. 괴리율 = 값 ÷ 문턱 − 1(문턱 = 배수 × 컷오프 — 구성종목은 유지 문턱, 아니면 편입
    문턱).

    required_rise = 조건(시총·유동 둘 다)을 맞추는 최소 주가 변화율(소수 — 1bp 올림, 음수면 그만큼
    떨어져도 조건 유지). basis = 둘 중 더 큰 쪽."""

    code: str
    name: str | None
    member: bool
    outcome: Outcome
    mktcap: int | None
    ff_mcap: int | None
    fif: float | None
    fif_raw: float | None
    fif_how: str | None
    gap_mcap: float | None
    gap_ff: float | None
    condition_met: bool | None
    required_rise: float | None
    required_basis: Literal["mcap", "ff"] | None
    threshold_mcap: int | None
    threshold_ff: int | None
    rank: int | None
    passive_won: int | None
    adv_won: int | None
    adv_multiple: float | None
    reasons: tuple[str, ...]
    estimated: bool


@dataclass(frozen=True)
class IndexResult:
    status: RunStatus
    cutoff: Cutoff | None
    stocks: tuple[StockOut, ...]
    n_members: int
    notes: tuple[str, ...]
    estimated: bool

    def by_outcome(self, outcome: Outcome) -> list[StockOut]:
        return [s for s in self.stocks if s.outcome == outcome]
