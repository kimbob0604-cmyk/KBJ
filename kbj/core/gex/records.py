"""GX 레코드 모델 한 곳 — 엔진 산출·poller 산출·녹화 봉투·틱·분봉·세션 기록(P7 묶음 FND-B).

근거: docs/p7_design.md §1.1·D-P7-3 ⑥·D-P7-8. GX 원래 자리에서 **모델만** 옮겼다(필드·검증 그대로 —
필드 이름 = 표 열 이름, GX `data/store.py` 의 `Table` 상수가 고정):

- `services/engine/records.py` → `LevelRecord`·`MetricRecord`·`StrikeGexRecord`·`OptionIvRecord`·
  `OiChangeRecord`(표 levels·metrics·strike_gex·option_iv·oi_changes)
- `services/poller/records.py` → `ChainRecord`·`FuturesRecord`·`InvestorRecord`·`ExpiryRecord`·
  `QuarantineRecord`·`HealthEvent`(chain_snapshots·fut_board·investor_flow·series_expiries·
  ops.quarantine·ops.health_events)
- `services/recorder/envelope.py` → `RawEnvelope`(raw_messages)
- `data/store.py` → `MinuteBarRecord`·`FuturesTickRecord`·`OptionTickRecord`·`SessionLogRecord`
  (minute_bars·fut_ticks·opt_ticks·ops.session_log)

- legacy 원래 자리는 이 이름을 다시 내보낸다(shim — GX 시험이 그대로 돈다).
- 코어 규칙(계약 ③): 어댑터(`kbj.data`)를 모른다. 그래서 KIS 응답 모델(분봉·웹소켓 틱·마스터
  줄)을 받는 `from_kis`·`from_tick` 은 **모양(Protocol)** 만 요구한다 — GX 모델이 그대로
  들어온다. 투자자 종류 `Investor` 는 GX `data/kis/models.py` 와 같은 열두 값(시험이 대조).
- 시각 변환(분봉·야간 24~30시)은 `kbj.core.calendar`(정본).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from kbj.core.calendar import SessionInfo, State, TradingCalendar, day_bar_time, night_bar_time

Quality = Literal["ok", "stale", "estimated", "invalid"]
SessionName = Literal["day", "night"]
CallPut = Literal["C", "P"]
LevelScope = Literal["all", "nearest", "0dte"]
MetricScope = Literal["all", "nearest", "0dte", "series"]  # series: 만기 하나 — key 가 시리즈 라벨
Flag = Literal["off", "shadow", "visible"]
IvSource = Literal["self", "kis"]  # 자체 역산 · KIS 폴백(자체 T 로 옮긴 σ, metrics §1.5)
ExcludeReason = Literal["no_forward", "no_price", "below_min_premium", "iv_invalid"]
# 투자자 12종(GX data/kis/models.py `Investor` 와 같은 값 — KIS 필드 접두)
Investor = Literal[
    "frgn",  # 외국인
    "prsn",  # 개인
    "orgn",  # 기관계
    "scrt",  # 증권 (딜러 프록시)
    "ivtr",  # 투신
    "pe_fund",  # 사모
    "bank",  # 은행
    "insu",  # 보험
    "mrbn",  # 종금
    "fund",  # 기금
    "etc_orgt",  # 기타단체
    "etc_corp",  # 기타법인
]


# ── 입력 모양(어댑터 모델을 모르고 받는다 — 계약 ③) ──────────────────────────────────────────


class MinuteBarLike(Protocol):
    """KIS 분봉 한 개(GX `data.kis.models.MinuteBar`)."""

    @property
    def stck_bsop_date(self) -> str: ...
    @property
    def stck_cntg_hour(self) -> str: ...
    @property
    def futs_oprc(self) -> Decimal: ...
    @property
    def futs_hgpr(self) -> Decimal: ...
    @property
    def futs_lwpr(self) -> Decimal: ...
    @property
    def futs_prpr(self) -> Decimal: ...
    @property
    def cntg_vol(self) -> int | None: ...
    @property
    def acml_tr_pbmn(self) -> int | None: ...


class TickLike(Protocol):
    """웹소켓 체결 한 건(GX `data.kis.ws.FuturesTick`·`OptionTick` 공통)."""

    @property
    def session(self) -> SessionName: ...
    @property
    def tr_id(self) -> str: ...
    @property
    def code(self) -> str: ...
    @property
    def price(self) -> Decimal: ...
    @property
    def qty(self) -> int | None: ...
    @property
    def cum_vol(self) -> int | None: ...
    @property
    def cum_value(self) -> int | None: ...
    @property
    def cum_buy_qty(self) -> int | None: ...
    @property
    def cum_sell_qty(self) -> int | None: ...
    @property
    def oi(self) -> int | None: ...
    @property
    def oi_chg(self) -> int | None: ...
    @property
    def bid(self) -> Decimal | None: ...
    @property
    def ask(self) -> Decimal | None: ...


class OptionTickLike(TickLike, Protocol):
    @property
    def delta(self) -> Decimal | None: ...
    @property
    def gamma(self) -> Decimal | None: ...
    @property
    def vega(self) -> Decimal | None: ...
    @property
    def theta(self) -> Decimal | None: ...
    @property
    def rho(self) -> Decimal | None: ...
    @property
    def iv_kis(self) -> Decimal | None: ...


class MasterLike(Protocol):
    """마스터 한 줄(`kbj.data.private.kis.master.MasterRow`)."""

    @property
    def code(self) -> str: ...
    @property
    def family(self) -> str: ...
    @property
    def expiry(self) -> str | None: ...
    @property
    def strike(self) -> Decimal | None: ...
    @property
    def cp(self) -> str: ...


# ── engine 산출(GX services/engine/records.py) ──────────────────────────────────────────────

EXPIRY_PATTERN = r"^[0-9]{6}$"


def _finite(v: float | None) -> float | None:
    if v is not None and not math.isfinite(v):
        raise ValueError(f"유한하지 않은 값: {v!r}")
    return v


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date
    session: SessionName

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


class LevelRecord(_Row):
    """`levels` 한 행 — 범위(all·nearest·0dte) 하나의 핵심 레벨 하나 (metrics §3).

    name: call_wall·put_wall·abs_gamma(값 = 행사가 pt) · flip(pt) · flip_distance(%) ·
    expected_move_calendar·expected_move_trading(±1σ pt) · top_levels(값 = 레벨 수). 없으면 value
    None(해당 없음·교차 없음·invalid). detail 은 이름마다(교차 목록·기대범위·레벨 목록 등).
    reasons: 품질을 떨어뜨린 사유(입력·S_ref·예외 등). flag: 계산 당시 기능 플래그(설계 §4 —
    shadow 는 저장만, engine.levels·engine:latest 에 싣지 않는다).
    """

    scope: LevelScope
    name: str = Field(min_length=1)
    value: float | None
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])
    quality: Quality
    reasons: tuple[str, ...] = ()
    flag: Flag = "visible"  # 계산 당시 기능 플래그(004_flags) — Phase 2 핵심 기본 visible

    _value = field_validator("value")(_finite)


class MetricRecord(_Row):
    """`metrics` 한 행 — 지표 하나의 값 (metrics §2·§3·§4~§7).

    scope: all·nearest·0dte(범위 합산) 또는 series(만기 하나 — key = 시리즈 라벨 `WKM:261001`).
    flag: 계산 당시 기능 플래그(off·shadow·visible, 설계 §4) — shadow 는 저장만 하고 발행하지
    않는다.
    """

    metric: str = Field(min_length=1)
    scope: MetricScope
    key: str = ""
    value: float | None
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])
    quality: Quality
    flag: Flag

    _value = field_validator("value")(_finite)


class StrikeGexRecord(_Row):
    """`strike_gex` 한 행 — 만기 하나·행사가 하나의 딜러 GEX (metrics §2.1, 원/1%).

    제외 종목은 0. quality·excluded_oi_ratio 는 그 만기 표의 것(§2.1 — 같은 종목의 §2.2 합산 규칙),
    옛 행으로 평가한 만기면 quality 는 stale 이상(metrics §0). forward 는 그 만기의 F(pt).
    """

    mrkt_cls: str
    expiry: str = Field(pattern=EXPIRY_PATTERN)
    strike: Decimal
    gex_call: float
    gex_put: float
    gex: float
    forward: float | None
    excluded_oi_ratio: float = Field(ge=0.0, le=1.0)
    quality: Quality

    _gex = field_validator("gex_call", "gex_put", "gex", "forward")(_finite)


class OptionIvRecord(_Row):
    """`option_iv` 한 행 — 종목 하나의 자체 IV·출처·T 환산 기록·자체 그릭스 (metrics §1.1~§1.6).

    iv: 쓴 σ(연율) — 자체 역산(source self) 또는 KIS 폴백(source kis, 자체 T 로 옮긴 값).
    없으면 None(source 도 None). rescaled·t_kis·reason: `core.iv.IvResult` 그대로(검증 수정 3 —
    옵션별 IV 를 처음 저장하는 곳이 세 필드를 함께 저장). delta·gamma: 자체 그릭스 — GEX 에 든
    종목만(KIS 그릭스가 아니다). excluded: GEX 에서 뺀 사유. quality: 종목 품질(IV 품질, 전 세션
    가격이면 estimated 이상, 옛 행으로 평가한 만기면 stale 이상). quote_source: 쓴 체인 행(board
    전광판·fill 단건 보강).
    """

    mrkt_cls: str
    expiry: str = Field(pattern=EXPIRY_PATTERN)
    strike: Decimal
    cp: CallPut
    quote_source: Literal["board", "fill"]
    price: Decimal | None
    price_kind: Literal["mid", "last"] | None
    prev_session: bool
    oi: int = Field(ge=0)
    iv: float | None
    source: IvSource | None
    rescaled: bool
    t_kis: float | None
    reason: str | None
    excluded: ExcludeReason | None
    delta: float | None
    gamma: float | None
    forward: float | None
    t_years: float
    quality: Quality

    _floats = field_validator("iv", "t_kis", "delta", "gamma", "forward", "t_years")(_finite)

    @model_validator(mode="after")
    def _consistent(self) -> OptionIvRecord:
        if (self.iv is None) != (self.source is None):
            raise ValueError("iv 와 source 는 함께 있거나 함께 없다")
        if self.rescaled and self.t_kis is None:
            raise ValueError("rescaled 면 t_kis 가 있다")
        if (self.excluded is None) != (self.gamma is not None):
            raise ValueError("그릭스는 GEX 에 든 종목(excluded 없음)에만 있다")
        return self


class OiChangeRecord(_Row):
    """`oi_changes` 한 행 — 스냅샷 사이 OI 증감 (metrics §6.7). outlier: 이상치 격리(표시 제외)."""

    mrkt_cls: str
    expiry: str = Field(pattern=EXPIRY_PATTERN)
    strike: Decimal
    cp: CallPut
    oi: int = Field(ge=0)
    prev_ts: AwareDatetime | None
    prev_oi: int | None = Field(default=None, ge=0)
    change: int | None
    outlier: bool = False
    quality: Quality
    flag: Flag = "shadow"  # 계산 당시 기능 플래그(004_flags) — 새 지표 기본 shadow

    @field_validator("prev_ts")
    @classmethod
    def _prev_utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else v.astimezone(UTC)


# ── poller 산출(GX services/poller/records.py) ────────────────────────────────────────────────

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


# ── 원본 녹화 봉투(GX services/recorder/envelope.py — wrap·Batcher 는 녹화 서비스에) ──

Source = Literal["kis_ws", "kis_rest", "krx"]
Tagger = Callable[[datetime], tuple[date | None, SessionName | None]]


class RawEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    received_at: AwareDatetime
    source: Source
    tr_id: str
    key: str = ""
    payload: str | dict[str, Any]
    trade_date: date | None
    session: SessionName | None

    @field_validator("received_at")
    @classmethod
    def _to_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


# ── poller 밖 입력·틱·세션 기록(GX data/store.py — 행 변환·저장은 저장소에) ──


class _Stamped(BaseModel):
    """시장 데이터 행 공통: ts(UTC)·trade_date·session (PLAN §4.5 공통)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date
    session: Literal["day", "night"]

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


