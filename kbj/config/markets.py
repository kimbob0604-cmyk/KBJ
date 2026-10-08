"""`config/markets.yaml`·`config/calendar_events.yaml` 해석기 — 시장·수급·ETF 튜닝값은 이 한 곳
(docs/p3_design.md §3.9, D-P3-9·15).

- 모르는 키는 오류(`extra="forbid"` — 철자 틀린 기준값이 조용히 기본값으로 돌지 않게).
- 금액은 원 단위 정수(metrics §0). 값의 근거·[확인 필요] 는 yaml 주석에 있고, 여기는 형식·범위만
  본다.
- 수집 처리기(C)·엔진(E2·E3)·API(A)·공개 내보내기(X)가 `load_markets()`·
  `load_calendar_events()` 로 읽기만 한다. 캐시하지 않는다 — 자주 읽는 쪽이 결과를 들고 있는다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

MARKETS_FILE: Final = "markets.yaml"
EVENTS_FILE: Final = "calendar_events.yaml"
_CODE = re.compile(r"[0-9A-Z]{4,6}")

KisVenue = Literal["KRX", "NXT", "TOTAL"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class KisCfg(_Model):
    venues: tuple[KisVenue, ...] = Field(min_length=1)

    @field_validator("venues")
    @classmethod
    def _unique(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(v)) != len(v):
            raise ValueError("venues 가 겹친다")
        return v


class ScreenCfg(_Model):
    min_avg_turnover_krw: int = Field(ge=0, strict=True)  # 원 — float 를 받지 않는다
    spike_min: float = Field(gt=1.0)
    spike_min_prior_days: int = Field(ge=1, le=19)  # 급증 배수 분모가 직전 19영업일
    streak_min: int = Field(ge=1)


class PremiumCfg(_Model):
    default: float = Field(gt=0)
    overseas: float = Field(gt=0)
    bond: float = Field(gt=0)


class HoldingsCfg(_Model):
    qty_floor: float = Field(ge=0)
    action_pp: float = Field(gt=0)
    max_gap_days: int = Field(ge=1)
    min_base_for_cu: int = Field(ge=1)


class EtfCfg(_Model):
    premium_warn_pct: PremiumCfg
    watch_top_n: int = Field(ge=1)
    investor_min_net_asset_krw: int = Field(ge=0, strict=True)
    split_tol: float = Field(gt=0, lt=0.5)
    split_ratios: tuple[int, ...] = Field(min_length=1)
    holdings: HoldingsCfg

    @field_validator("split_ratios")
    @classmethod
    def _ratios(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if any(r < 2 for r in v) or len(set(v)) != len(v):
            raise ValueError("split_ratios 는 2 이상, 겹치지 않게")
        return v


class ReconcileCfg(_Model):
    close_tol_pct: float = Field(ge=0)
    turnover_tol_pct: float = Field(ge=0)
    mktcap_tol_pct: float = Field(ge=0)
    warn_mismatch_pct: float = Field(gt=0, le=100)


def _codes(v: tuple[str, ...] | dict[str, str]) -> None:
    for c in v:
        if not _CODE.fullmatch(c):
            raise ValueError(f"코드 형식(대문자·숫자 4~6자): {c!r}")


class MarketCfg(_Model):
    intraday_indices: dict[str, str] = Field(min_length=1)  # KIS 업종 코드 → 이름
    sector_indices: tuple[str, ...] = ()  # [실측 필요] — 비어 있으면 히트맵은 '준비 중'
    limit_move_pct: float = Field(gt=0, le=30)

    @field_validator("intraday_indices", "sector_indices")
    @classmethod
    def _check_codes(cls, v: Any) -> Any:
        _codes(v)
        return v


class RibbonCfg(_Model):
    semis: tuple[str, ...] = Field(min_length=1)
    cyclical: tuple[str, ...] = ()
    defensive: tuple[str, ...] = ()

    @field_validator("semis", "cyclical", "defensive")
    @classmethod
    def _check_codes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        _codes(v)
        return v


class MarketsConfig(_Model):
    version: Literal[1]
    kis: KisCfg
    screen: ScreenCfg
    etf: EtfCfg
    reconcile: ReconcileCfg
    market: MarketCfg
    ribbon: RibbonCfg


class CalendarEvent(_Model):
    kind: Literal["bok_mpc"]  # 금통위 통화정책방향 결정(P5 경제 캘린더가 늘린다)
    date: date
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)


class CalendarEvents(_Model):
    version: Literal[1]
    events: tuple[CalendarEvent, ...]

    @field_validator("events")
    @classmethod
    def _sorted_unique(cls, v: tuple[CalendarEvent, ...]) -> tuple[CalendarEvent, ...]:
        keys = [(e.kind, e.date) for e in v]
        if len(set(keys)) != len(keys):
            raise ValueError("같은 (kind, date) 일정이 두 번 있다")
        if keys != sorted(keys, key=lambda k: (k[1], k[0])):
            raise ValueError("일정은 날짜 순으로 적는다")
        return v

    def upcoming(self, today: date, kind: str = "bok_mpc") -> CalendarEvent | None:
        """today 이후(당일 포함) 가장 가까운 일정 — 상단 띠 D-n. 없으면 None."""
        later = [e for e in self.events if e.kind == kind and e.date >= today]
        return later[0] if later else None


def parse_markets(data: dict[str, Any]) -> MarketsConfig:
    try:
        return MarketsConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{MARKETS_FILE}: {e}") from None


def parse_calendar_events(data: dict[str, Any]) -> CalendarEvents:
    try:
        return CalendarEvents.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{EVENTS_FILE}: {e}") from None


def load_markets(*, settings: Settings | None = None) -> MarketsConfig:
    return parse_markets(load_yaml_mapping(MARKETS_FILE, settings=settings))


def load_calendar_events(*, settings: Settings | None = None) -> CalendarEvents:
    return parse_calendar_events(load_yaml_mapping(EVENTS_FILE, settings=settings))
