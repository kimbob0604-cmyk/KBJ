"""수급·스크리닝 응답 모델(docs/p3_design.md §5.2 — `ScreenResponse`·`InvestorTotals`·
`StockFlowDetail`·`IntradayFlows`). 금액은 원 단위 정수."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "DayFlow",
    "DayTotals",
    "Excluded",
    "IntradayFlows",
    "IntradayRow",
    "InvestorTotals",
    "RankItem",
    "ScreenResponse",
    "ScreenRow",
    "StockFlowDetail",
]


class ScreenRow(ApiModel):
    rank: int
    code: str
    name: str | None
    market: str | None
    sector: str | None
    kind: str | None
    chg_pct: float | None
    turnover_sum: int | None
    turnover_avg: int | None  # 일평균(기간 합 ÷ 실제 거래된 영업일)
    turnover_rate_pct: float | None
    foreign: int | None
    inst: int | None
    other_corp: int | None
    indiv: int | None
    streak_foreign: int
    streak_inst: int
    spike_mult: float | None
    newhigh_label: str | None
    flags: list[str] | None  # None = 상태를 모른다(n_status_unknown 에 세었다)
    quality: Quality
    source: str


class Excluded(ApiModel):
    flagged: int
    invalid: int
    below_min: int
    status_unknown: int  # 상태를 몰라 빼지 못한 종목 수(결과에 남아 있다)


class ScreenResponse(ApiModel):
    mode: Literal["value", "foreign", "inst", "both", "streak", "spike"]
    market: Literal["all", "KOSPI", "KOSDAQ"]
    period: int
    share_class: Literal["common", "pref"]
    min_avg_turnover: int
    date: date | None  # 기간 끝 영업일
    n_total: int
    n_excluded: Excluded
    rows: list[ScreenRow]


class DayTotals(ApiModel):
    date: date
    by_investor: dict[str, int | None]
    quality: Quality


class InvestorTotals(ApiModel):
    date: date
    # foreign·institution·other_corp·individual(원). 값이 없는 구분은 None — 0 으로 바꾸지 않는다
    by_investor: dict[str, int | None]
    inst7: dict[str, int] | None  # 기관 7구분 — 없으면 None(패널을 숨긴다)
    check1_residual: int | None  # 검산 ① 잔차 — None = 검산 불가(사유는 notes)
    check2_residual: int | None
    by_market: dict[str, dict[str, int | None]]
    quality: Quality
    source: str
    notes: list[str]
    recent: list[DayTotals]  # 최근 5영업일(오름차순)


class DayFlow(ApiModel):
    date: date
    turnover: int | None
    foreign: int | None
    inst: int | None
    other_corp: int | None
    indiv: int | None
    check1: int | None
    check2: int | None
    traded: bool
    quality: Quality | None  # 그날 행이 없으면 None


class StockFlowDetail(ApiModel):
    code: str
    name: str | None
    days: list[DayFlow]
    cumulative: dict[str, int | None]
    checks: dict[str, int]  # c1_failed·c1_unavailable·c2_failed·c2_unavailable


class IntradayRow(ApiModel):
    rank: int | None
    code: str
    value: int | None  # 순매수 금액(원) — 증권사 가집계(잠정)
    qty: int | None


class RankItem(ApiModel):
    market: str
    rank: int
    code: str
    name: str | None
    turnover: int | None
    chg_pct: float | None


class IntradayFlows(ApiModel):
    slot: AwareDatetime  # 마지막 슬롯 시작
    top_foreign: list[IntradayRow]
    top_inst: list[IntradayRow]
    turnover_rank: list[RankItem]
