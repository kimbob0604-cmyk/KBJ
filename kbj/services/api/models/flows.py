"""수급·스크리닝 응답 모델(docs/p3_design.md §5.2 — `ScreenResponse`·`InvestorTotals`·
`StockFlowDetail`·`IntradayFlows`). 금액은 원 단위 정수."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "DayFlow",
    "DayTotals",
    "Excluded",
    "IntradayFlows",
    "IntradayRow",
    "InvestorTotals",
    "MarketMinute",
    "MinuteCard",
    "MinuteIndex",
    "MinuteLine",
    "RankItem",
    "ScreenResponse",
    "ScreenRow",
    "StockFlowDetail",
]


class ScreenRow(ApiModel):
    rank: int  # 두 시장을 합친 거래대금 순(시장 칸은 그대로)
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
    mktcap: int | None = None  # 기간 끝 날 시가총액(원) — 시총 대비 기준


class Excluded(ApiModel):
    flagged: int
    invalid: int
    below_min: int
    status_unknown: int  # 상태를 몰라 빼지 못한 종목 수(결과에 남아 있다)


class ScreenResponse(ApiModel):
    mode: Literal["value", "foreign", "inst", "both", "streak", "spike", "foreign_cap", "inst_cap"]
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
    rank: int | None  # 코스피·코스닥을 합쳐 순매수 금액 순(KIS 순위는 시장마다 따로다)
    code: str
    value: int | None  # 순매수 금액(원) — 증권사 가집계(잠정)
    qty: int | None
    name: str | None = None  # 종목 이름(최근 KRX 기본정보 — 모르면 None)
    mktcap: int | None = None  # 시가총액(원 — 그날 이하 최근 마감 스냅, 장중엔 전일)
    pct_of_mktcap: float | None = None  # 순매수 ÷ 시총 × 100(%) — 크기가 다른 종목을 같은 잣대로


class RankItem(ApiModel):
    market: str
    rank: int  # 두 시장을 합친 거래대금 순(시장 칸은 그대로)
    code: str
    name: str | None
    turnover: int | None
    chg_pct: float | None


class IntradayFlows(ApiModel):
    slot: AwareDatetime  # 마지막 슬롯 시작
    top_foreign: list[IntradayRow]
    top_inst: list[IntradayRow]
    turnover_rank: list[RankItem]
    # 같은 가집계를 순매수 ÷ 시총 순으로(시총 모르는 종목 제외) — 금액 순위는 대형주만 보인다
    top_foreign_by_cap: list[IntradayRow] = Field(default_factory=list[IntradayRow])
    top_inst_by_cap: list[IntradayRow] = Field(default_factory=list[IntradayRow])
    # 순매도 상위(가집계 순매도 순위 — 금액·시총 대비 오름차순). 급락일에 순매수만 보이던 것
    sell_foreign: list[IntradayRow] = Field(default_factory=list[IntradayRow])
    sell_inst: list[IntradayRow] = Field(default_factory=list[IntradayRow])
    sell_foreign_by_cap: list[IntradayRow] = Field(default_factory=list[IntradayRow])
    sell_inst_by_cap: list[IntradayRow] = Field(default_factory=list[IntradayRow])


# ── P5 F2(docs/p5_design.md §5, F2 §0-1 — `/api/flows/market-minute`) ────────────────────────────
# 모양은 웨이브 1(FND)이 고정, 본문은 F2 묶음. 원천 대금은 백만원(prv_gex.investor_flow) — API 가
# 원으로.


class MinuteCard(ApiModel):
    investor: str  # frgn·prsn·orgn·scrt·ivtr·fund·etc_corp(metrics §21 7카드)
    label: str
    net_value: int | None  # 원 — 오늘 누적 순매수(마지막 분)
    quality: Quality | None


class MinuteLine(ApiModel):
    investor: str
    label: str
    default_on: bool  # 기본 켬(개인·외국인·기관)
    values: list[int | None]  # 분 축과 같은 길이 — 빈 분은 None(잇지 않는다, 보간 금지)


class MinuteIndex(ApiModel):
    """수급 차트에 겹칠 지수(장중 10분 값 — `prv_market.index_intraday`). 축과 같은 길이, 값이 있는
    분은 10분 슬롯뿐(나머지 None — 화면이 잇는다)."""

    code: str  # KIS 업종 코드(0001 코스피·1001 코스닥·2001 코스피200)
    label: str
    values: list[float | None]
    last: float | None  # 마지막 값
    chg_pct: float | None  # 마지막 값의 전일 대비 %
    as_of: AwareDatetime | None
    quality: Quality | None


class MarketMinute(ApiModel):
    market: str  # 탭 id(config/markets.yaml flows.minute.pairs[].tab)
    venue: str  # 분 선은 KIS 기본값 하나 — 'KRX' 표시(F2 §1.3)
    venue_locked: bool
    trade_date: date
    axis: list[str]  # 'HH:MM' — 수집 범위 그대로
    lines: list[MinuteLine]
    cards: list[MinuteCard]
    collected_at: AwareDatetime | None  # 마지막 수집 시각(머리 꼬리표 '수집 HH:MM')
    refresh_s: int  # 60초 갱신
    provisional: bool  # 장중 잠정
    status: Literal["ok", "not_confirmed", "before_open", "holiday"]
    index: MinuteIndex | None = None  # 겹칠 지수 — 그 탭에 정한 지수가 없거나 값이 없으면 None
