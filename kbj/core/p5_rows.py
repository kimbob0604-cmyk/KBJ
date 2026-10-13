"""P5 행 자료형 — 매크로·경제 캘린더·미국 보드·금투협 통계·장중 투자자 1분·
프로그램매매·ETF 맵·브리핑 섹션(엔진·저장소·수집·API 가 같이 쓴다).

근거: docs/p5_design.md §1.1(FND)·§3.3(마이그레이션 0014~0017 열 — 테마 표는
사용자 별도 진행으로 뺐다)·§4(엔진 입출력), docs/metrics.md
§16~§23, docs/p5_flow_program_prompt.md §1.1·§1.4.

P3 `kbj.core.rows`·P4 `kbj.core.fin_rows` 와 같은 자리·같은 규칙이다(그 파일들은 고치지 않는다 —
설계 §1.1). 엔진은 저장소를 모르고(계약 ⑦·⑨) 저장소는 엔진을 모른다. 웨이브 2 묶음(MAC·ETF·BRF·
US·F2)은 서로의 코드가 아니라 이 모양으로만 맞춘다(D-P5-2). 이 모듈은 I/O 를 하지 않는다(계약 ③).

규칙(CLAUDE.md 절대 규칙 1·2, docs/metrics.md §0)
- 값 행은 `source`·`quality` 를 갖고 as_of 는 행의 날짜·시각(aware) 또는 `as_of` 칸이다.
- 매크로·금투협 값은 `Decimal`(출처 공표 값 그대로 — float 로 바꾸지 않는다).
  **없는 값은 None** 이고 0 으로 채우지 않는다. 값이 None 이면 quality 가 ok 일 수 없다
  (0014·0016 CHECK 와 같다).
- KR 금액(프로그램매매)은 **원 단위 정수**만(파서가 바꿔 넘긴다). 예외: 장중 투자자 1분
  (`InvestorMinute` — `prv_gex.investor_flow`)은 GX 계약 그대로 **대금 백만원** 정수
  (metrics §21, D-P5-21 ②). API 가 원으로 바꾼다.
- 가격·비율·배수는 `float`(유한값만). naive 시각은 받지 않는다. 날짜 칸에 `datetime` 은 거부.
- 종목코드 `^[0-9A-Z]{6}$`. 열거 값은 마이그레이션 CHECK 와 같다
  (tests/test_store_layout.py 가 대조).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final, Literal, cast

from kbj.core.quality import Quality

__all__ = [
    "BRIEF_KINDS",
    "BRIEF_STATUSES",
    "ETF_MAP_PERIODS",
    "MACRO_FREQS",
    "MARKET_STATS",
    "MINUTE_CARDS",
    "MINUTE_INVESTORS",
    "PROGRAM_KINDS",
    "PROGRAM_MARKETS",
    "PROGRAM_SCOPES",
    "PROGRAM_SIDES",
    "PUBLIC_CALENDAR_SOURCES",
    "PUBLIC_MACRO_SOURCES",
    "TIERS",
    "US_ARTIFACTS",
    "BriefSection",
    "CalendarEvent",
    "EtfHolder",
    "EtfMapCell",
    "InvestorMinute",
    "MacroSeriesMeta",
    "MacroSeriesRow",
    "MarketStatRow",
    "ProgramInvestorRow",
    "ProgramMarketRow",
    "ProgramStockRow",
    "UsArtifact",
    "UsDailyRow",
    "UsUniverseRow",
    "event_id",
]

# ── 열거(마이그레이션 CHECK 와 같은 말) ──────────────────────────────────────────────────────

TIERS: Final[tuple[str, ...]] = ("public", "private")
MACRO_FREQS: Final[tuple[str, ...]] = ("D", "W", "M", "Q", "A")
# pub_macro.series·series_meta 의 source(0014) — 공개 데이터셋 출처(DATA_TIERS §1)
PUBLIC_MACRO_SOURCES: Final[tuple[str, ...]] = ("ECOS", "KOSIS", "FRED", "TREASURY", "NYFED")
# pub_macro.calendar 의 source(0014 — 공식 일정·FRED 발표일만, D-P5-8)
PUBLIC_CALENDAR_SOURCES: Final[tuple[str, ...]] = ("bok", "fomc", "fred", "kosis", "ecos", "manual")
US_ARTIFACTS: Final[tuple[str, ...]] = ("us_summary", "us_brief")
MARKET_STATS: Final[tuple[str, ...]] = ("credit_balance", "market_capital", "fund_nav", "cma")
# 프로그램매매(F2 §1.4 — KIS 응답 칸 접두·접미 그대로)
PROGRAM_MARKETS: Final[tuple[str, ...]] = ("K", "Q")  # 코스피·코스닥(FID_MRKT_CLS_CODE)
PROGRAM_KINDS: Final[tuple[str, ...]] = ("arbt", "nabt", "whol")  # 차익·비차익·전체
PROGRAM_SIDES: Final[tuple[str, ...]] = ("entm", "onsl", "smtn")  # 위탁·자기·합계
PROGRAM_SCOPES: Final[tuple[str, ...]] = ("scan", "sweep")  # 장중 범위 훑기·마감 전 종목
PROGRAM_VENUES: Final[tuple[str, ...]] = ("KRX", "NXT", "TOTAL")
# 장중 투자자 12구분(KIS FHPTJ04030000 응답 접두 — GX data/kis/models.py:INVESTORS 와 같은 순서)
MINUTE_INVESTORS: Final[tuple[str, ...]] = (
    "frgn", "prsn", "orgn", "scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund", "etc_orgt",
    "etc_corp",
)  # fmt: skip
# 화면 카드 7(metrics §21 — 투신은 사모 제외 [확인 필요 §10 Q7]). (투자자, 표시 이름)
MINUTE_CARDS: Final[tuple[tuple[str, str], ...]] = (
    ("prsn", "개인"),
    ("frgn", "외국인"),
    ("orgn", "기관 합계"),
    ("scrt", "금융투자"),
    ("ivtr", "투신"),
    ("fund", "연기금"),
    ("etc_corp", "기타법인"),
)
ETF_MAP_PERIODS: Final[tuple[str, ...]] = ("1D", "1W", "1M", "3M")
BRIEF_KINDS: Final[tuple[str, ...]] = ("morning", "closing")
BRIEF_STATUSES: Final[tuple[str, ...]] = ("ok", "not_ready", "omitted")

_STOCK = re.compile(r"[0-9A-Z]{6}")
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_THEME_ID = re.compile(r"[a-z0-9][a-z0-9_]*")
_SERIES_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/^=-]*")
_PERIOD = re.compile(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?|-Q[1-4])?")
_DATASET = re.compile(r"[A-Z][A-Z0-9_]*:\S+")
_VERSION = re.compile(r"[0-9a-f]{12}-[0-9]{8}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COUNTRY = re.compile(r"[A-Z]{2}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_US_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.^=/-]*")
_EVENT_ID = re.compile(r"[A-Z]{2}:[a-z][a-z0-9_]*:[0-9]{4}-[0-9]{2}-[0-9]{2}:\S+")


# ── 검증 도구(이 모듈 전용 — rows·fin_rows 의 사설 함수에 기대지 않는다) ─────────────────────────


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _text(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} 가 비었다: {value!r}")


def _match(what: str, value: object, pattern: re.Pattern[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{what} 형식이 틀렸다: {value!r}")


def _choice(what: str, value: object, allowed: Iterable[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    allowed_t = tuple(allowed)
    if value not in allowed_t:
        raise ValueError(f"{what} 는 {allowed_t} 중 하나: {value!r}")


def _day(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{what} 는 date 여야 한다(datetime·문자열 아님): {value!r}")


def _aware(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, datetime):
        raise TypeError(f"{what} 는 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


def _int(what: str, value: object, *, nonneg: bool = False) -> None:
    """정수(원·백만원·주) 또는 None — float·bool 은 거부(단위 변환은 파서가 끝낸 뒤)."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} 는 정수여야 한다: {value!r}")
    if nonneg and value < 0:
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


