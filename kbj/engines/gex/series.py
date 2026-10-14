"""시리즈 키·확정 베이시스 모델 — 엔진 순수 계산이 쓰는 버스 자료형(P7 묶음 ENG).

GX `services/bus.py`(`MrktCls`·`SeriesKey`·`series_label`·`BasisEntry`·`BasisBook`)와
`services/poller/context.py`(`estimate_last_trade_date` 의 계산)의 순수 부분을 여기 한 곳에 둔다.
엔진(`kbj.engines` — 계약 ⑨: 서비스·어댑터를 모른다)이 Redis·KIS 마스터를 끌어오지 않고 쓰게
옮겼다. `kbj.services.runtime.bus`·`chain_feed` 는 이 이름을 다시 내보낸다(두 벌 금지 — CLAUDE §3).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from kbj.core.calendar import (
    TradingCalendar,
    monthly_expiry,
    weekly_monday_expiry,
    weekly_thursday_expiry,
)
from kbj.core.gex.records import SessionName

__all__ = [
    "CLS_ORDER",
    "MONDAY",
    "THURSDAY",
    "BasisBook",
    "BasisEntry",
    "ExpiryCode",
    "MrktCls",
    "SeriesKey",
    "last_trade_date_of",
    "nth_weekday",
    "series_label",
]

MrktCls = Literal["", "WKM", "WKI"]  # 월물리스트 시장분류 — '' 월물 · WKM · WKI
ExpiryCode = Annotated[str, Field(pattern=r"^[0-9]{6}$")]  # 6자리 mtrt_yymm
SeriesKey = tuple[MrktCls, ExpiryCode]

CLS_ORDER: Final = {"": 0, "WKM": 1, "WKI": 2}  # 같은 날 만기면 월물 먼저(가장 큰 OI)
MONDAY: Final = 0
THURSDAY: Final = 3


def series_label(key: SeriesKey) -> str:
    """poller `Series.label` 과 같은 표기 — '' 월물은 `M:202610`, 위클리는 `WKM:261001`."""
    cls, expiry = key
    return f"{cls or 'M'}:{expiry}"


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    if d.month != month:
        raise ValueError(f"{year}-{month} 에 {n}번째 요일이 없다")
    return d


def last_trade_date_of(cls: str, mtrt: str, cal: TradingCalendar) -> date | None:
    """캘린더로 계산한 최종거래일 (교차검증용, PLAN §6.2). 형식이 맞지 않으면 None.

    위클리 YYMMWW 의 WW 는 그 달 n번째 월요일(WKM)·목요일(WKI)로 읽는다 — 실측 두 건
    (2609W4 = 09-28, 2610W1 = 10-01·WKM 10-05 휴장 → 10-06)과 맞지만 규칙 자체는 확인 필요.
    """
    m = mtrt
    if len(m) != 6 or not m.isdigit() or cls not in CLS_ORDER:
        return None
    try:
        if cls == "":
            return monthly_expiry(int(m[:4]), int(m[4:]), cal)
        year, month, n = 2000 + int(m[:2]), int(m[2:4]), int(m[4:])
        if cls == "WKM":
            return weekly_monday_expiry(nth_weekday(year, month, MONDAY, n), cal)
        return weekly_thursday_expiry(nth_weekday(year, month, THURSDAY, n), cal)
    except ValueError:
        return None


def _utc(v: datetime) -> datetime:
    return v.astimezone(UTC)


class BasisEntry(BaseModel):
    """시리즈 하나의 확정 베이시스(pt) — 그 만기의 마지막 품질 ok F − 그때 근월물 선물가
    (`core.forward.confirm_basis`). trade_date·session 은 확정한 세션 — 나이는
    `core.forward.basis_age`(같은 저녁 야간 0, 다음 주간 1), 이월 상한 2거래일(metrics §1.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    basis: Decimal
    trade_date: date
    session: SessionName
    confirmed_at: AwareDatetime

    _at = field_validator("confirmed_at")(_utc)

    @field_validator("basis")
    @classmethod
    def _finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError(f"베이시스는 유한해야 한다: {v}")
        return v


class BasisBook(BaseModel):
    """`engine:basis` — 근월물 코드와 시리즈 라벨(`series_label`) → 확정 베이시스.

    near_code 가 지금 근월물과 다르면(분기 만기일 15:20 뒤 차월물로 롤) 옛 근월물 기준이라 비운다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    near_code: str | None = None
    entries: dict[str, BasisEntry] = Field(default_factory=dict[str, BasisEntry])