class MinuteBarRecord(_Stamped):
    """`minute_bars` 한 행. ts = 봉 시각(UTC), received_at = 적재 때 받은 시각."""

    received_at: AwareDatetime
    code: str
    market: Literal["F", "CM"]  # 주간 F · 야간 CM
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None  # 봉 체결량
    cum_value: int | None = None  # 누적 거래대금(천원)

    @field_validator("received_at")
    @classmethod
    def _received_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _market_is_session(self) -> Self:
        if (self.market == "F") != (self.session == "day"):
            raise ValueError(f"시장 {self.market} 과 세션 {self.session} 이 맞지 않는다")
        return self

    @classmethod
    def from_kis(
        cls,
        bar: MinuteBarLike,
        *,
        code: str,
        market: Literal["F", "CM"],
        received_at: datetime,
        calendar: TradingCalendar,
    ) -> MinuteBarRecord:
        """KIS 분봉 → 행. 야간 CM 봉의 (야간 시작일, 24~30시)는 core.calendar 가 푼다(설계 §8)."""
        if market == "CM":
            ts, trade_date = night_bar_time(bar.stck_bsop_date, bar.stck_cntg_hour, calendar)
            session: Literal["day", "night"] = "night"
        else:
            ts, trade_date = day_bar_time(bar.stck_bsop_date, bar.stck_cntg_hour)
            session = "day"
        return cls(
            ts=ts,
            trade_date=trade_date,
            session=session,
            received_at=received_at,
            code=code,
            market=market,
            open=bar.futs_oprc,
            high=bar.futs_hgpr,
            low=bar.futs_lwpr,
            close=bar.futs_prpr,
            volume=bar.cntg_vol,
            cum_value=bar.acml_tr_pbmn,
        )


