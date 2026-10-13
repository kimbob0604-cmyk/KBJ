"""`/api/index-review*` 응답 모델 — 패시브 지수 정기변경 예측(P5 2차 묶음 IDX, metrics §24).

로그인 전용(구성종목·컷오프 추정이 들어간다 — 공개 빌드·내보내기에 없다). 금액은 원 단위 정수,
비율은
소수(0.124 = 12.4%). 표의 열은 사용자 예시와 같다: 종목·시가총액·유동시총·유동비율·시총 컷오프
괴리율·
유동시총 컷오프 괴리율·조건 충족·필요 주가 상승률(시총·유동 중 큰 쪽)·예상 패시브 매수·거래대금
배수.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "CandidateRow",
    "CutoffInfo",
    "IndexReview",
    "IndexRunView",
    "IndexTab",
    "ReviewDates",
    "StockIndexBadge",
    "StockIndexBadges",
]

VerdictT = Literal["add", "delete", "borderline", "keep"]


class IndexTab(ApiModel):
    id: str
    title: str
    order: int


class ReviewDates(ApiModel):
    """리뷰 일정 + 오늘 기준 D-day(음수 = 지났다). confirmed=False 면 '추정'."""

    index_id: str
    index_title: str
    review_id: str
    base_date: date
    announce_date: date
    trade_date: date
    effective_date: date
    d_base: int
    d_announce: int
    d_trade: int
    d_effective: int
    confirmed: bool
    quality: Quality


class CutoffInfo(ApiModel):
    """컷오프(원). kind model = 엔진 추정(구간 low~high), user = 사용자가 넣은 증권사 추정치."""

    kind: Literal["model", "user"]
    won: int
    low_won: int | None
    high_won: int | None
    method: str | None


class CandidateRow(ApiModel):
    code: str
    name: str | None
    verdict: VerdictT
    member: bool
    mktcap: int | None
    ff_mcap: int | None
    fif: float | None
    fif_how: str | None
    gap_mcap: float | None
    gap_ff: float | None
    condition_met: bool | None
    required_rise: float | None
    required_basis: Literal["mcap", "ff"] | None
    passive_won: int | None
    adv_won: int | None
    adv_multiple: float | None
    rank: int | None
    reasons: list[str]
    quality: Quality


class IndexRunView(ApiModel):
    """한 컷오프 종류의 예측. status no_constituents = 구성종목 목록 없음(후보를 내지 않는다)."""

    cutoff_kind: Literal["model", "user"]
    status: Literal["ok", "no_constituents", "no_data"]
    base_date: date
    cutoff: CutoffInfo | None
    n_members: int
    n_add: int
    n_delete: int
    n_borderline: int
    aum_won: int | None
    notes: list[str]
    quality: Quality
    adds: list[CandidateRow]
    deletes: list[CandidateRow]
    borderline: list[CandidateRow]


class IndexReview(ApiModel):
    """페이지 10 '지수 정기변경' 패널 + 페이지 5 캘린더 줄(calendar — 다가오는 일정 전부)."""

    indexes: list[IndexTab]
    index_id: str
    review: ReviewDates | None
    reviews: list[ReviewDates]  # 이 지수의 모든 리뷰(탭 안 리뷰 고르기)
    runs: list[IndexRunView]  # model 먼저, 사용자 컷오프가 있으면 user 도
    calendar: list[ReviewDates]  # 모든 지수의 아직 효력 전 리뷰(날짜순)


class StockIndexBadge(ApiModel):
    """페이지 8 머리 배지 한 줄 — 예 'MSCI Korea 편입 후보 · 필요 +12.4%'."""

    index_id: str
    index_title: str
    review_id: str
    cutoff_kind: Literal["model", "user"]
    verdict: VerdictT
    required_rise: float | None
    label: str
    quality: Quality


class StockIndexBadges(ApiModel):
    code: str
    badges: list[StockIndexBadge]
