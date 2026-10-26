"""종목 스크리너 응답 모델(페이지 11 '스크리너' — `/api/screen/cis`).

금액은 원 단위 정수, 비율은 배수·%."""

from __future__ import annotations

from datetime import date
from typing import Literal

from kbj.services.api.models.common import ApiModel

__all__ = ["CisParams", "CisRegime", "CisRowOut", "ScreenCis"]


class CisRowOut(ApiModel):
    code: str
    name: str | None
    market: Literal["KOSPI", "KOSDAQ"]
    close: float
    chg_pct: float
    volume: int
    vol_ratio: float  # 오늘 거래량 ÷ 직전 평균
    turnover: int  # 원
    avg_turnover: int  # 원(직전 평균)
    box_high: float  # 직전 창 최고가(박스권 위)
    breakout: bool
    close_pos: float  # (종가−저가)/(고가−저가)
    limit_up: bool
    regime_on: bool | None
    passed: bool
    why_not: list[str]
    stop: float  # 종가 기준 최초 손절가
    gap_skip: float  # 다음 날 시가가 이 값을 넘으면 건너뜀


class CisRegime(ApiModel):
    market: Literal["KOSPI", "KOSDAQ"]
    index_code: str | None
    close: float | None
    ma: float | None
    on: bool | None


class CisParams(ApiModel):
    """판정 문턱·다음 날 계획(config/screen.yaml — 화면이 그대로 보여 준다)."""

    lookback_days: int
    vol_mult: float
    min_turnover_won: int
    min_avg_turnover_won: int
    min_price: float
    close_pos_min: float
    limit_pct: float
    regime_ma: int
    stop_pct: float
    trail_pct: float
    gap_skip_pct: float
    add_at_pct: float
    risk_pct: float
    max_positions: int


class ScreenCis(ApiModel):
    date: date
    scanned: int  # 그날 봉이 있는 코스피·코스닥 보통주(관리·정지 뺀) 수
    spikes: int  # 거래량 급증 종목 수(표 상한 전)
    passed: int  # 통과 종목 수(표 상한 전)
    excluded: dict[str, int]
    status_unknown: int
    regimes: list[CisRegime]
    params: CisParams
    rows: list[CisRowOut]
