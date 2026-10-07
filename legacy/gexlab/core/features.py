"""기능 플래그 (docs/metrics.md §8, docs/phase3_design.md §4, PLAN §6.4).

`config/features.yaml` 은 구역(metrics·widgets·alerts·strategies)마다 이름 → `off | shadow |
visible` 이다. 뜻(engine — services/engine):

- `off`: 계산하지 않는다
- `shadow`: 계산·저장만(`metrics.flag`·`levels.flag`·`oi_changes.flag` = shadow) — Redis 로 발행하지
  않고(engine.levels·engine.metrics·engine:latest) 알림에 쓰지 않는다
- `visible`: 계산·저장·발행

규칙:

- 이름은 `CATALOG` 에 있는 것만 — 모르는 구역·이름·값은 `FeatureError`(서비스 기동 실패). 이름은
  플래그 이름이다: 한 플래그가 산출 여럿을 정하기도 한다(`pcr` → pcr_oi·pcr_volume, `futures` →
  선물 지표 다섯, `expected_move` → 레벨 expected_move_calendar·expected_move_trading)
- 파일에 없는 이름은 카탈로그 기본값 — Phase 2 핵심(순GEX·DEX·콜월·풋월·절대감마·Flip·전환점 거리·
  기대변동폭·ATM IV와 같은 Phase 2 산출 상위 레벨·만기별 감마)은 `visible`, 새 지표는 `shadow`
- Phase 2 핵심은 `off` 로 둘 수 없다 [확인 필요] — 사이클 품질(범위 all 순GEX)·engine:latest·레벨
  사이 의존(전환점 거리 ← Flip, 상위 레벨 ← ±1σ)의 바탕이라 끄면 다른 산출이 조용히 빈다. 숨기려면
  `shadow`(계산·저장은 하고 내보내지 않는다)
- 값은 문자열로만 읽는다(`yaml.BaseLoader`) — YAML 1.1 은 따옴표 없는 `off` 를 false 로 읽는다
- 같은 매핑 안의 중복 키(같은 이름·같은 구역 두 번)는 `FeatureError` — 뒤 값이 조용히 이기지 않게
- 위젯·알림·전략 이름은 Phase 4 이후에 카탈로그에 더한다 — 그 전엔 그 구역에 이름을 쓰면 기동 실패

다시 읽기(`FeatureWatcher`): 30초마다 파일 서명(mtime·크기)을 보고 바뀌었으면 다시 읽는다. 다시
읽다 실패하면(모르는 이름·깨진 YAML·파일 없음) 직전 플래그를 그대로 두고 오류를 돌려준다 — 서비스가
health 로 낸다(돌고 있는 서비스를 설정 실수 하나로 멈추지 않는다) [확인 필요]. 같은 서명의 실패는
한 번만 알린다. 파일 입출력은 이 로더와 감시자만 한다(주입 가능) — 판정은 순수 함수.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml.nodes import MappingNode, ScalarNode

Flag = Literal["off", "shadow", "visible"]
Section = Literal["metrics", "widgets", "alerts", "strategies"]
OutputTable = Literal["metrics", "levels", "oi_changes"]

FEATURES_PATH = Path(__file__).resolve().parents[1] / "config" / "features.yaml"
RELOAD_EVERY_S = 30.0  # 파일 서명을 보는 주기 (설계 §4)
NEW_DEFAULT: Flag = "shadow"  # 새 지표 기본값 (PLAN §6.4)
FLAGS: tuple[Flag, ...] = get_args(Flag)
SECTIONS: tuple[Section, ...] = get_args(Section)


class FeatureError(ValueError):
    """플래그 파일을 쓸 수 없다 — 모르는 구역·이름·값, 깨진 YAML, 읽기 실패."""


@dataclass(frozen=True)
class FlagSpec:
    """카탈로그 한 줄. outputs: 이 플래그가 정하는 산출 이름(`metrics.metric`·`levels.name`, 표
    `oi_changes` 면 표 이름). core: Phase 2 핵심 — 기본 visible, off 불가. spec: metrics.md 절."""

    name: str
    default: Flag
    table: OutputTable
    outputs: tuple[str, ...]
    spec: str
    core: bool = False
    section: Section = "metrics"

    def __post_init__(self) -> None:
        if not self.outputs:
            raise ValueError(f"{self.name}: outputs 가 비었다")
        if self.core and self.default != "visible":
            raise ValueError(f"{self.name}: Phase 2 핵심의 기본값은 visible")


def _core(name: str, table: OutputTable, spec: str, *outputs: str) -> FlagSpec:
    return FlagSpec(name, "visible", table, outputs or (name,), spec, core=True)


def _new(name: str, table: OutputTable, spec: str, *outputs: str) -> FlagSpec:
    return FlagSpec(name, NEW_DEFAULT, table, outputs or (name,), spec)


CATALOG: tuple[FlagSpec, ...] = (
    # ── Phase 2 핵심 (engine 사이클이 직접 계산 — services/engine/evaluate.py) ──
    _core("net_gex", "metrics", "§2.2"),
    _core("dex", "metrics", "§2.3"),
    _core("atm_iv", "metrics", "§3.7"),
    _core("expiry_gamma", "metrics", "§3.10"),
    _core("call_wall", "levels", "§3.1"),
    _core("put_wall", "levels", "§3.2"),
    _core("abs_gamma", "levels", "§3.3"),
    _core("flip", "levels", "§3.4"),
    _core("flip_distance", "levels", "§3.5"),
    _core("expected_move", "levels", "§3.8", "expected_move_calendar", "expected_move_trading"),
    _core("top_levels", "levels", "§3.9"),
    # ── Phase 3 확장 지표 (새 지표 — 기본 shadow) ──
    _new("vex", "metrics", "§4.1"),
    _new("cex", "metrics", "§4.2"),
    _new("gex_pc", "metrics", "§4.3"),
    _new("iv_term", "metrics", "§5.2"),
    _new("skew_25d", "metrics", "§5.3"),
    _new("atm_iv_daily", "metrics", "§5.4"),
    _new("iv_rank", "metrics", "§5.4"),
    _new("iv_percentile", "metrics", "§5.4"),
    _new("iv_hv", "metrics", "§5.5"),
    _new("hiro", "metrics", "§6.1"),
    _new("investor_flow", "metrics", "§6.2"),
    _new("dealer_check", "metrics", "§6.3"),
    _new("block_trades", "metrics", "§6.4", "block_trades", "block_trade"),
    _new("pcr", "metrics", "§6.5", "pcr_oi", "pcr_volume"),
    _new("max_pain", "metrics", "§6.6"),
    _new("oi_changes", "oi_changes", "§6.7"),
    _new(
        "futures",
        "metrics",
        "§7",
        "futures_basis",
        "futures_theory_basis",
        "futures_divergence",
        "futures_oi_change",
        "futures_strength",
    ),
)
# 위젯·알림·전략은 Phase 4 이후(대시보드·notifier·trader)가 이름을 더한다


def _index() -> dict[tuple[Section, str], FlagSpec]:
    out: dict[tuple[Section, str], FlagSpec] = {}
    for s in CATALOG:
        k = (s.section, s.name)
        if k in out:
            raise ValueError(f"카탈로그에 같은 이름이 두 번: {k}")
        out[k] = s
    return out


SPECS: Mapping[tuple[Section, str], FlagSpec] = _index()
CORE_NAMES: frozenset[str] = frozenset(s.name for s in CATALOG if s.core)


def _outputs() -> dict[tuple[OutputTable, str], FlagSpec]:
    out: dict[tuple[OutputTable, str], FlagSpec] = {}
    for s in CATALOG:
        for o in s.outputs:
            k = (s.table, o)
            if k in out:
                raise ValueError(f"산출 {k} 를 두 플래그가 정한다: {out[k].name}·{s.name}")
            out[k] = s
    return out


OUTPUTS: Mapping[tuple[OutputTable, str], FlagSpec] = _outputs()


def spec_of(name: str, section: Section = "metrics") -> FlagSpec | None:
    return SPECS.get((section, name))


def flag_for_output(output: str, table: OutputTable = "metrics") -> str | None:
    """산출 이름(`metrics.metric`·`levels.name`·표 이름) → 그것을 정하는 플래그(없으면 None)."""
    s = OUTPUTS.get((table, output))
    return None if s is None else s.name


def default_flag(name: str, section: Section = "metrics") -> Flag:
    """카탈로그 기본값 — 카탈로그 밖 이름은 새 지표 기본(shadow)."""
    s = spec_of(name, section)
    return NEW_DEFAULT if s is None else s.default


class Features(BaseModel):
    """`config/features.yaml` 검증 모델. 구역마다 적은 이름만 담고, 조회(`flag`)는 기본값을
    채운다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metrics: dict[str, Flag] = Field(default_factory=dict[str, Flag])
    widgets: dict[str, Flag] = Field(default_factory=dict[str, Flag])
    alerts: dict[str, Flag] = Field(default_factory=dict[str, Flag])
    strategies: dict[str, Flag] = Field(default_factory=dict[str, Flag])

    @field_validator("metrics", "widgets", "alerts", "strategies", mode="before")
    @classmethod
    def _blank_is_empty(cls, v: object) -> object:
        """`widgets:` 처럼 값 없이 둔 구역은 빈 표(BaseLoader 는 빈 값을 '' 로 읽는다)."""
        return {} if v is None or v == "" else v

    @model_validator(mode="after")
    def _known(self) -> Features:
        errors: list[str] = []
        for section in SECTIONS:
            for name, flag in self.section(section).items():
                s = spec_of(name, section)
                if s is None:
                    errors.append(f"{section}.{name}: 모르는 이름")
                elif s.core and flag == "off":
                    errors.append(f"{section}.{name}: Phase 2 핵심은 off 불가(shadow 까지)")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def section(self, section: Section) -> dict[str, Flag]:
        return dict(getattr(self, section))

    def flag(self, name: str, section: Section = "metrics") -> Flag:
        """이름의 플래그 — 적었으면 그 값, 아니면 카탈로그 기본값."""
        return self.section(section).get(name, default_flag(name, section))

    def resolved(self, section: Section = "metrics") -> dict[str, Flag]:
        """그 구역 카탈로그 이름 전부 → 플래그(적지 않은 이름은 기본값)."""
        return {s.name: self.flag(s.name, section) for s in CATALOG if s.section == section}


