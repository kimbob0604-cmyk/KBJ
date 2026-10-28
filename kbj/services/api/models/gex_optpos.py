"""옵션 행사가별 투자자 추정 포지션 응답 모델 — `GET /api/gex/option-positions`(로그인).

근거: metrics §34, ADR 0044. 값은 모두 **추정치**(키움 방식 — 투자자별 합계 → 평균단가 → 가까운 두
행사가 2:1)라 quality 는 최고 estimated, OI 검산 실패 행은 invalid. 단위: 수량 계약(소수 배분 —
표시는 반올림), 가격·레벨 pt, 거리 %. 판정은 라벨 코드만(문장 없음 — 문장화는 나중에 LLM).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "OptPosDayStat",
    "OptPosEwy",
    "OptPosLabelT",
    "OptPosLevel",
    "OptPosSeries",
    "OptPosSeriesDelta",
    "OptPosStrike",
    "OptPosSynthetic",
    "OptPosThresholds",
    "OptPosTop",
    "OptPosWall",
    "OptionPositions",
]

OptPosLabelT = Literal["small", "new", "flipped", "cleared", "reduced", "added", "hold"]
OptPosInvestorT = Literal["frgn", "prsn", "orgn"]
CpT = Literal["C", "P"]
MrktClsT = Literal["", "WKM", "WKI"]


class OptPosStrike(ApiModel):
    strike: float
    cp: CpT
    holding: float  # 누적 추정 보유(계약 — + 매수 우위, − 매도 우위)
    prev_holding: float  # 전 거래일까지
    change: float  # 오늘 변화
    buy: float  # 오늘 배분 매수
    sell: float  # 오늘 배분 매도
    oi: int | None  # 검산에 쓴 OI(KRX 확정 → 없으면 전광판)
    oi_source: Literal["krx", "board"] | None
    oi_ratio: float | None  # |보유| ÷ OI
    delta: float | None
    label: OptPosLabelT
    quality: Quality
    reason: str = ""


class OptPosWall(ApiModel):
    strike: float
    holding: float
    label: OptPosLabelT | None = None


class OptPosSeries(ApiModel):
    mrkt_cls: MrktClsT
    expiry: str
    label: str  # 'M:202611' · 'WKI:261101'
    last_trade_date: date | None
    accum_from: date  # 누적 시작일 — 이 날 이후 변화분(실제 잔고 아님)
    days_used: int
    days_missing: int
    strikes: list[OptPosStrike]
    call_wall: OptPosWall | None
    put_wall: OptPosWall | None
    prev_call_wall: OptPosWall | None
    prev_put_wall: OptPosWall | None
    delta_contracts: float  # Σ 보유 × 델타(선물 환산 계약)
    missing_delta: int
    invalid: int
    quality: Quality
    reasons: list[str]


class OptPosSeriesDelta(ApiModel):
    series: str
    delta: float  # 선물 환산 계약


class OptPosSynthetic(ApiModel):
    futures: float | None  # 선물 누적 순매수(계약) — futures_from 이후
    futures_from: date | None
    options: float  # 옵션 델타 합(선물 환산 계약)
    total: float | None
    by_series: list[OptPosSeriesDelta]
    missing_delta: int
    quality: Quality


class OptPosTop(ApiModel):
    series: str
    strike: float
    cp: CpT
    change: float
    holding: float
    prev_holding: float
    label: OptPosLabelT


class OptPosLevel(ApiModel):
    kind: Literal["call_wall", "put_wall", "prev_call_wall", "prev_put_wall", "futures"]
    level: float
    distance_pct: float | None  # (레벨 − 선물가) ÷ 선물가 × 100
    ewy: float | None = None  # EWY 환산(원천 없음 — 보류)


class OptPosEwy(ApiModel):
    status: Literal["unavailable", "ok"]
    reason: str


class OptPosDayStat(ApiModel):
    """묶음(시장·업종) 오늘 요약 — 배분 품질."""

    market_code: str
    sector_code: str
    minutes: int
    gap_minutes: int
    unallocated: float  # 미배분(매수 + 매도)
    last_ts: AwareDatetime | None
    reasons: list[str]


class OptPosThresholds(ApiModel):
    min_judge: int
    clear_pct: float
    reduce_pct: float
    add_pct: float
    price_stale_min: int
    near_share: float
    option_multiplier: int


class OptionPositions(ApiModel):
    trade_date: date
    investor: OptPosInvestorT
    series: list[OptPosSeries]
    synthetic: OptPosSynthetic
    top_changes: list[OptPosTop]
    levels: list[OptPosLevel]
    futures_price: float | None
    futures_today: int | None  # 오늘 선물 순매수(계약)
    day_stats: list[OptPosDayStat]
    ewy: OptPosEwy
    thresholds: OptPosThresholds
    reasons: list[str]
