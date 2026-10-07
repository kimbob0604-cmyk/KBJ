"""출처별 호출 한도 — `config/limits.yaml` 을 읽어 리미터·일 예산 설정으로 바꾼다(설계 §4.2·§4.3).

- 한 파일, 한 모양: 최상위 `version: 1` + 출처 이름(소문자:
  kis·krx·dart·datago·kosis·ecos·telegram)마다 `SourceLimits`. 모르는 키는 오류(`extra="forbid"` —
  철자 틀린 상한이 조용히 무시되지 않게).
- 이 모듈이 limits.yaml 의 유일한 해석기다. A(KIS)·C(공개 어댑터)·D(로그인 어댑터)·F(등록부 검증)는
  `load_limits().source(<출처>)` 로 읽기만 한다(§11.3).
- 값의 근거와 [확인 필요] 표시는 yaml 주석에 있다. 여기에는 형식과 범위 검사만 둔다.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings
from kbj.data.ratelimit import Priority, RateLimitConfig

LIMITS_FILE: Final = "limits.yaml"
# 설계 §4.2 표에 있는 출처 — 파일에 빠지면 오류(쓰는 쪽이 기본값으로 조용히 돌지 않게)
REQUIRED_SOURCES: Final = ("kis", "krx", "dart", "datago", "kosis", "ecos", "telegram")
_SOURCE = re.compile(r"[a-z][a-z0-9_]*")

PriorityName = Literal["P0", "P1", "P2", "P3", "P4"]


class LimitsError(ConfigFileError):
    """limits.yaml 내용 오류·없는 출처."""


class SourceLimits(BaseModel):
    """한 출처의 한도. 속도·감속 값은 `RateLimitConfig` 와 같은 뜻이다(kbj/data/ratelimit.py)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── 초당 리미터(GCRA, 버킷 1 → 어떤 반열린 1초 창에도 ceil(rate) 건 이하) ──
    rate: float = Field(gt=0)
    capacity: int = Field(default=1, ge=1)
    floor_rate: float | None = Field(default=None, gt=0)  # 없으면 min(1.0, rate)
    hold_s: float = Field(default=60.0, ge=0)  # 한도초과 신호 뒤 감속 유지
    step_rate: float | None = Field(default=None, gt=0)  # 없으면 0.5 (KIS 와 같음)
    step_s: float = Field(default=60.0, gt=0)
    debounce_s: float = Field(default=1.0, ge=0)
    tr_min_interval_s: dict[str, float] = Field(default_factory=dict[str, float])
    state_ttl_s: float | None = Field(default=None, gt=0)  # 없으면 회복 시간에 맞춰 잡는다

    # ── 우선순위(KIS — 설계 §4.2 [제안]) ──
    default_priority: PriorityName = "P2"
    priorities: dict[str, PriorityName] = Field(default_factory=dict[str, PriorityName])

    # ── 일 예산(KST 날짜별 — kbj/data/budget.py) ──
    daily_cap: int | None = Field(default=None, gt=0)
    backfill_cap: int | None = Field(default=None, gt=0)
    daily_cap_per_dataset: int | None = Field(default=None, gt=0)
    close_after_throttles: int | None = Field(default=None, gt=0)  # 같은 날 감속 신호 N 회면 닫기

    # ── 텔레그램 ──
    per_chat_rate: float | None = Field(default=None, gt=0)
    retry_after_max: int | None = Field(default=None, ge=0)

    @field_validator("tr_min_interval_s")
    @classmethod
    def _non_negative(cls, v: dict[str, float]) -> dict[str, float]:
        if any(x < 0 for x in v.values()):
            raise ValueError("TR 최소 간격은 0 이상")
        return v

    @model_validator(mode="after")
    def _caps_fit(self) -> SourceLimits:
        if self.backfill_cap is not None and (
            self.daily_cap is None or self.backfill_cap > self.daily_cap
        ):
            raise ValueError("backfill_cap 은 daily_cap 안에 있어야 한다")
        self.rate_config()  # 감속 하한·회복 시간 검사(RateLimitConfig.__post_init__)를 읽을 때 한다
        return self

    def priority(self, role: str) -> Priority:
        """역할 이름(예: `close_collect`)의 우선순위. 없으면 `default_priority`."""
        return Priority[self.priorities.get(role, self.default_priority)]

    def rate_config(self) -> RateLimitConfig:
        """리미터 설정. 상태 키 수명을 안 주면 GX 기본 900초, 감속 회복이 더 길면 회복 + 60초."""
        cfg = RateLimitConfig(
            rate=self.rate,
            capacity=self.capacity,
            floor_rate=self.floor_rate if self.floor_rate is not None else min(1.0, self.rate),
            hold_s=self.hold_s,
            step_rate=self.step_rate if self.step_rate is not None else 0.5,
            step_s=self.step_s,
            debounce_s=self.debounce_s,
            tr_min_interval_s=dict(self.tr_min_interval_s),
            state_ttl_s=self.state_ttl_s if self.state_ttl_s is not None else math.inf,
        )
        if self.state_ttl_s is None:
            tr_max = max(self.tr_min_interval_s.values(), default=0.0)
            ttl = max(900.0, cfg.recovery_s() + tr_max + 60.0)
            cfg = dataclasses.replace(cfg, state_ttl_s=ttl)
        return cfg


class Limits(BaseModel):
    """limits.yaml 전체."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    sources: dict[str, SourceLimits]

    def source(self, name: str) -> SourceLimits:
        try:
            return self.sources[name]
        except KeyError:
            raise LimitsError(f"{LIMITS_FILE}: 출처 {name!r} 가 없다") from None


def parse_limits(data: Mapping[str, Any]) -> Limits:
    """매핑(yaml 을 읽은 것) → `Limits`. 형식 오류는 `LimitsError`(어느 출처인지 담는다)."""
    body = dict(data)
    version = body.pop("version", None)
    sources: dict[str, SourceLimits] = {}
    for name, raw in body.items():
        if not _SOURCE.fullmatch(name):
            raise LimitsError(f"{LIMITS_FILE}: 출처 이름은 소문자 식별자여야 한다: {name!r}")
        try:
            sources[name] = SourceLimits.model_validate(raw)
        except (ValidationError, ValueError) as e:
            raise LimitsError(f"{LIMITS_FILE}: {name}: {e}") from None
    missing = [s for s in REQUIRED_SOURCES if s not in sources]
    if missing:
        raise LimitsError(f"{LIMITS_FILE}: 출처가 빠졌다: {', '.join(missing)}")
    try:
        return Limits(version=version, sources=sources)  # pyright: ignore[reportArgumentType]
    except ValidationError as e:
        raise LimitsError(f"{LIMITS_FILE}: {e}") from None


def load_limits(*, settings: Settings | None = None) -> Limits:
    """`config/limits.yaml` 을 읽는다(캐시하지 않는다 — 서비스는 시작할 때 한 번 읽어 들고
    있는다)."""
    return parse_limits(load_yaml_mapping(LIMITS_FILE, settings=settings))