# 마스터 family → 시장분류('' 월물 · WKM · WKI). 정본은 어댑터의 MRKT_CLS_FAMILY
# (`kbj.data.private.kis.master` — 코어가 import 할 수 없어 역방향 표를 둔다, 시험이 대조)
_FAMILY_MRKT_CLS: Final[dict[str, str]] = {
    "kospi200": "",
    "kospi200_weekly_mon": "WKM",
    "kospi200_weekly_thu": "WKI",
}


class _TickRecord(_Stamped):
    """체결 틱 공통. ts = 체결 시각(UTC — 원문 HHMMSS 를 세션 날짜로 푼 것은 ws-gateway 몫),
    seq = ws-gateway 수신 순번(같은 초·같은 종목 체결을 가른다 — 키 (ts, code, seq))."""

    received_at: AwareDatetime
    tr_id: str
    code: str
    seq: int = Field(ge=0)
    price: Decimal
    qty: int | None = None
    cum_vol: int | None = None
    cum_value: int | None = None
    cum_buy_qty: int | None = None  # 누적 매수 체결수량 (HIRO-lite, PLAN §5.5)
    cum_sell_qty: int | None = None
    oi: int | None = None
    oi_chg: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None

    @field_validator("received_at")
    @classmethod
    def _received_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


def _tick_fields(tick: TickLike) -> dict[str, Any]:
    return {
        "session": tick.session,
        "tr_id": tick.tr_id,
        "code": tick.code,
        "price": tick.price,
        "qty": tick.qty,
        "cum_vol": tick.cum_vol,
        "cum_value": tick.cum_value,
        "cum_buy_qty": tick.cum_buy_qty,
        "cum_sell_qty": tick.cum_sell_qty,
        "oi": tick.oi,
        "oi_chg": tick.oi_chg,
        "bid": tick.bid,
        "ask": tick.ask,
    }