def parse_features(raw: object) -> Features:
    """YAML 을 읽은 값 → `Features`. 틀리면 `FeatureError`(검증 오류 문구를 한 줄로)."""
    try:
        return Features.model_validate({} if raw is None or raw == "" else raw)
    except ValidationError as e:
        parts = [
            f"{'.'.join(str(x) for x in err['loc']) or '(파일)'}: {err['msg']}"
            for err in e.errors()
        ]
        raise FeatureError("플래그 파일이 틀렸다 — " + "; ".join(parts)) from None


class _FlagLoader(yaml.BaseLoader):
    """`yaml.BaseLoader` + 같은 매핑 안의 중복 키는 `FeatureError` — 뒤 값이 앞 값을 조용히 덮지
    않게(같은 이름·같은 구역이 두 번이면 어느 쪽이 뜻인지 모른다. 앞 구역이 통째로 빠지면 그
    이름들이 기본값으로 돌아간다)."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, ScalarNode):
                continue  # 스칼라가 아닌 키는 BaseConstructor 가 거절한다(해시 불가)
            key = str(key_node.value)
            if key in seen:
                line = key_node.start_mark.line + 1
                raise FeatureError(f"플래그 파일에 같은 키가 두 번: {key!r} ({line}행)")
            seen.add(key)
        return super().construct_mapping(node, deep)


def parse_text(text: str) -> Features:
    """플래그 파일 본문 → `Features` — 스칼라는 모두 문자열로(`off` 가 false 가 되지 않게), 같은
    매핑 안의 중복 키는 실패. BaseLoader 는 객체를 만들지 않는다(safe_load 보다 더 좁다)."""
    try:
        raw: Any = yaml.load(text, Loader=_FlagLoader)  # noqa: S506
    except yaml.YAMLError as e:
        raise FeatureError(f"플래그 파일 YAML 이 깨졌다: {type(e).__name__}") from None
    return parse_features(raw)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_mtime_ns, st.st_size


def load_features(path: Path = FEATURES_PATH, read: Callable[[Path], str] = _read) -> Features:
    """파일 → `Features`. 읽기 실패도 `FeatureError`(기동 실패)."""
    try:
        text = read(path)
    except OSError as e:
        raise FeatureError(f"플래그 파일을 읽지 못했다: {path.name} ({type(e).__name__})") from None
    return parse_text(text)


def changed_flags(
    old: Features, new: Features, section: Section = "metrics"
) -> dict[str, tuple[Flag, Flag]]:
    """두 플래그 표의 차이 — 이름 → (전, 후). 기본값을 채워 비교한다."""
    a, b = old.resolved(section), new.resolved(section)
    return {k: (a[k], b[k]) for k in a if a[k] != b[k]}


@dataclass(frozen=True)
class Reload:
    """다시 읽은 결과. error: 실패 문구(그때 features 는 직전 값), changes: metrics 구역 차이."""

    features: Features
    changes: Mapping[str, tuple[Flag, Flag]] = field(default_factory=dict[str, tuple[Flag, Flag]])
    error: str | None = None


class FeatureWatcher:
    """플래그 파일 감시 — 기동 때 읽고(실패하면 `FeatureError` — 기동 실패), `poll()` 이
    `every_s` 마다 파일 서명(mtime·크기)을 보고 바뀌었으면 다시 읽는다."""

    def __init__(
        self,
        path: Path = FEATURES_PATH,
        *,
        mono: Callable[[], float],
        every_s: float = RELOAD_EVERY_S,
        read: Callable[[Path], str] = _read,
        signature: Callable[[Path], tuple[int, int]] = _signature,
    ) -> None:
        if not every_s > 0:
            raise ValueError(f"every_s 는 양수: {every_s!r}")
        self.path = path
        self._mono = mono
        self._every = every_s
        self._read = read
        self._sig_of = signature
        try:
            self._sig: tuple[int, int] | None = signature(path)
        except OSError as e:
            raise FeatureError(
                f"플래그 파일을 읽지 못했다: {path.name} ({type(e).__name__})"
            ) from None
        self.features = load_features(path, read)
        self._next = mono() + every_s

    def poll(self) -> Reload | None:
        """볼 때가 아니거나 서명이 그대로면 None. 바뀌었으면 다시 읽어 `Reload` — 실패하면 직전
        값과 error(같은 서명의 실패는 한 번만)."""
        now = self._mono()
        if now < self._next:
            return None
        self._next = now + self._every
        try:
            sig: tuple[int, int] | None = self._sig_of(self.path)
        except OSError as e:
            sig = None
            if sig == self._sig:
                return None
            self._sig = sig
            return Reload(self.features, error=f"플래그 파일을 읽지 못했다: {type(e).__name__}")
        if sig == self._sig:
            return None
        self._sig = sig
        try:
            new = load_features(self.path, self._read)
        except FeatureError as e:
            return Reload(self.features, error=str(e))
        changes = changed_flags(self.features, new)
        self.features = new
        return Reload(new, changes)
