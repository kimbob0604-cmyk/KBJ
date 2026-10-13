"""종목 상세 응답 모델 — 검색·머리·차트·컨센서스·밸류에이션·시나리오 적정가·관심종목
(docs/p4_design.md §5.2·§5.3, 페이지 8).

규칙(P3 `models/common.py` 그대로)
- 모든 데이터 응답은 `Envelope[T]`(source·as_of·quality 필수). 금액은 원 단위 정수, 배수·비율은
  실수.
- **적용 불가**(스팩 PER·금융업 EV/EBITDA·적자 PER·상세 층 아님 …)는 200 + `applicable=false` +
  `na_reason`(`NaReason` — 값 칸은 null, 0 으로 채우지 않는다). 아직 수집 안 됨은 404 `no_data`,
  모르는 코드는 404 `unknown_code`(§5.2).
- '시나리오 적정가'는 EPS × 배수 시나리오다(구 이름은 쓰지 않는다 — D-P4-12).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "BandView",
    "ChartBar",
    "ChartEvent",
    "ClassCap",
    "Consensus",
    "FyEstimateView",
    "MultipleCell",
    "NewHighBadges",
    "NtmView",
    "OpinionDist",
    "OpinionView",
    "PriceChart",
    "RevisionView",
    "Scenario",
    "ScenarioCase",
    "ScenarioCases",
    "SeriesPoint",
    "SourcedQuote",
    "StockHeader",
    "StockSearch",
    "StockSearchItem",
    "TargetView",
    "Valuation",
    "ValuationCurrent",
    "WatchRow",
    "Watchlist",
]

Kind = Literal["common", "pref", "spac", "reit", "etf", "etn"]


# ── 검색 ──────────────────────────────────────────────────────────────────────────────


class StockSearchItem(ApiModel):
    code: str
    name: str | None
    market: str | None  # KOSPI·KOSDAQ·KONEX(유니버스) — DART 고유번호 표에만 있으면 None
    kind: Kind | None
    corp_code: str | None
    is_watch: bool


class StockSearch(ApiModel):
    q: str
    n_total: int  # 맞은 수(잘리기 전)
    items: list[StockSearchItem]


# ── 머리 ──────────────────────────────────────────────────────────────────────────────


class SourcedQuote(ApiModel):
    """마지막 시세(전 거래일 또는 오늘 마감 — 원장 우선순위 krx > kis, D-P3-7)."""

    date: date
    close: float | None
    chg_pct: float | None
    volume: int | None
    turnover: int | None  # 원
    mktcap: int | None  # 원(이 종목 — 회사 시총은 밸류에이션)
    last_trade: date | None  # 거래가 있던 마지막 날(정지면 정지 전)
    stale: bool  # 정지 등으로 마지막 거래가 시세 날짜보다 앞
    source: str
    quality: Quality


class NewHighBadges(ApiModel):
    """신고가 3축(ADR 0017 — 120일 = 120 KRX 거래일, 52주 = 달력 364일, 역사적 = 상장 이후, 엄격
    '>'). board 엔진 산출을 읽기만 한다(D-P4-23). None = 창을 못 채운 축('—')."""

    date: date
    basis: Literal["close", "high"]
    d120: bool | None
    w52: bool | None
    hist: bool | None
    label: Literal["hist", "w52", "d120"] | None
    source: str
    quality: Quality


class StockHeader(ApiModel):
    code: str
    name: str | None
    market: str | None
    kind: Kind | None
    common_code: str | None  # 우선주면 보통주 코드(share_class — 못 맞추면 None)
    corp_code: str | None  # DART 고유번호(회사 — 우선주는 보통주 회사)
    corp_name: str | None
    status_flags: list[str] | None  # halted·managed·liquidation … — None = 모름(배지 없음과 구분)
    listed_on: date | None
    acc_mt: int | None  # 결산월(DART 기업개황)
    induty_code: str | None
    last: SourcedQuote | None
    is_watch: bool
    detail_tier: bool  # 상세 수집 대상(관심종목 ∪ 컨센서스 유니버스 ∪ 공개 데모 — D-P4-5)
    newhigh: NewHighBadges | None


# ── 차트 ──────────────────────────────────────────────────────────────────────────────


class ChartBar(ApiModel):
    date: date
    o: float | None
    h: float | None
    l: float | None  # noqa: E741 — 화면 약속 이름(o·h·l·c·v)
    c: float | None
    v: int | None
    turnover: int | None


class ChartEvent(ApiModel):
    date: date
    kind: Literal["split"]
    ratio: float  # 새 주식 수 ÷ 옛 주식 수(5:1 분할 = 5)
    note: str


class PriceChart(ApiModel):
    """KRX 일별(비수정 — `adjusted=false`). 거래가 없는 날(정지 — 거래량 0)은 봉에서 뺀다.

    `sma` 는 봉과 같은 길이(키 '20'·'60'·'120' — D-P4-21, 앞쪽 창을 못 채운 칸은 null).
    """

    code: str
    days: int
    adjusted: Literal[False] = False
    bars: list[ChartBar]
    sma: dict[str, list[float | None]]
    events: list[ChartEvent]
    last_trade: date | None


# ── 컨센서스(로그인 — KIS) ────────────────────────────────────────────────────────────


class FyEstimateView(ApiModel):
    fiscal_ym: str
    fiscal_end: date
    is_estimate: bool
    revenue: int | None
    op: int | None
    ni: int | None
    ni_owner: int | None
    eps: float | None
    ebitda: int | None
    quality: Quality


class NtmView(ApiModel):
    as_of: date
    snap_date: date
    fy1_ym: str
    fy2_ym: str | None
    weight: float | None  # FY1 가중(없으면 NTM 칸 전부 null — FY1 만으로 지어내지 않는다)
    revenue: float | None
    op: float | None
    ni: float | None
    eps: float | None
    ebitda: float | None
    quality: Quality


class TargetView(ApiModel):
    as_of: date
    mean: float
    median: float
    high: float
    low: float
    n_brokers: int
    window_days: int
    upside_pct: float | None  # 평균 ÷ 마지막 종가(보통주) − 1
    quality: Quality


class OpinionDist(ApiModel):
    buy: int
    hold: int
    sell: int
    unrated: int
    unknown: int
    n_brokers: int
    window_days: int


class RevisionView(ApiModel):
    as_of: date
    metric: str  # revenue·op·ni·eps·target_mean
    fiscal_ym: str
    window_days: int
    current: float | None
    baseline: float | None
    baseline_date: date | None
    pct: float | None
    signal: str | None
    quality: Quality


class OpinionView(ApiModel):
    broker: str
    opinion_date: date
    opinion: str | None
    opinion_prev: str | None
    target_price: float | None
    quality: Quality


class Consensus(ApiModel):
    """컨센서스(§4.7). 우선주는 보통주 것(`code` = 쓴 보통주 코드). `label` = "KIS 추정"(시장
    컨센서스인지 [확인 필요 #29] — 확인 전에는 컨센서스라 부르지 않는다). `lost` = KIS 교체로 잃은
    것 (D-P4-10 — 화면 '출처 없음 — KIS 교체로 잃음' 줄)."""

    applicable: bool = True
    na_reason: NaReason | None = None
    code: str
    label: str
    snap_date: date | None
    fy: list[FyEstimateView]
    ntm: NtmView | None
    target: TargetView | None
    opinions: OpinionDist | None
    revisions: list[RevisionView]
    recent: list[OpinionView]
    lost: list[str]


# ── 밸류에이션(로그인) ────────────────────────────────────────────────────────────────


class MultipleCell(ApiModel):
    value: float | None
    applicable: bool
    na_reason: NaReason | None
    out_of_cap: bool = False  # 거름값(config/fin.yaml valuation.caps) 밖 — 저장은 하되 표시


class ValuationCurrent(ApiModel):
    """그날(전 거래일) PIT 배수 — t 까지 공시된 정기보고서 분기만(D-P4-11)."""

    date: date
    price: float | None
    ttm_ref: str | None  # 그날 쓴 마지막 분기(`2026Q2:CFS`)
    ev: int | None
    per: MultipleCell
    pbr: MultipleCell
    psr: MultipleCell
    ev_ebitda: MultipleCell
    per_fwd: MultipleCell
    psr_fwd: MultipleCell
    ev_ebitda_fwd: MultipleCell
    quality: Quality


class BandView(ApiModel):
    metric: Literal["per", "pbr", "psr", "ev_ebitda"]
    as_of: date
    p10: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    p90: float | None
    min: float | None
    max: float | None
    n_days: int
    n_excluded: int
    current: float | None
    current_rank_pct: float | None  # 100 × #{x ≤ 현재} ÷ n(경험적 — ADR 0016)
    window_from: date | None
    applicable: bool
    na_reason: NaReason | None
    quality: Quality


class SeriesPoint(ApiModel):
    """밴드 차트 한 점(주 1점 — 그 주 마지막 거래일). 화면 밴드가 = 배수_p × price ÷ value."""

    date: date
    price: float | None
    value: float | None


class ClassCap(ApiModel):
    code: str
    share_class: Literal["common", "pref"]
    close: float | None
    shares: int | None
    mktcap: int | None


class Valuation(ApiModel):
    """밸류에이션(§4.8). 우선주는 회사 배수(보통 + 우선 시총 — 보통주와 같은 값)."""

    applicable: bool = True
    na_reason: NaReason | None = None  # 모든 칸이 같은 사유일 때만
    code: str
    metric: Literal["per", "pbr", "psr", "ev_ebitda"]  # series 의 지표
    current: ValuationCurrent | None
    bands: list[BandView]
    series: list[SeriesPoint]
    mktcap_company: int | None
    classes: list[ClassCap]


# ── 시나리오 적정가(로그인 — EPS × 배수, D-P4-12) ──────────────────────────────────────


class ScenarioCase(ApiModel):
    case: Literal["bear", "base", "bull"]
    method: Literal["PER", "PBR", "INSUFFICIENT"]
    eps: float | None  # PBR 폴백이면 BPS(원/주)
    eps_source: str | None  # CONSENSUS·TTM_EPS·BASE_×0.7 …
    multiple: float | None
    multiple_source: str | None  # 5Y_P25·5Y_P50·5Y_P75 …
    tp: float | None  # 적정가(원 — 정수 반올림은 화면)
    upside_pct: float | None
    na_reason: NaReason | None
    quality: Quality


class ScenarioCases(ApiModel):
    bear: ScenarioCase | None
    base: ScenarioCase | None
    bull: ScenarioCase | None


class Scenario(ApiModel):
    applicable: bool = True
    na_reason: NaReason | None = None
    code: str
    as_of: date | None
    method: Literal["PER", "PBR", "INSUFFICIENT"]
    cases: ScenarioCases
    price: float | None  # 업사이드 기준(전 거래일 종가)


# ── 관심종목(로그인·개인) ──────────────────────────────────────────────────────────────


class WatchRow(ApiModel):
    code: str
    name: str | None
    sort: int
    added_at: AwareDatetime | None
    note: str | None
    source: str
    market: str | None
    kind: Kind | None
    date: date | None  # 시세 날짜
    close: float | None
    chg_pct: float | None
    mktcap: int | None
    quality: Quality | None  # 시세가 없으면 None


class Watchlist(ApiModel):
    list_name: str
    n: int
    items: list[WatchRow]
