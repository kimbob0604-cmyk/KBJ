"""`config/rules.yaml` 해석기 — 규칙 알림·백테스트의 숫자 한 곳(P8 묶음 FND).

근거: docs/p8_design.md §3.5, docs/metrics.md §30.
엔진(`kbj.engines.rules`·`kbj.engines.backtest`)의
계산 함수는 이 값을 **인자로** 받는다(코드에 숫자를 두 벌 두지 않는다). 모르는 키는 오류.

- `rule_examples.yaml`(예시 10개)은 DSL 모델로 검사하므로 이 해석기가 아니라
  `kbj.engines.rules.dsl.load_examples` 가 읽는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = [
    "RULES_FILE",
    "AdhocCfg",
    "CooldownCfg",
    "CostsCfg",
    "DslCfg",
    "RulesConfig",
    "load_rules_cfg",
    "parse_rules_cfg",
]

RULES_FILE: Final = "rules.yaml"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CooldownCfg(_Model):
    default: int = Field(ge=1)
    floor: int = Field(ge=1)

    @model_validator(mode="after")
    def _order(self) -> CooldownCfg:
        if self.default < self.floor:
            raise ValueError("cooldown_min.default 가 floor 보다 작다")
        return self


class DslCfg(_Model):
    max_depth: int = Field(ge=1, le=5)
    max_conditions: int = Field(ge=1, le=50)


class CostsCfg(_Model):
    fee_bps: float = Field(ge=0, le=100)
    sell_tax_pct: float = Field(ge=0, le=5)
    slippage_bps: float = Field(ge=0, le=100)


class AdhocCfg(_Model):
    max_codes: int = Field(ge=1)
    max_years: int = Field(ge=1)
    timeout_s: int = Field(ge=1)
    cache_ttl_s: int = Field(ge=0)


class MatchCfg(_Model):
    window_days: int = Field(ge=1)


class NightlyCfg(_Model):
    years: int = Field(ge=1, le=20)


class RetentionCfg(_Model):
    watch_quote_slot_days: int = Field(ge=1)


class StatsCfg(_Model):
    min_samples: int = Field(ge=1)


class RulesConfig(_Model):
    version: Literal[1]
    anchor_date: date
    horizons: tuple[int, ...]
    cooldown_min: CooldownCfg
    intraday_max_codes: int = Field(ge=1, le=200)
    dsl: DslCfg
    index: dict[Literal["KOSPI", "KOSDAQ"], str]
    costs: CostsCfg
    adhoc: AdhocCfg
    match: MatchCfg
    nightly: NightlyCfg
    retention: RetentionCfg
    stats: StatsCfg

    @field_validator("horizons")
    @classmethod
    def _horizons(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if not v or list(v) != sorted(set(v)) or v[0] < 1:
            raise ValueError(f"horizons 는 1 이상 오름차순(겹침 없음): {v}")
        return v

    @field_validator("index")
    @classmethod
    def _index(cls, v: dict[str, str]) -> dict[str, str]:
        if set(v) != {"KOSPI", "KOSDAQ"} or not all(c.strip() for c in v.values()):
            raise ValueError(f"index 는 KOSPI·KOSDAQ 둘 다(코드 비면 안 됨): {v}")
        return v


def parse_rules_cfg(data: dict[str, Any]) -> RulesConfig:
    try:
        return RulesConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{RULES_FILE}: {e}") from None


def load_rules_cfg(*, settings: Settings | None = None) -> RulesConfig:
    return parse_rules_cfg(load_yaml_mapping(RULES_FILE, settings=settings))
