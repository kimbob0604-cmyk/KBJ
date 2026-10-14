"""페이지 3 진단·GEX 화면의 행 자료형 — 0020 `pub_diag.*`·`prv_diag.*`(P7 묶음 FND-B).

근거: docs/p7_design.md §1.1·§3.3, docs/metrics.md §D0~§D9. 저장소(`kbj.store.repos.diag`·
`kbj.store.repos.gex`)·진단 엔진(DIAG)·API(ENG·DIAG)·공개 내보내기가 같이 쓴다.

- 모든 행은 `source`·`as_of`·`quality`(절대 규칙 1). 진단 행의 source 는 계산한 작업
  (`KBJ:diag.public`·`KBJ:diag.daily`), VKOSPI 는 `KRX:<데이터셋>`, 이벤트는 그 근거(캘린더 정본 ·
  `config/calendar_events.yaml` · 공표 일정).
- 값이 없으면 None — 0 으로 채우지 않는다. 값이 없는 행은 quality 가 ok 일 수 없다(절대 규칙 2).
- 백분위 `pct_rank` 는 0~100(그날 포함 250거래일 — metrics §D0). 공포탐욕 점수 0~100 소수 첫째.
- 이 모듈은 `kbj.core` 밖을 모른다(GEX 코어 `kbj.core.gex` 도 모른다 — 화면 읽기 모양
  `GexLevelView` 는 저장된 행만 옮긴다).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal, cast

from kbj.core.quality import Quality

__all__ = [
    "DIAG_PRV_TABLES",
    "DIAG_PUB_TABLES",
    "DIAG_STATES",
    "EVENT_KINDS",
    "FEAR_GREED_BANDS",
    "GEX_FLAGS",
    "GEX_SCOPES",
    "GEX_SESSIONS",
    "VERDICT_LABELS",
    "DiagIndicatorRow",
    "DiagState",
    "EventKind",
    "EventMarker",
    "FearGreedBand",
    "FearGreedRow",
    "GaugeRow",
    "GexFlag",
    "GexLevelView",
    "GexScope",
    "VerdictLabel",
    "VerdictRow",
    "VolIndexRow",
    "fear_greed_band",
]

DiagState = Literal["extreme_low", "low", "normal", "high", "extreme_high", "narrow", "wide"]
FearGreedBand = Literal["extreme_fear", "fear", "neutral", "greed", "extreme_greed"]
VerdictLabel = Literal["risk_on", "neutral", "risk_off", "hold"]
EventKind = Literal["expiry", "mpc", "export_flash", "earnings_cluster"]
GexScope = Literal["all", "nearest", "0dte"]
GexFlag = Literal["off", "shadow", "visible"]
GexSession = Literal["day", "night"]

# 0020 CHECK 와 같은 목록(tests/test_store_layout.py 가 대조)
# 백분위 다섯 칸(§D0) + 신용스프레드 σ 세 칸(§D1 — narrow·normal·wide)
DIAG_STATES: Final[tuple[str, ...]] = (
    "extreme_low", "low", "normal", "high", "extreme_high", "narrow", "wide",
)  # fmt: skip
FEAR_GREED_BANDS: Final[tuple[str, ...]] = (
    "extreme_fear", "fear", "neutral", "greed", "extreme_greed",
)  # fmt: skip
VERDICT_LABELS: Final[tuple[str, ...]] = ("risk_on", "neutral", "risk_off", "hold")
EVENT_KINDS: Final[tuple[str, ...]] = ("expiry", "mpc", "export_flash", "earnings_cluster")
GEX_SCOPES: Final[tuple[str, ...]] = ("all", "nearest", "0dte")
GEX_FLAGS: Final[tuple[str, ...]] = ("off", "shadow", "visible")
GEX_SESSIONS: Final[tuple[str, ...]] = ("day", "night")
# 0020 의 표(쓰기 주인은 config/jobs.yaml — 등록부 시험)
DIAG_PUB_TABLES: Final[tuple[str, ...]] = (
    "pub_diag.indicator", "pub_diag.gauge", "pub_diag.event_marker",
)  # fmt: skip
DIAG_PRV_TABLES: Final[tuple[str, ...]] = (
    "prv_diag.indicator", "prv_diag.fear_greed", "prv_diag.verdict", "prv_diag.vol_index",
)  # fmt: skip

_KEY = re.compile(r"[a-z][a-z0-9_]*")
_HUNDRED = Decimal(100)


def fear_greed_band(score: Decimal) -> FearGreedBand:
    """공포탐욕 점수(0~100) → 구간(metrics §D7): 0–24 극단적 공포 · 25–44 공포 · 45–55 중립 ·
    56–75 탐욕 · 76–100 극단적 탐욕. 경계는 소수 첫째 점수 기준(24.9 → 극단적 공포)."""
    if score < 25:
        return "extreme_fear"
    if score < 45:
        return "fear"
    if score <= 55:
        return "neutral"
    if score <= 75:
        return "greed"
    return "extreme_greed"


# ── 검증 도구(이 모듈 전용) ──────────────────────────────────────────────────────────────────


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _text(what: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} 가 비었다: {value!r}")


def _key(what: str, value: object) -> None:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ValueError(f"{what} 는 소문자 영숫자·밑줄: {value!r}")


def _choice(what: str, value: object, allowed: Iterable[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    allowed_t = tuple(allowed)
    if value not in allowed_t:
        raise ValueError(f"{what} 는 {allowed_t} 중 하나: {value!r}")


def _day(what: str, value: object) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{what} 는 date 여야 한다: {value!r}")


def _aware(what: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{what} 는 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


def _dec(what: str, value: object, *, lo: Decimal | None = None, hi: Decimal | None = None) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{what} 는 Decimal 이어야 한다(float 아님): {value!r}")
    if not value.is_finite():
        raise ValueError(f"{what} 는 유한한 수: {value!r}")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{what} 는 {lo}~{hi} 범위: {value!r}")


def _int(what: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} 는 정수여야 한다: {value!r}")


def _reasons(obj: object, name: str = "reasons") -> None:
    raw: object = getattr(obj, name)
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise TypeError(f"{name} 는 문자열 목록이어야 한다: {raw!r}")
    items = tuple(cast("Iterable[object]", raw))
    for r in items:
        _text(name, r)
    _set(obj, name, cast("tuple[str, ...]", items))


def _frozen_map(obj: object, name: str) -> None:
    raw: object = getattr(obj, name)
    if not isinstance(raw, Mapping):
        raise TypeError(f"{name} 는 Mapping 이어야 한다: {raw!r}")
    m = cast("Mapping[object, object]", raw)
    for k in m:
        _text(f"{name} 키", k)
    _set(obj, name, MappingProxyType(dict(m)))


def _common(obj: object) -> None:
    _text("source", getattr(obj, "source"))  # noqa: B009 — 행 형이 여럿
    _aware("as_of", getattr(obj, "as_of"))  # noqa: B009
    q: object = getattr(obj, "quality")  # noqa: B009
    try:
        _set(obj, "quality", Quality(cast("str", q)))
    except ValueError:
        raise ValueError(f"quality 는 ok·stale·estimated·invalid 중 하나: {q!r}") from None


def _value_or_not_ok(obj: object, name: str) -> None:
    if getattr(obj, name) is None and getattr(obj, "quality") is Quality.OK:  # noqa: B009
        raise ValueError(f"{name} 가 없는(None) 행은 quality 가 ok 일 수 없다(0 으로 채우지 않음)")


# ── 진단 지표(§D1~§D6 — pub_diag.indicator·prv_diag.indicator 같은 모양) ─────────────────────


@dataclass(frozen=True)
class DiagIndicatorRow:
    """지표 하나의 하루 값. key = `config/diag.yaml` 지표 키, value = 지표 값(단위 unit — `%p`·`%`·
    `bp`·`원`·`ratio`), state = 구간(§D0 다섯 칸 또는 §D1 σ 세 칸 — 이력 부족이면 None),
    pct_rank = 250거래일 백분위(0~100, 없으면 None), inputs = 계산에 쓴 입력(시점 입력만 —
    `published_at ≤ as_of`), reasons = 품질을 떨어뜨린 사유. flags = 상태와 별도 꼬리표
    (`overheat`·`fragile` 등 — 점수에 들지 않음)."""

    trade_date: date
    key: str
    value: Decimal | None
    unit: str
    state: DiagState | None
    pct_rank: Decimal | None
    inputs: Mapping[str, object]
    source: str
    as_of: datetime
    quality: Quality
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _key("key", self.key)
        _dec("value", self.value)
        _text("unit", self.unit)
        _choice("state", self.state, DIAG_STATES, optional=True)
        _dec("pct_rank", self.pct_rank, lo=Decimal(0), hi=_HUNDRED)
        _frozen_map(self, "inputs")
        _reasons(self)
        _reasons(self, "flags")
        for f in self.flags:
            _key("flags", f)
        _common(self)
        _value_or_not_ok(self, "value")
        if self.value is None and (self.state is not None or self.pct_rank is not None):
            raise ValueError("값이 없는 지표에 구간·백분위를 둘 수 없다")


@dataclass(frozen=True)
class GaugeRow:
    """매크로 위험 게이지(공공 판정 — §D4, `pub_diag.gauge`). parts = 성분 키 → 상태 점수
    (−1·0·+1, 빠진 성분은 키 없음). score = 합(보류면 None). label hold = 성분 < 3."""

    trade_date: date
    label: VerdictLabel
    score: int | None
    parts: Mapping[str, int]
    source: str
    as_of: datetime
    quality: Quality
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _choice("label", self.label, VERDICT_LABELS)
        _int("score", self.score)
        _frozen_map(self, "parts")
        _parts(self.parts)
        _reasons(self)
        _common(self)
        _hold_score(self.label, self.score, self.quality)


def _parts(parts: Mapping[str, int]) -> None:
    for k, v in parts.items():
        _key("parts 키", k)
        if isinstance(v, bool) or v not in (-1, 0, 1):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(f"parts[{k}] 는 −1·0·+1: {v!r}")


def _hold_score(label: str, score: int | None, quality: Quality) -> None:
    if label == "hold":
        if score is not None:
            raise ValueError("보류(hold) 판정에 점수를 둘 수 없다")
        if quality is Quality.OK:
            raise ValueError("보류(hold) 판정은 quality 가 ok 일 수 없다")
    elif score is None:
        raise ValueError(f"{label} 판정에는 점수가 있어야 한다")


@dataclass(frozen=True)
class FearGreedRow:
    """국장 공포탐욕(§D7, `prv_diag.fear_greed`). components = 성분 키 → 0~100(뒤집은 뒤 값,
    빠진 성분은 None). score = 있는 성분 평균(소수 첫째, 성분 < 4 이면 None)."""

    trade_date: date
    score: Decimal | None
    band: FearGreedBand | None
    components: Mapping[str, Decimal | None]
    source: str
    as_of: datetime
    quality: Quality
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _dec("score", self.score, lo=Decimal(0), hi=_HUNDRED)
        _choice("band", self.band, FEAR_GREED_BANDS, optional=True)
        _frozen_map(self, "components")
        for k, v in self.components.items():
            _key("components 키", k)
            _dec(f"components[{k}]", v, lo=Decimal(0), hi=_HUNDRED)
        _reasons(self)
        _common(self)
        _value_or_not_ok(self, "score")
        if (self.score is None) != (self.band is None):
            raise ValueError("score 와 band 는 같이 있거나 같이 없다")
        if self.score is not None and self.band != fear_greed_band(self.score):
            raise ValueError(f"band {self.band!r} 가 점수 {self.score} 와 맞지 않는다")


@dataclass(frozen=True)
class VerdictRow:
    """종합 판정(§D8, `prv_diag.verdict`). parts = 성분 → 상태 점수, event_flags = 이벤트 주의
    꼬리표(§D9 kind — 오늘·다음 거래일)."""

    trade_date: date
    label: VerdictLabel
    score: int | None
    parts: Mapping[str, int]
    event_flags: tuple[str, ...]
    source: str
    as_of: datetime
    quality: Quality
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _choice("label", self.label, VERDICT_LABELS)
        _int("score", self.score)
        _frozen_map(self, "parts")
        _parts(self.parts)
        _reasons(self, "event_flags")
        for f in self.event_flags:
            _choice("event_flags", f, EVENT_KINDS)
        _reasons(self)
        _common(self)
        _hold_score(self.label, self.score, self.quality)


@dataclass(frozen=True)
class EventMarker:
    """이벤트 표시(§D9, `pub_diag.event_marker`). date = 이벤트 날(KST), title = 표시 이름
    (기본 키에 듦 — 같은 날 같은 종류 둘을 가른다), detail = 부가(시리즈·종목 수 등)."""

    date: date
    kind: EventKind
    title: str
    source: str
    as_of: datetime
    quality: Quality
    detail: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _day("date", self.date)
        _choice("kind", self.kind, EVENT_KINDS)
        _text("title", self.title)
        _frozen_map(self, "detail")
        _common(self)


@dataclass(frozen=True)
class VolIndexRow:
    """변동성 지수 일별 종가(`prv_diag.vol_index` — `krx.vkospi`, KRX:idx/drvprod_dd_trd
    [실측 필요 #P7-1]). index_id 예 `vkospi`."""

    trade_date: date
    index_id: str
    close: Decimal | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _key("index_id", self.index_id)
        _dec("close", self.close, lo=Decimal(0))
        _common(self)
        _value_or_not_ok(self, "close")


# ── GEX 화면 읽기 모양(API — prv_gex.levels 한 행) ───────────────────────────────────────────


@dataclass(frozen=True)
class GexLevelView:
    """`prv_gex.levels` 한 행의 화면 모양(ENG 리더가 채운다). value 는 pt 또는 지표 단위(GX §3 —
    float, 엔진 산출 그대로). flag 가 visible 이 아니면 API 는 값을 싣지 않는다(§10 Q3).
    값 None 은 '없음'(예: Flip 교차 없음 — GX §3.5)이라 quality ok 와 함께 올 수 있다."""

    ts: datetime
    trade_date: date
    session: GexSession
    scope: GexScope
    name: str
    value: float | None
    flag: GexFlag
    source: str
    as_of: datetime
    quality: Quality
    reasons: tuple[str, ...] = ()
    detail: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _aware("ts", self.ts)
        _day("trade_date", self.trade_date)
        _choice("session", self.session, GEX_SESSIONS)
        _choice("scope", self.scope, GEX_SCOPES)
        _text("name", self.name)
        if self.value is not None and (
            isinstance(self.value, bool) or not isinstance(self.value, int | float)  # pyright: ignore[reportUnnecessaryIsInstance]
        ):
            raise TypeError(f"value 는 수여야 한다: {self.value!r}")
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError(f"value 는 유한한 수: {self.value!r}")
        _choice("flag", self.flag, GEX_FLAGS)
        _frozen_map(self, "detail")
        _reasons(self)
        _common(self)
