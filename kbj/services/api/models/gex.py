"""GEX 응답 모델(docs/p7_design.md §5, metrics §G0~§G8 — `/api/gex/*`, 로그인). 모양은 웨이브 1
(FND-B)이 고정한다. 바꿔야 하면 FND 담당에게 요청 — ENG 는 리더 본문만.

- 값의 단위는 GX 명세 그대로: 가격·레벨 pt, GEX 원/1%(표시 억원은 화면), IV 연율 소수, 대금 **원**
  (investor_flow 의 백만원 × 10⁶ — API 가 바꾼다).
- **섀도 지표는 값을 싣지 않는다**(§10 Q3): `flag` 가 visible 이 아니면 `value` 는 None 이고 화면은
  '섀도 검증 중' 자리만. off 는 아예 싣지 않는다.
- 행마다 quality, 봉투가 source('KBJ:gex.engine'·'KIS(옵션 체인·웹소켓)')·as_of(사이클 시각)를
  싣는다.
- 엔진이 stale 이면 `stale` + 사유, 세션 밖이면 `session_open: false`(화면 '마지막 세션 — <시각>').
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "GexFlagT",
    "GexFlow",
    "GexFlowPoint",
    "GexInvestorRow",
    "GexLevel",
    "GexLevels",
    "GexMetricPoint",
    "GexMetricSeries",
    "GexProfile",
    "GexScopeT",
    "GexStrikeBar",
]

GexScopeT = Literal["all", "nearest", "0dte"]
GexFlagT = Literal["off", "shadow", "visible"]
SessionT = Literal["day", "night"]


class GexLevel(ApiModel):
    """레벨 하나(`prv_gex.levels` — GX §3). name 예 call_wall·put_wall·abs_gamma·flip·flip_distance·
    expected_move_calendar·expected_move_trading·top_levels. 섀도면 value None."""

    name: str = Field(min_length=1)
    value: float | None
    flag: GexFlagT
    quality: Quality
    reasons: list[str] = Field(default_factory=list[str])
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])


class GexLevels(ApiModel):
    """`/api/gex/levels?scope=` — 최신 사이클 레벨 + 현재가(S_ref)."""

    scope: GexScopeT
    trade_date: date
    session: SessionT
    cycle_at: AwareDatetime
    session_open: bool
    price: float | None  # 현재가 S_ref(pt — 근월물 선물 기준, GX §1.3)
    price_quality: Quality | None
    stale: bool = False
    stale_reason: str | None = None
    levels: list[GexLevel]


class GexStrikeBar(ApiModel):
    """행사가 하나의 GEX(원/1%, GX §2.1 — 콜 롱·풋 숏 naive 딜러)."""

    mrkt_cls: str  # '' 월물 · WKM · WKI
    expiry: str = Field(pattern=r"^[0-9]{6}$")
    strike: float
    gex_call: float
    gex_put: float
    gex: float
    quality: Quality


class GexProfile(ApiModel):
    """`/api/gex/profile?scope=` — 행사가별 GEX 막대 + 현재가·Flip·월(화면 `profile`·`ladder`)."""

    scope: GexScopeT
    cycle_at: AwareDatetime
    price: float | None
    flip: float | None
    call_wall: float | None
    put_wall: float | None
    expected_move: float | None  # ±1σ(달력 — pt)
    bars: list[GexStrikeBar]


class GexMetricPoint(ApiModel):
    ts: AwareDatetime
    value: float | None  # 섀도면 None
    quality: Quality


class GexMetricSeries(ApiModel):
    """`/api/gex/metrics?name=&from=&to=` — 지표 시계열. 섀도면 points 의 값이 모두 None(상태만)."""

    name: str = Field(min_length=1)
    scope: str
    key: str = ""
    flag: GexFlagT
    points: list[GexMetricPoint]


class GexFlowPoint(ApiModel):
    """HIRO-lite 한 점(GX §6.1 — 섀도면 value None)."""

    ts: AwareDatetime
    value: float | None
    quality: Quality


class GexInvestorRow(ApiModel):
    """투자자별 선물·옵션 순매수(`prv_gex.investor_flow` 마지막 행 — 대금은 **원**)."""

    market_code: str  # K2I · WKM · WKI
    sector_code: str  # F001 · OC01 · OP01 · OC05 · OP05 · OC04 · OP04
    investor: str
    net_qty: int | None
    net_value_won: int | None  # 백만원 × 10⁶
    ts: AwareDatetime
    quality: Quality


class GexFlow(ApiModel):
    """`/api/gex/flow` — HIRO-lite·투자자별. 야간 투자자별은 수집하지 않는다(D-P7-6)."""

    hiro_flag: GexFlagT
    hiro: list[GexFlowPoint]
    investors: list[GexInvestorRow]
    night_investor_collected: bool = False  # '수집 안 함(야간)'(§6.2)
