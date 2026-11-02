"""상대 시총·연도별 수익률 응답 모델(2026-11-02 — stock-dashboard 화면을 옮김)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from kbj.services.api.models.common import ApiModel
from kbj.services.api.models.diag import SeriesPoint

__all__ = ["AnnualIndexRow", "RelCap", "RelCapPair", "YearReturn"]


class RelCapPair(ApiModel):
    name: str
    legs_a: list[str]
    legs_b: list[str]
    latest: float | None
    date: date | None
    high: float | None
    low: float | None
    pct_of_range: float | None
    error: str | None
    series: list[SeriesPoint]


class YearReturn(ApiModel):
    year: int
    pct: float


class AnnualIndexRow(ApiModel):
    symbol: str
    name: str
    asof: date | None
    error: str | None
    returns: list[YearReturn]


class RelCap(ApiModel):
    period: Literal["6M", "1Y", "2Y"]
    pairs: list[RelCapPair]
    years: list[int]
    annual: list[AnnualIndexRow]
