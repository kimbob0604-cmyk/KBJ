"""수출입·매핑 응답 모델(docs/p6_design.md §5, metrics §25~§29 — `/api/trade/*`). 모양은
웨이브 1(FND)이 고정한다(D-P6-5). 바꿔야 하면 FND 담당에게 요청 — 도메인 묶음(FLASH·EXP·MAP)은
리더 본문만.

- 금액은 **미화 달러 정수**(원화 환산 없음 — 사용자 결정 2026-10-13 Q8, '환율 미조정'), 중량 kg,
  비율은 소수(0.5 = +50%). 값이 없으면 None(0 으로 채우지 않는다).
- 행마다 quality 를 싣고, 봉투(`Envelope`)가 source('관세청(공공데이터포털)')·as_of 를 싣는다.
- 시세 칸은 없다(공개 등급). 로그인 종가 줄은 기존 `/api/stock/{code}/chart`(새 경로 없음 — §4.6).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "TenDayKindT",
    "TradeCountries",
    "TradeCountryItems",
    "TradeCountryRow",
    "TradeFlash",
    "TradeFlashCell",
    "TradeFlashSegment",
    "TradeFlow",
    "TradeFlowPoint",
    "TradeFlowProduct",
    "TradeGroupRow",
    "TradeItemCountries",
    "TradeItemCountryRow",
    "TradeItemNode",
    "TradeItemPoint",
    "TradeItemSeries",
    "TradeItems",
    "TradeItems104",
    "TradeMapEvidence",
    "TradeMapLine",
    "TradeRegionRow",
    "TradeRegions",
    "TradeStock",
    "TradeStockFlows",
    "TradeStockPoint",
    "TradeStockRank",
    "TradeStocks",
    "TradeSurgeRow",
    "TradeSurges",
]

TenDayKindT = Literal["exp_item", "exp_country", "imp_item", "imp_country"]
FlowT = Literal["exp", "imp"]
StatusT = Literal["ok", "no_revenue", "short_overlap", "no_trade"]


# ── 속보(FLASH — metrics §25) ────────────────────────────────────────────────────────────────


class TradeFlashCell(ApiModel):
    """한 구간의 한 열. usd = 최신(정정 반영), first_usd = 발표 당시. yoy 가 None 이면 사유
    (`yoy_reason` — 전년 값 없음·열 이름 바뀜 등)."""

    col_no: int = Field(ge=0, le=10)
    name: str
    usd: int | None
    first_usd: int | None
    yoy: float | None
    yoy_reason: str | None = None
    contribution: float | None = None  # 10대 기여도 Δvᵢ / Δv₀₀(분모 0 → None)
    mom: float | None = None  # 전월 같은 구간 대비(metrics §25 — 화면 보조, WRAP 이 칸을 더했다)


class TradeFlashSegment(ApiModel):
    ym: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    seg: int = Field(ge=1, le=3)
    span_text: str  # '01~10'·'01~20'·'01~31'
    first_seen_at: AwareDatetime
    revised_at: AwareDatetime | None
    quality: Quality
    cells: list[TradeFlashCell]


class TradeFlash(ApiModel):
    kind: TenDayKindT
    months: int
    columns: list[str]  # 최신 열 이름(00~10)
    segments: list[TradeFlashSegment]  # 최신이 먼저


# ── 탐색(EXP — metrics §26·§27) ─────────────────────────────────────────────────────────────


class TradeItemNode(ApiModel):
    """접두 트리 한 칸(트리맵 입력). usd·kg 는 기간 합(가장 깊은 행만 합산 — §26)."""

    hs: str = Field(pattern=r"^[0-9]{2}([0-9]{2}){0,4}$")
    name: str | None
    usd: int | None
    kg: int | None
    yoy: float | None
    share: float | None
    unit_price: float | None  # 달러/kg
    has_children: bool
    quality: Quality


class TradeItems(ApiModel):
    level: Literal[2, 4, 6, 10]
    parent: str | None
    flow: FlowT
    ym_from: str
    ym_to: str
    hs10_login_only: bool = False  # 공개판은 HS10 이 없다('로그인판에서 HS10' 안내 — §6)
    rows: list[TradeItemNode]


class TradeItemPoint(ApiModel):
    ym: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    usd: int | None
    kg: int | None
    unit_price: float | None
    yoy: float | None
    yoy_3m: float | None
    # 단가 대 물량 분해(§27 — ln(1+금액 YoY) = ln(1+단가 YoY) + ln(1+중량 YoY)).
    # 하나라도 None 이면 None
    ln_price: float | None
    ln_volume: float | None
    quality: Quality


class TradeItemSeries(ApiModel):
    hs: str = Field(pattern=r"^[0-9]{2}([0-9]{2}){0,4}$")
    name: str | None
    flow: FlowT
    weight_unfit: bool  # 중량 기준 부적합(config/trade.yaml price_volume.weight_unfit)
    points: list[TradeItemPoint]


class TradeItemCountryRow(ApiModel):
    country: str = Field(pattern=r"^[A-Z0-9]{2}$")  # 관세청 특수 코드 'Z1'(실측 2026-10-20)
    country_name: str | None
    usd: int | None
    yoy: float | None
    share: float | None
    quality: Quality


class TradeItemCountries(ApiModel):
    hs: str = Field(pattern=r"^[0-9]{2}([0-9]{2}){0,4}$")
    ym: str
    flow: FlowT
    rows: list[TradeItemCountryRow]


class TradeCountryRow(ApiModel):
    country: str = Field(pattern=r"^[A-Z0-9]{2}$")  # 관세청 특수 코드 'Z1'(실측 2026-10-20)
    country_name: str
    exp_usd: int | None
    imp_usd: int | None
    bal_usd: int | None
    exp_yoy: float | None
    imp_yoy: float | None
    exp_share: float | None
    quality: Quality


class TradeCountries(ApiModel):
    ym: str
    rows: list[TradeCountryRow]


class TradeCountryItems(ApiModel):
    country: str = Field(pattern=r"^[A-Z0-9]{2}$")  # 관세청 특수 코드 'Z1'(실측 2026-10-20)
    country_name: str | None
    ym: str
    flow: FlowT
    rows: list[TradeItemNode]


class TradeRegionRow(ApiModel):
    region_id: str | None
    sido_cd: str
    sgg_nm: str
    hs6: str
    exp_usd: int | None
    exp_yoy: float | None
    share: float | None
    quality: Quality


class TradeRegions(ApiModel):
    hs6: str | None
    sido: str | None
    ym: str
    rows: list[TradeRegionRow]


class TradeSurgeRow(ApiModel):
    level: Literal["hs2", "hs4", "hs6", "hs10", "country"]
    key: str
    name: str | None
    flow: FlowT
    rule: Literal["surge", "plunge"]
    yoy: float | None
    yoy_3m: float | None
    usd: int | None
    rank: int = Field(ge=1)
    quality: Quality


class TradeSurges(ApiModel):
    ym: str
    level: Literal["hs2", "hs4", "hs6", "hs10", "country"] | None
    min_usd: int  # 수준 문턱(config/trade.yaml surge.min_usd)
    rows: list[TradeSurgeRow]


class TradeGroupRow(ApiModel):
    group_id: str
    name: str
    exp_usd: int | None
    yoy: float | None
    unit_price: float | None
    share: float | None
    quality: Quality


class TradeItems104(ApiModel):
    ym: str
    mode: Literal["fixed", "top_n"]
    auto_selected: bool  # top_n 이면 '자동 선택' 꼬리표(§4.4)
    rows: list[TradeGroupRow]


# ── 매핑·겹쳐 보기(MAP — metrics §28) ───────────────────────────────────────────────────────


class TradeStockRank(ApiModel):
    """매핑 종목 랭킹 한 줄 — 최근 3개월 수출 지수 YoY 내림차순(동률 코드 순)."""

    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str
    yoy_3m: float | None
    status: StatusT
    reviewed: bool
    origin: Literal["public", "local"]
    quality: Quality
    # P10 단계 2 — 맵핑 트렌드(공개 파일에는 없을 수 있다 — 기본값)
    sector: str | None = None  # 매핑표 업종(자체 분류)
    last_ym: str | None = None  # 지수가 있는 마지막 달
    yoy_1m: float | None = None  # 마지막 달 지수 / 전년 같은 달 − 1
    spark: list[int | None] = Field(default_factory=list[int | None])  # 최근 12개월 지수(달러)


class TradeStocks(ApiModel):
    map_version: str
    rows: list[TradeStockRank]


class TradeMapLine(ApiModel):
    """매핑 한 줄 — 품목 행(region_id None) 또는 지역 행(region_id·hs = HS6)."""

    hs: str
    flow: FlowT
    region_id: str | None
    weight: float = Field(ge=0, le=1)
    countries: list[str]
    name: str | None


class TradeMapEvidence(ApiModel):
    kind: str
    note: str
    rcept_no: str | None


class TradeStockPoint(ApiModel):
    period: str = Field(pattern=r"^[0-9]{4}(-(0[1-9]|1[0-2])|Q[1-4])$")
    export_usd: int | None
    yoy: float | None
    revenue_yoy: float | None  # 분기 행만(DART 분기 매출 — 보고 기준)
    quality: Quality


class TradeStock(ApiModel):
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str
    reviewed: bool
    origin: Literal["public", "local"]
    map_version: str
    status: StatusT
    fx_note: str = "환율 미조정(달러)"  # 사용자 결정 2026-10-13 Q8
    lines: list[TradeMapLine]
    evidence: list[TradeMapEvidence]
    monthly: list[TradeStockPoint]
    quarterly: list[TradeStockPoint]
    lag_corr: dict[str, float | None]  # '0'·'1'·'2' 분기(수출이 앞섬)


# ── 종목별 흐름(P10 단계 1 — docs/p10_trade_stocks_plan.md) ───────────────────────────────────────


class TradeFlowProduct(ApiModel):
    hs: str
    name: str | None  # 매핑표 이름, 없으면 관세청 품목명(hs_dim)
    weight: float = Field(ge=0, le=1)  # 종목 수출 지수 가중치(그 품목 줄)


class TradeFlowPoint(ApiModel):
    ym: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    usd: int | None  # 흐름 합(가중치 미적용 — 통관 금액)
    kg: int | None  # 시군구 흐름은 None(시군구 표에 중량 없음)
    unit_price: float | None  # 달러/kg
    usd_yoy: float | None
    kg_yoy: float | None
    price_yoy: float | None
    days: int | None  # 그 달 영업일(거래일 달력)
    daily_usd: int | None
    daily_yoy: float | None
    products: dict[str, int | None]  # hs → 금액
    quality: Quality


class TradeFlow(ApiModel):
    id: str
    name: str
    kind: Literal["national", "sigungu"]
    direction: FlowT
    nature: Literal["company", "mixed", "affiliate"]
    nature_label: str  # 회사 출하 · 업황 혼합 · 관계사·타사 물량 혼입
    partners: list[str]  # ISO2(비면 전 세계)
    regions: list[str]  # 시군구 이름(trade_regions.yaml — 못 찾으면 통합 ID)
    products: list[TradeFlowProduct]
    basis: str  # 선정 근거(자체 요약 문장 — 비면 '')
    last_ym: str | None
    has_kg: bool
    points: list[TradeFlowPoint]  # 달 오름차순(최근 FLOW_MONTHS 개월)
    notes: list[str]


class TradeStockFlows(ApiModel):
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str
    reviewed: bool
    origin: Literal["public", "local"]
    map_version: str
    evidence: list[TradeMapEvidence]
    flows: list[TradeFlow]


# ── 실적 프리뷰(P10 단계 3 — 로그인: 컨센서스 KIS) ─────────────────────────────────────────


class TradePreviewQuarter(ApiModel):
    quarter: str = Field(pattern=r"^[0-9]{4}Q[1-4]$")
    export_krw: float | None  # 분기 수출 원 환산(진행 중 분기는 있는 달 평균 × 3)
    revenue: int | None  # 공시 매출(원)
    fitted: float | None  # 회귀 값
    months: int


class TradePreview(ApiModel):
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str
    quarter: str = Field(pattern=r"^[0-9]{4}Q[1-4]$")
    months_seen: int
    reported: bool  # 그 분기 매출이 이미 공시됨(사후 대조)
    export_krw: float | None
    revenue_est: float | None
    consensus: float | None
    consensus_note: str | None
    diff: float | None  # 추정 ÷ 컨센서스 − 1
    verdict: Literal["BEAT", "IN LINE", "MISS"] | None
    prev_verdict: Literal["BEAT", "IN LINE", "MISS"] | None  # 마지막 달을 뺀 판정
    r2: float | None
    points: int
    confidence: Literal["높음", "보통", "낮음"]
    band: float
    history: list[TradePreviewQuarter]


# ── 월별 추이(metrics §29a — 2026-10-20 사용자 요청: 쌓아서 보고 QoQ·YoY) ─────────────────────────


class TradeChapterName(ApiModel):
    hs: str  # HS 2자리, 또는 'other'(기타)
    name: str


class TradeMonthlyPoint(ApiModel):
    ym: str
    total: int  # 그 달 수출(수입) 달러 합 — HS10 합
    parts: dict[str, int]  # 류(또는 'other') → 달러
    mom: float | None  # 전월비(비율)
    yoy: float | None  # 전년동월비(비율)


class TradeQuarterPoint(ApiModel):
    q: str  # YYYYQn — 세 달이 다 있는 분기만
    total: int
    qoq: float | None
    yoy: float | None


class TradeMonthlyStack(ApiModel):
    flow: FlowT
    chapters: list[TradeChapterName]  # 쌓는 순서(큰 것부터), 마지막이 기타
    months: list[TradeMonthlyPoint]
    quarters: list[TradeQuarterPoint]


class TradeMonthly(ApiModel):
    exp: TradeMonthlyStack
    imp: TradeMonthlyStack
