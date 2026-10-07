"""engine 산출 레코드 — `levels`·`metrics`·`strike_gex`·`option_iv`·`oi_changes` 한 행씩
(docs/phase3_design.md §2, db/migrations/003_engine.sql·004_flags.sql).

- 모든 행: `ts`(사이클 기준 시각 as_of, UTC)·`trade_date`·`session`·`quality`(ok|stale|estimated|
  invalid). `levels`·`metrics`·`oi_changes` 는 계산 당시 기능 플래그 `flag`(설계 §4). 필드 이름 =
  표 열 이름(data/store.py `Table` 상수 — 단위 테스트가 고정)
- 금액은 원(GEX 는 원/기초 1%, 표시 억원), 가격·행사가·F 는 pt, IV 는 연율 소수, T 는 년
- 값(float)은 유한해야 한다 — nan·inf 는 계산 결함이라 쓰기 전에 막는다
- 같은 사이클을 다시 계산하면 같은 키 — 저장은 DO UPDATE(멱등)
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Quality = Literal["ok", "stale", "estimated", "invalid"]
SessionName = Literal["day", "night"]
LevelScope = Literal["all", "nearest", "0dte"]
MetricScope = Literal["all", "nearest", "0dte", "series"]  # series: 만기 하나 — key 가 시리즈 라벨
Flag = Literal["off", "shadow", "visible"]
CallPut = Literal["C", "P"]
IvSource = Literal["self", "kis"]  # 자체 역산 · KIS 폴백(자체 T 로 옮긴 σ, metrics §1.5)
ExcludeReason = Literal["no_forward", "no_price", "below_min_premium", "iv_invalid"]

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
