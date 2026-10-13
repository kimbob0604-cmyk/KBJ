"""`config/briefs.yaml` 해석기 — 아침 브리핑·마감 요약 섹션 구성(docs/p5_design.md §3.6·§4.5,
metrics §20).

- 모르는 키는 오류(`extra="forbid"`). 문구·순서는 그 파일 한 곳(BRF 가 고친다).
- omitted 섹션(단계 밖 — P7·P8)은 `phase` 를 적는다. on 섹션 중 재료 작업이 있는 것은
  `source_job`·`due`
  ('미준비 — <작업>(<예정 시각>)' 문구에 쓴다). 작업 이름이 등록부에 있는지는 시험이 본다.
- LLM 없음(절대 규칙 3) — 이 파일은 템플릿 문구만 담는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

BRIEFS_FILE: Final = "briefs.yaml"
_HHMM = r"^[0-2][0-9]:[0-5][0-9]$"
_KEY = re.compile(r"[a-z][a-z0-9_]*")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SectionCfg(_Model):
    key: str
    title: str = Field(min_length=1)
    status: Literal["include", "omitted"]
    source_job: str | None = None
    due: str | None = Field(default=None, pattern=_HHMM)
    phase: Literal["P6", "P7", "P8"] | None = None
    empty: Literal["skip", "none_line"] | None = None
    # none_line 섹션의 '없음' 문구(없으면 texts.none_line)
    none_text: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _shape(self) -> SectionCfg:
        if not _KEY.fullmatch(self.key):
            raise ValueError(f"섹션 key 는 소문자 식별자: {self.key!r}")
        if self.status == "omitted" and self.phase is None:
            raise ValueError(f"{self.key}: omitted 섹션은 phase(어느 단계에 싣는지)를 적는다")
        if self.status == "omitted" and (self.source_job or self.due):
            raise ValueError(f"{self.key}: omitted 섹션은 재료 작업이 없다")
        if (self.source_job is None) != (self.due is None):
            raise ValueError(f"{self.key}: source_job 과 due 는 함께 준다")
        if self.none_text is not None and self.empty != "none_line":
            raise ValueError(f"{self.key}: none_text 는 empty: none_line 섹션에만 준다")
        return self


class KindCfg(_Model):
    send_at: str = Field(pattern=_HHMM)
    sections: tuple[SectionCfg, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> KindCfg:
        keys = [s.key for s in self.sections]
        if len(set(keys)) != len(keys):
            raise ValueError("섹션 key 가 겹친다")
        if keys[-1] != "quality":
            raise ValueError("마지막 섹션은 데이터 품질 줄(quality — metrics §20 ⑤)")
        return self


class TextsCfg(_Model):
    not_ready: str = Field(min_length=1)
    none_line: str = Field(min_length=1)
    quality_ok: str = Field(min_length=1)
    stale_basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def _placeholders(self) -> TextsCfg:
        if "{job}" not in self.not_ready or "{due}" not in self.not_ready:
            raise ValueError("not_ready 문구에 {job}·{due} 가 있어야 한다")
        if "{as_of}" not in self.stale_basis:
            raise ValueError("stale_basis 문구에 {as_of} 가 있어야 한다")
        return self


class IndexCfg(_Model):
    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)


class BriefsConfig(_Model):
    version: Literal[1]
    morning: KindCfg
    closing: KindCfg
    table_series: tuple[str, ...] = Field(min_length=1)
    skip_titles: tuple[str, ...] = ()
    texts: TextsCfg
    us_indexes: tuple[IndexCfg, ...] = Field(min_length=1)

    def us_index_pairs(self) -> tuple[tuple[str, str], ...]:
        """간밤 미국 지수 (심볼, 이름) — 아침 ① 이 읽는다."""
        return tuple((i.symbol, i.name) for i in self.us_indexes)

    def jobs(self) -> set[str]:
        return {
            s.source_job
            for k in (self.morning, self.closing)
            for s in k.sections
            if s.source_job is not None
        }


def parse_briefs(data: Mapping[str, Any]) -> BriefsConfig:
    try:
        return BriefsConfig.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{BRIEFS_FILE}: {e}") from None


def load_briefs(*, settings: Settings | None = None) -> BriefsConfig:
    return parse_briefs(load_yaml_mapping(BRIEFS_FILE, settings=settings))
