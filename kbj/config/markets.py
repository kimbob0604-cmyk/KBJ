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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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

    @field_validator("intraday_indices")
    @classmethod
    def _check_codes(cls, v: Any) -> Any:
        _codes(v)
        return v

    @field_validator("sector_indices")
    @classmethod
    def _check_sector_codes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        # KRX 지수 일별에는 코드 칸이 없어 공백 뺀 이름이 코드다(krx_daily.index_code) —
        # KIS 업종 코드(대문자·숫자)도 같이 허용한다
        for c in v:
            if not c or any(ch.isspace() for ch in c):
                raise ValueError(f"업종 코드는 비지 않고 공백이 없다: {c!r}")
        if len(set(v)) != len(v):
            raise ValueError("업종 코드가 겹친다")
        return v


class RibbonCfg(_Model):
    semis: tuple[str, ...] = Field(min_length=1)
    cyclical: tuple[str, ...] = ()
    defensive: tuple[str, ...] = ()

    @field_validator("semis")
    @classmethod
    def _check_codes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        _codes(v)
        return v

    @field_validator("cyclical", "defensive")
    @classmethod
    def _check_sector_codes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        # 업종 지수 코드 — market.sector_indices 와 같은 규칙(KRX 는 공백 뺀 이름이 코드)
        return MarketCfg._check_sector_codes(v)


_PAIR_CODE = re.compile(r"[0-9A-Z]{3,4}")
_IDENT = re.compile(r"[a-z][a-z0-9_]*")


class MinutePair(_Model):
    """장중 투자자 1분 조합 하나(P5 F2 — docs/p5_design.md §3.4, D-P5-21).

    코드를 모르는 조합은 market·sector 를 비우고 꺼 둔다(지어내지 않는다). `index` = 차트에 겹칠
    지수의 KIS 업종 코드(`market.intraday_indices` 의 키 — 없으면 지수 선 없음)."""

    id: str
    market: str | None
    sector: str | None
    session: Literal["day", "night"]
    tab: str
    label: str = Field(min_length=1)
    enabled: bool
    index: str | None = None
    # 주식 정규장 마감 뒤 몇 분까지 부르나(ADR 0044 §2-1). 현물 10(15:40 — 종가 단일가 결과),
    # 선물·옵션 25(15:55 — 파생 종가 단일가 15:35~15:45 와 그 뒤 확정 누적).
    # 등록부 `flows.market_minute` 의 `end` 는 가장 큰 값.
    tail_min: int = Field(default=10, ge=0, le=60)
    note: str = ""

    @field_validator("id", "tab")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not _IDENT.fullmatch(v):
            raise ValueError(f"소문자 식별자여야 한다: {v!r}")
        return v

    @field_validator("market", "sector")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is not None and not _PAIR_CODE.fullmatch(v):
            raise ValueError(f"KIS 시장·업종 코드 형식(대문자·숫자 3~4자): {v!r}")
        return v

    @model_validator(mode="after")
    def _known_codes_when_on(self) -> MinutePair:
        if (self.market is None) != (self.sector is None):
            raise ValueError(f"{self.id}: market·sector 는 함께 비우거나 함께 준다")
        if self.enabled and self.market is None:
            raise ValueError(f"{self.id}: 코드를 모르는 조합은 켤 수 없다(실측 #39 뒤)")
        return self


class MinuteCfg(_Model):
    pairs: tuple[MinutePair, ...] = Field(min_length=1)

    @field_validator("pairs")
    @classmethod
    def _unique(cls, v: tuple[MinutePair, ...]) -> tuple[MinutePair, ...]:
        ids = [p.id for p in v]
        keys = [(p.market, p.sector, p.session) for p in v if p.market is not None]
        if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
            raise ValueError("flows.minute.pairs 의 id 또는 (시장, 업종, 세션)이 겹친다")
        return v

    def enabled_pairs(self, session: str = "day") -> tuple[tuple[str, str], ...]:
        """켠 (시장 코드, 업종 코드) — 수집·API 가 이것만 쓴다."""
        return tuple(
            (p.market, p.sector)
            for p in self.pairs
            if p.enabled and p.session == session and p.market is not None and p.sector is not None
        )


class FlowsCfg(_Model):
    minute: MinuteCfg


class ProgramCfg(_Model):
    """프로그램매매(P5 F2 — F2 §1.4·§3.4, metrics §22)."""

    scan_top_n: int = Field(ge=1, le=200)  # [확인 필요 #41]
    scan_watch_max: int = Field(ge=0, le=100)
    sweep_markets: tuple[Literal["K", "Q"], ...] = Field(min_length=1)
    daily_days: int = Field(ge=1, le=250)
    top_n: int = Field(ge=1, le=100)


class MarketsConfig(_Model):
    version: Literal[1]
    kis: KisCfg
    screen: ScreenCfg
    etf: EtfCfg
    reconcile: ReconcileCfg
    market: MarketCfg
    ribbon: RibbonCfg
    # P5(묶음 FND — docs/p5_design.md §3.6)
    flows: FlowsCfg
    program: ProgramCfg

    @model_validator(mode="after")
    def _minute_index_known(self) -> MarketsConfig:
        known = set(self.market.intraday_indices)
        bad = [
            p.id for p in self.flows.minute.pairs if p.index is not None and p.index not in known
        ]
        if bad:
            raise ValueError(f"flows.minute.pairs index 가 market.intraday_indices 에 없다: {bad}")
        return self


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
