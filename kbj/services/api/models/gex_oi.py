"""코스피200 선물 미결제약정(OI) 패널 응답 모델 — `GET /api/gex/futures-oi`(로그인).

근거: metrics §32, ADR 0042.
- 행마다 source·as_of·quality(절대 규칙 1). 확정 행 = KRX 일별(전 거래일분 — 다음 날
  08:05), 잠정 행 = KIS 선물 전광판(오늘 — estimated). 투자자 칸도 각자 ts·quality·사유.
- 단위: OI·순매수 수량 계약, 대금 **원**(investor_flow 백만원 × 10⁶ — API 가 바꾼다),
  가격 pt, 비율 %.
- 라벨은 규칙 결과(`label` 코드 + `label_text` 한국어 + `reasons`) — 주체를 단정하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "FutOiDay",
    "FutOiInvestor",
    "FutOiLabelT",
    "FutOiOption",
    "FutOiThresholds",
    "FuturesOi",
]

FutOiLabelT = Literal["new_long", "new_short", "short_cover", "long_liquidation", "flat", "unknown"]
OiInvestorT = Literal["foreign", "institution", "individual"]


class FutOiInvestor(ApiModel):
    """코스피200 선물(K2I·F001) 투자자 하나의 그날 순매수 — investor_flow 정규장 마지막 행."""

    investor: OiInvestorT
    net_qty: int | None  # 계약
    net_value_won: int | None  # 원
    as_of: AwareDatetime  # 그 행 ts(수집 슬롯)
    quality: Quality
    reason: str = ""  # 예: '선물 마감(15:45) 전 마지막 슬롯 15:40'


class FutOiDay(ApiModel):
    """하루 — 합계 OI·증감·근월물 가격·해석 라벨·선물 투자자 순매수."""

    trade_date: date
    provisional: bool  # true = KRX 확정 전 전광판 잠정
    source: str = Field(min_length=1)
    as_of: AwareDatetime
    quality: Quality
    total_oi: int | None  # 전 월물 합(계약)
    oi_change: int | None  # 만기 종목 제외 증감(라벨 입력)
    oi_change_raw: int | None  # 합계 단순 차(표시만)
    oi_change_pct: float | None  # %
    near_expiry: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    near_oi: int | None
    near_close: float | None  # pt
    near_change: float | None  # pt
    near_change_pct: float | None  # %
    expiry_day: bool
    label: FutOiLabelT
    label_text: str
    reasons: list[str] = Field(default_factory=list[str])
    flows: list[FutOiInvestor] = Field(default_factory=list[FutOiInvestor])


class FutOiOption(ApiModel):
    """옵션 투자자 일별 순매수 — 월물(K2I OC01/OP01)·위클리 월(WKM)·위클리 목(WKI) 콜/풋."""

    trade_date: date
    series: Literal["monthly", "weekly_mon", "weekly_thu"]
    cp: Literal["C", "P"]
    investor: OiInvestorT
    net_qty: int | None
    net_value_won: int | None
    as_of: AwareDatetime
    quality: Quality
    reason: str = ""


class FutOiThresholds(ApiModel):
    """라벨 보합 임계(%) — 기본값 [확인 필요](엔진 상수)."""

    price_flat_pct: float
    oi_flat_pct: float
    board_sanity_pct: float


class FuturesOi(ApiModel):
    """`/api/gex/futures-oi?days=` — 확정 행(오래된 것부터)·잠정 행·옵션 일별·외국인 연속일."""

    days: list[FutOiDay]
    provisional: FutOiDay | None
    options: list[FutOiOption]
    foreign_streak: int | None  # + 매수 연속·− 매도 연속(거래일)
    thresholds: FutOiThresholds
    basis: Literal["total"] = "total"  # OI 기준 — 전 월물 합(근월물은 참고 칸)