def _dec(what: str, value: object) -> None:
    """출처 공표 값 — Decimal(유한) 또는 None. float 는 거부(공표 자릿수를 잃지 않게)."""
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{what} 는 Decimal 이어야 한다(float 아님): {value!r}")
    if not value.is_finite():
        raise ValueError(f"{what} 는 유한한 수여야 한다: {value!r}")


def _bool(what: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{what} 는 bool 이어야 한다: {value!r}")


def _quality(obj: object, name: str = "quality") -> None:
    q = getattr(obj, name)
    try:
        _set(obj, name, Quality(q))
    except ValueError:
        raise ValueError(f"{name} 는 ok·stale·estimated·invalid 중 하나: {q!r}") from None


def _value_quality(obj: object) -> None:
    """값이 없으면 quality 가 ok 일 수 없다(0014·0016 CHECK — 절대 규칙 2)."""
    value: object = getattr(obj, "value")  # noqa: B009 — 행 형이 여럿(Macro·MarketStat)
    quality: object = getattr(obj, "quality")  # noqa: B009
    if value is None and quality is Quality.OK:
        raise ValueError("값이 없는(None) 행은 quality 가 ok 일 수 없다 — 0 으로 채우지 않는다")


def _strs(obj: object, name: str) -> None:
    v: object = getattr(obj, name)
    if isinstance(v, str) or not isinstance(v, Iterable):
        raise TypeError(f"{name} 는 문자열 모음이어야 한다: {v!r}")
    items: tuple[object, ...] = tuple(cast("Iterable[object]", v))
    for x in items:
        if not isinstance(x, str):
            raise TypeError(f"{name} 의 원소는 문자열: {x!r}")
    _set(obj, name, items)


def _frozen_map(obj: object, name: str) -> None:
    v = getattr(obj, name)
    if not isinstance(v, Mapping):
        raise TypeError(f"{name} 는 Mapping 이어야 한다: {v!r}")
    _set(obj, name, dict(v))  # pyright: ignore[reportUnknownArgumentType]


def _flows(obj: object) -> None:
    for n in ("sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", "net_value"):
        _int(n, getattr(obj, n))
    for n in ("sell_qty", "buy_qty", "sell_value", "buy_value"):
        _int(n, getattr(obj, n), nonneg=True)


def event_id(country: str, kind: str, day: date, code: str) -> str:
    """캘린더 행 키 `<country>:<kind>:<YYYY-MM-DD>:<code>`(0014)."""
    eid = f"{country}:{kind}:{day.isoformat()}:{code}"
    _match("event_id", eid, _EVENT_ID)
    return eid


# ── 매크로(0014 — metrics §16) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MacroSeriesMeta:
    """시리즈 메타(`pub_macro.series_meta`·`prv_macro.series_meta`). tier 가 표를 정한다(D-P5-6).

    dataset = 카탈로그 id(예 `FRED:gov_series`·`ECOS:722Y001`·`YAHOO:macro`)."""

    series_id: str
    tab: str
    title: str
    unit: str
    freq: Literal["D", "W", "M", "Q", "A"]
    tier: Literal["public", "private"]
    source: str
    dataset: str

    def __post_init__(self) -> None:
        _match("series_id", self.series_id, _SERIES_ID)
        _match("tab", self.tab, _IDENT)
        _text("title", self.title)
        _text("unit", self.unit)
        _choice("freq", self.freq, MACRO_FREQS)
        _choice("tier", self.tier, TIERS)
        _text("source", self.source)
        _match("dataset", self.dataset, _DATASET)
        if self.tier == "public":
            _choice("공개 시리즈의 source", self.source, PUBLIC_MACRO_SOURCES)
            if self.dataset == "FRED:restricted_series":
                raise ValueError("FRED 저작권 시리즈는 공개 시리즈가 될 수 없다(D-P5-6)")


@dataclass(frozen=True)
class MacroSeriesRow:
    """시리즈 관측값 한 줄(`pub_macro.series`·`prv_macro.series`).

    period = YYYY·YYYY-MM·YYYY-Qn·YYYY-MM-DD. as_of = 이 값의 기준 시각(받은 시각),
    released_at = 출처 공표 시각(모르면 None). prev_value = 개정으로 덮기 전 값
    (없으면 None — [확인 필요 metrics §16])."""

    series_id: str
    period: str
    value: Decimal | None
    unit: str
    source: str
    as_of: datetime
    quality: Quality
    released_at: datetime | None = None
    prev_value: Decimal | None = None

    def __post_init__(self) -> None:
        _match("series_id", self.series_id, _SERIES_ID)
        _match("period", self.period, _PERIOD)
        _dec("value", self.value)
        _dec("prev_value", self.prev_value)
        _text("unit", self.unit)
        _text("source", self.source)
        _aware("as_of", self.as_of)
        _aware("released_at", self.released_at, optional=True)
        _quality(self)
        _value_quality(self)


@dataclass(frozen=True)
class CalendarEvent:
    """경제 캘린더 한 줄(`pub_macro.calendar`·`prv_macro.calendar` — D-P5-8).

    공개 행은 source 가 공개 출처(`PUBLIC_CALENDAR_SOURCES`)이고 forecast 가 None 이다
    (공개 표에 칸이 없다 — 저장소가 거부). actual = 수집된 시리즈 값(actual_series)만 —
    지어내지 않는다."""

    event_id: str
    kind: str
    date: date
    time_kst: time | None
    title: str
    country: str
    importance: int
    source: str
    quality: Quality
    actual: Decimal | None = None
    actual_series: str | None = None
    unit: str | None = None
    forecast: Decimal | None = None

    def __post_init__(self) -> None:
        _match("event_id", self.event_id, _EVENT_ID)
        _match("kind", self.kind, _IDENT)
        _day("date", self.date)
        if self.time_kst is not None and not isinstance(self.time_kst, time):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"time_kst 는 time 이어야 한다: {self.time_kst!r}")
        _text("title", self.title)
        _match("country", self.country, _COUNTRY)
        _int("importance", self.importance)
        if not 1 <= self.importance <= 3:
            raise ValueError(f"importance 는 1~3: {self.importance!r}")
        _text("source", self.source)
        _quality(self)
        _dec("actual", self.actual)
        _match("actual_series", self.actual_series, _SERIES_ID, optional=True)
        _text("unit", self.unit, optional=True)
        _dec("forecast", self.forecast)
        country, kind, day, _ = self.event_id.split(":", 3)
        if (country, kind, day) != (self.country, self.kind, self.date.isoformat()):
            raise ValueError(f"event_id 가 country·kind·date 와 맞지 않는다: {self.event_id!r}")

    @property
    def is_public(self) -> bool:
        return self.source in PUBLIC_CALENDAR_SOURCES and self.forecast is None


