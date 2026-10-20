"""공시·잠정실적·실적 일정·오버행·내부자 응답 모델 — 종목 상세(페이지 8)와 공시 패널(페이지 5)
(docs/p4_design.md §5.2·§5.3·§6.4).

공개 원천(DART)의 칸과 로그인에서만 읽을 때 계산하는 칸(관심종목·시총 가산 중요도·오버행 금액·ITM·
내부자 금액 — `pub_*` 에 쓰지 않는다, 설계 §2 혼합 값)을 함께 싣는다. 금액은 원 단위 정수.
보호예수는 출처가 없어 **추정 해제일만**(D-P4-18 — 언제나 estimated).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "DisclosureView",
    "Disclosures",
    "Earnings",
    "EarningsList",
    "EarningsSchedule",
    "FilingsFeed",
    "InsiderRecent",
    "InsiderRecentRow",
    "InsiderReportView",
    "Insiders",
    "InstrumentView",
    "LockupView",
    "MajorHolderView",
    "Overhang",
    "OverhangRecent",
    "OverhangRecentRow",
    "OverhangSummaryView",
    "PrelimView",
    "ScheduleView",
    "SurpriseView",
    "TimelineItem",
    "WindowView",
]

Importance = Literal["critical", "high", "medium", "low"]
Instrument = Literal["cb", "bw", "eb", "paid_in", "bonus"]


# ── 공시 ──────────────────────────────────────────────────────────────────────────────


class DisclosureView(ApiModel):
    """공시 한 건. `kw_score` = 제목 키워드 점수(공개), `login_score`·`importance` = 키워드 +
    관심종목 + 시총 가산(로그인 — 읽을 때 계산). `first_seen_at` = 우리가 처음 본 시각(DART
    응답에는 날짜만)."""

    rcept_no: str
    rcept_dt: date
    first_seen_at: AwareDatetime
    corp_code: str
    stock_code: str | None
    corp_name: str
    corp_cls: str | None
    title: str
    kind: str
    is_correction: bool
    kw_score: int | None
    kw_matched: list[str]
    login_score: int
    importance: Importance
    is_watch: bool
    parse_state: str
    link: str


class Disclosures(ApiModel):
    code: str
    corp_code: str
    days: int
    kind: str | None
    rows: list[DisclosureView]


class FilingsFeed(ApiModel):
    """페이지 5 공시 피드(하루). `by_kind` = 거르기 전 종류별 수."""

    date: date
    kind: str | None
    importance: Importance | None
    market: Literal["KOSPI", "KOSDAQ", "KONEX"] | None
    watch_only: bool
    n_total: int
    by_kind: dict[str, int]
    rows: list[DisclosureView]


# ── 잠정실적·실적 일정·서프라이즈 ──────────────────────────────────────────────────────


class PrelimView(ApiModel):
    """잠정실적 한 건(원 — 본문 단위 × unit_mult) + YoY 분류(공개 규칙 — §4.2).

    `signal`·`priority` 는 알림 작업이 남긴 분류(`prv_fin.earnings_surprise`)가 있으면 그것, 없으면
    읽을 때 같은 엔진 규칙(`classify_prelim`)으로 낸다. `alerted` = 관심종목 알림을 넣었다.
    """

    rcept_no: str
    rcept_dt: date
    corp_code: str
    stock_code: str | None
    corp_name: str | None
    scope: Literal["consolidated", "standalone", "subsidiary"]
    period: str  # 2026Q3 · 2026FY
    label: str
    fiscal_year: int
    fiscal_q: int
    is_annual: bool
    period_end: date
    period_inferred: bool
    currency: str
    revenue_cur: int | None
    revenue_prev_q: int | None
    revenue_prev_y: int | None
    op_cur: int | None
    op_prev_q: int | None
    op_prev_y: int | None
    ni_cur: int | None
    ni_prev_q: int | None
    ni_prev_y: int | None
    ni_owner_cur: int | None
    eps_cur: float | None
    rev_yoy: float | None
    op_yoy: float | None
    rev_qoq: float | None
    op_qoq: float | None
    signal: str | None
    priority: int | None
    is_correction: bool
    is_watch: bool
    alerted: bool
    quality: Quality
    notes: list[str]
    link: str


class ScheduleView(ApiModel):
    """실적 일정 — pre_announce(결산실적공시예고 본문 — ok)·statutory(법정 제출기한 — 추정)."""

    corp_code: str
    stock_code: str | None
    corp_name: str | None
    period_end: date
    kind: Literal["pre_announce", "statutory"]
    planned_date: date
    fiscal_year: int
    fiscal_q: int
    rcept_no: str | None
    note: str | None
    is_watch: bool
    quality: Quality


class SurpriseView(ApiModel):
    """알림 작업의 분류·알림·성과 기록(로그인). 서프라이즈(컨센 대비)는 분기 컨센서스가 있을
    때만."""

    rcept_no: str
    period: str
    scope: Literal["consolidated", "standalone", "subsidiary"]
    signal: str
    priority: int
    rev_yoy: float | None
    op_yoy: float | None
    rev_surprise: float | None
    op_surprise: float | None
    alerted_at: AwareDatetime | None
    price_at_alert: float | None
    perf_3d: float | None
    perf_5d: float | None
    perf_7d: float | None
    quality: Quality


class Earnings(ApiModel):
    code: str
    corp_code: str
    prelims: list[PrelimView]
    schedule: list[ScheduleView]
    surprises: list[SurpriseView]


class EarningsList(ApiModel):
    """페이지 5 잠정실적(기간 — 최대 31일)."""

    from_date: date
    to_date: date
    rows: list[PrelimView]


class EarningsSchedule(ApiModel):
    """페이지 5 실적 일정(오늘부터 days 일)."""

    days: int
    rows: list[ScheduleView]


# ── 오버행 ────────────────────────────────────────────────────────────────────────────


class OverhangSummaryView(ApiModel):
    """회사 요약. 공개 칸(DART 주식총수 분모) + 로그인 칸(`listed_shares` 분모 비율·금액·ITM 주식
    수 — 전 거래일 종가 기준 추정, 시가가 아님)."""

    as_of: date
    potential_new_shares: int
    exercisable_shares: int
    exchange_shares: int  # EB 교환 대상(신주 아님 — 비율 분자에 없다)
    denominator: int | None
    denom_source: str | None
    ratio_pct: float | None
    exercisable_ratio_pct: float | None
    listed_shares: int | None  # 로그인 분모(전 거래일 KRX 상장주식수 — 보통주)
    login_ratio_pct: float | None
    amount: int | None  # 로그인 — Σ 잠재 주식 × 종가(추정)
    itm_shares: int | None  # 로그인 — 종가 ≥ 현재 전환·행사가인 잠재 주식 합
    quality: Quality
    notes: list[str]


class InstrumentView(ApiModel):
    rcept_no: str
    rcept_dt: date
    instrument: Instrument
    seq: str | None
    face_value: int | None
    remaining_face: int | None
    price: float | None  # 현재 전환·행사·교환가(조정 반영)
    refix_floor: float | None
    potential_shares: int | None
    active: bool
    exercisable: bool
    exercise_start: date | None
    exercise_end: date | None
    maturity: date | None
    listing_date: date | None
    alloc_method: str | None
    lockup_end_est: date | None  # 추정(D-P4-18)
    itm: bool | None  # 로그인
    amount: int | None  # 로그인(추정)
    quality: Quality
    notes: list[str]
    link: str


class LockupView(ApiModel):
    rcept_no: str
    listing_date: date
    lockup_end_est: date
    shares: int | None
    months: int
    basis: str  # "보호예수 출처 없음 — 법정 전매제한으로 계산한 추정 해제일"
    quality: Quality  # 언제나 estimated


class TimelineItem(ApiModel):
    rcept_no: str
    instrument: Instrument
    start: date | None  # 청구기간 시작(유상증자는 결정일)
    end: date | None  # 청구기간 끝·만기 중 이른 날(유상증자는 신주 상장일)
    active: bool


class Overhang(ApiModel):
    code: str
    corp_code: str
    close: float | None  # 로그인 칸의 기준 종가(보통주, 전 거래일)
    close_date: date | None
    summary: OverhangSummaryView | None
    instruments: list[InstrumentView]
    lockups: list[LockupView]
    timeline: list[TimelineItem]


class OverhangRecentRow(ApiModel):
    rcept_no: str
    rcept_dt: date
    corp_code: str
    stock_code: str | None
    corp_name: str | None
    instrument: Instrument
    face_value: int | None
    price: float | None
    shares: int | None
    exercise_start: date | None
    exercise_end: date | None
    ratio_pct: float | None  # 회사 오버행 비율(최근 요약 — 공개 분모, 없으면 KRX 상장주식수)
    is_watch: bool
    quality: Quality
    link: str


class OverhangRecent(ApiModel):
    days: int
    instrument: Instrument | None
    rows: list[OverhangRecentRow]


# ── 내부자·대량보유 ────────────────────────────────────────────────────────────────────


class WindowView(ApiModel):
    """보고 기준 순증감(사유 미구분 포함 — 장내 순매수가 아니다, §4.4). `amount` = Σ 증감 ×
    보고일 종가(로그인 — 추정), `amount_missing` = 종가를 몰라 금액에서 뺀 보고 수."""

    days: int
    as_of: date
    net_shares: int
    buy_shares: int
    sell_shares: int
    n_reports: int
    n_reporters: int
    ratio_pct: float | None
    reason_known_pct: float | None
    amount: int | None
    amount_missing: int
    quality: Quality


class InsiderReportView(ApiModel):
    rcept_no: str
    rcept_dt: date
    reporter: str
    position: str | None
    registered: bool | None
    major_holder: bool | None
    shares_after: int | None
    shares_delta: int | None
    ratio_after: float | None
    ratio_delta: float | None
    reason: str | None
    quality: Quality
    link: str


class MajorHolderView(ApiModel):
    rcept_no: str
    rcept_dt: date
    reporter: str
    report_type: str | None
    shares: int | None
    shares_delta: int | None
    ratio: float | None
    ratio_delta: float | None
    reason: str | None
    quality: Quality
    link: str


class Insiders(ApiModel):
    code: str
    corp_code: str
    days: int  # 보고 목록의 기간(30·90·180)
    label: str  # "보고 기준 순증감(사유 미구분 포함)"
    windows: list[WindowView]
    reports: list[InsiderReportView]  # 최근 days 일, 최대 50(정정 대체 뒤)
    major_holders: list[MajorHolderView]  # 목록만(창 합계 없음 — 이중 계산 방지)


class InsiderRecentRow(ApiModel):
    corp_code: str
    stock_code: str | None
    corp_name: str | None
    n_reports: int
    n_reporters: int
    net_shares: int
    buy_shares: int
    sell_shares: int
    ratio_pct: float | None
    last_rcept_dt: date
    is_watch: bool
    quality: Quality


class InsiderRecent(ApiModel):
    days: int
    min_ratio_pct: float | None
    label: str
    rows: list[InsiderRecentRow]
