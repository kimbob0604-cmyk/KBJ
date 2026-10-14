"""규칙 DSL v1 — 모양(pydantic)·의미 검사·정규화·`dsl_hash`·JSON 스키마(P8 묶음 FND).

근거: docs/p8_design.md §4.1(D-P8-5), docs/metrics.md §30. 평가기는 문자열 식을 **실행하지
않는다**(eval 없음) — 지표·연산자는 아래 닫힌 목록뿐이다.

- 모양: `{"v": 1, "name", "universe", "mode", "when": {"all"|"any": [...]}, "alert", "track"}`. 잎은
  조건
  `{ind, p?, field?, op, value? | ref?}`, `ref` 는 다른 지표(`scale` 배수 허용 — 예 `high_n(250) ×
  0.97`).
- 의미 검사(`validate_rule`): 지표·칸·파라미터 범위, `ref` 단위 일치(가격 대 가격·비율 대 비율),
  모드별
  허용(`market` 우니버스·`new_high` 는 마감만), `trail_from_peak` 는 포트폴리오만, 깊이·조건 수
  상한,
  쿨다운 하한. 거절은 위치(JSON 포인터)·사유 종류만 — **입력값을 싣지 않는다**(422 규칙, D-P8-16).
- 정규화(`normalize`): 파라미터·칸 기본값과 쿨다운 기본값을 채운다 → 같은 뜻이면 같은 `dsl_hash`.
  `dsl_hash` = 이름(`name`)을 뺀 정규화 JSON(키 정렬·공백 없음)의 sha256 16진 — 이름만 바꾼 판은
  같은 논리.
- 장중 수급(사용자 결정 2026-10-13): 수급 지표(`foreign_*`·`inst_*`)는 장중에도 허용 — 장중 값은 KIS
  가집계(잠정)이고, 없으면 평가기가 '판단 불가'로 둔다(`kbj.engines.rules.evaluate`).

CLI: `python -m kbj.engines.rules.dsl schema [--check | --write]` —
`config/schemas/rule_v1.schema.json`·
`web/src/api/rule.schema.json`(생성 전용 — 손으로 고치지 않는다).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Final, Literal, TextIO

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
)

from kbj.config.files import load_yaml_mapping
from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.config.settings import Settings

__all__ = [
    "EXAMPLES_FILE",
    "INDICATORS",
    "OPS",
    "AllGroup",
    "AnyGroup",
    "Cond",
    "DslError",
    "DslIssue",
    "DslLimits",
    "IndSpec",
    "Node",
    "Param",
    "Ref",
    "Rule",
    "Unit",
    "conditions",
    "dsl_hash",
    "json_schema",
    "limits_of",
    "load_example_rules",
    "load_examples",
    "normalize",
    "parse_rule",
    "validate_rule",
]

Unit = Literal["price", "price_diff", "pct", "osc", "ratio", "days", "won", "flag"]
Mode = Literal["close", "intraday", "both"]
Op = Literal[
    "<", "<=", ">", ">=", "==", "between",
    "cross_above", "cross_below", "turns_positive", "turns_negative",
]  # fmt: skip
IndName = Literal[
    "close", "open", "high", "low", "change_pct", "sma", "ema", "rsi", "macd", "bb",
    "bb_width_rank", "adx", "atr", "vol_ratio", "high_n", "low_n", "new_high",
    "foreign_streak", "inst_streak", "foreign_sum", "inst_sum", "trail_from_peak",
]  # fmt: skip
ParamValue = int | float | str

OPS: Final[tuple[str, ...]] = (
    "<", "<=", ">", ">=", "==", "between",
    "cross_above", "cross_below", "turns_positive", "turns_negative",
)  # fmt: skip
EXAMPLES_FILE: Final = "rule_examples.yaml"
_COMPARE: Final = frozenset({"<", "<=", ">", ">=", "==", "between", "cross_above", "cross_below"})
_TURNS: Final = frozenset({"turns_positive", "turns_negative"})
_SIGNED_UNITS: Final = frozenset({"price_diff", "pct", "won"})


# ── 지표 목록(닫힌 목록 — §4.1.2) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Param:
    default: ParamValue
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] = ()
    integer: bool = True


@dataclass(frozen=True)
class IndSpec:
    """지표 하나의 규격. `fields` 가 있으면 여러 값 지표(칸마다 단위)."""

    unit: Unit
    params: Mapping[str, Param] = field(default_factory=dict[str, Param])
    fields: Mapping[str, Unit] | None = None
    default_field: str | None = None
    intraday: bool = True
    portfolio_only: bool = False
    integer: bool = False  # `==` 허용(정수 지표)
    flow: bool = False  # 수급 지표(장중 = 가집계 잠정)


def _n(default: int, lo: int, hi: int) -> Param:
    return Param(default=default, lo=lo, hi=hi)


_PRICE: Final = IndSpec(unit="price")
INDICATORS: Final[Mapping[str, IndSpec]] = {
    "close": _PRICE,
    "open": _PRICE,
    "high": _PRICE,
    "low": _PRICE,
    "change_pct": IndSpec(unit="pct"),
    "sma": IndSpec(unit="price", params={"n": _n(5, 2, 250)}),
    "ema": IndSpec(unit="price", params={"n": _n(5, 2, 250)}),
    "rsi": IndSpec(unit="osc", params={"n": _n(14, 2, 50)}),
    "macd": IndSpec(
        unit="price_diff",
        params={"fast": _n(12, 2, 50), "slow": _n(26, 3, 100), "signal": _n(9, 2, 50)},
        fields={"line": "price_diff", "signal": "price_diff", "hist": "price_diff"},
        default_field="line",
    ),
    "bb": IndSpec(
        unit="price",
        params={"n": _n(20, 5, 100), "k": Param(default=2.0, lo=0.5, hi=4.0, integer=False)},
        fields={
            "upper": "price",
            "mid": "price",
            "lower": "price",
            "pctb": "ratio",
            "width": "ratio",
        },
    ),
    "bb_width_rank": IndSpec(
        unit="osc",
        params={
            "n": _n(20, 5, 100),
            "k": Param(default=2.0, lo=0.5, hi=4.0, integer=False),
            "lookback": _n(120, 20, 500),
        },
    ),
    "adx": IndSpec(
        unit="osc",
        params={"n": _n(14, 2, 50)},
        fields={"adx": "osc", "pdi": "osc", "mdi": "osc"},
        default_field="adx",
    ),
    "atr": IndSpec(unit="price", params={"n": _n(14, 2, 50)}),
    "vol_ratio": IndSpec(unit="ratio", params={"n": _n(20, 2, 120)}),
    "high_n": IndSpec(unit="price", params={"n": _n(20, 2, 250)}),
    "low_n": IndSpec(unit="price", params={"n": _n(20, 2, 250)}),
    "new_high": IndSpec(
        unit="flag",
        params={"axis": Param(default="52w", choices=("d120", "52w", "ath"))},
        intraday=False,
        integer=True,
    ),
    "foreign_streak": IndSpec(
        unit="days",
        params={"side": Param(default="buy", choices=("buy", "sell"))},
        integer=True,
        flow=True,
    ),
    "inst_streak": IndSpec(
        unit="days",
        params={"side": Param(default="buy", choices=("buy", "sell"))},
        integer=True,
        flow=True,
    ),
    "foreign_sum": IndSpec(unit="won", params={"n": _n(5, 1, 60)}, flow=True),
    "inst_sum": IndSpec(unit="won", params={"n": _n(5, 1, 60)}, flow=True),
    "trail_from_peak": IndSpec(unit="pct", portfolio_only=True),
}


# ── 모양(pydantic) ──────────────────────────────────────────────────────────────────────────


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Ref(_M):
    """비교 대상 지표. `scale` 배(예 0.97)를 곱한 값과 비교한다."""

    ind: IndName
    p: dict[str, ParamValue] = Field(default_factory=dict[str, ParamValue])
    field: str | None = None
    scale: float = Field(default=1.0, gt=0, le=10)


class Cond(_M):
    """조건(잎). `value`(수 또는 between 의 [lo, hi]) 또는 `ref` 하나 — turns_* 는 둘 다 없다."""

    ind: IndName
    p: dict[str, ParamValue] = Field(default_factory=dict[str, ParamValue])
    field: str | None = None
    op: Op
    value: float | tuple[float, float] | None = None
    ref: Ref | None = None


def _node_tag(v: Any) -> str:
    if isinstance(v, Mapping):
        if "all" in v:
            return "all"
        if "any" in v:
            return "any"
        return "cond"
    if isinstance(v, AllGroup):
        return "all"
    if isinstance(v, AnyGroup):
        return "any"
    return "cond"


class AllGroup(_M):
    all: list[Node] = Field(min_length=1)


class AnyGroup(_M):
    any: list[Node] = Field(min_length=1)


Node = Annotated[
    Annotated[AllGroup, Tag("all")]
    | Annotated[AnyGroup, Tag("any")]
    | Annotated[Cond, Tag("cond")],
    Discriminator(_node_tag),
]
Group = Annotated[
    Annotated[AllGroup, Tag("all")] | Annotated[AnyGroup, Tag("any")],
    Discriminator(_node_tag),
]


class UCodes(_M):
    codes: list[Annotated[str, Field(pattern=r"^[0-9A-Z]{6}$")]] = Field(
        min_length=1, max_length=200
    )


class UWatchlist(_M):
    watchlist: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class UPortfolio(_M):
    portfolio: Literal[True]


class UMarket(_M):
    market: Literal["KOSPI", "KOSDAQ", "ALL"]


def _universe_tag(v: Any) -> str:
    keys = ("codes", "watchlist", "portfolio", "market")
    if isinstance(v, Mapping):
        for k in keys:
            if k in v:
                return k
        return "codes"
    for k in keys:
        if hasattr(v, k):
            return k
    return "codes"


Universe = Annotated[
    Annotated[UCodes, Tag("codes")]
    | Annotated[UWatchlist, Tag("watchlist")]
    | Annotated[UPortfolio, Tag("portfolio")]
    | Annotated[UMarket, Tag("market")],
    Discriminator(_universe_tag),
]


class Alert(_M):
    cooldown_min: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    priority: int = Field(default=2, ge=1, le=3)
    silent: bool = False


class Rule(_M):
    """규칙 DSL v1."""

    v: Literal[1]
    name: str = Field(min_length=1, max_length=80)
    universe: Universe
    mode: Mode
    when: Group
    alert: Alert = Field(default_factory=Alert)
    track: bool = True


AllGroup.model_rebuild()
AnyGroup.model_rebuild()
Rule.model_rebuild()


# ── 검증 ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DslLimits:
    """의미 검사 상한(`config/rules.yaml` 의 `dsl`·`cooldown_min` — 부르는 쪽이 넘긴다)."""

    max_depth: int = 3
    max_conditions: int = 12
    cooldown_floor: int = 60
    cooldown_default: int = 1440


@dataclass(frozen=True)
class DslIssue:
    pointer: str  # JSON 포인터(RFC 6901)
    reason: str  # 사유 종류(영문 식별자) — 입력값 없음


class DslError(ValueError):
    def __init__(self, issues: Sequence[DslIssue]) -> None:
        self.issues: tuple[DslIssue, ...] = tuple(issues)
        super().__init__("; ".join(f"{i.pointer or '/'}: {i.reason}" for i in self.issues))


_TAGS: Final = frozenset({"all", "any", "cond", "codes", "watchlist", "portfolio", "market"})


def _pointer(loc: Sequence[int | str]) -> str:
    parts: list[str] = []
    items = list(loc)
    for k, part in enumerate(items):
        prev = items[k - 1] if k else None
        if (
            isinstance(part, str)
            and part in _TAGS
            and (prev in ("when", "universe") or isinstance(prev, int))
        ):
            nxt = items[k + 1] if k + 1 < len(items) else None
            if part == "cond" or prev == "universe" or nxt == part:
                continue  # 판별 태그(모양 이름)는 포인터에 넣지 않는다
        parts.append(str(part).replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts) if parts else ""


def parse_rule(data: Any) -> Rule:
    """모양 검사만(pydantic). 실패하면 `DslError`(위치·종류 — 입력값 없음)."""
    try:
        return Rule.model_validate(data)
    except ValidationError as e:
        raise DslError(
            [DslIssue(_pointer(err["loc"]), err["type"]) for err in e.errors()]
        ) from None


def conditions(node: AllGroup | AnyGroup | Cond, path: str = "/when") -> list[tuple[str, Cond]]:
    """잎 조건을 (JSON 포인터, 조건) 으로 — 왼쪽부터."""
    if isinstance(node, Cond):
        return [(path, node)]
    key, kids = ("all", node.all) if isinstance(node, AllGroup) else ("any", node.any)
    out: list[tuple[str, Cond]] = []
    for i, kid in enumerate(kids):
        out.extend(conditions(kid, f"{path}/{key}/{i}"))
    return out


def _depth(node: AllGroup | AnyGroup | Cond) -> int:
    if isinstance(node, Cond):
        return 0
    kids = node.all if isinstance(node, AllGroup) else node.any
    return 1 + max(_depth(k) for k in kids)


def _check_params(
    spec: IndSpec, p: Mapping[str, ParamValue], at: str, issues: list[DslIssue]
) -> None:
    for k, v in p.items():
        ps = spec.params.get(k)
        if ps is None:
            issues.append(DslIssue(f"{at}/p/{k}", "unknown_param"))
            continue
        if ps.choices:
            if not isinstance(v, str) or v not in ps.choices:
                issues.append(DslIssue(f"{at}/p/{k}", "param_choice"))
            continue
        if isinstance(v, str) or isinstance(v, bool):
            issues.append(DslIssue(f"{at}/p/{k}", "param_type"))
            continue
        if ps.integer and float(v) != int(v):
            issues.append(DslIssue(f"{at}/p/{k}", "param_integer"))
            continue
        if (ps.lo is not None and v < ps.lo) or (ps.hi is not None and v > ps.hi):
            issues.append(DslIssue(f"{at}/p/{k}", "param_range"))


def _unit_of(spec: IndSpec, fld: str | None, at: str, issues: list[DslIssue]) -> Unit | None:
    if spec.fields is None:
        if fld is not None:
            issues.append(DslIssue(f"{at}/field", "unknown_field"))
            return None
        return spec.unit
    name = fld if fld is not None else spec.default_field
    if name is None:
        issues.append(DslIssue(f"{at}/field", "field_required"))
        return None
    unit = spec.fields.get(name)
    if unit is None:
        issues.append(DslIssue(f"{at}/field", "unknown_field"))
    return unit


def _check_mode_ind(rule: Rule, spec: IndSpec, at: str, issues: list[DslIssue]) -> None:
    if rule.mode != "close" and not spec.intraday:
        issues.append(DslIssue(f"{at}/ind", "close_only"))
    if spec.portfolio_only and not isinstance(rule.universe, UPortfolio):
        issues.append(DslIssue(f"{at}/ind", "portfolio_only"))


def _check_cond(rule: Rule, c: Cond, at: str, issues: list[DslIssue]) -> None:
    spec = INDICATORS[c.ind]
    _check_params(spec, c.p, at, issues)
    unit = _unit_of(spec, c.field, at, issues)
    _check_mode_ind(rule, spec, at, issues)
    if c.op in _TURNS:
        if c.value is not None or c.ref is not None:
            issues.append(DslIssue(at, "turns_no_operand"))
        if unit is not None and unit not in _SIGNED_UNITS:
            issues.append(DslIssue(f"{at}/op", "turns_unit"))
        return
    if (c.value is None) == (c.ref is None):
        issues.append(DslIssue(at, "value_or_ref"))
        return
    if c.op == "==" and not spec.integer:
        issues.append(DslIssue(f"{at}/op", "eq_integer_only"))
    if c.op == "between":
        if c.ref is not None or not isinstance(c.value, tuple) or c.value[0] > c.value[1]:
            issues.append(DslIssue(f"{at}/value", "between_range"))
        return
    if isinstance(c.value, tuple):
        issues.append(DslIssue(f"{at}/value", "value_scalar"))
        return
    if c.ref is not None:
        rspec = INDICATORS[c.ref.ind]
        _check_params(rspec, c.ref.p, f"{at}/ref", issues)
        runit = _unit_of(rspec, c.ref.field, f"{at}/ref", issues)
        _check_mode_ind(rule, rspec, f"{at}/ref", issues)
        if unit is not None and runit is not None and unit != runit:
            issues.append(DslIssue(f"{at}/ref", "unit_mismatch"))


def validate_rule(data: Any, limits: DslLimits | None = None) -> Rule:
    """모양 + 의미 검사 → 정규화한 규칙. 실패하면 `DslError`(모든 문제를 한 번에)."""
    lim = limits or DslLimits()
    rule = parse_rule(data)
    issues: list[DslIssue] = []
    if _depth(rule.when) > lim.max_depth:
        issues.append(DslIssue("/when", "max_depth"))
    leaves = conditions(rule.when)
    if len(leaves) > lim.max_conditions:
        issues.append(DslIssue("/when", "max_conditions"))
    if isinstance(rule.universe, UMarket) and rule.mode != "close":
        issues.append(DslIssue("/universe/market", "close_only"))
    cd = rule.alert.cooldown_min
    if cd is not None and cd < lim.cooldown_floor:
        issues.append(DslIssue("/alert/cooldown_min", "cooldown_floor"))
    for at, c in leaves:
        _check_cond(rule, c, at, issues)
    if issues:
        raise DslError(issues)
    return normalize(rule, lim)


# ── 정규화·해시 ─────────────────────────────────────────────────────────────────────────────


def _fill(spec: IndSpec, p: Mapping[str, ParamValue]) -> dict[str, ParamValue]:
    out: dict[str, ParamValue] = {}
    for k, ps in spec.params.items():
        v = p.get(k, ps.default)
        if not ps.choices and not isinstance(v, str):
            v = int(v) if ps.integer else float(v)
        out[k] = v
    return out


def _norm_node(node: AllGroup | AnyGroup | Cond) -> AllGroup | AnyGroup | Cond:
    if isinstance(node, AllGroup):
        return AllGroup(all=[_norm_node(k) for k in node.all])
    if isinstance(node, AnyGroup):
        return AnyGroup(any=[_norm_node(k) for k in node.any])
    spec = INDICATORS[node.ind]
    ref = node.ref
    if ref is not None:
        rspec = INDICATORS[ref.ind]
        ref = ref.model_copy(
            update={
                "p": _fill(rspec, ref.p),
                "field": ref.field if ref.field is not None else rspec.default_field,
            }
        )
    return node.model_copy(
        update={
            "p": _fill(spec, node.p),
            "field": node.field if node.field is not None else spec.default_field,
            "ref": ref,
        }
    )


def normalize(rule: Rule, limits: DslLimits | None = None) -> Rule:
    """파라미터·칸·쿨다운 기본값을 채운 규칙(같은 뜻 → 같은 모양)."""
    lim = limits or DslLimits()
    when = _norm_node(rule.when)
    if isinstance(when, Cond):  # pragma: no cover — 뿌리는 언제나 묶음
        raise TypeError("when 의 뿌리는 all·any 묶음")
    cd = rule.alert.cooldown_min if rule.alert.cooldown_min is not None else lim.cooldown_default
    return rule.model_copy(
        update={"when": when, "alert": rule.alert.model_copy(update={"cooldown_min": cd})}
    )


def canonical_json(rule: Rule) -> str:
    """해시용 정규화 JSON — 이름 제외, 키 정렬, 공백 없음, None 칸 제외."""
    data = rule.model_dump(mode="json", exclude_none=True, exclude={"name"})
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dsl_hash(rule: Rule) -> str:
    """정규화 JSON 의 sha256(16진 64자). 부르는 쪽이 `normalize` 한 규칙을 넘긴다."""
    return hashlib.sha256(canonical_json(rule).encode("utf-8")).hexdigest()


# ── 예시·스키마 ─────────────────────────────────────────────────────────────────────────────


def load_examples(data: Any, limits: DslLimits | None = None) -> dict[str, Rule]:
    """`config/rule_examples.yaml` 의 `examples: {id: rule}` → 검사·정규화한 규칙(순서 유지)."""
    if not isinstance(data, Mapping) or not isinstance(data.get("examples"), Mapping):  # pyright: ignore[reportUnknownMemberType]
        raise DslError([DslIssue("/examples", "mapping_required")])
    out: dict[str, Rule] = {}
    examples: Mapping[str, Any] = data["examples"]
    issues: list[DslIssue] = []
    for key, raw in examples.items():
        try:
            out[str(key)] = validate_rule(raw, limits)
        except DslError as e:
            issues.extend(DslIssue(f"/examples/{key}{i.pointer}", i.reason) for i in e.issues)
    if issues:
        raise DslError(issues)
    return out


def limits_of(cfg: RulesConfig) -> DslLimits:
    """`config/rules.yaml` → 의미 검사 상한."""
    return DslLimits(
        max_depth=cfg.dsl.max_depth,
        max_conditions=cfg.dsl.max_conditions,
        cooldown_floor=cfg.cooldown_min.floor,
        cooldown_default=cfg.cooldown_min.default,
    )


def load_example_rules(*, settings: Settings | None = None) -> dict[str, Rule]:
    """`config/rule_examples.yaml` 을 읽어 검사·정규화한 예시(편집기·완료 시험이 같은 함수)."""
    cfg = load_rules_cfg(settings=settings)
    return load_examples(load_yaml_mapping(EXAMPLES_FILE, settings=settings), limits_of(cfg))


REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SCHEMA_PATHS: Final = (
    REPO_ROOT / "config" / "schemas" / "rule_v1.schema.json",
    REPO_ROOT / "web" / "src" / "api" / "rule.schema.json",
)


def json_schema() -> dict[str, Any]:
    schema = Rule.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "kbj:rule/v1"
    schema["description"] = (
        "KBJ 규칙 DSL v1 — 생성 전용(python -m kbj.engines.rules.dsl schema --write). "
        "의미 검사(단위·모드·상한)는 서버 validate_rule."
    )
    return schema


def schema_text() -> str:
    return json.dumps(json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    o = out or sys.stdout
    ap = argparse.ArgumentParser(prog="python -m kbj.engines.rules.dsl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("schema", help="규칙 JSON 스키마 쓰기·검사")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="낡았으면 종료 코드 1")
    g.add_argument("--write", action="store_true", help="두 경로에 쓴다")
    args = ap.parse_args(argv)
    text = schema_text()
    if args.write:
        for path in SCHEMA_PATHS:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"썼다: {path.relative_to(REPO_ROOT)}", file=o)
        return 0
    if args.check:
        stale = [p for p in SCHEMA_PATHS if not p.exists() or p.read_text(encoding="utf-8") != text]
        for p in stale:
            fix = "python -m kbj.engines.rules.dsl schema --write"
            print(f"낡았다: {p.relative_to(REPO_ROOT)} — `{fix}`", file=o)
        return 1 if stale else 0
    o.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