# ── 미국 보드(0015 — metrics §23, 로그인) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class UsUniverseRow:
    """미국 유니버스 한 줄(`prv_market.us_universe` — Nasdaq 스크리너). market_cap 은 달러."""

    symbol: str
    as_of: date
    name: str | None
    exchange: str | None
    sector: str | None
    industry: str | None
    market_cap: float | None
    is_fund: bool
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _match("symbol", self.symbol, _US_SYMBOL)
        _day("as_of", self.as_of)
        _num("market_cap", self.market_cap, lo=0.0)
        _bool("is_fund", self.is_fund)
        _text("source", self.source)
        _quality(self)


@dataclass(frozen=True)
class UsDailyRow:
    """미국 일봉 한 줄 — `prv_market.daily_bar`(market='US', venue '')에 쓴다
    (새 표 없음 — D-P5-18).

    P3 `Bar` 는 turnover(원)·adjusted 없음이 미국 일봉과 맞지 않아 따로 둔다
    [확인 필요 → US 묶음이 확정]. source = 실제 출처(nasdaq·stooq·yahoo),
    adjusted = 수정주가 여부(모르면 None)."""

    symbol: str
    date: date
    asset: Literal["stock", "etf", "index"]
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    adjusted: bool | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        if not self.symbol or re.search(r"\s", self.symbol):
            raise ValueError(f"symbol 은 공백 없는 문자열: {self.symbol!r}")
        _day("date", self.date)
        _choice("asset", self.asset, ("stock", "etf", "index"))
        for n in ("open", "high", "low", "close"):
            _num(n, getattr(self, n))
        _int("volume", self.volume, nonneg=True)
        if self.adjusted is not None:
            _bool("adjusted", self.adjusted)
        _text("source", self.source)
        _quality(self)