class FuturesTickRecord(_TickRecord):
    """`fut_ticks` 한 행 (H0IFCNT0 주간 · H0MFCNT0 야간)."""

    @classmethod
    def from_tick(
        cls, tick: TickLike, *, ts: datetime, trade_date: date, seq: int, received_at: datetime
    ) -> FuturesTickRecord:
        return cls(
            ts=ts, trade_date=trade_date, seq=seq, received_at=received_at, **_tick_fields(tick)
        )


class OptionTickRecord(_TickRecord):
    """`opt_ticks` 한 행 (H0IOCNT0 주간 · H0EUCNT0 야간). 시리즈 정보는 마스터에서."""

    mrkt_cls: str | None = None  # '' 월물 · WKM · WKI (미니 등은 None)
    expiry: str | None = Field(default=None, pattern=r"^\d{6}$")
    strike: Decimal | None = None
    cp: Literal["C", "P"] | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    vega: Decimal | None = None
    theta: Decimal | None = None
    rho: Decimal | None = None
    iv: Decimal | None = None  # %, KIS 0 은 값 없음 → None

    @classmethod
    def from_tick(
        cls,
        tick: OptionTickLike,
        *,
        ts: datetime,
        trade_date: date,
        seq: int,
        received_at: datetime,
        master: MasterLike | None = None,
    ) -> OptionTickRecord:
        """master 는 이 종목코드의 마스터 줄(없으면 만기·행사가·콜풋 NULL)."""
        series: dict[str, Any] = {}
        if master is not None:
            if master.code != tick.code:
                raise ValueError(f"마스터 코드 {master.code} ≠ 틱 코드 {tick.code}")
            series = {
                "mrkt_cls": _FAMILY_MRKT_CLS.get(master.family),
                "expiry": master.expiry,
                "strike": master.strike,
                "cp": master.cp or None,
            }
        return cls(
            ts=ts,
            trade_date=trade_date,
            seq=seq,
            received_at=received_at,
            delta=tick.delta,
            gamma=tick.gamma,
            vega=tick.vega,
            theta=tick.theta,
            rho=tick.rho,
            iv=tick.iv_kis,
            **_tick_fields(tick),
            **series,
        )


class SessionLogRecord(BaseModel):
    """`session_log` 한 행 — 상태 전이, 캘린더/데이터 불일치('예상 밖 개장' 등, 설계 §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date | None = None
    session: Literal["day", "night"] | None = None
    service: str = "scheduler"
    kind: str  # transition · calendar_mismatch · unexpected_open …
    state: State | None = None
    prev_state: State | None = None
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @classmethod
    def transition(
        cls, ts: datetime, info: SessionInfo, prev: State | None, **detail: Any
    ) -> SessionLogRecord:
        """`core.calendar.state_at` 결과로 상태 전이 한 건."""
        return cls(
            ts=ts,
            trade_date=info.trade_date,
            session=info.session,
            kind="transition",
            state=info.state,
            prev_state=prev,
            detail=detail,
        )
