"""`config/theme.yaml` 해석기 — 테마 마스터 받기(원천 주소·검증 문턱·화면 품질 기준).

근거: ADR 0039, docs/metrics.md §31. 숫자는 이 파일 한 곳 — 작업·엔진은 이 값을 받는다. 모르는 키는
오류.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = ["THEME_FILE", "MasterCfg", "ThemeConfig", "load_theme", "parse_theme"]

THEME_FILE: Final = "theme.yaml"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MasterCfg(_Model):
    base_url: str = Field(min_length=1)
    min_themes: int = Field(ge=1)
    min_naver_themes: int = Field(ge=1)
    min_members: int = Field(ge=1)
    min_stocks: int = Field(ge=1)
    max_drop_ratio: float = Field(gt=0.0, lt=1.0)
    max_file_mb: int = Field(ge=1, le=64)
    stale_days: int = Field(ge=1)

    @field_validator("base_url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://") or v.endswith("/") or "?" in v or "#" in v:
            raise ValueError("base_url 은 https:// 로 시작하고 끝에 / ·쿼리가 없어야 한다")
        return v


class ThemeConfig(_Model):
    master: MasterCfg


def parse_theme(raw: object) -> ThemeConfig:
    try:
        return ThemeConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigFileError(f"{THEME_FILE}: {e.errors()[0]['msg']}") from None


def load_theme(*, settings: Settings | None = None) -> ThemeConfig:
    return parse_theme(load_yaml_mapping(THEME_FILE, settings=settings))
