"""진단 응답 모델(docs/p7_design.md §5, metrics §D0~§D9 — `/api/diag/*`, 로그인). 모양은 웨이브 1
(FND-B)이 고정한다. 바꿔야 하면 FND 담당에게 요청 — DIAG 는 리더 본문만.

- 공개 지표(공공지표 진단 — tier public)와 로그인 지표(시장 진단 — tier login)를 같은 모양으로
  싣는다(`DiagIndicator.tier`). 공개 정적 사이트는 공개 지표·게이지·이벤트만 내보낸다(§7 — 이 모델의
  로그인 칸 `verdict`·`fear_greed` 는 공개 내보내기에 없다).
- 값이 없으면 None(0 으로 채우지 않는다), 행마다 quality·reasons, 봉투가 source·as_of.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "DiagEvent",
    "DiagEvents",
    "DiagFearGreed",
    "DiagGauge",
    "DiagHistory",
    "DiagHistoryPoint",
    "DiagIndicator",
    "DiagSummary",
    "DiagVerdict",
    "EventKindT",
    "VerdictLabelT",
]

StateT = Literal["extreme_low", "low", "normal", "high", "extreme_high", "narrow", "wide"]
BandT = Literal["extreme_fear", "fear", "neutral", "greed", "extreme_greed"]
VerdictLabelT = Literal["risk_on", "neutral", "risk_off", "hold"]
EventKindT = Literal["expiry", "mpc", "export_flash", "earnings_cluster"]
TierT = Literal["public", "login"]


class DiagIndicator(ApiModel):
    """지표 카드 하나(§D1~§D6). state None = 이력 부족(estimated)."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    tier: TierT
    value: float | None
    unit: str
    state: StateT | None
    pct_rank: float | None = Field(default=None, ge=0, le=100)
    score: Literal[-1, 0, 1] | None = None  # 상태 점수(§D0 — 위험 선호 방향 +1)
    flags: list[str] = Field(default_factory=list[str])  # overheat·fragile 등(점수 밖)
    quality: Quality
    reasons: list[str] = Field(default_factory=list[str])


class DiagGauge(ApiModel):
    """매크로 위험 게이지(공개 판정 — §D4)."""

    label: VerdictLabelT
    score: int | None
    parts: dict[str, int]
    quality: Quality
    reasons: list[str] = Field(default_factory=list[str])


class DiagFearGreed(ApiModel):
    """국장 공포탐욕(§D7). components 는 0~100(뒤집은 뒤), 빠진 성분 None."""

    score: float | None = Field(default=None, ge=0, le=100)
    band: BandT | None
    components: dict[str, float | None]
    substitutes: list[str] = Field(default_factory=list[str])  # 예 '대체: ATM IV'
    quality: Quality
    reasons: list[str] = Field(default_factory=list[str])


class DiagVerdict(ApiModel):
    """종합 판정(§D8). top = 기여 큰 셋(부호·이름)."""

    label: VerdictLabelT
    score: int | None
    parts: dict[str, int]
    top: list[str] = Field(default_factory=list[str])
    event_flags: list[EventKindT] = Field(default_factory=list[EventKindT])
    quality: Quality
    reasons: list[str] = Field(default_factory=list[str])


class DiagEvent(ApiModel):
    """이벤트 표시(§D9)."""

    date: date
    kind: EventKindT
    title: str = Field(min_length=1)
    days_left: int | None = None  # D-n(오늘 기준, 지난 이벤트는 None)
    quality: Quality


class DiagSummary(ApiModel):
    """`/api/diag/summary?date=` — 판정 띠·공포탐욕·지표 카드·이벤트(페이지 3 위쪽)."""

    trade_date: date
    verdict: DiagVerdict | None
    fear_greed: DiagFearGreed | None
    gauge: DiagGauge | None
    indicators: list[DiagIndicator]
    events: list[DiagEvent]
    caution: bool = False  # 이벤트 주의(오늘·다음 거래일)


class DiagHistoryPoint(ApiModel):
    trade_date: date
    value: float | None
    state: StateT | None
    pct_rank: float | None = Field(default=None, ge=0, le=100)
    quality: Quality


class DiagHistory(ApiModel):
    """`/api/diag/history?key=&days=` — 지표(또는 fear_greed) 시계열과 구간 경계."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    tier: TierT
    unit: str
    bands: list[float]  # 백분위 경계(§D0) — 공포탐욕이면 점수 경계(§D7)
    points: list[DiagHistoryPoint]


class DiagEvents(ApiModel):
    """`/api/diag/events?from=&to=` — 이벤트 달력(페이지 3 timeline)."""

    start: date
    end: date
    events: list[DiagEvent]
    generated_for: AwareDatetime | None = None
