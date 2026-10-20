"""패시브 지수 정기변경 예측의 행 자료형 — 0018 `prv_index.*`(P5 2차 묶음 IDX, 로그인 등급).

근거: docs/p5_index_rebalance_prompt.md §3-3, docs/metrics.md §24.
저장소(`kbj.store.repos.index_review`)·
서비스(`kbj.services.engine.index_review`)·API 가 같이 쓴다. 엔진(`kbj.engines.index_review`)의 계산
결과는 서비스가 이 행으로 바꿔 쓴다.

- 금액은 원 단위 정수. 비율(유동비율·괴리율·필요 상승률)은 소수(0.124 = 12.4%).
- 구성종목 목록은 MSCI·KRX 독점 자료라 로그인 등급 — 레포에는 합성 행만(source 'synthetic').
- 근거 JSON(`PredictionRow.evidence`)의 키는 `EVIDENCE_KEYS` 로 고정한다(화면·API 가 이 키만
  읽는다).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Literal

from kbj.core.quality import Quality

__all__ = [
    "CONSTITUENT_SOURCES",
    "CUTOFF_KINDS",
    "EVIDENCE_KEYS",
    "RUN_STATUSES",
    "VERDICTS",
    "ConstituentRow",
    "CutoffKind",
    "PassiveAumRow",
    "PredictionRow",
    "PredictionRun",
    "ReviewEvent",
    "RunStatus",
    "Verdict",
]

Verdict = Literal["add", "delete", "borderline", "keep"]
CutoffKind = Literal["model", "user"]
RunStatus = Literal["ok", "no_constituents", "no_data"]
VERDICTS: Final[tuple[str, ...]] = ("add", "delete", "borderline", "keep")
CUTOFF_KINDS: Final[tuple[str, ...]] = ("model", "user")
RUN_STATUSES: Final[tuple[str, ...]] = ("ok", "no_constituents", "no_data")
# user = 사용자가 VM 에 올린 목록, press = 운용사·거래소 보도자료(리뷰 결과 공지)를 사람이 반영,
# synthetic = 합성 세계(시험·데모)
# etf = 대리 ETF(완전 복제 — config/index_reviews.yaml proxy_etf)의 구성종목 공시로 맞춘 목록(추정).
CONSTITUENT_SOURCES: Final[tuple[str, ...]] = ("user", "press", "synthetic", "etf")
EVIDENCE_KEYS: Final[tuple[str, ...]] = (
    "mktcap", "ff_mcap", "fif", "fif_raw", "fif_how", "gap_mcap", "gap_ff", "condition_met",
    "required_rise", "required_basis", "passive_won", "adv_won", "adv_multiple", "rank",
    "threshold_mcap", "threshold_ff", "reasons",
)  # fmt: skip

_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_REVIEW = re.compile(r"\d{4}-\d{2}")
_CODE = re.compile(r"[0-9A-Z]{6}")


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _match(what: str, value: object, pat: re.Pattern[str]) -> None:
    if not isinstance(value, str) or not pat.fullmatch(value):
        raise ValueError(f"{what} 형식: {value!r}")


def _common(obj: Any) -> None:
    _match("index_id", obj.index_id, _IDENT)
    if not isinstance(obj.source, str) or not obj.source.strip():
        raise ValueError("source 가 비었다")
    try:
        _set(obj, "quality", Quality(obj.quality))
    except ValueError:
        raise ValueError(f"quality 는 ok·stale·estimated·invalid: {obj.quality!r}") from None


def _won(what: str, v: object, *, nonneg: bool = True) -> None:
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"{what} 는 원 단위 정수: {v!r}")
    if nonneg and v < 0:
        raise ValueError(f"{what} 는 0 이상: {v!r}")


def _ratio(what: str, v: object, lo: float = 0.0, hi: float = 1.0) -> None:
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise TypeError(f"{what} 는 수: {v!r}")
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{what} 는 {lo}~{hi}: {v!r}")


def _aware(what: str, v: datetime) -> None:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


@dataclass(frozen=True)
class ConstituentRow:
    """구성종목 한 기간(`prv_index.constituents`). valid_to = 마지막 날(포함), None = 지금도
    구성종목.

    fif = 지수 운용사가 적용한 유동비율(목록에 있으면 — 완충의 '직전 FIF'). 모르면 None."""

    index_id: str
    code: str
    valid_from: date
    valid_to: date | None
    name: str | None
    fif: float | None
    source: str
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _common(self)
        _match("code", self.code, _CODE)
        if self.source not in CONSTITUENT_SOURCES:
            raise ValueError(f"source 는 {CONSTITUENT_SOURCES}: {self.source!r}")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to 는 valid_from 이후")
        _ratio("fif", self.fif)
        if self.fif is not None and self.fif <= 0:
            raise ValueError("fif 는 0 보다 크다")

    def active_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True)
class ReviewEvent:
    """리뷰 일정 한 줄(`prv_index.review_calendar` — 설정 `config/index_reviews.yaml` 에서).

    confirmed=False(공표 일정표와 대조 전)면 quality 는 estimated 여야 한다. user_cutoff_won =
    사용자가
    넣은 증권사 컷오프 추정치(있으면 그것으로도 같이 계산 — cutoff_kind 'user')."""

    index_id: str
    review_id: str
    index_title: str
    display_order: int
    base_date: date
    announce_date: date
    trade_date: date
    effective_date: date
    confirmed: bool
    source: str
    quality: Quality
    user_cutoff_won: int | None = None

    def __post_init__(self) -> None:
        _common(self)
        _match("review_id", self.review_id, _REVIEW)
        if not self.index_title.strip():
            raise ValueError("index_title 이 비었다")
        if not self.base_date <= self.announce_date <= self.trade_date < self.effective_date:
            raise ValueError("base ≤ announce ≤ trade < effective")
        if not self.confirmed and self.quality is Quality.OK:
            raise ValueError("확인 전 일정(confirmed=False)은 quality 가 ok 일 수 없다")
        _won("user_cutoff_won", self.user_cutoff_won)


@dataclass(frozen=True)
class PredictionRun:
    """예측 한 번의 요약(`prv_index.prediction_run`) — 상태·컷오프 추정·건수.

    status: ok · no_constituents(구성종목 목록 없음 — 예측을 내지 않는다) · no_data(시세 없음)."""

    index_id: str
    review_id: str
    base_date: date
    cutoff_kind: CutoffKind
    status: RunStatus
    cutoff_won: int | None
    cutoff_low_won: int | None
    cutoff_high_won: int | None
    cutoff_method: str | None
    n_members: int
    n_add: int
    n_delete: int
    n_borderline: int
    aum_won: int | None
    data_as_of: datetime
    source: str
    quality: Quality
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _common(self)
        _match("review_id", self.review_id, _REVIEW)
        if self.cutoff_kind not in CUTOFF_KINDS:
            raise ValueError(f"cutoff_kind: {self.cutoff_kind!r}")
        if self.status not in RUN_STATUSES:
            raise ValueError(f"status: {self.status!r}")
        for n in ("cutoff_won", "cutoff_low_won", "cutoff_high_won", "aum_won"):
            _won(n, getattr(self, n))
        for n in ("n_members", "n_add", "n_delete", "n_borderline"):
            _won(n, getattr(self, n))
        _aware("data_as_of", self.data_as_of)
        if self.status != "ok" and (self.n_add or self.n_delete or self.n_borderline):
            raise ValueError("ok 가 아닌 실행은 후보를 내지 않는다(지어내지 않음)")
        if self.status != "ok" and self.quality is Quality.OK:
            raise ValueError("ok 가 아닌 실행은 quality 가 ok 일 수 없다")
        _set(self, "notes", tuple(self.notes))


@dataclass(frozen=True)
class PredictionRow:
    """종목 한 줄 판정(`prv_index.predictions`). evidence 키는 `EVIDENCE_KEYS`."""

    index_id: str
    review_id: str
    base_date: date
    cutoff_kind: CutoffKind
    code: str
    name: str | None
    verdict: Verdict
    member: bool
    evidence: Mapping[str, Any]
    cutoff_won: int | None
    data_as_of: datetime
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _common(self)
        _match("review_id", self.review_id, _REVIEW)
        _match("code", self.code, _CODE)
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict: {self.verdict!r}")
        if self.cutoff_kind not in CUTOFF_KINDS:
            raise ValueError(f"cutoff_kind: {self.cutoff_kind!r}")
        unknown = set(self.evidence) - set(EVIDENCE_KEYS)
        if unknown:
            raise ValueError(f"근거 JSON 에 모르는 키: {sorted(unknown)}")
        _won("cutoff_won", self.cutoff_won)
        _aware("data_as_of", self.data_as_of)
        _set(self, "evidence", dict(self.evidence))


@dataclass(frozen=True)
class PassiveAumRow:
    """지수 추종 패시브 자금(`prv_index.passive_aum`). kind: etf(P3 ETF 순자산 합) · non_etf(설정
    추정) ·
    user(사용자가 넣은 전체 추정 — 있으면 이것을 쓴다)."""

    index_id: str
    as_of: date
    kind: Literal["etf", "non_etf", "user"]
    aum_won: int
    n_funds: int | None
    source: str
    quality: Quality
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        _common(self)
        if self.kind not in ("etf", "non_etf", "user"):
            raise ValueError(f"kind: {self.kind!r}")
        _won("aum_won", self.aum_won)
        _won("n_funds", self.n_funds)
        _set(self, "detail", dict(self.detail))
