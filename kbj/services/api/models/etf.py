"""ETF 수급 응답 모델(docs/p3_design.md §5.2 — `EtfFlows`·`EtfTypes`·`EtfPremium`·
`EtfHoldingChanges`). 금액은 원 단위 정수(엔진의 부동소수 원을 반올림)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.core.quality import Quality
from kbj.core.rows import EtfType
from kbj.services.api.models.common import ApiModel

__all__ = [
    "ChangeRow",
    "Check3",
    "EtfFlowRow",
    "EtfFlows",
    "EtfHoldingChanges",
    "EtfPremium",
    "EtfTypes",
    "NewListing",
    "PremiumRow",
    "TypeRollup",
]


class EtfFlowRow(ApiModel):
    code: str
    name: str | None
    etf_type: EtfType
    etf_type_label: str
    theme: str | None
    issuer: str | None
    net_asset: int | None
    net_inflow: int | None  # 기간 순유입 = Σ(Sₜ − Sₜ₋₁)·NAVₜ
    price_effect: int | None
    ret_pct: float | None  # 기간 종가 수익률(분할이 낀 기간은 None)
    turnover: int | None
    # 장내 투자자별 순매수(KIS — 순유입과 합치지 않는다, LP 상대)
    indiv: int | None
    foreign: int | None
    inst: int | None
    days: int  # 집계에 넣은 날 수
    n_invalid: int
    status: Literal["ok", "new", "split_adjusted", "invalid"]
    quality: Quality


class NewListing(ApiModel):
    code: str
    name: str | None
    listed_on: date
    net_asset: int | None


class EtfFlows(ApiModel):
    mode: Literal["in", "out", "value", "indiv", "foreign", "inst"]
    etf_type: EtfType | None
    period: int
    start: date | None
    end: date | None
    n_total: int
    rows: list[EtfFlowRow]
    new_listings: list[NewListing]


class TypeRollup(ApiModel):
    etf_type: EtfType
    label: str
    net_inflow: int
    price_effect: int
    n_etfs: int
    n_new: int
    n_invalid: int


class Check3(ApiModel):
    """검산 ③(순자산 변화 = 순유입 + 가격효과) — 집계에 넣은 흐름의 합."""

    net_asset_chg: int | None
    inflow: int
    price_effect: int
    residual: int | None
    n_checked: int
    n_failed: int
    n_unavailable: int


class EtfTypes(ApiModel):
    period: int
    start: date | None
    end: date | None
    rows: list[TypeRollup]
    check3: Check3


class PremiumRow(ApiModel):
    code: str
    name: str | None
    price: float
    nav: float
    premium_pct: float
    threshold: float
    warn: bool
    as_of: AwareDatetime
    quality: Quality
    source: str


class EtfPremium(ApiModel):
    basis: Literal["nav", "inav"]
    n_checked: int
    rows: list[PremiumRow]  # 경고(|괴리율| ≥ 기준)만, |괴리율| 큰 순


class ChangeRow(ApiModel):
    fund_id: str
    etf_code: str | None
    fund_name: str | None
    issuer: str | None
    is_active: bool | None
    theme: str | None
    code: str
    name: str | None
    kind: Literal["NEW", "DROP", "IN10", "OUT10", "ADD", "CUT"]
    kind_label: str
    prev_qty: float | None
    cur_qty: float | None
    prev_wt: float | None
    cur_wt: float | None
    qty_pct_adj: float | None
    asof: date
    prev_asof: date
    gap_days: int


class EtfHoldingChanges(ApiModel):
    run_date: date
    rows: list[ChangeRow]