@dataclass(frozen=True)
class UsArtifact:
    """미국 보드 산출 하나(`prv_board.artifact` market='US' — 0015 가 이름을 넓혔다).

    us_brief = 아침 브리핑 ① 이 그대로 싣는 텍스트 산출(BRF 는 읽기만 — D-P5-18)."""

    trade_date: date  # 미국 거래일(뉴욕 날짜)
    name: Literal["us_summary", "us_brief"]
    payload: Mapping[str, Any]
    engine_version: str
    input_digest: str
    as_of: datetime
    generated_at: datetime
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _choice("name", self.name, US_ARTIFACTS)
        _frozen_map(self, "payload")
        _text("engine_version", self.engine_version)
        _text("input_digest", self.input_digest)
        _aware("as_of", self.as_of)
        _aware("generated_at", self.generated_at)
        _text("source", self.source)
        _quality(self)


# ── 금투협 종합통계(0016 — 공개) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketStatRow:
    """금투협 통계 한 값(`pub_market_stats.<stat>` — stat 이 표 이름, 열 stat_date).

    단위는 출처 공표 그대로."""

    stat: Literal["credit_balance", "market_capital", "fund_nav", "cma"]
    date: date
    item: str
    value: Decimal | None
    unit: str
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _choice("stat", self.stat, MARKET_STATS)
        _day("date", self.date)
        _match("item", self.item, _IDENT)
        _dec("value", self.value)
        _text("unit", self.unit)
        if self.source != "DATAGO":
            raise ValueError(f"금투협 통계 source 는 DATAGO(0016): {self.source!r}")
        _aware("as_of", self.as_of)
        _quality(self)
        _value_quality(self)


