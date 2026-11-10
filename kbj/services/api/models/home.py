"""첫 화면 묶음 `/api/home` 응답 모델(docs/metrics.md §32, ADR 0043).

- 카드마다 따로 봉투 칸(source·as_of·quality·notes)과 상태를 싣는다 — `ok`(값), `no_data`(안내
  문구 `message` — '아직 없음 — <작업> <시각>'), `error`(오류 코드 `error` — 본문·내부 문구 없음).
  한 카드가 실패해도 다른 카드는 그대로다(절대 규칙 4 — 격리).
- 값은 기존 읽기 함수의 결과를 고르고 자른 것이다(계산 두 벌 금지). 새로 계산한 값은 급등·급락
  (`MarketMovers` — metrics §6.8)뿐이고 그것도 `/api/market/movers` 와 같은 함수다.
- **개인 데이터 없음**: 관심종목·규칙 신호·관심종목 가산이 든 공시 중요도(importance·login_score)·
  잠정실적 알림 여부(alerted)는 싣지 않는다 — 공개 사이트가 인자 없는 GET 을 모두 올린다(ADR 0042).
- `page` = '더 보기'가 갈 페이지 번호, `keys` = 같은 값을 주는 기존 경로('/api/' 뒤 — 쿼리 포함).
- 금액은 원 단위 정수(보드의 억원 값도 원으로 바꿨다), 등락률은 %, 수출 YoY 는 비율(0.123 = +12.3%).
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel
from kbj.services.api.models.diag import DiagFearGreed, DiagVerdict
from kbj.services.api.models.etf import TypeRollup
from kbj.services.api.models.market import IndexTile, MarketMovers
from kbj.services.api.models.program import ProgramDayBar
from kbj.services.api.models.theme import ThemeStrengthRow

__all__ = [
    "CardStatus",
    "Home",
    "HomeBreadth",
    "HomeCard",
    "HomeCards",
    "HomeDiag",
    "HomeEtf",
    "HomeEtfRow",
    "HomeExport",
    "HomeFilingRow",
    "HomeFilings",
    "HomeFlowRow",
    "HomeForeignInst",
    "HomeFutures",
    "HomeGex",
    "HomeIndex",
    "HomeIndices",
    "HomeInvestorNet",
    "HomeNewhigh",
    "HomeNewhighRow",
    "HomePrelimRow",
    "HomeProgram",
    "HomeSpike",
    "HomeSpikeRow",
    "HomeThemes",
    "HomeUpDown",
]

CardStatus = Literal["ok", "no_data", "error"]


class HomeCard[T](ApiModel):
    """카드 하나 — 상태 + 봉투 칸 + 값. ok 면 data·source·as_of·quality 가 있고, no_data 면 message,
    error 면 error 코드가 있다(그 둘은 data 없음)."""

    status: CardStatus
    page: int = Field(ge=1, le=13)
    keys: list[str]
    source: str | None = None
    as_of: AwareDatetime | None = None
    quality: Quality | None = None
    notes: list[str] = Field(default_factory=list[str])
    message: str | None = None  # no_data — 서버 안내 문구('아직 없음 — <작업> <시각>')
    error: str | None = None  # error — 사유 코드(unavailable·unknown_code·internal)
    data: T | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "ok":
            missing = [
                k for k in ("data", "source", "as_of", "quality") if getattr(self, k) is None
            ]
            if missing:
                raise ValueError(f"ok 카드에 없는 칸: {missing}")
        elif self.data is not None:
            raise ValueError(f"{self.status} 카드에는 값이 없다")
        elif self.status == "no_data" and not self.message:
            raise ValueError("no_data 카드에는 안내 문구가 있어야 한다")
        elif self.status == "error" and not self.error:
            raise ValueError("error 카드에는 사유 코드가 있어야 한다")
        return self


# ── 지수·선물 ─────────────────────────────────────────────────────────────────────────────


class HomeInvestorNet(ApiModel):
    """시장 하나의 투자자 순매수(원). 값이 없는 구분은 None — 0 으로 바꾸지 않는다.

    basis: `minute` = 장중 1분 누적(KIS 잠정 — `flows/market-minute`, 개인 prsn·외국인 frgn·기관
    orgn·기타법인 etc_corp), `daily` = 마감 일별(`flows/investors` by_market)."""

    basis: Literal["minute", "daily"]
    date: date
    individual: int | None
    foreign: int | None
    institution: int | None
    other_corp: int | None
    source: str = Field(min_length=1)
    as_of: AwareDatetime
    quality: Quality


class HomeIndex(ApiModel):
    tile: IndexTile  # market/summary 지수 타일 그대로(값·등락·live·20일 spark·원천)
    investors: HomeInvestorNet | None  # 그 시장 투자자 순매수 — 없으면 None(사유는 카드 notes)


class HomeUpDown(ApiModel):
    """상승·보합·하락 — **코스피+코스닥 합산**(market/summary breadth, 마감 확정). 시장별은 없다."""

    date: date
    up: int
    flat: int
    down: int
    unknown: int  # 등락률을 모르는 종목(보합으로 넣지 않는다)
    total: int  # 등락률을 아는 종목
    source: str
    quality: Quality | None


class HomeIndices(ApiModel):
    kospi: HomeIndex | None
    kosdaq: HomeIndex | None
    breadth: HomeUpDown | None


class HomeFutures(ApiModel):
    """코스피200 선물(근월물). 전일 대비 등락률 칸은 없다(전일 정산가를 API 에 싣지 않는다 —
    notes). 야간이면 `night`(주간 마지막 값 대비 등락)."""

    trade_date: date | None  # GEX 사이클의 귀속 거래일
    session: Literal["day", "night"] | None
    session_open: bool | None  # 지금 장(주간·야간)이 열려 있나
    cycle_at: AwareDatetime | None  # 가격을 잡은 엔진 사이클 시각
    price: float | None  # pt — 엔진 F_ref, 없으면 전광판 근월물(estimated)
    price_quality: Quality | None
    stale: bool | None  # 장 중인데 사이클이 늦다
    night: IndexTile | None  # market/summary K200N 타일(있을 때만)
    investors: HomeInvestorNet | None  # 투자자 순매수(1분 누적 — flows/market-minute k200_fut)


# ── 공시 ─────────────────────────────────────────────────────────────────────────────────


class HomeFilingRow(ApiModel):
    """공시 한 건 — 키워드 점수(kw_score, 공개 규칙)로 고른 것. 관심종목 가산 중요도는 싣지
    않는다."""

    rcept_no: str
    first_seen_at: AwareDatetime  # 우리가 처음 본 시각(DART 응답은 날짜만)
    corp_name: str
    stock_code: str | None
    title: str
    kind: str
    kw_score: int
    kw_matched: list[str]
    link: str


class HomePrelimRow(ApiModel):
    """잠정실적 한 건(서프·쇼크 갈래 — metrics §14). 금액은 원, 비율은 %(전년동기 대비)."""

    rcept_no: str
    corp_name: str | None
    stock_code: str | None
    label: str  # 회계기간 이름(예 '2026 3Q')
    scope: Literal["consolidated", "standalone", "subsidiary"]
    signal: str  # 분류 이름(TURNAROUND·YOY_BIG_UP …)
    side: Literal["surprise", "shock"]
    priority: int | None  # 작을수록 중요(1·2·5)
    revenue_cur: int | None
    op_cur: int | None
    rev_yoy: float | None
    op_yoy: float | None
    is_correction: bool
    quality: Quality
    link: str


class HomeFilings(ApiModel):
    date: date  # 오늘이 거래일이면 오늘, 아니면 직전 거래일
    n_total: int  # 상장사 소식 수(피드 기본 — 발행 서류 뺌)
    by_kind: dict[str, int]  # 그날 종류별 수(거르기 전 — 피드 by_kind 그대로)
    top: list[HomeFilingRow]  # 키워드 점수 상위 8(정정 공시·점수 0 뺌)
    n_prelim: int  # 그날 잠정실적 건수(전부)
    n_surprise: int  # 서프 갈래(자회사 범위 뺌)
    n_shock: int  # 쇼크 갈래(자회사 범위 뺌)
    prelims: list[HomePrelimRow]  # 서프·쇼크 상위 4


# ── 종목 순위 카드 ─────────────────────────────────────────────────────────────────────────


class HomeSpikeRow(ApiModel):
    code: str
    name: str | None
    market: str | None
    spike_mult: float | None  # 그날 거래대금 ÷ 직전 19영업일 평균(배)
    turnover: int | None  # 그날 거래대금(원)
    chg_pct: float | None
    mktcap: int | None
    quality: Quality


class HomeSpike(ApiModel):
    """거래대금 급증(flows/screen mode=spike period=1) — 확정 원장(장중엔 직전 거래일)."""

    date: date | None
    min_turnover: int  # 그날 거래대금 하한(원 — screen.min_avg_turnover_krw)
    spike_min: float  # 배수 하한(screen.spike_min)
    n_hits: int  # 두 하한을 넘은 보통주 수
    rows: list[HomeSpikeRow]  # 배수 큰 순 10


class HomeFlowRow(ApiModel):
    code: str
    name: str | None
    market: str | None  # 장중 가집계 행은 None
    value: int  # 순매수(원) — 장중이면 증권사 가집계(잠정)
    chg_pct: float | None  # 마감 기준만(장중 가집계에는 등락률이 없다)
    mktcap: int | None


class HomeForeignInst(ApiModel):
    """외국인·기관 순매수 상위 5씩. basis `intraday` = 오늘 장중 가집계(flows/intraday, 잠정),
    `close` = 마감 원장 1일(flows/screen mode=foreign·inst period=1)."""

    basis: Literal["intraday", "close"]
    date: date
    foreign: list[HomeFlowRow]
    inst: list[HomeFlowRow]


class HomeNewhighRow(ApiModel):
    code: str
    name: str | None
    market: str | None
    label: str  # hist(역사적)·w52(52주) — 종가 기준
    chg_pct: float | None
    mktcap: int | None  # 원(보드 억원 × 10⁸)


class HomeNewhigh(ApiModel):
    """신고가 보드(종가 기준) — 라벨별 수와 52주 이상 종목 시총 큰 순 8."""

    date: date
    counts: dict[str, int]  # 라벨별 종목 수(hist·w52·d120 — 겹치지 않음)
    labels: dict[str, str]  # 라벨 이름(hist → '역사적' …)
    rows: list[HomeNewhighRow]


# ── GEX·진단·프로그램·ETF·수출·테마·시장 폭 ────────────────────────────────────────────────


class HomeGex(ApiModel):
    """GEX 요약 — 국면(gex/briefing)·Flip·Flip 거리·기대변동폭(gex/levels). 섀도 값은 None."""

    trade_date: date
    session: Literal["day", "night"]
    session_open: bool
    cycle_at: AwareDatetime
    stale: bool
    price: float | None  # pt
    regime: Literal["long", "short"] | None
    regime_label: str | None
    headline: str | None
    flip: float | None  # pt
    flip_distance: float | None  # % — (현재가 − Flip) ÷ 현재가 × 100(metrics §G3.5)
    expected_move: float | None  # pt — ±1σ(달력, metrics §G3.8)


class HomeDiag(ApiModel):
    trade_date: date
    verdict: DiagVerdict | None  # 종합 판정(label·score·top)
    fear_greed: DiagFearGreed | None  # 공포탐욕(score·band)
    caution: bool  # 이벤트 주의(오늘·다음 거래일)


class HomeProgram(ApiModel):
    """프로그램 순매수 — 시장마다 마지막 날(마감 일별, 검산 ④ 통과). 장중 누적은 없다(notes)."""

    kospi: ProgramDayBar | None
    kosdaq: ProgramDayBar | None


class HomeEtfRow(ApiModel):
    code: str
    name: str | None
    etf_type_label: str
    net_inflow: int | None  # 기간 순유입(원)
    ret_pct: float | None
    quality: Quality


class HomeEtf(ApiModel):
    """ETF 순유입(기간 5일 — 페이지 10 기본). 유형은 순유입 큰 순 전부, 종목은 상위 3."""

    period: int
    start: date | None
    end: date | None
    types: list[TypeRollup]
    top: list[HomeEtfRow]


class HomeExport(ApiModel):
    """수출 속보(관세청 10일 잠정 — 상단 띠 칩과 같은 함수, metrics §25)."""

    ym: str  # 'YYYY-MM'
    seg: int  # 구간(1 = 1~10일, 2 = 1~20일, 3 = 월)
    span_text: str
    span_label: str  # '1~10일'
    usd: int  # 수출액(달러)
    yoy: float | None  # 발표 당시 전년 같은 구간 대비(비율 — 0.123 = +12.3%)
    yoy_reason: str | None  # YoY 가 없을 때 사유
    first_seen_at: AwareDatetime


class HomeThemes(ApiModel):
    trade_date: date
    period: Literal[1, 5, 20]
    rows: list[ThemeStrengthRow]  # 강도 상위 5(themes/strength 그대로)


class HomeBreadth(ApiModel):
    """시장 폭 — 20일선 위 비율(코스피+코스닥 합산, 마감 확정). 60·120일선·시장별은 없다(notes)."""

    date: date
    above_ma20_pct: float | None
    above_ma20_n: int
    ma20_base: int  # 20일선을 계산할 수 있는 종목 수(분모)


# ── 묶음 ───────────────────────────────────────────────────────────────────────────────────


class HomeCards(ApiModel):
    indices: HomeCard[HomeIndices]
    futures: HomeCard[HomeFutures]
    filings: HomeCard[HomeFilings]
    breadth: HomeCard[HomeBreadth]
    spike: HomeCard[HomeSpike]
    movers: HomeCard[MarketMovers]
    foreign_inst: HomeCard[HomeForeignInst]
    newhigh: HomeCard[HomeNewhigh]
    gex: HomeCard[HomeGex]
    diag: HomeCard[HomeDiag]
    program: HomeCard[HomeProgram]
    etf: HomeCard[HomeEtf]
    themes: HomeCard[HomeThemes]
    export: HomeCard[HomeExport]


class Home(ApiModel):
    """`/api/home` — 첫 화면 카드 묶음(카드 순서 = 위 칸 순서)."""

    date: date  # 오늘(KST)
    cards: HomeCards
    n_ok: int  # 값이 있는 카드 수
    n_no_data: int
    n_error: int
