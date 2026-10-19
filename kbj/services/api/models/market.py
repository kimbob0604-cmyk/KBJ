"""시장 응답 모델(docs/p3_design.md §5.2 — `MarketSummary`·`SectorHeat`·`Ribbon`)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel, ChipValue, SourcedInt
from kbj.services.api.models.flows import InvestorTotals

__all__ = [
    "Breadth",
    "Chip",
    "CreditLine",
    "CreditPanel",
    "CreditPoint",
    "DayValue",
    "IndexTile",
    "MarketSummary",
    "Ribbon",
    "SectorCell",
    "SectorHeat",
    "TurnoverPanel",
]


class IndexTile(ApiModel):
    code: str
    name: str | None
    value: float | None
    chg_pct: float | None
    spark: list[float]  # 최근 종가(오름차순, 장중이면 끝에 현재값)
    live: bool  # 장중 시세(잠정)인가
    source: str
    as_of: AwareDatetime
    quality: Quality


class DayValue(ApiModel):
    date: date
    value: int | None
    quality: Quality | None
    source: str


class TurnoverPanel(ApiModel):
    today: SourcedInt | None
    basis: Literal["close", "intraday_index"] | None  # 확정(종목 합) / 장중(지수 누적 — R23)
    avg20: int | None  # 직전 20영업일 평균
    ratio: float | None
    n_prior: int
    series: list[DayValue]
    tags: list[str]  # "마감"·"장중(지수 기준)"·"NXT 미포함"


class Breadth(ApiModel):
    date: date
    up: int
    flat: int
    down: int
    unknown: int
    total: int
    above_ma20_n: int
    ma20_base: int
    above_ma20_pct: float | None
    newhigh_n: int
    newhigh_pct: float | None
    limit_up: int
    limit_down: int
    quality: Quality | None
    source: str
    notes: list[str]


class MarketSummary(ApiModel):
    date: date | None  # 확정 원장의 마지막 영업일
    indices: list[IndexTile]
    turnover: TurnoverPanel | None
    breadth: Breadth | None
    investors: InvestorTotals | None


class SectorCell(ApiModel):
    code: str
    name: str | None
    market: str | None
    chg_pct: float | None  # None 이면 note 에 사유 — 0 으로 그리지 않는다
    turnover: int | None
    quality: Quality
    source: str
    as_of: AwareDatetime | None
    note: str | None


class SectorHeat(ApiModel):
    period: int
    cells: list[SectorCell]


class Chip(ApiModel):
    key: str
    label: str
    value: ChipValue | None  # 없으면 note 에 사유, '준비 중' 칩은 phase_pending
    tier: Literal["public", "login"]
    phase_pending: str | None
    tags: list[str]
    note: str | None
    detail: dict[str, Any] = Field(default_factory=dict)


class Ribbon(ApiModel):
    chips: list[Chip]


# ── 페이지 1 '신용잔고 · 예탁금 · 펀드 자금'(금투협 종합통계 — 공개 등급 원천, 로그인 API) ──


class CreditPoint(ApiModel):
    date: date
    value: int | None  # 원(출처 단위를 원으로 바꾼 값). 없으면 None — 0 으로 그리지 않는다


class CreditLine(ApiModel):
    key: Literal["credit_balance", "investor_deposit", "fund_nav"]
    label: str
    freq: Literal["D", "M"]  # 금투협 일별, 예탁금이 ECOS 월별로 대신 채워지면 M
    value: int | None  # 마지막 값(원)
    date: date | None
    change: int | None  # 직전 값 대비(원)
    prev_date: date | None
    spark: list[CreditPoint]  # 최근 20개(오름차순)
    source: str
    as_of: AwareDatetime | None
    quality: Quality | None
    note: str | None  # 값이 없거나 늦은 사유


class CreditPanel(ApiModel):
    lines: list[CreditLine]
