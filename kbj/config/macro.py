"""`config/macro.yaml` 해석기 — 매크로 탭·시리즈·파생·FF 스위치·수기 캘린더(docs/p5_design.md
§3.6·§4.1,
D-P5-5~8). 숫자·목록은 그 파일 한 곳.

- 모르는 키는 오류(`extra="forbid"`). 형식·범위·서로 맞음만 본다 — 시리즈 tier 와 카탈로그 데이터셋
  tier 의
  대조는 시험(tests/unit/config/test_p5_config.py — D-P5-6)과 `check_tiers`(카탈로그를 넘겨받는다 —
  `kbj.config` 는 `kbj.data` 를 import 하지 않는다).
- 공개 수집(`collectors.macro`)은 `public_series()`, 로그인 수집(`collectors.macro_prv`)은
  `private_series()` 만 쓴다. item 이 null(실측 전)인 시리즈는 `ready=False` — 부르지 않는다(키
  skipped).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

MACRO_FILE: Final = "macro.yaml"
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_SERIES_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/^=-]*")
_PUB_SERIES_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")  # pub_macro CHECK(0014)
_DATASET = re.compile(r"[A-Z][A-Z0-9_]*:\S+")
_PROBE = re.compile(r"#[0-9]{2}")

Tier = Literal["public", "private"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MacroTab(_Model):
    id: str
    title: str = Field(min_length=1)
    public: bool

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _IDENT.fullmatch(v):
            raise ValueError(f"탭 id 는 소문자 식별자: {v!r}")
        return v


class SeriesCfg(_Model):
    id: str
    dataset: str
    item: str | None
    probe: str | None = None
    title: str = Field(min_length=1)
    tab: str
    unit: str = Field(min_length=1)
    freq: Literal["D", "W", "M", "Q", "A"]
    tier: Tier
    brief: bool = False

    @field_validator("id")
    @classmethod
    def _sid(cls, v: str) -> str:
        if not _SERIES_ID.fullmatch(v):
            raise ValueError(f"시리즈 id 형식: {v!r}")
        return v

    @field_validator("dataset")
    @classmethod
    def _ds(cls, v: str) -> str:
        if not _DATASET.fullmatch(v):
            raise ValueError(f"데이터셋은 카탈로그 id('<출처>:<이름>'): {v!r}")
        return v

    @model_validator(mode="after")
    def _shape(self) -> SeriesCfg:
        if self.item is None and (self.probe is None or not _PROBE.fullmatch(self.probe)):
            raise ValueError(f"{self.id}: item 이 없으면 실측 번호(probe '#NN')를 적는다")
        if self.tier == "public":
            if not _PUB_SERIES_ID.fullmatch(self.id):
                raise ValueError(
                    f"{self.id}: 공개 시리즈 id 에 ^·= 를 쓰지 않는다(pub_macro CHECK)"
                )
            if self.dataset == "FRED:restricted_series":
                raise ValueError(f"{self.id}: FRED 저작권 시리즈는 공개가 될 수 없다(D-P5-6)")
        return self

    @property
    def source(self) -> str:
        return self.dataset.split(":", 1)[0]

    @property
    def ready(self) -> bool:
        """항목 코드를 알아 부를 수 있는가(실측 전이면 False)."""
        return self.item is not None


class DerivedCfg(_Model):
    id: str
    kind: Literal["spread", "yoy", "mom", "ann3m", "real_rate"]
    inputs: tuple[str, ...] = Field(min_length=1)
    title: str = Field(min_length=1)
    tab: str
    unit: str = Field(min_length=1)
    tier: Tier


class FfCfg(_Model):
    enabled: bool


class WindowCfg(_Model):
    past: int = Field(ge=0, le=365)
    future: int = Field(ge=0, le=365)


class ManualEvent(_Model):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    importance: int = Field(ge=1, le=3)
    source: Literal["bok", "fomc", "kosis", "ecos", "manual"]
    time_kst: str | None = Field(default=None, pattern=r"^[0-9]{2}:[0-9]{2}$")


class CalendarCfg(_Model):
    window_days: WindowCfg
    manual: tuple[ManualEvent, ...] = ()


class MacroConfig(_Model):
    version: Literal[1]
    tabs: tuple[MacroTab, ...] = Field(min_length=1)
    series: tuple[SeriesCfg, ...] = Field(min_length=1)
    derived: tuple[DerivedCfg, ...] = ()
    ff: FfCfg
    calendar: CalendarCfg

    @model_validator(mode="after")
    def _consistent(self) -> MacroConfig:
        tab_ids = [t.id for t in self.tabs]
        if len(set(tab_ids)) != len(tab_ids):
            raise ValueError("탭 id 가 겹친다")
        public_tabs = {t.id for t in self.tabs if t.public}
        ids = [s.id for s in self.series] + [d.id for d in self.derived]
        if len(set(ids)) != len(ids):
            raise ValueError("시리즈·파생 id 가 겹친다")
        by_id = {s.id: s for s in self.series}
        for s in self.series:
            if s.tab not in tab_ids:
                raise ValueError(f"{s.id}: 없는 탭 {s.tab!r}")
            if s.tier == "private" and s.tab in public_tabs:
                raise ValueError(f"{s.id}: 로그인 시리즈를 공개 탭({s.tab})에 두지 않는다")
        for d in self.derived:
            if d.tab not in tab_ids:
                raise ValueError(f"{d.id}: 없는 탭 {d.tab!r}")
            missing = [i for i in d.inputs if i not in by_id]
            if missing:
                raise ValueError(f"{d.id}: 없는 입력 시리즈 {missing}")
            want: Tier = "public" if all(by_id[i].tier == "public" for i in d.inputs) else "private"
            if d.tier != want:
                raise ValueError(f"{d.id}: 입력 등급으로 정한 tier 는 {want}(metrics §16)")
        return self

    def tab_ids(self) -> tuple[str, ...]:
        return tuple(t.id for t in self.tabs)

    def public_series(self) -> tuple[SeriesCfg, ...]:
        return tuple(s for s in self.series if s.tier == "public")

    def private_series(self) -> tuple[SeriesCfg, ...]:
        return tuple(s for s in self.series if s.tier == "private")

    def brief_series(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.series if s.brief)

    def check_tiers(self, dataset_tier: Callable[[str], str]) -> list[str]:
        """시리즈 tier ↔ 카탈로그 데이터셋 tier 불일치 목록(D-P5-6).

        `dataset_tier(데이터셋 id)` 는 'public'·'private' 를 돌려준다(없으면 KeyError)."""
        errs: list[str] = []
        for s in self.series:
            try:
                t = dataset_tier(s.dataset)
            except KeyError:
                errs.append(f"{s.id}: 카탈로그에 없는 데이터셋 {s.dataset}")
                continue
            if t != s.tier:
                errs.append(f"{s.id}: 시리즈 tier {s.tier} ≠ 데이터셋 {s.dataset} tier {t}")
        return errs


def parse_macro(data: Mapping[str, Any]) -> MacroConfig:
    try:
        return MacroConfig.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{MACRO_FILE}: {e}") from None


def load_macro(*, settings: Settings | None = None) -> MacroConfig:
    return parse_macro(load_yaml_mapping(MACRO_FILE, settings=settings))
