"""poller 산출 레코드 (docs/phase1_design.md §8 chain_snapshots·fut_board·investor_flow·quarantine·
health_events, PLAN §4.5).

- 모든 레코드는 `ts`(UTC, 응답 수신 시각)와 `trade_date`·`session`(core.calendar.state_at)을 갖는다
- 모든 지표성 산출물엔 `quality`(ok|stale|estimated|invalid, CLAUDE.md). 쓰는 순간엔 ok 또는
  invalid(검증 실패)이고, stale·estimated 는 읽을 때 나이·세션으로 판정한다(`quality.py`)
- 가격·행사가·그릭스·IV 는 `Decimal`(KIS 문자열 그대로의 값), 수량은 `int`
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from data.kis.models import Investor

Quality = Literal["ok", "stale", "estimated", "invalid"]
SessionName = Literal["day", "night"]
CallPut = Literal["C", "P"]
ChainSource = Literal["board", "fill"]
HealthLevel = Literal["info", "warning", "error"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date
    session: SessionName

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


class ChainRecord(_Record):
    """`chain_snapshots` 한 행. source board = 전광판(호가 있음), fill = 단건 현재가(호가 없음)."""

    mrkt_cls: str  # '' 월물 · WKM · WKI (월물리스트 시장분류)
    expiry: str = Field(pattern=r"^\d{6}$")  # 6자리 mtrt_yymm
    strike: Decimal
    cp: CallPut
    source: ChainSource
    code: str | None = None
    last: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    oi: int | None = None
    oi_chg: int | None = None
    volume: int | None = None
    iv_kis: Decimal | None = None  # %, 0 은 값 없음(None)
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    quality: Quality = "ok"


class FuturesRecord(_Record):
    """`fut_board` 한 행 — 선물 전광판(board) 또는 야간 단건 현재가(single)."""

    code: str
    name: str | None = None
    market: str  # F · CM
    source: Literal["board", "single"]
    price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: int | None = None
    oi: int | None = None
    remaining_days: int | None = None
    quality: Quality = "ok"


class InvestorRecord(_Record):
    """`investor_flow` 긴 형식 한 행 (수량 계약, 대금 백만원 — data/kis/models.py)."""

    market_code: str  # K2I · WKM · WKI
    sector_code: str  # F001 · OC01 · OP01 · OC05 · OP05 · OC04 · OP04
    investor: Investor
    sell_qty: int | None = None
    buy_qty: int | None = None
    net_qty: int | None = None
    sell_value: int | None = None
    buy_value: int | None = None
    net_value: int | None = None
    quality: Quality = "ok"


class ExpiryRecord(_Record):
    """시리즈 최종거래일. 원천은 KIS 단건 `futs_last_tr_date`, 없으면 캘린더 계산값(§2.3)."""

    mrkt_cls: str
    expiry: str = Field(pattern=r"^\d{6}$")
    last_trade_date: date
    source: Literal["kis", "calendar"]
    calendar_date: date | None = None  # 교차검증용 계산값 (PLAN §6.2)
    matches: bool | None = None  # KIS 값과 계산값이 같은가 (둘 다 있을 때)
    code: str | None = None  # 조회에 쓴 종목코드
    quality: Quality = "ok"


class QuarantineRecord(_Record):
    """검증 실패 원본 (PLAN §6.1, 설계 §8 quarantine)."""

    source: Literal["kis_rest"] = "kis_rest"
    tr_id: str
    key: str
    payload: dict[str, Any]
    error: str


class HealthEvent(BaseModel):
    """`health_events` 한 행. 수집 중에 나므로 trade_date·session 이 보통 있지만 없을 수도 있다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date | None
    session: SessionName | None
    service: Literal["poller"] = "poller"
    kind: str
    level: HealthLevel
    message: str
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)
