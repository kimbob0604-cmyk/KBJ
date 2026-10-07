"""지표 등록부 — Phase 3 확장 지표가 engine 사이클에 꽂히는 자리 (docs/phase3_design.md §3·§4).

engine 사이클(services/engine/evaluate.py)은 Phase 2 핵심 산출(레벨·순GEX·DEX·ATM IV·만기별 감마)을
늘 계산하고, 그 밖의 지표는 `MetricPlugin` 을 차례로 부른다(항목 2·3 — `core/metrics/`). 등록 목록은
`services/engine/extended.py` `REGISTRY`(항목 2 — 익스포저·변동성), 여기는 계약(모양)만.

- 플러그인마다 기능 플래그 이름(`flag` — 설계 §4 `config/features.yaml`, 카탈로그
  `core.features.CATALOG`)을 둔다. 사이클은 플래그 값으로 부른다: `off` = 계산 안 함, `shadow` =
  계산·저장(`metrics.flag = shadow`)만 하고 발행 안 함, `visible` = 전부. 값은 `flags` 인자(서비스가
  플래그 파일에서 읽은 표) — 표에 없는 이름은 카탈로그 기본값(Phase 2 핵심 visible, 새 지표·카탈로그
  밖 shadow)
- 플러그인 하나의 예외는 그 지표만 `invalid` 행 + health — 사이클은 계속(CLAUDE.md 격리)
- 입력은 `CycleView`(그 사이클의 만기 평가·범위·S_ref·품질) — core 순수 함수만 부른다
- 계산 주기(`every_s`): 없으면 사이클마다, 있으면 그 지표를 마지막으로 계산한 사이클 시각(as_of)에서
  every_s 가 지난 사이클만(세션이 바뀌면 바로) — 서비스가 들고 있다(Charm 2분, metrics §4.2)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from core.calendar import TradingCalendar
from core.features import CORE_NAMES, NEW_DEFAULT, Flag, OutputTable, default_flag, flag_for_output
from core.forward import SRef
from core.gex import ExpiryEval
from core.preprocess import Quality

MetricScope = Literal["all", "nearest", "0dte", "series"]
DEFAULT_FLAG: Flag = NEW_DEFAULT  # 새 지표 기본값 (설계 §4, PLAN §6.4)


@dataclass(frozen=True)
class SeriesGap:
    """사이클에서 평가하지 못한 시리즈 — `failed`(평가 예외, 최종거래일은 안다)·`no_expiry`(최종
    거래일을 모른다 — last_trade_date None). 만기를 골라 쓰는 지표(기간구조 칸)가 그 시리즈가
    들었을지 판단한다 — 범위 지표의 `series_failed`·`series_no_expiry` 와 같은 규칙."""

    label: str
    mrkt_cls: str
    status: Literal["failed", "no_expiry"]
    last_trade_date: date | None = None


@dataclass(frozen=True)
class CycleView:
    """플러그인 입력 — 한 사이클의 평가 결과 (읽기만).

    evals: 평가한 만기(만기 코드 자리에 시리즈 라벨 — WKM·WKI 261001 이 겹치지 않게). scopes: 범위
    (all·nearest·0dte) → 그 만기들. scope_quality·scope_reasons: 범위 입력 품질·사유(S_ref·행 품질·
    실패한 시리즈). series_quality·series_reasons: 시리즈 라벨 → 그 시리즈 입력 품질(S_ref ⊕ 행
    품질)·사유. series_class: 시리즈 라벨 → 시장분류('' 월물·WKM·WKI). gaps: 평가하지 못한 시리즈
    (실패·최종거래일 모름 — 만기 지난 시리즈는 들지 않는다). input_quality·input_reasons: 시리즈
    라벨 → 그 시리즈 체인 행 입력 품질·사유(S_ref 를 빼고 — F 를 쓰지 않는 지표용: OI·거래량).
    strikes: 시리즈 라벨 → 마스터 상장 행사가(모르면 없다).
    """

    as_of: datetime
    trade_date: date
    session: Literal["day", "night"]
    s_ref: SRef
    evals: tuple[ExpiryEval, ...]
    scopes: Mapping[str, tuple[ExpiryEval, ...]]
    scope_quality: Mapping[str, Quality]
    calendar: TradingCalendar
    scope_reasons: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    series_quality: Mapping[str, Quality] = field(default_factory=dict[str, Quality])
    series_reasons: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict[str, tuple[str, ...]]
    )
    series_class: Mapping[str, str] = field(default_factory=dict[str, str])
    gaps: tuple[SeriesGap, ...] = ()
    input_quality: Mapping[str, Quality] = field(default_factory=dict[str, Quality])
    input_reasons: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    strikes: Mapping[str, tuple[Decimal, ...]] = field(
        default_factory=dict[str, tuple[Decimal, ...]]
    )


@dataclass(frozen=True)
class PluginValue:
    """플러그인 산출 한 칸 → `metrics` 한 행(지표 이름·플래그는 플러그인 것)."""

    scope: MetricScope
    value: float | None
    quality: Quality
    key: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class MetricPlugin:
    """지표 하나. name = `metrics.metric`, flag = 기능 플래그 이름(보통 name 과 같다).
    every_s: 계산 주기(초, 사이클 시각으로 센다) — None 이면 사이클마다."""

    name: str
    flag: str
    compute: Callable[[CycleView], Sequence[PluginValue]]
    every_s: float | None = None

    def __post_init__(self) -> None:
        if self.every_s is not None and not self.every_s > 0:
            raise ValueError(f"every_s 는 양수: {self.every_s!r}")


def resolve_flag(name: str, flags: Mapping[str, Flag] | None) -> Flag:
    """플래그 값 — 주어진 표에 없으면 카탈로그 기본값(Phase 2 핵심 visible, 그 밖 shadow)."""
    if flags is not None and name in flags:
        return flags[name]
    return default_flag(name)


def core_flag(
    output: str, flags: Mapping[str, Flag] | None, table: OutputTable = "metrics"
) -> Flag:
    """Phase 2 핵심 산출(engine 사이클이 직접 계산 — 순GEX·DEX·ATM IV·만기별 감마·레벨)의 플래그.
    산출 이름 → 카탈로그 플래그(`expected_move_calendar` → `expected_move`). 핵심은 늘 계산하고
    (사이클 품질·레벨 사이 의존) off 는 로더가 막는다 — 직접 넘긴 off 도 행을 떨구지 않고 shadow."""
    name = flag_for_output(output, table) or output
    flag = resolve_flag(name, flags)
    return "shadow" if flag == "off" and name in CORE_NAMES else flag
