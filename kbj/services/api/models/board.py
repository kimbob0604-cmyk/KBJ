"""신고가 보드 응답 모델(docs/p3_design.md §5.2 — ET `newhigh.json`·`sectors.json`·`events.json`·
`rankings.json` 키 그대로).

보드 산출 JSON(`prv_board.artifact.payload`)을 그대로 싣는다 — 선언한 키는 화면이 쓰는 것이고 나머지
키는 그대로 지나간다(`extra="allow"`). **금액 단위는 ET 산출 그대로 억원**(키 이름 `_eok`·
`turnover`·`mktcap` — 봉투 notes 에도 적는다). 원 단위 정수로 바꾼 보드 행은 저장소
`prv_board.stock_day` 에 있다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kbj.services.api.models.common import ApiModel

__all__ = [
    "BoardEvents",
    "BoardFilter",
    "BoardNewhigh",
    "BoardRankings",
    "BoardSectors",
    "RowFlows",
]


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    as_of: str | None = None
    source: str | None = None
    generated_at: str | None = None


class RowFlows(ApiModel):
    """신고가 행에 붙이는 그날 원장 순매수(원) — 값이 없으면 None."""

    foreign: int | None
    inst: int | None
    quality: str | None


class BoardFilter(ApiModel):
    basis: str
    kind: str | None
    min_turnover_eok: float | None
    n_before_filter: int


class BoardNewhigh(_Payload):
    basis: str | None = None
    prev_asof: str | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    priority: list[str] = Field(default_factory=list)
    counts_close: dict[str, int] = Field(default_factory=dict)
    counts_high: dict[str, int] = Field(default_factory=dict)
    min_mktcap_eok: float | None = None
    min_turnover_eok: float | None = None
    n_below_mktcap: int | None = None
    n_suspect: int | None = None
    n_hist_not_evaluated: int | None = None
    achieved: list[dict[str, Any]] = Field(default_factory=list)
    proximity: list[dict[str, Any]] = Field(default_factory=list)
    # 역사적 신고가(상장 이후 전체 — ADR 0017) 계산 범위 진단. 화면은 날짜를 내세우지 않고
    # hist_notes(보류 수·원천 바닥 기준 수 — 품질 메모)만 적는다
    hist_scope: dict[str, Any] | None = None
    hist_notes: list[str] = Field(default_factory=list)
    flows: dict[str, RowFlows] = Field(default_factory=dict)  # code → 그날 외국인·기관
    filter: BoardFilter | None = None


class BoardSectors(_Payload):
    taxonomy_layer1: str | None = None
    sectors: list[dict[str, Any]] = Field(default_factory=list)
    themes: list[dict[str, Any]] = Field(default_factory=list)
    heatmap_theme: list[dict[str, Any]] = Field(default_factory=list)
    heatmap_sector: list[dict[str, Any]] = Field(default_factory=list)


class BoardEvents(_Payload):
    events: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None


class BoardRankings(_Payload):
    prev_asof: str | None = None
    taxonomy: str | None = None
    universe: int | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)
    sector_boards: list[dict[str, Any]] = Field(default_factory=list)
    stock_boards: list[dict[str, Any]] = Field(default_factory=list)
    cross_codes: list[str] = Field(default_factory=list)