# ── 장중 투자자 1분(0006 prv_gex.investor_flow — F2, metrics §21) ──────────────────────────────


@dataclass(frozen=True)
class InvestorMinute:
    """`prv_gex.investor_flow` 한 행 — 그 분(슬롯)의 **오늘 누적**(KIS FHPTJ04030000 한 줄).

    단위: 수량 계약·주, **대금 백만원**(GX 계약 — D-P5-21 ②). ts = 수집 슬롯 분(호출 시각 아님).
    표에 source 열이 없다(뷰 `prv_flows.market_investor_intraday` 가 'KIS' 를 붙인다) —
    행은 'KIS'."""

    ts: datetime
    trade_date: date
    session: Literal["day", "night"]
    market_code: str
    sector_code: str
    investor: str
    sell_qty: int | None
    buy_qty: int | None
    net_qty: int | None
    sell_value: int | None
    buy_value: int | None
    net_value: int | None
    quality: Quality
    source: str = "KIS"

    def __post_init__(self) -> None:
        _aware("ts", self.ts)
        _day("trade_date", self.trade_date)
        _choice("session", self.session, ("day", "night"))
        _text("market_code", self.market_code)
        _text("sector_code", self.sector_code)
        _choice("investor", self.investor, MINUTE_INVESTORS)
        _flows(self)
        _quality(self)
        if self.source != "KIS":
            raise ValueError(f"investor_flow 는 KIS 행만(뷰가 'KIS' 를 붙인다): {self.source!r}")
        if self.ts.second or self.ts.microsecond:
            raise ValueError(f"ts 는 분 경계(슬롯 분): {self.ts.isoformat()}")


# ── 프로그램매매(0017 — F2, metrics §22, 금액 원) ─────────────────────────────────────────────


@dataclass(frozen=True)
class ProgramMarketRow:
    """시장 프로그램 한 줄 — ts 가 없으면 일별(`program_market_daily`), 있으면 장중
    (`program_market_intraday` — 슬롯 분). kind × side 긴 형식."""

    trade_date: date
    market: Literal["K", "Q"]
    venue: Literal["KRX", "NXT", "TOTAL"]
    kind: Literal["arbt", "nabt", "whol"]
    side: Literal["entm", "onsl", "smtn"]
    sell_qty: int | None
    buy_qty: int | None
    net_qty: int | None
    sell_value: int | None
    buy_value: int | None
    net_value: int | None
    index_close: float | None
    source: str
    quality: Quality
    ts: datetime | None = None

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _choice("market", self.market, PROGRAM_MARKETS)
        _choice("venue", self.venue, PROGRAM_VENUES)
        _choice("kind", self.kind, PROGRAM_KINDS)
        _choice("side", self.side, PROGRAM_SIDES)
        _flows(self)
        _num("index_close", self.index_close)
        _text("source", self.source)
        _quality(self)
        _aware("ts", self.ts, optional=True)


@dataclass(frozen=True)
class ProgramInvestorRow:
    """투자자별 프로그램 당일(`prv_flows.program_investor_daily` — HHPPG046600C1)."""

    trade_date: date
    market: Literal["K", "Q"]
    venue: Literal["KRX", "NXT", "TOTAL"]
    investor: str
    kind: Literal["arbt", "nabt", "whol"]
    sell_qty: int | None
    buy_qty: int | None
    net_qty: int | None
    sell_value: int | None
    buy_value: int | None
    net_value: int | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _choice("market", self.market, PROGRAM_MARKETS)
        _choice("venue", self.venue, PROGRAM_VENUES)
        if not isinstance(self.investor, str) or not re.fullmatch(r"[a-z][a-z_]*", self.investor):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(f"investor 는 소문자 식별자: {self.investor!r}")
        _choice("kind", self.kind, PROGRAM_KINDS)
        _flows(self)
        _text("source", self.source)
        _quality(self)


