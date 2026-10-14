"""`config/diag.yaml` 해석기 — 페이지 3 진단 지표의 창·구간·성분·판정 문턱.

P7 — FND-B 가 읽기 틀, DIAG 가 값.

근거: docs/p7_design.md §3.6, docs/metrics.md §D0~§D9(부록 A). 숫자는 이 파일 한 곳 — 엔진은 이 값을
받는다(코드에 숫자를 두 벌 두지 않는다). 모르는 키는 오류.

- 반도체·경기민감·방어 묶음은 `config/markets.yaml ribbon`(정본) — 여기 없다(`semis` 등 키를
  쓰면 오류).
- 지표 키는 저장 표(`pub_diag.indicator`·`prv_diag.indicator`)의 `key` 와 같다. 공개 지표(tier
  public)만 공개 진단(`diag.public`)이 계산한다(D-P7-15).
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = [
    "DIAG_FILE",
    "DiagConfig",
    "EventsCfg",
    "FearGreedCfg",
    "IndicatorCfg",
    "JudgeCfg",
    "load_diag",
    "parse_diag",
]

DIAG_FILE: Final = "diag.yaml"
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
Component = Literal["vol", "breadth", "credit_balance", "foreign", "credit_spread"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _ascending(v: tuple[int, ...]) -> tuple[int, ...]:
    if list(v) != sorted(set(v)) or not all(0 < x < 100 for x in v):
        raise ValueError(f"구간 경계는 0~100 사이 오름차순(겹침 없음): {v}")
    return v


class IndicatorCfg(_Model):
    tier: Literal["public", "login"]
    unit: str = Field(min_length=1)
    risk_on: Literal["high", "low", "none"]
    section: Literal["D1", "D2", "D3", "D5", "D6"]
    overheat_flag: bool = False
    fragile_flag: bool = False


class FearGreedCfg(_Model):
    components: tuple[Component, ...]
    min_components: int = Field(ge=1)
    weights: Literal["equal"]
    bands: tuple[int, int, int, int]

    _b = field_validator("bands")(_ascending)

    @model_validator(mode="after")
    def _shape(self) -> FearGreedCfg:
        if len(set(self.components)) != len(self.components):
            raise ValueError("성분이 겹친다")
        if self.min_components > len(self.components):
            raise ValueError("min_components 가 성분 수보다 크다")
        return self


class JudgeCfg(_Model):
    risk_on_at: int = Field(gt=0)
    risk_off_at: int = Field(lt=0)
    min_components: int = Field(ge=1)
    top_reasons: int | None = Field(default=None, ge=1)


class EventsCfg(_Model):
    earnings_cluster_min: int = Field(ge=1)
    universe_top: int = Field(ge=1)
    caution_days: int = Field(ge=0)
    horizon_days: int = Field(ge=1)


class DiagConfig(_Model):
    version: Literal[1]
    window: int = Field(ge=20)
    min_history: int = Field(ge=1)
    bands: tuple[int, int, int, int]
    sigma_band: float = Field(gt=0)
    change_days: int = Field(ge=1)
    indicators: dict[str, IndicatorCfg]
    fear_greed: FearGreedCfg
    gauge: JudgeCfg
    verdict: JudgeCfg
    events: EventsCfg

    _b = field_validator("bands")(_ascending)

    @field_validator("indicators")
    @classmethod
    def _keys(cls, v: dict[str, IndicatorCfg]) -> dict[str, IndicatorCfg]:
        bad = [k for k in v if not _IDENT.fullmatch(k)]
        if bad:
            raise ValueError(f"지표 키는 소문자 식별자: {bad}")
        return v

    @model_validator(mode="after")
    def _shape(self) -> DiagConfig:
        if self.min_history > self.window:
            raise ValueError("min_history 가 window 보다 길다")
        return self

    def keys(self, tier: Literal["public", "login"]) -> tuple[str, ...]:
        return tuple(k for k, c in self.indicators.items() if c.tier == tier)


def parse_diag(data: dict[str, Any]) -> DiagConfig:
    try:
        return DiagConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{DIAG_FILE}: {e}") from None


def load_diag(*, settings: Settings | None = None) -> DiagConfig:
    return parse_diag(load_yaml_mapping(DIAG_FILE, settings=settings))
