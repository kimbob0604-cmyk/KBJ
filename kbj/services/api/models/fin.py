"""재무 응답 모델 — 분기·연간 재무·비율, dart-report 블록, 부문 매출(docs/p4_design.md §5.2, 공개
원천 DART).

금액은 원 단위 정수(외국 통화 회사는 `currency` 의 단위 그대로 — 환산하지 않는다), 비율은 % 실수.
없는 값은 null(0 으로 채우지 않는다), 적용 불가는 `applicable=false` + `na_reason`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, JsonValue

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "FinChecks",
    "FinRow",
    "Financials",
    "RatioView",
    "ReportBlocks",
    "ReportView",
    "SegmentView",
    "Segments",
]


class FinRow(ApiModel):
    """분기 단독(basis=q — 누적 차분, Q4 = 연간 − 3분기 누적) 또는 회계연도 합(basis=a)."""

    period: str  # 2026Q3 · 2026FY
    label: str  # 화면 라벨(비12월 결산 `FY26 2Q (26.07~26.09)`)
    fiscal_year: int
    fiscal_q: int | None  # 연간이면 None
    period_start: date
    period_end: date
    rcept_dt: date | None  # 정기보고서 접수일(PIT 기준) — 모르면 None
    revenue: int | None
    op: int | None
    ni: int | None
    ni_owner: int | None
    opm: float | None  # 그 기간 영업이익 ÷ 매출(%)
    npm: float | None  # 그 기간 지배순이익 ÷ 매출(%)
    rev_yoy: float | None
    op_yoy: float | None
    derived: bool  # 직전 누적이 없어 분기 단독을 못 만든 행(값 칸 null)
    quality: Quality
    notes: list[str]


class RatioView(ApiModel):
    """최근 분기 비율(§4.5 — DART 만, 공개). 분모 ≤ 0 이면 null + `na_reasons[칸]`."""

    period: str
    fs_div: Literal["CFS", "OFS"]
    ttm_revenue: int | None
    ttm_op: int | None
    ttm_ni_owner: int | None
    opm: float | None
    npm: float | None
    roe: float | None
    roa: float | None
    debt_ratio: float | None
    net_debt_ratio: float | None
    rev_yoy: float | None
    op_yoy: float | None
    eps_ttm: float | None
    bps: float | None
    shares_basis: int | None
    na_reasons: dict[str, NaReason]
    quality: Quality
    notes: list[str]


class FinChecks(ApiModel):
    diff3m_max_pct: float | None  # 보고서 3개월값과 차분값의 최대 차이(% — 1% 넘으면 그 행 추정)
    n_derived: int
    n_estimated: int


class Financials(ApiModel):
    applicable: bool = True
    na_reason: NaReason | None = None  # no_filings(정기보고서 없음)
    code: str
    corp_code: str
    basis: Literal["q", "a"]
    fs_div: Literal["CFS", "OFS"] | None
    currency: str | None
    tier: Literal["main", "full"] | None  # 주요계정 층·전체 재무제표 층(D-P4-5)
    rows: list[FinRow]
    ratios: RatioView | None
    checks: FinChecks


class ReportBlocks(ApiModel):
    """dart-report 화면 블록(엑셀과 같은 입력·같은 셈 — `kbj.reports.dart_excel.blocks`, 금액
    억원)."""

    quarterly: JsonValue
    annual_costs: JsonValue
    bridge: JsonValue
    orders: JsonValue
    quarterly_costs: JsonValue


class ReportView(ApiModel):
    code: str  # 리포트를 만든 종목코드(우선주는 보통주 리포트)
    corp_code: str
    built_on: date
    fs_div_used: Literal["CFS", "OFS"]
    year_from: int
    year_to: int
    unit: str  # 블록 금액 단위(억원)
    blocks: ReportBlocks
    warnings: list[str]
    unmapped: JsonValue
    xlsx_available: bool  # 엑셀 내려받기(`/api/stock/<코드>/report.xlsx`)가 있는가
    engine_version: str
    generated_at: AwareDatetime


class SegmentView(ApiModel):
    segment: str
    amounts: dict[str, int | None]


class Segments(ApiModel):
    """부문 매출(사업보고서 원문 표 best-effort — 언제나 추정, D-P4-22)."""

    code: str
    corp_code: str
    year: int
    rcept_no: str | None
    rows: list[SegmentView]
    quality: Quality
