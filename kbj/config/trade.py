"""`config/trade.yaml` + `config/knowledge/{trade_map,trade_items,trade_regions,hs_chapters}.yaml`
해석기 — P6 수출입·매핑(docs/p6_design.md §3.5·§4.5, D-P6-9·14). 숫자·목록은 그 파일들 한 곳.

- 모르는 키는 오류(`extra="forbid"` — `kbj/config/macro.py` 방식). 여기서는 **형식·범위·한 파일 안의
  맞음**만 본다(짝수 자리 HS·가중치 0~1·종목별 합 ≤ 1·중복·근거 1개 이상·묶음 접두 겹침·지역 기간).
  파일 사이 대조(매핑 HS2 장이 `hs_chapters` 에 있는가·지역 ID 가 `trade_regions` 에 있는가)와 DB
  대조(`pub_trade.hs_dim`·상장사 목록)는 MAP 의 검증기(`kbj.engines.trade.mapping`·
  `kbj.services.engine.verify_trade`)가 한다.
- 매핑표 본문(공개 60종목 초안 — `reviewed: false`)은 MAP, 104품목·시군구·HS 장 이름 본문은 EXP 가
  웨이브 2 에 채운다. FND 는 틀만 둔다(빈 목록이어도 읽힌다).
- VM local 매핑(관심종목 전용 — 공개 내보내기는 읽지 않는다, D-P6-14)은 같은 스키마의 파일이고
  경로는 `trade.yaml map.local_file`(기본 없음 — 사용자 설정).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = [
    "HS_CHAPTERS_FILE",
    "TRADE_FILE",
    "TRADE_ITEMS_FILE",
    "TRADE_MAP_FILE",
    "TRADE_REGIONS_FILE",
    "HsChapters",
    "ItemGroup",
    "MapEvidence",
    "MapItem",
    "MapRegion",
    "MapStock",
    "RegionCfg",
    "TradeConfig",
    "TradeItems",
    "TradeMap",
    "TradeRegions",
    "load_hs_chapters",
    "load_local_map",
    "load_trade",
    "load_trade_items",
    "load_trade_map",
    "load_trade_regions",
    "parse_hs_chapters",
    "parse_trade",
    "parse_trade_items",
    "parse_trade_map",
    "parse_trade_regions",
]

TRADE_FILE: Final = "trade.yaml"
TRADE_MAP_FILE: Final = "knowledge/trade_map.yaml"
TRADE_ITEMS_FILE: Final = "knowledge/trade_items.yaml"
TRADE_REGIONS_FILE: Final = "knowledge/trade_regions.yaml"
HS_CHAPTERS_FILE: Final = "knowledge/hs_chapters.yaml"

_HS = re.compile(r"[0-9]{2}([0-9]{2}){0,4}")
_CODE = re.compile(r"[0-9A-Z]{6}")
_COUNTRY = re.compile(r"[A-Z]{2}")
_REGION = re.compile(r"KR-[0-9]{2}-[0-9a-z_]+")
_GROUP = re.compile(r"[a-z0-9][a-z0-9_]*")
_YM = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_CHAPTER = re.compile(r"[0-9]{2}")

Flow = Literal["exp", "imp"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _hs(v: str, what: str = "hs") -> str:
    if not _HS.fullmatch(v):
        raise ValueError(f"{what} 는 2~10 자리 짝수 숫자 문자열: {v!r}")
    return v


# ── config/trade.yaml ────────────────────────────────────────────────────────────────────────


class PollCfg(_Model):
    """10일 잠정치 폴링 창(D-P6-6). 등록부 `trade.customs_tenday` 의 cron·month_days 와 같아야 한다
    (등록부 시험이 대조)."""

    month_days: tuple[int, ...] = Field(min_length=1)
    start: str
    end: str
    every_min: int = Field(ge=5, le=60)

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.fullmatch(v):
            raise ValueError(f"시각은 HH:MM: {v!r}")
        return v

    @model_validator(mode="after")
    def _days(self) -> PollCfg:
        if any(not 1 <= d <= 31 for d in self.month_days) or len(set(self.month_days)) != len(
            self.month_days
        ):
            raise ValueError(f"month_days 는 1~31 겹침 없이: {self.month_days}")
        if self.start >= self.end:
            raise ValueError(f"폴링 창 start {self.start} ≥ end {self.end}")
        return self


class TenDayCfg(_Model):
    lookback_months: int = Field(ge=1, le=12)
    poll: PollCfg


class MonthlyCfg(_Model):
    # by_chapter = 15101609 를 HS2 장별로 나눠 부른다(응답 잘림 대비 — R6), all = 한 번에
    items_mode: Literal["by_chapter", "all"]
    countries: Literal["all"]
    sido: Literal["all"]
    # 시군구(15134343)는 매핑표의 HS6 만 받는다(probe_results F3 — DB 의존 없음)
    sigungu_hs6_from: Literal["trade_map"]


class BackfillCfg(_Model):
    years: int = Field(ge=1, le=10)
    stop_at_ratio: float = Field(gt=0, le=1)


class SurgeCfg(_Model):
    """급등·급락 문턱(metrics §26 — [제안] 값). yoy 는 소수(0.5 = +50%)."""

    min_usd: int = Field(gt=0)
    surge_yoy: float = Field(gt=0)
    surge_yoy_3m: float = Field(gt=0)
    plunge_yoy: float = Field(lt=0, ge=-1)
    plunge_yoy_3m: float = Field(lt=0, ge=-1)
    levels: tuple[Literal["hs2", "hs4", "hs6", "hs10", "country"], ...] = Field(min_length=1)
    top: int = Field(ge=1, le=500)


class PublicExportCfg(_Model):
    """공개 정적 사이트 `trade/` 폴더(§7 — 상한 [제안], 실측 #57 뒤 확정)."""

    months: int = Field(ge=1, le=120)
    country_months: int = Field(ge=1, le=120)
    dir_cap_mb: int = Field(ge=1, le=200)
    file_cap_kb: int = Field(ge=1, le=10240)
    flash_cap_kb: int = Field(ge=1, le=10240)
    region_cap_kb: int = Field(ge=1, le=10240)
    stock_cap_kb: int = Field(ge=1, le=10240)


class OverlayCfg(_Model):
    min_quarters: int = Field(ge=2, le=40)
    lags: tuple[int, ...] = Field(min_length=1)
    rank_months: int = Field(ge=1, le=12)

    @field_validator("lags")
    @classmethod
    def _lags(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if any(x < 0 or x > 4 for x in v) or len(set(v)) != len(v):
            raise ValueError(f"lags 는 0~4 겹침 없이: {v}")
        return v


class Items104Cfg(_Model):
    mode: Literal["fixed", "top_n"]
    n: int = Field(ge=1, le=500)
    top_n_months: int = Field(ge=1, le=36)


class PriceVolumeCfg(_Model):
    # 중량 기준이 의미 없는 품목(HS 접두) — '중량 기준 부적합' 꼬리표 [확인 필요]
    weight_unfit: tuple[str, ...] = ()

    @field_validator("weight_unfit")
    @classmethod
    def _hs_list(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for x in v:
            _hs(x, "weight_unfit 원소")
        return v


class MapCfg(_Model):
    # VM local 매핑 파일(관심종목 전용 — D-P6-14). 절대 경로 또는 KBJ_DATA_DIR 기준.
    # 없으면 local 없음
    local_file: str | None = None


class TradeConfig(_Model):
    version: Literal[1]
    tenday: TenDayCfg
    monthly: MonthlyCfg
    backfill: BackfillCfg
    surge: SurgeCfg
    public_export: PublicExportCfg
    overlay: OverlayCfg
    items104: Items104Cfg
    price_volume: PriceVolumeCfg
    map: MapCfg


def parse_trade(data: Mapping[str, Any]) -> TradeConfig:
    try:
        return TradeConfig.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{TRADE_FILE}: {e}") from None


def load_trade(*, settings: Settings | None = None) -> TradeConfig:
    return parse_trade(load_yaml_mapping(TRADE_FILE, settings=settings))


# ── knowledge/trade_map.yaml (§4.5 — MAP 이 본문) ──────────────────────────────────────────


class MapItem(_Model):
    hs: str
    flow: Flow
    weight: float = Field(ge=0, le=1)
    countries: tuple[str, ...] = ()

    @field_validator("hs")
    @classmethod
    def _h(cls, v: str) -> str:
        return _hs(v)

    @field_validator("countries")
    @classmethod
    def _c(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _COUNTRY.fullmatch(c) for c in v) or len(set(v)) != len(v):
            raise ValueError(f"countries 는 ISO2 대문자 겹침 없이: {v}")
        return v


class MapRegion(_Model):
    region_id: str
    hs6: str
    weight: float = Field(ge=0, le=1)

    @field_validator("region_id")
    @classmethod
    def _r(cls, v: str) -> str:
        if not _REGION.fullmatch(v):
            raise ValueError(f"region_id 는 'KR-<시도 2자리>-<id>': {v!r}")
        return v

    @field_validator("hs6")
    @classmethod
    def _h6(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{6}", v):
            raise ValueError(f"hs6 는 6자리: {v!r}")
        return v


class MapEvidence(_Model):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    note: str = Field(min_length=1)
    rcept_no: str | None = Field(default=None, pattern=r"^[0-9]{14}$")


class MapStock(_Model):
    code: str
    name: str = Field(min_length=1)
    reviewed: bool
    items: tuple[MapItem, ...] = ()
    regions: tuple[MapRegion, ...] = ()
    evidence: tuple[MapEvidence, ...] = Field(min_length=1)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE.fullmatch(v):
            raise ValueError(f"종목코드는 6자리 영숫자: {v!r}")
        return v

    @model_validator(mode="after")
    def _shape(self) -> MapStock:
        if not self.items and not self.regions:
            raise ValueError(f"{self.code}: items·regions 가 모두 비었다")
        keys = [(i.hs, i.flow) for i in self.items] + [(r.hs6, r.region_id) for r in self.regions]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.code}: 같은 품목·지역 행이 두 번 있다")
        total = sum(i.weight for i in self.items) + sum(r.weight for r in self.regions)
        if total > 1 + 1e-9:
            raise ValueError(f"{self.code}: 가중치 합 {total:.4f} > 1")
        return self


class TradeMap(_Model):
    version: Literal[1]
    stocks: tuple[MapStock, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> TradeMap:
        codes = [s.code for s in self.stocks]
        if len(set(codes)) != len(codes):
            raise ValueError("매핑표에 같은 종목이 두 번 있다")
        return self


def parse_trade_map(data: Mapping[str, Any], *, name: str = TRADE_MAP_FILE) -> TradeMap:
    try:
        return TradeMap.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{name}: {e}") from None


def load_trade_map(*, settings: Settings | None = None) -> TradeMap:
    return parse_trade_map(load_yaml_mapping(TRADE_MAP_FILE, settings=settings))


def load_local_map(path: Path) -> TradeMap:
    """VM local 매핑 파일(같은 스키마). 파일이 없으면 FileNotFoundError(조용히 비우지 않는다)."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigFileError(f"{path.name}: 최상위가 매핑이 아니다")
    return parse_trade_map(data, name=path.name)  # pyright: ignore[reportUnknownArgumentType]


# ── knowledge/trade_items.yaml (104품목 — EXP 가 본문, D-P6-9) ───────────────────────────────


class ItemGroup(_Model):
    id: str
    name: str = Field(min_length=1)
    hs: tuple[str, ...] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _GROUP.fullmatch(v):
            raise ValueError(f"묶음 id 는 소문자·숫자: {v!r}")
        return v

    @field_validator("hs")
    @classmethod
    def _h(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for x in v:
            _hs(x)
        return v


class TradeItems(_Model):
    version: Literal[1]
    groups: tuple[ItemGroup, ...] = ()

    @model_validator(mode="after")
    def _no_overlap(self) -> TradeItems:
        ids = [g.id for g in self.groups]
        if len(set(ids)) != len(ids):
            raise ValueError("묶음 id 가 겹친다")
        owner: dict[str, str] = {}
        for g in self.groups:
            for p in g.hs:
                for q, gid in owner.items():
                    if p.startswith(q) or q.startswith(p):
                        raise ValueError(f"묶음 접두가 겹친다(metrics §29): {gid}:{q} · {g.id}:{p}")
                owner[p] = g.id
        return self


def parse_trade_items(data: Mapping[str, Any]) -> TradeItems:
    try:
        return TradeItems.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{TRADE_ITEMS_FILE}: {e}") from None


def load_trade_items(*, settings: Settings | None = None) -> TradeItems:
    return parse_trade_items(load_yaml_mapping(TRADE_ITEMS_FILE, settings=settings))


# ── knowledge/trade_regions.yaml (시군구 통합 ID — 개편 대응, F4) ────────────────────────────


class RegionCfg(_Model):
    id: str
    sido_cd: str
    sgg_nm: str = Field(min_length=1)
    valid_from: str | None = None  # 신고 기준 월 YYYY-MM(포함), None = 처음부터
    valid_to: str | None = None  # 포함, None = 지금까지

    @model_validator(mode="after")
    def _shape(self) -> RegionCfg:
        if not _REGION.fullmatch(self.id):
            raise ValueError(f"region id 는 'KR-<시도 2자리>-<id>': {self.id!r}")
        if not re.fullmatch(r"[0-9]{2}", self.sido_cd):
            raise ValueError(f"sido_cd 는 2자리: {self.sido_cd!r}")
        for v in (self.valid_from, self.valid_to):
            if v is not None and not _YM.fullmatch(v):
                raise ValueError(f"기간은 YYYY-MM: {v!r}")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError(f"{self.id}: valid_from > valid_to")
        return self


class TradeRegions(_Model):
    version: Literal[1]
    regions: tuple[RegionCfg, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> TradeRegions:
        keys = [(r.sido_cd, r.sgg_nm, r.valid_from) for r in self.regions]
        if len(set(keys)) != len(keys):
            raise ValueError("같은 (시도, 시군구 이름, valid_from) 이 두 번 있다")
        return self

    def ids(self) -> frozenset[str]:
        return frozenset(r.id for r in self.regions)


def parse_trade_regions(data: Mapping[str, Any]) -> TradeRegions:
    try:
        return TradeRegions.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{TRADE_REGIONS_FILE}: {e}") from None


def load_trade_regions(*, settings: Settings | None = None) -> TradeRegions:
    return parse_trade_regions(load_yaml_mapping(TRADE_REGIONS_FILE, settings=settings))


# ── knowledge/hs_chapters.yaml (HS2 장 자체 한글 이름 — EXP 가 본문) ─────────────────────────


class HsChapters(_Model):
    version: Literal[1]
    chapters: dict[str, str] = Field(default_factory=dict[str, str])

    @field_validator("chapters")
    @classmethod
    def _ch(cls, v: dict[str, str]) -> dict[str, str]:
        for k, name in v.items():
            if not _CHAPTER.fullmatch(k) or not 1 <= int(k) <= 99:
                raise ValueError(f"장 번호는 '01'~'99': {k!r}")
            if not name.strip():
                raise ValueError(f"장 {k} 이름이 비었다")
        return v


def parse_hs_chapters(data: Mapping[str, Any]) -> HsChapters:
    try:
        return HsChapters.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{HS_CHAPTERS_FILE}: {e}") from None


def load_hs_chapters(*, settings: Settings | None = None) -> HsChapters:
    return parse_hs_chapters(load_yaml_mapping(HS_CHAPTERS_FILE, settings=settings))
