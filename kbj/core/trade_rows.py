"""수출입(관세청)·종목↔HS 매핑의 행 자료형 — 0019 `pub_trade.*`(P6 묶음 FND, 공개 등급).

근거: docs/p6_design.md §1.1·§3.3, docs/metrics.md §25~§29. 저장소(`kbj.store.repos.trade`)·수집
처리기(COL)·엔진(FLASH·EXP·MAP)·API 가 같이 쓴다.

- 금액은 **미화 달러 정수**(`Decimal` — float 아님, 소수면 오류). 10일 잠정치는 응답이 천 달러라
  파서가 × 1,000 해서 넘긴다(metrics §25). 중량은 kg(`Decimal`, 0 이상). 건수는 정수.
- 기준 월 `ym` 은 `YYYY-MM`(기존 데이터 키와 같은 모양 — 응답의 `YYYYMM` 은 파서가 바꾼다).
- 모든 행은 `source`·`as_of`·`quality`(절대 규칙 1). 관세청에서 받은 행의 source 는
  `DATAGO:<포털 ID>`, 파생 행(급등·묶음·종목 지수)은 계산한 작업 이름(`KBJ:<작업>`),
  매핑표는 `KBJ:trade_map`.
  `as_of` = 이 값의 기준 시각(받은·계산한 시각, tz 필수).
- 값 칸이 모두 None 인 행은 quality 가 ok 일 수 없다(0 으로 채우지 않는다 — 절대 규칙 2).
- 이 모듈은 `kbj.core` 밖을 모른다(어댑터 `kbj.data.public.customs` 의 열거와 같은 말을 따로
  적는다 — 시험이 대조).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal, cast

from kbj.core.quality import Quality

__all__ = [
    "FLOWS",
    "ITEM_GROUP_MODES",
    "MAP_ORIGINS",
    "STOCK_EXPORT_STATUSES",
    "SURGE_LEVELS",
    "SURGE_RULES",
    "TEN_DAY_KINDS",
    "TRADE_TABLES",
    "ChapterMonth",
    "CountryMonthRow",
    "Flow",
    "HsDim",
    "ItemCountryMonthRow",
    "ItemGroupMonthRow",
    "ItemMonthRow",
    "MapOrigin",
    "SigunguMonthRow",
    "StockExportRow",
    "StockExportStatus",
    "StockMapRow",
    "SurgeLevel",
    "SurgeRow",
    "SurgeRule",
    "TenDayColumn",
    "TenDayKind",
    "TenDayRow",
    "hs_ok",
    "prev_ym",
    "ym_ok",
]

TenDayKind = Literal["exp_item", "exp_country", "imp_item", "imp_country"]
Flow = Literal["exp", "imp"]
SurgeLevel = Literal["hs2", "hs4", "hs6", "hs10", "country"]
SurgeRule = Literal["surge", "plunge"]
MapOrigin = Literal["public", "local"]
StockExportStatus = Literal["ok", "no_revenue", "short_overlap", "no_trade"]
ItemGroupMode = Literal["fixed", "top_n"]

# 0019 CHECK 와 같은 목록(tests/test_store_layout.py 가 대조)
TEN_DAY_KINDS: Final[tuple[str, ...]] = ("exp_item", "exp_country", "imp_item", "imp_country")
FLOWS: Final[tuple[str, ...]] = ("exp", "imp")
SURGE_LEVELS: Final[tuple[str, ...]] = ("hs2", "hs4", "hs6", "hs10", "country")
SURGE_RULES: Final[tuple[str, ...]] = ("surge", "plunge")
MAP_ORIGINS: Final[tuple[str, ...]] = ("public", "local")
STOCK_EXPORT_STATUSES: Final[tuple[str, ...]] = ("ok", "no_revenue", "short_overlap", "no_trade")
ITEM_GROUP_MODES: Final[tuple[str, ...]] = ("fixed", "top_n")
TEN_DAY_COLUMN_COUNT: Final = 11  # 열 번호 00~10(00 = 전체)
# 0019 의 표 11개(쓰기 주인은 config/jobs.yaml — 등록부 시험)
TRADE_TABLES: Final[tuple[str, ...]] = (
    "pub_trade.ten_day", "pub_trade.ten_day_columns", "pub_trade.item_monthly",
    "pub_trade.country_monthly", "pub_trade.item_country_monthly", "pub_trade.sigungu_monthly",
    "pub_trade.hs_dim", "pub_trade.surge", "pub_trade.item_group_monthly",
    "pub_trade.stock_map", "pub_trade.stock_export",
)  # fmt: skip

_YM = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_HS = re.compile(r"[0-9]{2}([0-9]{2}){0,4}")  # 2·4·6·8·10 자리(짝수)
_COUNTRY = re.compile(r"[A-Z0-9]{2}")  # 관세청 특수 코드에 숫자가 섞인다('Z1' — 실측 2026-10-20)
_SIDO = re.compile(r"[0-9]{2}")
_REGION = re.compile(r"KR-[0-9]{2}-[0-9a-z_]+")
_CODE = re.compile(r"[0-9A-Z]{6}")
_GROUP = re.compile(r"[a-z0-9][a-z0-9_]*")
_SPAN = re.compile(r"01~(10|20|2[89]|3[01])")
_PERIOD = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])|[0-9]{4}Q[1-4]")
_COLLECTED = re.compile(r"DATAGO:[0-9]+")


def ym_ok(ym: object) -> bool:
    """`YYYY-MM`(월 01~12)인가."""
    return isinstance(ym, str) and _YM.fullmatch(ym) is not None


def hs_ok(hs: object) -> bool:
    """HS 접두(2~10 자리 짝수 숫자)인가."""
    return isinstance(hs, str) and _HS.fullmatch(hs) is not None


def prev_ym(ym: str, months: int = 1) -> str:
    """`YYYY-MM` 에서 months 개월 앞(음수면 뒤). 전년 같은 달 = prev_ym(ym, 12)."""
    if not ym_ok(ym):
        raise ValueError(f"ym 형식(YYYY-MM): {ym!r}")
    y, m = int(ym[:4]), int(ym[5:])
    n = y * 12 + (m - 1) - months
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


# ── 검증 도구(이 모듈 전용) ──────────────────────────────────────────────────────────────────


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _text(what: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} 가 비었다: {value!r}")


def _match(what: str, value: object, pattern: re.Pattern[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{what} 형식이 틀렸다: {value!r}")


def _choice(what: str, value: object, allowed: Iterable[str]) -> None:
    allowed_t = tuple(allowed)
    if value not in allowed_t:
        raise ValueError(f"{what} 는 {allowed_t} 중 하나: {value!r}")


def _aware(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, datetime):
        raise TypeError(f"{what} 는 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


def _usd(what: str, value: object, *, nonneg: bool = True) -> None:
    """미화 달러 정수(Decimal) 또는 None. float·소수는 거부(조용히 자르지 않는다)."""
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{what} 는 Decimal 이어야 한다(float 아님): {value!r}")
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError(f"{what} 는 달러 정수여야 한다: {value!r}")
    if nonneg and value < 0:
        raise ValueError(f"{what} 는 0 이상: {value!r}")


def _kg(what: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{what} 는 Decimal 이어야 한다(float 아님): {value!r}")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{what} 는 0 이상의 유한한 수: {value!r}")


def _count(what: str, value: object, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} 는 정수여야 한다: {value!r}")
    if value < 0:
        raise ValueError(f"{what} 는 0 이상: {value!r}")


def _num(what: str, value: object, *, lo: float | None = None, hi: float | None = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{what} 는 수여야 한다: {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{what} 는 유한한 수여야 한다: {value!r}")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{what} 는 {lo}~{hi} 범위: {value!r}")


def _common(obj: object, *, collected: bool) -> None:
    source: object = getattr(obj, "source")  # noqa: B009 — 행 형이 여럿
    if collected:
        _match("source(DATAGO:<포털 ID>)", source, _COLLECTED)
    else:
        _text("source", source)
    _aware("as_of", getattr(obj, "as_of"))  # noqa: B009
    q: object = getattr(obj, "quality")  # noqa: B009
    try:
        _set(obj, "quality", Quality(cast("str", q)))
    except ValueError:
        raise ValueError(f"quality 는 ok·stale·estimated·invalid 중 하나: {q!r}") from None


def _values_or_not_ok(obj: object, names: Iterable[str]) -> None:
    """값 칸이 모두 None 이면 quality 가 ok 일 수 없다(절대 규칙 2)."""
    if all(getattr(obj, n) is None for n in names) and getattr(obj, "quality") is Quality.OK:  # noqa: B009
        raise ValueError("값이 없는(None) 행은 quality 가 ok 일 수 없다 — 0 으로 채우지 않는다")


def _hs(obj: object, *, name: str = "hs") -> None:
    hs: object = getattr(obj, name)
    _match(name, hs, _HS)
    hs_len: object = getattr(obj, "hs_len", None)
    if hs_len is not None and hs_len != len(cast("str", hs)):
        raise ValueError(f"hs_len({hs_len}) ≠ len({hs!r})")


# ── 10일 잠정치(15157908·941·901·909 — metrics §25) ──────────────────────────────────────────


@dataclass(frozen=True)
class TenDayRow:
    """10일 단위 잠정치 한 칸(`pub_trade.ten_day`) — 그 달 1일부터 구간 끝 날까지 **누계**.

    seg 1 = 1~10일, 2 = 1~20일, 3 = 1~말일. col_no 0~10(0 = 전체), name = 그 시점 열 이름
    (`ten_day_columns`). usd = 최신 값, first_usd·first_seen_at = 처음 본 값·시각(발표 당시 —
    저장소가 덮지 않는다), revised_at = 값이 바뀐 마지막 시각(없으면 None). span_text = 응답
    구간 표기(`01~10`·`01~20`·`01~28`~`01~31`)."""

    kind: TenDayKind
    ym: str
    seg: int
    col_no: int
    name: str
    usd: Decimal | None
    first_usd: Decimal | None
    first_seen_at: datetime
    span_text: str
    source: str
    as_of: datetime
    quality: Quality
    revised_at: datetime | None = None

    def __post_init__(self) -> None:
        _choice("kind", self.kind, TEN_DAY_KINDS)
        _match("ym", self.ym, _YM)
        if isinstance(self.seg, bool) or self.seg not in (1, 2, 3):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(f"seg 는 1·2·3: {self.seg!r}")
        _count("col_no", self.col_no, optional=False)
        if self.col_no >= TEN_DAY_COLUMN_COUNT:
            raise ValueError(f"col_no 는 0~{TEN_DAY_COLUMN_COUNT - 1}: {self.col_no}")
        _text("name", self.name)
        _usd("usd", self.usd)
        _usd("first_usd", self.first_usd)
        _aware("first_seen_at", self.first_seen_at)
        _aware("revised_at", self.revised_at, optional=True)
        _match("span_text", self.span_text, _SPAN)
        want = {1: "10", 2: "20"}.get(self.seg)
        if (want is not None and self.span_text[3:] != want) or (
            self.seg == 3 and self.span_text[3:] in ("10", "20")
        ):
            raise ValueError(f"seg {self.seg} 와 구간 표기 {self.span_text!r} 가 맞지 않는다")
        _common(self, collected=True)
        _values_or_not_ok(self, ("usd",))

    @property
    def data_key(self) -> str:
        """데이터 키 `ten_day` 규칙 — `YYYYMM-n`(kbj.services.scheduler.conditions)."""
        return f"{self.ym[:4]}{self.ym[5:]}-{self.seg}"


@dataclass(frozen=True)
class TenDayColumn:
    """10일 잠정치 열 번호 → 이름의 대응 이력(`pub_trade.ten_day_columns`). valid_from = 이 이름이
    처음 쓰인 기준 월(`YYYY-MM`), seen_at = 처음 본 시각."""

    kind: TenDayKind
    col_no: int
    valid_from: str
    name: str
    seen_at: datetime

    def __post_init__(self) -> None:
        _choice("kind", self.kind, TEN_DAY_KINDS)
        _count("col_no", self.col_no, optional=False)
        if self.col_no >= TEN_DAY_COLUMN_COUNT:
            raise ValueError(f"col_no 는 0~{TEN_DAY_COLUMN_COUNT - 1}: {self.col_no}")
        _match("valid_from", self.valid_from, _YM)
        _text("name", self.name)
        _aware("seen_at", self.seen_at)


# ── 월간(15101609·15101612·15100475·15134343 — metrics §26) ─────────────────────────────────


@dataclass(frozen=True)
class ItemMonthRow:
    """품목별 월간(`pub_trade.item_monthly`). hs = 응답 그대로의 자릿수(hs_len) — 접두 합산은 엔진이
    가장 깊은 행만 더한다(metrics §26). bal_usd = 무역수지(음수 가능)."""

    ym: str
    hs: str
    hs_len: int
    exp_usd: Decimal | None
    exp_kg: Decimal | None
    imp_usd: Decimal | None
    imp_kg: Decimal | None
    bal_usd: Decimal | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _match("ym", self.ym, _YM)
        _hs(self)
        _usd("exp_usd", self.exp_usd)
        _usd("imp_usd", self.imp_usd)
        _usd("bal_usd", self.bal_usd, nonneg=False)
        _kg("exp_kg", self.exp_kg)
        _kg("imp_kg", self.imp_kg)
        _common(self, collected=True)
        _values_or_not_ok(self, ("exp_usd", "imp_usd"))


@dataclass(frozen=True)
class CountryMonthRow:
    """국가별 월간(`pub_trade.country_monthly`) — 중량 없음, 건수 있음."""

    ym: str
    country: str
    country_name: str
    exp_cnt: int | None
    exp_usd: Decimal | None
    imp_cnt: int | None
    imp_usd: Decimal | None
    bal_usd: Decimal | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _match("ym", self.ym, _YM)
        _match("country", self.country, _COUNTRY)
        _text("country_name", self.country_name)
        _count("exp_cnt", self.exp_cnt)
        _count("imp_cnt", self.imp_cnt)
        _usd("exp_usd", self.exp_usd)
        _usd("imp_usd", self.imp_usd)
        _usd("bal_usd", self.bal_usd, nonneg=False)
        _common(self, collected=True)
        _values_or_not_ok(self, ("exp_usd", "imp_usd"))


@dataclass(frozen=True)
class ItemCountryMonthRow:
    """품목×국가 월간(`pub_trade.item_country_monthly`)."""

    ym: str
    country: str
    hs: str
    hs_len: int
    exp_usd: Decimal | None
    exp_kg: Decimal | None
    imp_usd: Decimal | None
    imp_kg: Decimal | None
    bal_usd: Decimal | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _match("ym", self.ym, _YM)
        _match("country", self.country, _COUNTRY)
        _hs(self)
        _usd("exp_usd", self.exp_usd)
        _usd("imp_usd", self.imp_usd)
        _usd("bal_usd", self.bal_usd, nonneg=False)
        _kg("exp_kg", self.exp_kg)
        _kg("imp_kg", self.imp_kg)
        _common(self, collected=True)
        _values_or_not_ok(self, ("exp_usd", "imp_usd"))


@dataclass(frozen=True)
class SigunguMonthRow:
    """시군구×HS6 월간(`pub_trade.sigungu_monthly`). 응답은 시군구 **이름만** 준다 — region_id 는
    `config/knowledge/trade_regions.yaml` 의 통합 ID(개편 대응 — 못 찾으면 None)."""

    ym: str
    sido_cd: str
    sgg_nm: str
    hs6: str
    region_id: str | None
    exp_usd: Decimal | None
    exp_cnt: int | None
    imp_usd: Decimal | None
    imp_cnt: int | None
    bal_usd: Decimal | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _match("ym", self.ym, _YM)
        _match("sido_cd", self.sido_cd, _SIDO)
        _text("sgg_nm", self.sgg_nm)
        _match("hs6", self.hs6, _HS)
        if len(self.hs6) != 6:
            raise ValueError(f"hs6 는 6자리: {self.hs6!r}")
        _match("region_id", self.region_id, _REGION, optional=True)
        _usd("exp_usd", self.exp_usd)
        _usd("imp_usd", self.imp_usd)
        _usd("bal_usd", self.bal_usd, nonneg=False)
        _count("exp_cnt", self.exp_cnt)
        _count("imp_cnt", self.imp_cnt)
        _common(self, collected=True)
        _values_or_not_ok(self, ("exp_usd", "imp_usd"))


@dataclass(frozen=True)
class HsDim:
    """HS 코드 차원(`pub_trade.hs_dim`) — 응답에서 본 코드·한글 품목명·처음·마지막 본 달."""

    hs: str
    hs_len: int
    name_ko: str
    first_ym: str
    last_ym: str
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _hs(self)
        _text("name_ko", self.name_ko)
        _match("first_ym", self.first_ym, _YM)
        _match("last_ym", self.last_ym, _YM)
        if self.first_ym > self.last_ym:
            raise ValueError(f"first_ym {self.first_ym} > last_ym {self.last_ym}")
        _common(self, collected=True)


# ── 파생(trade.derive — metrics §26·§29) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SurgeRow:
    """급등·급락 한 줄(`pub_trade.surge`). level = hs2·hs4·hs6·hs10·country, key = HS 접두 또는 국가
    ISO2. rule = surge·plunge. yoy·yoy_3m 은 소수(0.5 = +50%). rank 는 1부터."""

    ym: str
    level: SurgeLevel
    key: str
    flow: Flow
    rule: SurgeRule
    yoy: float | None
    yoy_3m: float | None
    usd: Decimal | None
    rank: int
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _match("ym", self.ym, _YM)
        _choice("level", self.level, SURGE_LEVELS)
        if self.level == "country":
            _match("key(국가)", self.key, _COUNTRY)
        else:
            _match("key(HS)", self.key, _HS)
            if len(self.key) != int(self.level[2:]):
                raise ValueError(f"level {self.level} 와 key {self.key!r} 자릿수가 다르다")
        _choice("flow", self.flow, FLOWS)
        _choice("rule", self.rule, SURGE_RULES)
        _num("yoy", self.yoy)
        _num("yoy_3m", self.yoy_3m)
        _usd("usd", self.usd)
        _count("rank", self.rank, optional=False)
        if self.rank < 1:
            raise ValueError(f"rank 는 1 이상: {self.rank}")
        _common(self, collected=False)
        _values_or_not_ok(self, ("yoy", "usd"))


@dataclass(frozen=True)
class ItemGroupMonthRow:
    """104품목 묶음 월간(`pub_trade.item_group_monthly`). mode = fixed(설정 묶음)·top_n(자동 선택).
    unit_price = 수출 금액 ÷ 중량(달러/kg — 중량 0·None 이면 None)."""

    ym: str
    group_id: str
    exp_usd: Decimal | None
    exp_kg: Decimal | None
    imp_usd: Decimal | None
    imp_kg: Decimal | None
    yoy: float | None
    unit_price: float | None
    mode: ItemGroupMode
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _match("ym", self.ym, _YM)
        _match("group_id", self.group_id, _GROUP)
        _usd("exp_usd", self.exp_usd)
        _usd("imp_usd", self.imp_usd)
        _kg("exp_kg", self.exp_kg)
        _kg("imp_kg", self.imp_kg)
        _num("yoy", self.yoy)
        _num("unit_price", self.unit_price, lo=0)
        _choice("mode", self.mode, ITEM_GROUP_MODES)
        _common(self, collected=False)
        _values_or_not_ok(self, ("exp_usd", "imp_usd"))


# ── 매핑표·종목 수출 지수(trade.map_load·trade.stock_index — metrics §28) ────────────────────


def _evidence(obj: object) -> None:
    v: object = getattr(obj, "evidence")  # noqa: B009
    if isinstance(v, str | bytes) or not isinstance(v, Iterable):
        raise TypeError(f"evidence 는 매핑 목록이어야 한다: {v!r}")
    items = tuple(cast("Iterable[object]", v))
    out: list[dict[str, object]] = []
    for x in items:
        if not isinstance(x, Mapping):
            raise TypeError(f"evidence 원소는 매핑: {x!r}")
        out.append({str(k): val for k, val in cast("Mapping[object, object]", x).items()})
    _set(obj, "evidence", tuple(out))


@dataclass(frozen=True)
class StockMapRow:
    """매핑표 한 줄(`pub_trade.stock_map`). 품목 행은 region_id = ''(빈 문자열 — 기본 키에 NULL 을
    두지 않는다), 지역 행은 region_id 와 hs = HS6·flow = exp. weight 0~1. countries = 품목 행의 국가
    목록(비면 전 국가). origin public = 공개 매핑표(레포), local = VM 전용(관심종목 —
    공개 내보내기는 읽지 않는다, D-P6-14). evidence = 근거 목록(DART 접수번호 등)."""

    code: str
    hs: str
    flow: Flow
    region_id: str
    name: str
    weight: float
    origin: MapOrigin
    reviewed: bool
    map_version: str
    source: str
    as_of: datetime
    quality: Quality
    countries: tuple[str, ...] = ()
    evidence: tuple[Mapping[str, object], ...] = field(default=())

    def __post_init__(self) -> None:
        _match("code", self.code, _CODE)
        _match("hs", self.hs, _HS)
        _choice("flow", self.flow, FLOWS)
        if self.region_id != "":
            _match("region_id", self.region_id, _REGION)
            if len(self.hs) != 6 or self.flow != "exp":
                raise ValueError("지역 행은 hs = HS6·flow = exp")
        _text("name", self.name)
        _num("weight", self.weight, lo=0.0, hi=1.0)
        if self.weight is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise ValueError("weight 가 없다")
        _choice("origin", self.origin, MAP_ORIGINS)
        if not isinstance(self.reviewed, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"reviewed 는 bool: {self.reviewed!r}")
        _text("map_version", self.map_version)
        cs = tuple(self.countries)
        for c in cs:
            _match("countries 원소", c, _COUNTRY)
        if len(set(cs)) != len(cs):
            raise ValueError(f"countries 가 겹친다: {cs}")
        _set(self, "countries", cs)
        _evidence(self)
        _common(self, collected=False)


@dataclass(frozen=True)
class StockExportRow:
    """종목 수출 지수 한 기간(`pub_trade.stock_export`). period = `YYYY-MM`(월 지수)·`YYYYQn`(분기 —
    매출 YoY·상관). lag_corr = {"0": r, "1": r, "2": r}(수출이 k 분기 앞섬, 겹침 부족이면 None).
    status = ok·no_revenue·short_overlap·no_trade(metrics §28). 시세 칸은 없다(공개)."""

    code: str
    period: str
    export_usd: Decimal | None
    yoy: float | None
    revenue_yoy: float | None
    status: StockExportStatus
    map_version: str
    source: str
    as_of: datetime
    quality: Quality
    lag_corr: Mapping[str, float | None] = field(default_factory=dict[str, float | None])

    def __post_init__(self) -> None:
        _match("code", self.code, _CODE)
        _match("period", self.period, _PERIOD)
        _usd("export_usd", self.export_usd)
        _num("yoy", self.yoy)
        _num("revenue_yoy", self.revenue_yoy)
        _choice("status", self.status, STOCK_EXPORT_STATUSES)
        _text("map_version", self.map_version)
        if not isinstance(self.lag_corr, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"lag_corr 는 Mapping: {self.lag_corr!r}")
        lc: dict[str, float | None] = {}
        for k, v in self.lag_corr.items():
            if k not in ("0", "1", "2"):
                raise ValueError(f"lag_corr 키는 '0'·'1'·'2': {k!r}")
            _num(f"lag_corr[{k}]", v, lo=-1.0, hi=1.0)
            lc[k] = v
        _set(self, "lag_corr", lc)
        _common(self, collected=False)

    @property
    def is_quarter(self) -> bool:
        return "Q" in self.period


@dataclass(frozen=True)
class ChapterMonth:
    """류(HS 2자리)·월 합 — `pub_trade.item_monthly` HS10 행만 더한 것(metrics §29a)."""

    ym: str  # YYYY-MM
    chapter: str  # HS 2자리
    exp_usd: int
    imp_usd: int
