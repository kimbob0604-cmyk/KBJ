"""섀도 운영 점검 — PLAN §12 Phase 3 '1주 섀도 운영 무오류' (docs/phase3_design.md §4, metrics §8).

실행(DATABASE_URL 은 .env·환경변수 — 출력에 싣지 않는다):

    uv run python -m scripts.shadow_report [--end 2026-10-07] [--days 5] [--flag shadow]

- 기간: end(기본 오늘 KST)까지 최근 days 거래일(기본 5 = 1주, 휴장일은 건너뜀) — 귀속 거래일
  (야간은 T+1)로 센다
- engine 산출(metrics·levels·oi_changes)을 산출 이름·행 플래그(계산 당시 값)별로 센다: 계산 횟수
  (행·사이클 수), invalid 비율, 예외(행 — 지표·레벨 격리가 남긴 `error` 행 / health — engine
  `engine_*_failed` 묶음 수, 같은 대상은 10분에 한 번이라 묶음 수다), null 비율과 명세 밖 null 비율
- 명세 밖 null(`null_reason`): metrics.md 각 절 '예외'가 허용한 null 이 아닌 것 — 빈 범위(만기일이
  아닌 날의 0DTE 등)·Σ콜 = 0·분모 0·보간 구간 밖·교차 없음·20일 안 됨·첫 스냅샷·빈 필드 등은
  명세대로(사유를 센다), 품질 invalid 인 null 도 명세대로(입력·F 없음 — invalid 비율로 따로
  보인다). 규칙이 없는 이름의 null 은 명세 밖으로 센다
- 판정(카탈로그 플래그마다): 행이 있고 예외 0(행·health)·명세 밖 null 0 이면 `무오류`, 행이 없으면
  `계산 없음` — 단 지금 off 인 플래그는 `꺼짐`(정상 — off 는 계산하지 않는다), 아니면 `오류`(off 로
  계산된 행도 오류). 고른 플래그(--flag, 기본 shadow: 그 값으로 계산된 행이 있는 플래그와 지금
  플래그 파일에서 그 값인 플래그)가 모두 무오류·꺼짐이고 사이클·시리즈·행 만들기 예외 health
  (`engine_cycle_failed`·`engine_series_failed`·`engine_rows_failed` — 지표 하나에 묶이지 않는
  예외)와 대상 플래그를 못 가린 지표 예외 health(`?이름` — engine 새 문구·이름 바뀜)가 0 이면
  종료 코드 0, 아니면 1. 설정(캘린더·플래그 파일)·DB 오류는 2
- null 묶음은 행 플래그별이라(저장소가 플래그로도 묶는다) 기간 중에 플래그를 바꿔도 제 줄에 든다
- 운영 기간 기준이라 전용 앱키로 라이브 녹화가 1주 쌓인 뒤 판정한다(PLAN §12 — 2026-09-29 결정)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from core.calendar import KST, PRE_NIGHT_START, TradingCalendar
from core.features import (
    CATALOG,
    FEATURES_PATH,
    Features,
    OutputTable,
    flag_for_output,
    load_features,
    spec_of,
)
from scripts.nogap_report import trading_days
from services.engine.daily import DAILY_METRICS
from services.runtime import utcnow

Verdict = Literal["clean", "errors", "no_rows", "off"]
VERDICT_KO: dict[Verdict, str] = {
    "clean": "무오류",
    "errors": "오류",
    "no_rows": "계산 없음",
    "off": "꺼짐",
}
PASSING: frozenset[Verdict] = frozenset({"clean", "off"})  # 완료 기준을 막지 않는 판정
Selection = Literal["shadow", "visible", "off", "all"]
# null 판정에 쓰는 payload(metrics)·detail(levels) 칸 — 저장소가 이 칸만 묶어 돌려준다
NULL_KEYS: tuple[str, ...] = (
    "expiries",
    "empty",
    "reasons",
    "call_gex",
    "call",
    "tied",
    "gex_won",
    "crossings",
    "flip",
)
# 지표 하나에 묶이는 예외 health — 지표·레벨·플로우 곁일·일별 격리 (문구 머리가 대상)
METRIC_FAILURES = (
    "engine_metric_failed",
    "engine_level_failed",
    "engine_flow_failed",
    "engine_daily_failed",
)
# 지표 하나에 묶이지 않는 예외 — 사이클 통째·시리즈 평가·strike_gex·option_iv 행 만들기
CYCLE_FAILURES: tuple[str, ...] = (
    "engine_cycle_failed",
    "engine_series_failed",
    "engine_rows_failed",
)
FAILURE_KINDS = (*METRIC_FAILURES, *CYCLE_FAILURES)
# 플로우 곁일 이름 → 플래그 (services/engine/service.py `_flow_jobs` — ws_events 는 HIRO 리셋 입력)
_JOB_FLAGS = {"ws_events": "hiro"}


class ShadowSource(Protocol):
    """data/store.py `PostgresSink` 의 섀도 점검 읽기."""

    def engine_output_counts(
        self, first: date, last: date
    ) -> list[tuple[str, str, str, int, int, int, int, int]]: ...

    def engine_null_groups(
        self, first: date, last: date, keys: Sequence[str]
    ) -> list[tuple[str, str, str, str, str, dict[str, Any], int]]: ...

    def engine_failures(
        self, first: date, last: date, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> list[tuple[str, str, int]]: ...


# ── 명세 null ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NullGroup:
    """값 없는 산출 행 묶음 — row_flag 는 행의 계산 당시 플래그, payload 는 `NULL_KEYS` 칸만(없으면
    None)."""

    table: str
    name: str
    row_flag: str
    scope: str
    quality: str
    payload: Mapping[str, Any]
    count: int


def project(payload: Mapping[str, Any], keys: Sequence[str] = NULL_KEYS) -> dict[str, Any]:
    """저장소가 돌려주는 모양 그대로 — keys 칸만, 없으면 None(`jsonb_build_object(k, p -> k)`)."""
    return {k: payload.get(k) for k in keys}


NullRule = Callable[[Mapping[str, Any]], str | None]


def _reasons(p: Mapping[str, Any]) -> set[str]:
    r = p.get("reasons")
    return {str(x) for x in r} if isinstance(r, list) else set()


def _any_reason(*codes: str) -> NullRule:
    def rule(p: Mapping[str, Any]) -> str | None:
        hit = sorted(_reasons(p) & set(codes))
        return hit[0] if hit else None

    return rule


def _is(key: str, value: object, reason: str) -> NullRule:
    """p[key] 가 value 와 같으면 reason — bool 과 수를 섞지 않는다(False == 0 이 아니게)."""

    def rule(p: Mapping[str, Any]) -> str | None:
        v = p.get(key)
        same = isinstance(v, bool) == isinstance(value, bool) and v == value
        return reason if same else None

    return rule


def _first(*rules: NullRule) -> NullRule:
    def rule(p: Mapping[str, Any]) -> str | None:
        return next((r for f in rules if (r := f(p)) is not None), None)

    return rule


def _none() -> NullRule:
    return lambda _p: None


_EMPTY_SCOPE = _is("expiries", [], "empty_scope")  # §2.2 범위에 종목 없음(0DTE 가 아닌 날 등)
_EMPTY = _is("empty", True, "empty_scope")
_FIELD_MISSING = _any_reason("field_missing")  # §6.2·§7 빈 필드

# (표, 산출 이름) → 그 산출의 null 이 명세대로인 사유(metrics.md 각 절 '예외'). 품질 invalid 인 null
# 은 모든 산출에서 명세대로(`null_reason`) — 여기엔 invalid 아닌 null 만
NULL_RULES: dict[tuple[OutputTable, str], NullRule] = {
    # §2.2·§2.3·§4.1·§4.2 범위 합산 — 빈 범위(F 없음·전부 제외는 invalid)
    ("metrics", "net_gex"): _EMPTY_SCOPE,
    ("metrics", "dex"): _EMPTY_SCOPE,
    ("metrics", "vex"): _EMPTY_SCOPE,
    ("metrics", "cex"): _EMPTY_SCOPE,
    # §4.3 Σ GEX_call = 0
    ("metrics", "gex_pc"): _first(_EMPTY_SCOPE, _is("call_gex", 0.0, "call_gex_zero")),
    ("metrics", "atm_iv"): _none(),  # §3.7 없으면 invalid
    ("metrics", "expiry_gamma"): _none(),  # §3.10 = §2.2(만기 하나) — 전부 제외·F 없음 invalid
    ("metrics", "iv_term"): _is("empty", True, "empty_slot"),  # §5.2 해당 만기 없는 칸
    # §5.3 보간 구간 밖·한쪽 종목 없음
    ("metrics", "skew_25d"): _any_reason(
        "put_out_of_range", "call_out_of_range", "no_put_points", "no_call_points"
    ),
    ("metrics", "atm_iv_daily"): _none(),
    ("metrics", "iv_rank"): _any_reason("too_few_days", "flat_window"),  # §5.4 n < 20·max = min
    ("metrics", "iv_percentile"): _any_reason("too_few_days"),
    ("metrics", "iv_hv"): _any_reason("no_data", "missing_returns", "no_today"),  # §5.5 20일 안 됨
    ("metrics", "hiro"): _none(),  # §6.1 늘 값(estimated)
    ("metrics", "investor_flow"): _FIELD_MISSING,
    ("metrics", "dealer_check"): _none(),  # §6.3 판정 없음은 invalid
    ("metrics", "block_trades"): _none(),  # §6.4 값 = 기록 거래일 수
    ("metrics", "block_trade"): _none(),  # 값 = 1틱 체결량
    ("metrics", "pcr_oi"): _is("call", 0, "denominator_zero"),  # §6.5 분모 0
    ("metrics", "pcr_volume"): _is("call", 0, "denominator_zero"),
    ("metrics", "max_pain"): _is("tied", 0, "no_oi"),  # §6.6 OI 전부 0·후보 없음
    ("metrics", "futures_basis"): _FIELD_MISSING,
    ("metrics", "futures_theory_basis"): _FIELD_MISSING,
    ("metrics", "futures_divergence"): _FIELD_MISSING,
    ("metrics", "futures_oi_change"): _FIELD_MISSING,
    ("metrics", "futures_strength"): _FIELD_MISSING,
    # §3.1~§3.3 GEX 가 0 보다 큰 행사가 없음(빈 범위·OI 0·전부 제외)
    ("levels", "call_wall"): _is("gex_won", None, "no_positive_gex"),
    ("levels", "put_wall"): _is("gex_won", None, "no_positive_gex"),
    ("levels", "abs_gamma"): _is("gex_won", None, "no_positive_gex"),
    ("levels", "flip"): _is("crossings", [], "no_crossing"),  # §3.4 교차 0 개(none)
    ("levels", "flip_distance"): _is("flip", None, "no_flip"),  # §3.5 Flip 없음
    ("levels", "expected_move_calendar"): _EMPTY,  # §3.8 빈 범위
    ("levels", "expected_move_trading"): _EMPTY,
    ("levels", "top_levels"): _EMPTY,  # §3.9
    ("oi_changes", "oi_changes"): _is("first_snapshot", True, "first_snapshot"),  # §6.7
}


def null_reason(g: NullGroup) -> str | None:
    """명세가 허용한 null 이면 그 사유, 아니면 None(명세 밖). 규칙이 없는 이름은 invalid 만."""
    rule = NULL_RULES.get((g.table, g.name))  # type: ignore[arg-type]
    if rule is not None and (why := rule(g.payload)) is not None:
        return why
    return "invalid" if g.quality == "invalid" else None


# ── 예외 health → 플래그 ─────────────────────────────────────────────────────


def _to_flag(name: str, table: OutputTable = "metrics") -> str | None:
    if spec_of(name) is not None:
        return name
    return flag_for_output(name, table) or _JOB_FLAGS.get(name)


def failure_flags(kind: str, message: str) -> tuple[str, ...]:
    """engine 예외 health 의 대상 플래그. 문구 머리는 `<대상>: <예외 종류>: …`(services/engine) —
    지표 `이름[/범위/키]`·레벨 `범위/이름`·플로우 곁일 이름. 일별 지표 통째 실패는 네 일별 플래그,
    딜러 가정 점검 실패는 dealer_check. 못 가리면 `?` 를 붙인 대상 그대로."""
    if kind == "engine_daily_failed":
        return ("dealer_check",) if message.startswith("딜러 가정 점검") else DAILY_METRICS
    what = message.split(":", 1)[0].strip()
    parts = what.split("/")
    if kind == "engine_level_failed":
        name, table = (parts[1] if len(parts) > 1 else parts[0]), "levels"
    else:
        name, table = parts[0], "metrics"
    flag = _to_flag(name, table)  # type: ignore[arg-type]
    return (flag,) if flag is not None else (f"?{name}",)


# ── 집계 ─────────────────────────────────────────────────────────────────────


@dataclass
class OutputStat:
    """산출 이름·행 플래그 하나."""

    table: str
    name: str
    flag_name: str  # 카탈로그 플래그(모르면 `?이름`)
    row_flag: str
    rows: int = 0
    cycles: int = 0
    invalid: int = 0
    errors: int = 0
    nulls: int = 0
    off_spec: int = 0
    spec_reasons: dict[str, int] = field(default_factory=dict[str, int])


@dataclass
class FlagResult:
    """플래그 하나. now: 지금 플래그 파일의 값(카탈로그 밖 `?이름` 은 None)."""

    name: str
    outputs: list[OutputStat] = field(default_factory=list["OutputStat"])
    health: int = 0  # 예외 health 묶음 수
    health_samples: list[str] = field(default_factory=list[str])
    now: str | None = None

    @property
    def rows(self) -> int:
        return sum(o.rows for o in self.outputs)

    @property
    def off_rows(self) -> int:
        """off 로 계산된 행 — off 는 계산하지 않으니 있어선 안 된다."""
        return sum(o.rows for o in self.outputs if o.row_flag == "off")

    @property
    def errors(self) -> int:
        return sum(o.errors for o in self.outputs)

    @property
    def off_spec(self) -> int:
        return sum(o.off_spec for o in self.outputs)

    @property
    def verdict(self) -> Verdict:
        """예외·명세 밖 null·off 행이 있으면 오류, 행이 있으면 무오류, 행이 없으면 지금 off 인
        플래그는 꺼짐(정상 — off 는 계산하지 않는다) 아니면 계산 없음."""
        if self.errors or self.health or self.off_spec or self.off_rows:
            return "errors"
        if self.rows:
            return "clean"
        return "off" if self.now == "off" else "no_rows"


@dataclass(frozen=True)
class ShadowReport:
    first: date
    last: date
    days: tuple[date, ...]
    selection: Selection
    flags: tuple[FlagResult, ...]
    cycle_failures: Mapping[str, int]  # `CYCLE_FAILURES` 종류 → 묶음 수
    # 대상을 카탈로그 플래그로 못 가린 예외 health(`?이름` → 묶음 수)·문구 견본 — 버리지 않는다
    unattributed: Mapping[str, int] = field(default_factory=dict[str, int])
    unattributed_samples: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return (
            bool(self.flags)
            and all(f.verdict in PASSING for f in self.flags)
            and not any(self.cycle_failures.values())
            and not any(self.unattributed.values())
        )


def _flag_name(table: str, name: str) -> str:
    if table == "oi_changes":
        return "oi_changes"
    return flag_for_output(name, table) or f"?{name}"  # type: ignore[arg-type]


def summarize(
    counts: Sequence[tuple[str, str, str, int, int, int, int, int]],
    nulls: Sequence[NullGroup],
    failures: Sequence[tuple[str, str, int]],
    *,
    first: date,
    last: date,
    days: Sequence[date],
    selection: Selection,
    features: Features,
) -> ShadowReport:
    """저장소 집계 → 플래그별 판정. selection: 행 플래그가 그 값인 산출과, 지금 플래그 파일에서 그
    값인 플래그(행이 없으면 `계산 없음`) — all 이면 전부."""
    stats: dict[tuple[str, str, str], OutputStat] = {}
    for table, name, row_flag, rows, cycles, invalid, errors, n_null in counts:
        stats[(table, name, row_flag)] = OutputStat(
            table, name, _flag_name(table, name), row_flag, rows, cycles, invalid, errors, n_null
        )
    for g in nulls:
        key = (g.table, g.name, g.row_flag)
        target = stats.get(key)
        if target is None:  # 두 읽기 사이에 새로 쓴 행 — 행 수 없이 null 만 센다
            target = stats[key] = OutputStat(
                g.table, g.name, _flag_name(g.table, g.name), g.row_flag
            )
        why = null_reason(g)
        if why is None:
            target.off_spec += g.count
        else:
            target.spec_reasons[why] = target.spec_reasons.get(why, 0) + g.count
    picked: dict[str, FlagResult] = {}

    def take(name: str) -> FlagResult:
        if name not in picked:
            s = spec_of(name)
            now = None if s is None else features.flag(s.name, s.section)
            picked[name] = FlagResult(name, now=now)
        return picked[name]

    for s in stats.values():
        if selection == "all" or s.row_flag == selection:
            take(s.flag_name).outputs.append(s)
    for spec in CATALOG:
        now = features.flag(spec.name, spec.section)
        if selection == "all" or now == selection:
            take(spec.name)
    cycle_failures: dict[str, int] = dict.fromkeys(CYCLE_FAILURES, 0)
    unattributed: dict[str, int] = {}
    samples: list[str] = []
    for kind, message, n in failures:
        if kind in cycle_failures:
            cycle_failures[kind] += n
            continue
        for flag in failure_flags(kind, message):
            if flag in picked:
                r = picked[flag]
                r.health += n
                if len(r.health_samples) < 3:
                    r.health_samples.append(message[:120])
            elif flag.startswith("?"):  # 어느 플래그인지 모른다 — 고른 것일 수도 있다
                unattributed[flag] = unattributed.get(flag, 0) + n
                if len(samples) < 3:
                    samples.append(message[:120])
    for r in picked.values():
        r.outputs.sort(key=lambda o: (o.table, o.name, o.row_flag))
    order = {s.name: i for i, s in enumerate(CATALOG)}
    flags = tuple(sorted(picked.values(), key=lambda r: (order.get(r.name, len(order)), r.name)))
    return ShadowReport(
        first, last, tuple(days), selection, flags, cycle_failures, unattributed, tuple(samples)
    )


# ── 출력 ─────────────────────────────────────────────────────────────────────


def _pct(n: int, d: int) -> str:
    return "-" if d == 0 else f"{100.0 * n / d:.1f}%"


def render(r: ShadowReport) -> str:
    sel = "전부" if r.selection == "all" else r.selection
    lines = [
        f"# 섀도 운영 점검 — 귀속 거래일 {r.first} ~ {r.last} ({len(r.days)}거래일, 플래그 {sel})",
        "",
        "| 플래그 | 산출 | 표 | 행 플래그 | 행 | 사이클 | invalid | 예외 행 | null | 명세 밖 null "
        "| 명세 null 사유 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for f in r.flags:
        if not f.outputs:
            lines.append(f"| {f.name} | - | - | - | 0 | 0 | - | 0 | - | - | - |")
        for o in f.outputs:
            why = ", ".join(f"{k} {v}" for k, v in sorted(o.spec_reasons.items())) or "-"
            lines.append(
                f"| {f.name} | {o.name} | {o.table} | {o.row_flag} | {o.rows} | {o.cycles} | "
                f"{_pct(o.invalid, o.rows)} | {o.errors} | {_pct(o.nulls, o.rows)} | "
                f"{_pct(o.off_spec, o.rows)} ({o.off_spec}) | {why} |"
            )
    lines += [
        "",
        "## 판정",
        "",
        "| 플래그 | 판정 | 행 | 예외 행 | 예외 health(10분 묶음) | 명세 밖 null | 비고 |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in r.flags:
        note = "; ".join(f.health_samples) or "-"
        lines.append(
            f"| {f.name} | {VERDICT_KO[f.verdict]} | {f.rows} | {f.errors} | {f.health} | "
            f"{f.off_spec} | {note} |"
        )
    cycle = " · ".join(f"{k} {n}" for k, n in r.cycle_failures.items())
    lost = " · ".join(f"{k} {n}" for k, n in sorted(r.unattributed.items())) or "0"
    if r.unattributed_samples:
        lost += " — " + "; ".join(r.unattributed_samples)
    lines += [
        "",
        f"지표에 묶이지 않는 예외 health(10분 묶음): {cycle}",
        f"대상 플래그를 못 가린 예외 health(10분 묶음): {lost}",
        "완료 기준 '1주 섀도 운영 무오류(예외 0, 명세 밖 null 0)': "
        + ("충족" if r.satisfied else "미충족"),
    ]
    return "\n".join(lines)


# ── 실행 ─────────────────────────────────────────────────────────────────────


def untagged_window(first: date, last: date, cal: TradingCalendar) -> tuple[datetime, datetime]:
    """태그 없는(세션 밖) health 를 기간에 넣는 시각 [start, end) — first 로 귀속되는 밤의 준비
    (앞 거래일 17:50 KST)부터 last 의 POST_DAY 끝(17:50 KST)까지."""
    prev = cal.prev_trading_day(first)
    start = datetime.combine(prev, PRE_NIGHT_START, tzinfo=KST)
    end = datetime.combine(last, PRE_NIGHT_START, tzinfo=KST)
    return start, end


def build(
    store: ShadowSource,
    cal: TradingCalendar,
    days: Sequence[date],
    selection: Selection,
    features: Features,
) -> ShadowReport:
    first, last = days[0], days[-1]
    start, end = untagged_window(first, last, cal)
    counts = store.engine_output_counts(first, last)
    nulls = [
        NullGroup(t, n, f, s, q, p, c)
        for t, n, f, s, q, p, c in store.engine_null_groups(first, last, NULL_KEYS)
    ]
    failures = store.engine_failures(first, last, start, end, FAILURE_KINDS)
    return summarize(
        counts,
        nulls,
        failures,
        first=first,
        last=last,
        days=days,
        selection=selection,
        features=features,
    )


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.shadow_report", description="1주 섀도 운영 무오류 판정 (설계 §4)"
    )
    p.add_argument("--end", type=date.fromisoformat, help="마지막 귀속 거래일(기본: 오늘 KST)")
    p.add_argument("--days", type=int, default=5, help="살펴볼 최근 거래일 수(기본 5 = 1주)")
    p.add_argument(
        "--flag",
        choices=("shadow", "visible", "off", "all"),
        default="shadow",
        help="볼 플래그(행 플래그·지금 플래그 파일 — 기본 shadow)",
    )
    p.add_argument("--features", type=Path, default=FEATURES_PATH, help="플래그 파일")
    args = p.parse_args(argv)
    if args.days < 1:
        p.error("--days 는 1 이상")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    store: ShadowSource | None = None,
    cal: TradingCalendar | None = None,
    now: datetime | None = None,
) -> int:
    """종료 코드: 0 충족, 1 미충족, 2 설정·DB 오류. store 는 시험이 넣는다(기본 DATABASE_URL)."""
    args = parse_args(argv)
    owned = None
    try:
        cal = cal or TradingCalendar.default()
        features = load_features(args.features)
        end = args.end or (now or utcnow()).astimezone(KST).date()
        days = trading_days(end, args.days, cal)
        if store is None:
            from data.store import PostgresSink

            owned = store = PostgresSink.from_settings(service="shadow_report")
        report = build(store, cal, days, args.flag, features)
    except Exception as e:  # 설정(캘린더·플래그 파일)·DB 오류 — 문구는 저장소가 가린 첫 줄만
        text = str(e).splitlines()[0][:200] if str(e) else ""
        print(f"섀도 점검을 못 했다(설정·DB 오류): {type(e).__name__}: {text}", file=sys.stderr)
        return 2
    finally:
        if owned is not None:
            owned.close()
    print(render(report))
    return 0 if report.satisfied else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