@dataclass(frozen=True)
class ProgramStockRow:
    """종목 프로그램 한 줄 — ts 가 있으면 장중(`program_stock_intraday`, scope scan), 없으면 일별
    (`program_stock_daily`, scope sweep). price 원, chg_pct %, 금액 원."""

    trade_date: date
    code: str
    market: Literal["K", "Q"]
    venue: Literal["KRX", "NXT", "TOTAL"]
    scope: Literal["scan", "sweep"]
    price: float | None
    chg_pct: float | None
    sell_qty: int | None
    buy_qty: int | None
    net_qty: int | None
    sell_value: int | None
    buy_value: int | None
    net_value: int | None
    source: str
    quality: Quality
    ts: datetime | None = None

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _match("code", self.code, _STOCK)
        _choice("market", self.market, PROGRAM_MARKETS)
        _choice("venue", self.venue, PROGRAM_VENUES)
        _choice("scope", self.scope, PROGRAM_SCOPES)
        _num("price", self.price, lo=0.0)
        _num("chg_pct", self.chg_pct)
        _flows(self)
        _text("source", self.source)
        _quality(self)
        _aware("ts", self.ts, optional=True)


# ── ETF 맵·보유 탐색(metrics §19 — 로그인, 저장하지 않는 계산 행) ───────────────────────────────


@dataclass(frozen=True)
class EtfMapCell:
    """ETF 맵 셀 하나 — 크기 = 순자산(원, 마지막 확정일), 색 = 기간 수익률(%, 분할 보정).

    엔진(ETF 묶음)이 만든다."""

    code: str
    name: str | None
    etf_type: str | None  # metrics §8.1 유형(kbj.core.rows.EtfType 값)
    theme: str | None  # kbj.engines.etf.types.classify
    net_asset: int | None
    ret_pct: float | None
    period: Literal["1D", "1W", "1M", "3M"]
    leveraged: bool
    new_listing: bool  # 기간보다 짧은 이력(회색 + 꼬리표)
    asof: date
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("code 가 비었다")
        _int("net_asset", self.net_asset, nonneg=True)
        _num("ret_pct", self.ret_pct)
        _choice("period", self.period, ETF_MAP_PERIODS)
        _bool("leveraged", self.leveraged)
        _bool("new_listing", self.new_listing)
        _day("asof", self.asof)
        _text("source", self.source)
        _quality(self)


@dataclass(frozen=True)
class EtfHolder:
    """보유 한 줄 — ETF(펀드) 하나가 종목 하나를 담은 것.

    asof = 마지막 PDF 기준일(운용사마다 다르면 그대로 — 섞지 않는다)."""

    fund_id: str
    etf_code: str | None  # 상장 코드(펀드 ticker — 없으면 None)
    etf_name: str | None
    is_active: bool
    asof: date
    code: str
    name: str | None
    wt: float | None  # 비중 %
    qty: float | None
    val: float | None  # 평가금액(원)
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _text("fund_id", self.fund_id)
        _bool("is_active", self.is_active)
        _day("asof", self.asof)
        _text("code", self.code)
        for n in ("wt", "qty", "val"):
            _num(n, getattr(self, n))
        _text("source", self.source)
        _quality(self)


# ── 브리핑(metrics §20) ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BriefSection:
    """브리핑 섹션 하나(metrics §20).

    status = ok(본문 있음)·not_ready('미준비 — <작업>(<예정 시각>)')·omitted(단계 밖 — 싣지 않고
    품질 줄에도 적지 않는다). 재료가 없으면 as_of·source 도 없다(None — 지어내지 않는다)."""

    kind: Literal["morning", "closing"]
    key: str
    title: str
    lines: tuple[str, ...]
    status: Literal["ok", "not_ready", "omitted"]
    source: str | None
    as_of: datetime | None
    quality: Quality | None

    def __post_init__(self) -> None:
        _choice("kind", self.kind, BRIEF_KINDS)
        _match("key", self.key, re.compile(r"[a-z0-9][a-z0-9_]*"))
        _text("title", self.title)
        _strs(self, "lines")
        _choice("status", self.status, BRIEF_STATUSES)
        _text("source", self.source, optional=True)
        _aware("as_of", self.as_of, optional=True)
        if self.quality is not None:
            _quality(self)
        if self.status == "ok" and (
            self.source is None or self.as_of is None or self.quality is None
        ):
            raise ValueError("ok 섹션은 source·as_of·quality 가 있어야 한다(절대 규칙 1)")
        if self.status == "omitted" and self.lines:
            raise ValueError("omitted 섹션은 본문이 없다")
