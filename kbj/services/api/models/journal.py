"""저널·포트폴리오 응답·요청 모델(docs/p8_design.md §5 — `/api/journal/*`·`/api/portfolio*`,
로그인). 모양은 웨이브 1(FND)이 고정한다. 바꿔야 하면 FND 담당에게 요청 — JNL 은 리더·쓰기 본문만.

- 금액은 원(일지 가격·적정가·매매 가격은 소수 둘째까지 — 문자열이 아니라 수). 값이 없으면 None.
- 포지션·손익은 읽을 때 계산(이동평균 단가 — D-P8-19 [제안]). 현금 원장 없음 — 월별 수익률은
  [확인 필요](§4.7).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "AnalysisIn",
    "AnalysisList",
    "AnalysisMarkdown",
    "AnalysisOut",
    "AnalysisPrefill",
    "Correlation",
    "Deleted",
    "MonthlyOut",
    "PickList",
    "PickOut",
    "Portfolio",
    "PositionOut",
    "Scenario3",
    "TradeIn",
    "TradeList",
    "TradeOut",
]

CODE = r"^[0-9A-Z]{6}$"
SourceT = Literal["api", "cli", "import:sd", "import:csv"]
ValueSourceT = Literal["kbj", "consensus", "manual"]


class Scenario3(BaseModel):
    """bear·base·bull 세 칸(빈칸 None)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bear: float | None = None
    base: float | None = None
    bull: float | None = None


class AnalysisFields(BaseModel):
    """분석 일지 칸(SD `analysis_journal` 그대로 + 출처). 쓰기·읽기·prefill 이 같이 쓴다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=CODE)
    analyzed_on: date
    chain_ids: list[str | None] = Field(default_factory=lambda: [None, None, None], min_length=3,
                                        max_length=3)  # fmt: skip
    price: float | None = Field(default=None, ge=0)
    ret_52w: float | None = None
    fwd_per: float | None = None
    band_pos: float | None = None
    ev_ebitda: float | None = None
    mktcap: int | None = Field(default=None, ge=0)
    opm: float | None = None
    eps: Scenario3 = Scenario3()
    per: Scenario3 = Scenario3()
    tp: Scenario3 = Scenario3()
    tp_source: str | None = Field(default=None, max_length=200)
    conviction: str | None = Field(default=None, max_length=50)
    memo: str | None = Field(default=None, max_length=20_000)
    tags: list[str] = Field(default_factory=list[str], max_length=20)
    conclusion: str | None = Field(default=None, max_length=2_000)
    value_sources: dict[str, ValueSourceT] = Field(default_factory=dict[str, ValueSourceT])


class AnalysisIn(AnalysisFields):
    """일지 만들기·고치기 본문(PATCH 도 전체 칸 — 64 KiB 상한)."""


class AnalysisOut(AnalysisFields):
    id: int = Field(ge=1)
    source: SourceT
    updated_at: AwareDatetime


class AnalysisList(ApiModel):
    items: list[AnalysisOut]


class AnalysisPrefill(ApiModel):
    """prefill(§4.7) — P4 밸류에이션·시나리오 적정가·컨센서스 읽기. 칸마다 출처(value_sources)."""

    fields: AnalysisFields
    as_of: AwareDatetime | None
    quality: Quality


class AnalysisMarkdown(ApiModel):
    id: int = Field(ge=1)
    markdown: str


class TradeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=CODE)
    side: Literal["buy", "sell"]
    trade_date: date
    price: float = Field(gt=0)
    qty: int = Field(gt=0)
    fee: float = Field(default=0, ge=0)
    tax: float = Field(default=0, ge=0)
    strategy: str | None = Field(default=None, max_length=100)
    memo: str | None = Field(default=None, max_length=2_000)
    signal_id: str | None = Field(default=None, pattern=r"^(si|sc)_[0-9A-HJKMNP-TV-Z]{26}$")


class TradeOut(ApiModel):
    id: int = Field(ge=1)
    code: str = Field(pattern=CODE)
    name_at_trade: str | None
    side: Literal["buy", "sell"]
    trade_date: date
    price: float
    qty: int
    fee: float
    tax: float
    strategy: str | None
    memo: str | None
    signal_id: str | None
    source: SourceT
    created_at: AwareDatetime


class TradeList(ApiModel):
    items: list[TradeOut]


class PositionOut(ApiModel):
    code: str = Field(pattern=CODE)
    name: str | None
    qty: int = Field(ge=0)
    avg_cost: float | None
    last_close: float | None
    close_date: date | None
    market_value: int | None
    unrealized: int | None
    unrealized_pct: float | None
    realized: int
    quality: Quality | None  # 최신 종가의 품질(없으면 None)


class MonthlyOut(ApiModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    realized: int
    unrealized_change: int | None
    ret_pct: float | None  # [확인 필요 — 현금 원장 없음]


class Portfolio(ApiModel):
    positions: list[PositionOut]
    realized_total: int
    unrealized_total: int | None
    monthly: list[MonthlyOut]
    trades_n: int = Field(ge=0)


class Correlation(ApiModel):
    """상관 행렬(피어슨 — §4.7, 50종목 상한). 값 없음(겹치는 날 부족)은 None."""

    set: Literal["watchlist", "holdings"]
    window_days: Literal[60, 120]
    codes: list[str]
    names: list[str | None]
    matrix: list[list[float | None]]


class PickOut(ApiModel):
    date: date
    source: str
    rank: int | None
    code: str = Field(pattern=CODE)
    name: str | None
    market: str | None
    score: float | None
    price_at_rec: float | None
    rets: dict[str, float | None]  # "d1".."d7" — 이관분 사후 수익(확정 일봉)


class PickList(ApiModel):
    items: list[PickOut]


class Deleted(ApiModel):
    """지운 행 id(일지·매매 — 되돌리기 없음)."""

    id: int = Field(ge=1)
