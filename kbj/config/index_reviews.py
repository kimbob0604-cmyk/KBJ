"""`config/index_reviews.yaml` 해석기 — 패시브 지수 정기변경 예측의 규칙 숫자·일정(P5 2차 묶음 IDX).

근거: docs/p5_index_rebalance_prompt.md §1·§3, docs/metrics.md §24. 숫자·일정은 그 파일 한 곳.

- 숫자는 전부 `Param`(값·출처·확인일·[확인 필요] 여부) — 엔진은 `.v` 만 읽고, 화면·문서는 `verify`
  로
  '추정'을 보인다(절대 규칙 2). 모르는 키는 오류(`extra="forbid"`).
- 유동비율 반올림 규칙은 리뷰 날짜로 고른다(`fif_rule_for`) — 새 규칙(2026-05 리뷰부터)·옛 규칙.
- MSCI 편입 배수는 GIMI 2026-08판 §3.1.5·§3.1.6(정기 리뷰): 비구성종목 1.0×·하위 세그먼트 이동
  1.5×·유동 0.5×, 유동비율 < min_fif_add 종목은 유동 0.9×(신규)·0.6×(유지). Light Rebalancing
  (1.8×·0.9×)은 `light_rebalance`(기본 꺼짐). FOL 은 한도 0.1% 반올림 + foreign room 계수(`FolCfg`).
- 일정은 사람이 적은 공표 일정표다(계산 추정 금지). `confirmed=False` 면 일정 표에 estimated 로
  들어간다.
- 구성종목 목록은 여기 없다(로그인 등급 — DB `prv_index.constituents`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = [
    "INDEX_REVIEWS_FILE",
    "FifBand",
    "FifRule",
    "FolCfg",
    "IndexCfg",
    "IndexReviewsConfig",
    "KrxRules",
    "LightRebalance",
    "MsciRules",
    "Param",
    "ReviewCfg",
    "load_index_reviews",
    "parse_index_reviews",
]

INDEX_REVIEWS_FILE: Final = "index_reviews.yaml"
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_REVIEW_ID = re.compile(r"\d{4}-\d{2}")
_CODE = re.compile(r"[0-9A-Z]{6}")

Family = Literal["msci", "krx"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Param(_Model):
    """규칙 숫자 하나 — 값·출처·확인일·[확인 필요]. v=None 은 '아직 모름'(계산하지 않는다)."""

    v: float | None
    src: str = Field(min_length=1)
    checked: date
    verify: bool


class IndexCfg(_Model):
    id: str
    title: str = Field(min_length=1)
    family: Family
    enabled: bool
    order: int = Field(ge=1)
    markets: tuple[Literal["KOSPI", "KOSDAQ"], ...] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _IDENT.fullmatch(v):
            raise ValueError(f"지수 id 는 소문자 식별자: {v!r}")
        return v


class FifBand(_Model):
    above: float = Field(ge=0, lt=1)
    step: float = Field(gt=0, le=1)
    buffer: float | None = Field(default=None, gt=0, lt=1)
    mode: Literal["nearest", "up"]


class FifRule(_Model):
    id: str
    from_: date | None = Field(alias="from")
    src: str = Field(min_length=1)
    checked: date
    verify: bool
    bands: tuple[FifBand, ...] = Field(min_length=1)

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _bands(self) -> FifRule:
        aboves = [b.above for b in self.bands]
        if aboves != sorted(aboves, reverse=True) or len(set(aboves)) != len(aboves):
            raise ValueError(f"{self.id}: 띠는 above 내림차순·겹치지 않게")
        if aboves[-1] != 0.0:
            raise ValueError(f"{self.id}: 마지막 띠는 above 0(모든 양수 유동비율을 덮는다)")
        return self


class FolLimit(_Model):
    code: str
    limit: float = Field(gt=0, le=1)
    why: str = Field(min_length=1)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE.fullmatch(v):
            raise ValueError(f"종목코드 형식: {v!r}")
        return v


class FolCfg(_Model):
    """외국인 지분 한도(FOL) — GIMI 2026-08판 §2.3.6.2·§3.1.2.6·§3.1.6.3·App.VI.

    FIF = min(외국인 가용 유동비율(반올림), FOL(limit_step 단위 반올림)) × foreign room 계수.
    room = (FOL − 외국인 보유) ÷ FOL. 신규: room ≥ room_full → 1, room_min_add ≤ room < room_full →
    room_add_factor, room < room_min_add → 편입 불가. 기존: room < room_keep_floor →
    room_keep_floor_factor, room_keep_floor ≤ room < room_full → room_keep_mid_factor(원문 표가 직전
    계수별 단계라 옮기지 못함 — null = 조정하지 않음 [확인 필요])."""

    src: str
    checked: date
    verify: bool
    limit_step: Param
    room_full: Param
    room_min_add: Param
    room_add_factor: Param
    room_keep_floor: Param
    room_keep_floor_factor: Param
    room_keep_mid_factor: Param
    limits: tuple[FolLimit, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> FolCfg:
        for n in ("limit_step", "room_full", "room_min_add", "room_add_factor",
                  "room_keep_floor", "room_keep_floor_factor"):  # fmt: skip
            v = getattr(self, n).v
            if v is None or not 0 < v <= 1:
                raise ValueError(f"fol.{n}: 0 < 값 ≤ 1")
        if not self.val("room_keep_floor") <= self.val("room_min_add") <= self.val("room_full"):
            raise ValueError("fol: room_keep_floor ≤ room_min_add ≤ room_full")
        mid = self.room_keep_mid_factor.v
        if mid is not None and not 0 < mid <= 1:
            raise ValueError("fol.room_keep_mid_factor: 0 < 값 ≤ 1 또는 null")
        return self

    def val(self, name: str) -> float:
        v = getattr(self, name).v
        if v is None:
            raise ValueError(f"fol.{name}: 값이 없다")
        return float(v)

    def limit_of(self, code: str) -> float | None:
        for x in self.limits:
            if x.code == code:
                return x.limit
        return None


class FifEstimateCfg(_Model):
    src: str
    checked: date
    verify: bool
    financial_holder_patterns: tuple[str, ...] = ()
    # 추정하는 시총 상위 종목 수(+ 구성종목) — 저장소 호출 상한
    top_n: int = Field(default=400, ge=1)


class SectorCfg(_Model):
    src: str
    checked: date
    verify: bool
    default: str = Field(min_length=1)
    map: Mapping[str, str] = Field(default_factory=dict[str, str])

    def sector_of(self, induty_code: str | None) -> str:
        if not induty_code:
            return self.default
        return self.map.get(induty_code[:2], self.default)


class LightRebalance(_Model):
    """Light Rebalancing(시장 스트레스 때 MSCI 공지 — GIMI App.VIII) 편입 배수. 기본 꺼짐."""

    enabled: bool = False
    add_mcap_mult: Param
    add_ff_mult: Param


class MsciRules(_Model):
    """컷오프 배수 규칙(MSCI·FTSE 형).

    비구성종목 편입 시총 배수: 하위 세그먼트(Small Cap) 구성종목이면 add_mcap_mult_migrate, IMI 밖
    (새로 투자 가능해진 종목)이면 add_mcap_mult_new, 모르면 segment_default 쪽. 유동비율 <
    min_fif_add 종목은 유동 배수 low_fif_ff_mult_add·_keep(없으면 신규 편입 불가 — 옛 처리)."""

    coverage: Param
    coverage_low: Param
    coverage_high: Param
    add_mcap_mult_new: Param
    add_mcap_mult_migrate: Param
    add_ff_mult: Param
    keep_mcap_mult: Param
    keep_ff_mult: Param
    min_fif_add: Param
    low_fif_ff_mult_add: Param | None = None
    low_fif_ff_mult_keep: Param | None = None
    segment_default: Literal["new", "migrate"] = "migrate"
    light_rebalance: LightRebalance | None = None
    borderline: Param
    adv_days: Param
    aum_won: Param
    exclude_flags: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ("common",)

    @model_validator(mode="after")
    def _check(self) -> MsciRules:
        need = (
            "coverage", "coverage_low", "coverage_high", "add_mcap_mult_new",
            "add_mcap_mult_migrate", "add_ff_mult", "keep_mcap_mult", "keep_ff_mult",
            "min_fif_add", "borderline", "adv_days",
        )  # fmt: skip
        for n in need:
            if getattr(self, n).v is None:
                raise ValueError(f"{n}: 값이 없다(null 은 aum 만)")
        lo, mid, hi = self.val("coverage_low"), self.val("coverage"), self.val("coverage_high")
        if not 0 < lo <= mid <= hi < 1:
            raise ValueError("커버리지는 0 < low ≤ coverage ≤ high < 1")
        if (self.low_fif_ff_mult_add is None) != (self.low_fif_ff_mult_keep is None):
            raise ValueError("low_fif_ff_mult_add·_keep 은 같이 두거나 같이 뺀다")
        lr = self.light_rebalance
        if lr is not None and (lr.add_mcap_mult.v is None or lr.add_ff_mult.v is None):
            raise ValueError("light_rebalance: 배수 값이 없다")
        return self

    def opt(self, name: str) -> float | None:
        """선택 배수(없거나 null 이면 None)."""
        p = getattr(self, name)
        return None if p is None or p.v is None else float(p.v)

    def val(self, name: str) -> float:
        v = getattr(self, name).v
        if v is None:
            raise ValueError(f"{name}: 값이 없다")
        return float(v)


class KrxRules(_Model):
    """시총 순위·완충 규칙(KRX 형)."""

    size: Param
    add_rank_ratio: Param
    keep_rank_ratio: Param
    liquidity_pct: Param
    min_listing_days: Param
    fast_entry_rank: Param
    borderline: Param
    adv_days: Param
    non_etf_aum_won: Param
    # 산업군 안 누적 시총 비중 문턱(신규 편입 — 없으면 규칙 없음), 심사기간 평균 시총 창(거래일)
    sector_cum_mcap: Param | None = None
    mcap_avg_days: Param | None = None
    etf_index_patterns: tuple[str, ...] = ()
    exclude_flags: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ("common",)

    @model_validator(mode="after")
    def _check(self) -> KrxRules:
        for n in ("size", "add_rank_ratio", "keep_rank_ratio", "liquidity_pct",
                  "min_listing_days", "fast_entry_rank", "borderline", "adv_days"):  # fmt: skip
            if getattr(self, n).v is None:
                raise ValueError(f"{n}: 값이 없다")
        add, keep = self.val("add_rank_ratio"), self.val("keep_rank_ratio")
        if not 0 < add <= 1 <= keep:
            raise ValueError("순위 완충은 0 < add_rank_ratio ≤ 1 ≤ keep_rank_ratio")
        cum = self.opt("sector_cum_mcap")
        if cum is not None and not 0 < cum <= 1:
            raise ValueError("sector_cum_mcap 은 0 < 값 ≤ 1")
        days = self.opt("mcap_avg_days")
        if days is not None and days < 1:
            raise ValueError("mcap_avg_days 는 1 이상")
        return self

    def val(self, name: str) -> float:
        v = getattr(self, name).v
        if v is None:
            raise ValueError(f"{name}: 값이 없다")
        return float(v)

    def opt(self, name: str) -> float | None:
        p = getattr(self, name)
        return None if p is None or p.v is None else float(p.v)


class ReviewCfg(_Model):
    id: str
    base: date
    announce: date
    trade: date
    effective: date
    confirmed: bool

    @field_validator("id")
    @classmethod
    def _rid(cls, v: str) -> str:
        if not _REVIEW_ID.fullmatch(v):
            raise ValueError(f"리뷰 id 는 YYYY-MM: {v!r}")
        return v

    @model_validator(mode="after")
    def _order(self) -> ReviewCfg:
        if not self.base <= self.announce <= self.trade < self.effective:
            raise ValueError(f"{self.id}: base ≤ announce ≤ trade < effective 여야 한다")
        return self


class IndexReviewsConfig(_Model):
    version: Literal[1]
    indexes: tuple[IndexCfg, ...] = Field(min_length=1)
    fif_rounding: tuple[FifRule, ...] = Field(min_length=1)
    fol: FolCfg
    fif_estimate: FifEstimateCfg
    sectors: SectorCfg
    msci: Mapping[str, MsciRules] = Field(default_factory=dict[str, MsciRules])
    krx: Mapping[str, KrxRules] = Field(default_factory=dict[str, KrxRules])
    reviews: Mapping[str, tuple[ReviewCfg, ...]] = Field(
        default_factory=dict[str, tuple[ReviewCfg, ...]]
    )

    @model_validator(mode="after")
    def _shape(self) -> IndexReviewsConfig:
        ids = [i.id for i in self.indexes]
        if len(set(ids)) != len(ids):
            raise ValueError("지수 id 가 겹친다")
        for i in self.indexes:
            rules: Mapping[str, Any] = self.msci if i.family == "msci" else self.krx
            if i.id not in rules:
                raise ValueError(f"{i.id}: {i.family} 규칙 절이 없다")
        known = set(ids)
        for name in (*self.msci, *self.krx, *self.reviews):
            if name not in known:
                raise ValueError(f"모르는 지수 {name!r}")
        for name, revs in self.reviews.items():
            rids = [r.id for r in revs]
            if len(set(rids)) != len(rids):
                raise ValueError(f"{name}: 리뷰 id 가 겹친다")
            if [r.effective for r in revs] != sorted(r.effective for r in revs):
                raise ValueError(f"{name}: 리뷰는 효력일 순서로")
        froms = [r.from_ for r in self.fif_rounding]
        if froms.count(None) > 1:
            raise ValueError("from: null(가장 옛 규칙)은 하나만")
        dated = [f for f in froms if f is not None]
        if len(set(dated)) != len(dated):
            raise ValueError("fif 규칙 from 날짜가 겹친다")
        return self

    # ── 조회 ──
    def index(self, index_id: str) -> IndexCfg:
        for i in self.indexes:
            if i.id == index_id:
                return i
        raise KeyError(f"모르는 지수: {index_id!r}")

    def enabled_indexes(self) -> tuple[IndexCfg, ...]:
        return tuple(sorted((i for i in self.indexes if i.enabled), key=lambda i: i.order))

    def msci_rules(self, index_id: str) -> MsciRules:
        return self.msci[index_id]

    def krx_rules(self, index_id: str) -> KrxRules:
        return self.krx[index_id]

    def reviews_of(self, index_id: str) -> tuple[ReviewCfg, ...]:
        return tuple(self.reviews.get(index_id, ()))

    def review(self, index_id: str, review_id: str) -> ReviewCfg:
        for r in self.reviews_of(index_id):
            if r.id == review_id:
                return r
        raise KeyError(f"{index_id}: 모르는 리뷰 {review_id!r}")

    def current_review(self, index_id: str, today: date) -> ReviewCfg | None:
        """아직 효력이 나지 않은 첫 리뷰(today < effective). 없으면 None — 일정 표를 더 적을 때."""
        for r in self.reviews_of(index_id):
            if today < r.effective:
                return r
        return None

    def fif_rule_for(self, review_date: date) -> FifRule:
        """리뷰 날짜 ≥ from 인 규칙 가운데 from 이 가장 늦은 것(없으면 from: null 규칙)."""
        dated = sorted(
            (r for r in self.fif_rounding if r.from_ is not None and r.from_ <= review_date),
            key=lambda r: r.from_ or date.min,
        )
        if dated:
            return dated[-1]
        for r in self.fif_rounding:
            if r.from_ is None:
                return r
        raise ValueError(f"{review_date}: 맞는 유동비율 규칙이 없다(from: null 규칙을 두라)")

    def unverified(self) -> list[str]:
        """[확인 필요] 숫자 목록(`지수.이름`) — probe·화면 각주용."""
        out: list[str] = []
        for name, rules in [*self.msci.items(), *self.krx.items()]:
            for field_name, value in rules:
                if isinstance(value, Param) and value.verify:
                    out.append(f"{name}.{field_name}")
        for field_name, value in self.fol:
            if isinstance(value, Param) and value.verify:
                out.append(f"fol.{field_name}")
        out += [f"fif_rounding.{r.id}" for r in self.fif_rounding if r.verify]
        return out


def parse_index_reviews(data: Mapping[str, Any]) -> IndexReviewsConfig:
    try:
        return IndexReviewsConfig.model_validate(dict(data))
    except ValidationError as e:
        raise ConfigFileError(f"{INDEX_REVIEWS_FILE}: {e}") from None


def load_index_reviews(*, settings: Settings | None = None) -> IndexReviewsConfig:
    return parse_index_reviews(load_yaml_mapping(INDEX_REVIEWS_FILE, settings=settings))
