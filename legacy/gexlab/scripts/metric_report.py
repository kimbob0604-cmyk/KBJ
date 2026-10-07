"""지표별 검증 리포트 — Phase 3 완료 기준 (docs/phase3_design.md §5, metrics §8, PLAN §12).

실행(로컬 녹화·fixture 만 읽는다 — 네트워크·.env·DB 를 쓰지 않는다):

    uv run python -m scripts.metric_report [--probe-dir probe_out] [--junit 결과.xml | --no-tests] \
        [--write | --out 경로]

`--write` 면 docs/metric_validation.md 를 새로 쓰고(`--out` 이면 그 경로), 아니면 stdout. 기능
플래그(카탈로그 `core.features.CATALOG` — Phase 3 확장 지표)마다:

1. 명세 테스트 — 그 지표의 단위·속성·engine 시험(`ENTRIES` 의 파일·이름 패턴)을 pytest 로 돌린
   JUnit XML 에서 센다(`--junit` 이면 그 파일, `--no-tests` 면 건너뜀). 파일 수집 실패는 그 파일을
   쓰는 지표마다 실패, 시험을 돌렸는데 0 건인 지표·묶음은 '명세 테스트 없음'(실패처럼 종료 코드 1)
2. 2026-09-28 체인 스냅샷 두 장(14:27·14:52, `<probe-dir>/runs/20260928_{1427,1452}_chain/
   chain_snapshot.json` — 로컬·git 제외, 읽기만)의 engine 사이클 값·품질: `snapshot_cycle` 로 engine
   입력을 만들어 `services.engine.evaluate.evaluate_cycle`(core_golden 흐름)에 engine 등록부(확장
   지표·PCR·맥스페인)를 붙여 시각 순으로(확정 베이시스를 넘긴다), OI 증감은 두 장 사이
   (`OiTracker`). 로컬 스냅샷이 없으면 git 에 있는 작은 발췌(tests/fixtures/validation — CI)로 —
   리포트 머리에 적는다
3. 교차 확인 — 구현과 따로 짠 계산으로 대조한다(불일치는 종료 코드 1):
   Vanna·Charm 해석식 대 vollib 델타의 수치 미분(종목마다·범위 합), GEX P/C 를 행사가별 GEX 행으로,
   25Δ 스큐·기간구조 ATM IV 를 스마일에서 손 보간, PCR·맥스페인을 체인 행과 KRX 일별 fixture
   (tests/fixtures/krx/opt_daily.json — 이름 문자열을 따로 풀어)로, KRX 일별 ATM IV·IV 랭크·
   퍼센타일·HV20 손계산(fixture 는 하루뿐이라 식 대조는 SYNTHETIC 이력 — 252일 이력은 Phase 6 백필
   뒤), 투자자별·딜러 점검을 KIS 투자자 fixture 로, 선물 KIS basis 대 자체 선물가 − 지수·이론가 −
   지수(분봉 fixture + probe-dir 의 분봉 응답), OI 증감을 두 스냅샷 OI 차로
4. 남은 [확인 필요] — docs/metrics.md 그 절의 표시(앞 문맥 몇 글자)

종료 코드: 0 = 명세 테스트 실패 0(수집 실패 포함)·명세 테스트 없는 지표 0·교차 확인 불일치 0,
1 = 실패·없음·불일치가 있다(리포트는 쓴다),
2 = 입력 오류(스냅샷·fixture 를 못 읽음). 1주 섀도 운영(PLAN §12)은 `scripts.shadow_report` — 전용
앱키로 라이브 녹화가 쌓인 뒤.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast
from xml.etree import ElementTree

from core.calendar import KST, TradingCalendar, state_at
from core.features import CATALOG, FEATURES_PATH, Features, FlagSpec, load_features
from core.gex import OPTION_MULTIPLIER, ExpiryEval
from core.greeks import DAYS_PER_YEAR, charm, greeks, vanna
from core.metrics.exposure import VANNA_SCALE, cex, vex
from core.metrics.flow import block_thresholds, dealer_check, max_pain, pcr
from core.metrics.futures import BASIS_CHECK_TOLERANCE, futures_metrics
from core.metrics.vol import (
    DailyIv,
    KrxIvRow,
    Settlement,
    iv_rank,
    krx_atm_iv,
    realized_vol,
)
from data.kis.models import InvestorRow
from data.krx.models import parse_option_rows
from scripts.make_golden import CORE_FIXTURES, shift_snapshot
from scripts.validate_greeks import Snapshot, load_snapshot, series_rows
from services.bus import BasisBook, SeriesKey
from services.engine.evaluate import CycleInput, CycleResult, ExpiryInfo, evaluate_cycle
from services.engine.flow import OiTracker
from services.engine.futures import quote_of
from services.engine.records import LevelRecord, MetricRecord, OiChangeRecord
from services.engine.service import ENGINE_REGISTRY
from services.poller.records import ChainRecord, FuturesRecord

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "metric_validation.md"
METRICS_MD = ROOT / "docs" / "metrics.md"
PROBE_DIR = ROOT / "probe_out"
SNAPSHOT_RUNS = ("20260928_1427_chain", "20260928_1452_chain")
FIXTURES = ROOT / "tests" / "fixtures"
KRX_OPT = FIXTURES / "krx" / "opt_daily.json"
KRX_FUT = FIXTURES / "krx" / "fut_daily.json"
KIS_MINUTE = FIXTURES / "kis" / "minute_day.json"
KIS_INVESTOR = FIXTURES / "kis" / "investor.json"
REGENERATE = "uv run python -m scripts.metric_report --probe-dir <probe_out> --write"

# 교차 확인 허용 — 구현과 따로 짠 계산의 차(부동소수 순서 차이만 남아야 한다)
EXACT_REL = 1e-9
DERIV_REL = 1e-5  # 중앙 차분(σ ± 1e-5, T ± T·1e-4)의 절단·반올림 오차
DERIV_H_SIGMA = 1e-5
DERIV_H_T = 1e-4

Outcome = Literal["passed", "failed", "skipped"]


class InputError(ValueError):
    """스냅샷·fixture 를 읽지 못했다(종료 코드 2)."""


# ── 지표 목록 (카탈로그 플래그 → 명세 테스트) ──────────────────────────────────

T_GREEKS = "tests/unit/test_greeks.py"
T_EXPO = "tests/unit/test_exposure.py"
P_EXPO = "tests/property/test_exposure_properties.py"
T_VOL = "tests/unit/test_vol.py"
T_FLOW = "tests/unit/test_flow.py"
P_FLOW = "tests/property/test_flow_properties.py"
T_FUT = "tests/unit/test_futures_metrics.py"
P_FUT = "tests/property/test_futures_properties.py"
E_EXT = "tests/unit/test_engine_extended.py"
E_FLOW = "tests/unit/test_engine_flow.py"
E_DAILY = "tests/unit/test_engine_daily.py"
E_INV = "tests/unit/test_engine_investor.py"
E_TICKS = "tests/unit/test_engine_ticks.py"
E_FUT = "tests/unit/test_engine_futures.py"


@dataclass(frozen=True)
class Entry:
    """Phase 3 지표(기능 플래그) 하나 — tests: (시험 파일, 시험 이름 정규식 `re.search`)."""

    flag: str
    title: str
    tests: tuple[tuple[str, str], ...]

    @property
    def spec(self) -> FlagSpec:
        return next(s for s in CATALOG if s.name == self.flag)


ENTRIES: tuple[Entry, ...] = (
    Entry(
        "vex",
        "Vanna 익스포저",
        (
            (T_GREEKS, "vanna"),
            (T_EXPO, "units|sign|wings|cancel|vex_|scopes|quality_and_nulls"),
            (P_EXPO, "vex|cancel|calls_only"),
            (E_EXT, "registered|input_quality|measured_snapshot"),
        ),
    ),
    Entry(
        "cex",
        "Charm 익스포저",
        (
            (T_GREEKS, "charm"),
            (T_EXPO, "units|sign|wings|cancel|cex|charm|scopes|quality_and_nulls"),
            (P_EXPO, "cex|cancel"),
            (E_EXT, "registered|input_quality|not_due|measured_snapshot"),
        ),
    ),
    Entry(
        "gex_pc",
        "GEX P/C 비율",
        (
            (T_EXPO, "gex_put_call"),
            (P_EXPO, "gex_put_call"),
            (E_EXT, "registered|measured_snapshot"),
        ),
    ),
    Entry(
        "iv_term",
        "IV 기간구조",
        (
            (T_VOL, "term_structure|monthly_expiry_day"),
            (
                E_EXT,
                "term_slot|failed_nearest_monthly|failed_weeklies|without_an_expiry_date"
                "|registered|measured_snapshot",
            ),
        ),
    ),
    Entry(
        "skew_25d",
        "25Δ 스큐",
        (
            (T_VOL, "skew|smile|interpolate_at_delta"),
            (E_EXT, "registered|input_quality|measured_snapshot"),
        ),
    ),
    Entry(
        "atm_iv_daily",
        "일별 월물 ATM IV",
        (
            (T_VOL, "krx_atm_iv|atm_iv_from_points"),
            (E_DAILY, "today|nearest_monthly|close|settled|krx_backfill|flags_off|input_helpers"),
        ),
    ),
    Entry(
        "iv_rank",
        "IV 랭크",
        ((T_VOL, "iv_rank"), (E_DAILY, "krx_backfill|flags_off|without_a_close")),
    ),
    Entry(
        "iv_percentile",
        "IV 퍼센타일",
        ((T_VOL, "iv_rank"), (E_DAILY, "krx_backfill|flags_off|without_a_close")),
    ),
    Entry(
        "iv_hv",
        "IV − HV20",
        ((T_VOL, "hv20|iv_minus_hv"), (E_DAILY, "iv_minus_hv|without_a_close")),
    ),
    Entry(
        "hiro",
        "HIRO-lite",
        (
            (
                T_FLOW,
                "signed_quantity|customer_call|reversing|cumulative|delta_or_forward"
                "|session_change|resets|hiro|sequence_gaps",
            ),
            (P_FLOW, "hiro"),
            (
                E_TICKS,
                "flow_is|priced|reversal|sequence_gap|session_change|reset|connection_event"
                "|hiro|reconnect|bad_tick|flags_off|run_loop",
            ),
        ),
    ),
    Entry(
        "investor_flow",
        "투자자별 순매수",
        ((E_INV, "pass_through|same_row|missing_net|thirty_seconds|visible_investor"),),
    ),
    Entry(
        "dealer_check",
        "딜러 가정 점검",
        (
            (T_FLOW, "dealer_check|missing_pairs_are_left_out|mismatch_streak"),
            (
                E_INV,
                "the_check|missing_pairs_are_estimated|mismatching|checks_once|failed_check"
                "|check_follows",
            ),
        ),
    ),
    Entry(
        "block_trades",
        "대량 체결",
        ((T_FLOW, "moneyness|p99|block"), (P_FLOW, "p99"), (E_TICKS, "block")),
    ),
    Entry(
        "pcr",
        "PCR",
        (
            (T_FLOW, "pcr|unknown_values"),
            (P_FLOW, "pcr|swapping"),
            (E_FLOW, "pcr|not_evaluated|flow_plugins"),
        ),
    ),
    Entry(
        "max_pain",
        "맥스페인",
        (
            (T_FLOW, "max_pain|ties_go|tie_without|zero_oi|listed_strikes"),
            (P_FLOW, "max_pain|shifting"),
            (E_FLOW, "max_pain|flow_plugins"),
        ),
    ),
    Entry(
        "oi_changes",
        "OI 증감 히트맵",
        (
            (T_FLOW, "changes_are|dip|stepping|recovery|snapshots_must"),
            (P_FLOW, "oi_changes"),
            (
                E_FLOW,
                "first_snapshot|dip|cell_quality|without_oi|expired_series|writes_oi_changes"
                "|restart|oi_changes_follow",
            ),
        ),
    ),
    Entry(
        "futures",
        "선물 베이시스·괴리율·OI 증감·체결강도",
        ((T_FUT, "."), (P_FUT, "."), (E_FUT, ".")),
    ),
)
# Phase 2 핵심(visible) — 골든·부호 고정·레벨 시험을 한 묶음으로
CORE_TESTS: tuple[tuple[str, str], ...] = (
    ("tests/test_sign_conventions.py", "."),
    ("tests/unit/test_gex.py", "."),
    ("tests/unit/test_levels.py", "."),
    ("tests/property/test_gex_properties.py", "."),
    ("tests/property/test_levels_properties.py", "."),
    ("tests/golden/test_core_golden.py", "."),
    ("tests/unit/test_engine_evaluate.py", "."),
)
FLAG_TESTS: tuple[tuple[str, str], ...] = (
    ("tests/unit/test_features.py", "."),
    ("tests/unit/test_shadow_report.py", "."),
)


# ── 명세 테스트 (JUnit XML) ───────────────────────────────────────────────────


# pytest 가 파일(또는 클래스) 통째로 내는 보고의 JUnit 문구 → 리포트 표기. 그 파일의 시험은 하나도
# 돌지 않았다(수집 실패 — pytest 는 기본으로 거기서 멈춘다)
WHOLE_KO: dict[str, str] = {
    "collection failure": "수집 실패",
    "collection skipped": "수집 건너뜀",
    "internal error": "pytest 내부 오류",
}


@dataclass(frozen=True)
class TestCase:
    """JUnit testcase 하나 — file 은 저장소 상대 경로, name 은 매개변수 꼬리 `[…]` 를 뗀 함수
    이름. whole: 파일 통째 보고(`WHOLE_KO` 의 JUnit 문구 — 수집 실패·수집 건너뜀·pytest 내부
    오류)면 그 문구, name 은 비고 그 파일의 모든 이름 패턴에 든다."""

    __test__ = False  # pytest 가 모으지 않게

    file: str
    name: str
    param: str
    outcome: Outcome
    whole: str = ""

    @property
    def label(self) -> str:
        if self.whole:
            return f"{self.file} ({WHOLE_KO.get(self.whole, self.whole)})"
        return f"{self.file}::{self.name}" + (f"[{self.param}]" if self.param else "")


def _module_path(address: str) -> str:
    """점 주소 → 파일 경로. 클래스 안 시험이면 끝이 클래스 이름 — test_ 로 시작하는 마지막
    조각까지가 모듈."""
    parts = address.split(".")
    mods = [i for i, p in enumerate(parts) if p.startswith("test_")]
    return "/".join(parts[: mods[-1] + 1] if mods else parts) + ".py"


def parse_junit(text: str) -> list[TestCase]:
    """pytest `--junitxml` 결과 → 시험 목록(classname `tests.unit.test_flow` → 파일 경로). 모듈
    수집 보고는 classname 이 비고 name 이 모듈 주소(`tests.unit.test_vol`) — 그 파일 통째 보고로."""
    root = ElementTree.fromstring(text)  # noqa: S314 — 이 스크립트가 돌린 pytest 의 출력
    out: list[TestCase] = []
    for tc in root.iter("testcase"):
        cls, full = tc.get("classname", ""), tc.get("name", "")
        marks = [tc.find(t) for t in ("failure", "error", "skipped")]
        outcome: Outcome = "passed"
        if marks[0] is not None or marks[1] is not None:
            outcome = "failed"
        elif marks[2] is not None:
            outcome = "skipped"
        found = (e.get("message", "") for e in marks if e is not None)
        whole = next((m for m in found if m in WHOLE_KO), "")
        if whole == "internal error":
            out.append(TestCase("pytest", "", "", outcome, whole))
        elif whole:
            out.append(TestCase(_module_path(cls or full), "", "", outcome, whole))
        else:
            name, _, param = full.partition("[")
            out.append(TestCase(_module_path(cls), name, param.rstrip("]"), outcome))
    return out


@dataclass(frozen=True)
class Tally:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    files: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped

    def text(self) -> str:
        if self.total == 0:
            return "없음"
        head = f"{self.passed} 통과"
        if self.failed:
            head += f" · **{self.failed} 실패**"
        if self.skipped:
            head += f" · {self.skipped} 건너뜀"
        return head


def tally(cases: Sequence[TestCase], mapping: Sequence[tuple[str, str]]) -> Tally:
    """mapping 의 (파일, 이름 패턴)에 드는 시험을 센다(매개변수마다 하나, 겹쳐도 한 번). 파일 통째
    보고(수집 실패 등)는 그 파일의 어느 패턴에나 든다 — 그 파일의 시험이 돌지 않았다."""
    seen: dict[tuple[str, str, str, str], TestCase] = {}
    for c in cases:
        if any(c.file == f and (c.whole or re.search(p, c.name)) for f, p in mapping):
            seen[(c.file, c.name, c.param, c.whole)] = c
    got = list(seen.values())
    n = {o: sum(1 for c in got if c.outcome == o) for o in ("passed", "failed", "skipped")}
    fails = tuple(sorted(c.label for c in got if c.outcome == "failed"))
    files = tuple(sorted({c.file for c in got}))
    return Tally(n["passed"], n["failed"], n["skipped"], files, fails)


def spec_test_files() -> list[str]:
    files = {f for e in ENTRIES for f, _ in e.tests} | {f for f, _ in (*CORE_TESTS, *FLAG_TESTS)}
    return sorted(files)


def run_spec_tests(files: Sequence[str], out: Path) -> None:
    """pytest 를 따로 돌려 JUnit XML 을 out 에 — 실패해도 XML 은 남는다(결과는 리포트가 센다)."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={out}",
        *files,
    ]
    subprocess.run(cmd, cwd=ROOT, check=False, capture_output=True, timeout=1800)  # noqa: S603


# ── 스냅샷 → engine 입력 ──────────────────────────────────────────────────────

_KIND_CLS = {"MONTH": "", "WKM": "WKM", "WKI": "WKI"}


@dataclass(frozen=True)
class SnapshotCycle:
    inp: CycleInput
    labels: dict[str, str]  # 스냅샷 라벨(MONTH:202610) → engine 라벨(M:202610)


def snapshot_cycle(
    path: Path,
    *,
    shift: timedelta | None = None,
    fresh_since: datetime | None = None,
    strikes: Mapping[SeriesKey, Sequence[Decimal]] | None = None,
    cal: TradingCalendar | None = None,
) -> SnapshotCycle:
    """체인 스냅샷(probe `chain_snapshot.json` 또는 그 작은 발췌) 하나 → engine `CycleInput`.

    전광판·보강 행을 `scripts.validate_greeks.series_rows` 로 OptionQuote 로 푼 뒤 체인 행으로
    되돌린다(행 시각 = 그 전광판 시각 — core_golden 의 평가 시각과 같게). 최종거래일은 스냅샷의
    월물리스트(없으면 캘린더), S_ref 는 스냅샷의 근월물 — 주간 전광판 F, 야간(시각을 옮긴 파생
    입력)이면 단건 CM. shift: 시각만 옮긴다(`scripts.make_golden.shift_snapshot`)."""
    cal = cal or TradingCalendar.default()
    snap: Snapshot = load_snapshot(path)
    if shift is not None:
        snap = shift_snapshot(snap, shift)
    info = state_at(snap.started_kst, cal)
    if info.trade_date is None or info.session is None:
        raise ValueError(f"세션 밖 스냅샷: {snap.started_kst}")
    rows: list[ChainRecord] = []
    expiries: dict[SeriesKey, ExpiryInfo] = {}
    labels: dict[str, str] = {}
    for label in snap.boards:
        kind, _, code = label.partition(":")
        cls = _KIND_CLS[kind]
        s = series_rows(snap, label, cal)
        key = cast(SeriesKey, (cls, code))
        expiries[key] = ExpiryInfo(s.expiry_date, "kis" if s.expiry_source == "kis" else "calendar")
        labels[label] = f"{cls or 'M'}:{code}"
        for q in s.quotes:
            rows.append(
                ChainRecord(
                    ts=s.now,
                    trade_date=info.trade_date,
                    session=info.session,
                    mrkt_cls=cls,
                    expiry=code,
                    strike=q.strike,
                    cp=q.cp,
                    source=q.source,
                    last=q.last,
                    bid=q.bid,
                    ask=q.ask,
                    oi=q.oi,
                    volume=q.volume,
                    iv_kis=None if q.kis_iv_pct is None else Decimal(str(q.kis_iv_pct)),
                )
            )
    day = info.session == "day"
    fut = FuturesRecord(
        ts=snap.started_kst,
        trade_date=info.trade_date,
        session=info.session,
        code=snap.atm_ref.code,
        market="F" if day else "CM",
        source="board" if day else "single",
        price=Decimal(str(snap.atm_ref.price)),
    )
    inp = CycleInput(
        as_of=max(r.ts for r in rows),
        trade_date=info.trade_date,
        session=info.session,
        chain=rows,
        futures=[fut],
        expiries=expiries,
        near_code=snap.atm_ref.code,
        strikes=dict(strikes or {}),
        fresh_since=fresh_since,
    )
    return SnapshotCycle(inp, labels)


@dataclass(frozen=True)
class SnapshotRun:
    """스냅샷 한 장의 engine 사이클 — oi: 그 사이클에 OI 증감 추적이 쓸 행."""

    path: Path
    name: str  # HH:MM (KST)
    inp: CycleInput
    result: CycleResult
    oi: tuple[OiChangeRecord, ...]

    def evals(self) -> dict[str, ExpiryEval]:
        return {o.label: o.ev for o in self.result.series if o.ev is not None}

    def metric(self, name: str, scope: str, key: str = "") -> MetricRecord | None:
        return next(
            (m for m in self.result.metrics if (m.metric, m.scope, m.key) == (name, scope, key)),
            None,
        )


def evaluate_snapshots(paths: Sequence[Path], cal: TradingCalendar) -> list[SnapshotRun]:
    """스냅샷들을 시각 순으로 engine 사이클에 — 확정 베이시스·OI 추적을 다음 장에 넘긴다."""
    cycles: list[tuple[Path, SnapshotCycle]] = []
    for p in paths:
        try:
            cycles.append((p, snapshot_cycle(p, cal=cal)))
        except (OSError, ValueError) as e:
            raise InputError(f"{p.name}: 체인 스냅샷을 읽지 못했다 ({type(e).__name__})") from None
    cycles.sort(key=lambda x: x[1].inp.as_of)
    book = BasisBook()
    oi = OiTracker()
    out: list[SnapshotRun] = []
    for p, c in cycles:
        r = evaluate_cycle(c.inp, book, cal=cal, registry=ENGINE_REGISTRY)
        rows, _ = oi.cycle(c.inp, r, "shadow")
        book = r.basis
        name = c.inp.as_of.astimezone(KST).strftime("%H:%M")
        out.append(SnapshotRun(p, name, c.inp, r, tuple(rows)))
    return out


# ── 값 표 ────────────────────────────────────────────────────────────────────

Unit = Literal["won", "ratio", "iv", "iv_pt", "pt", "pct", "count", "qty"]
UNITS: dict[str, Unit] = {
    "net_gex": "won",
    "dex": "won",
    "expiry_gamma": "won",
    "vex": "won",
    "cex": "won",
    "gex_pc": "ratio",
    "pcr_oi": "ratio",
    "pcr_volume": "ratio",
    "atm_iv": "iv",
    "iv_term": "iv",
    "skew_25d": "iv_pt",
    "max_pain": "pt",
    "call_wall": "pt",
    "put_wall": "pt",
    "abs_gamma": "pt",
    "flip": "pt",
    "flip_distance": "pct",
    "expected_move_calendar": "pt",
    "expected_move_trading": "pt",
    "top_levels": "count",
}


def fmt(value: float | None, unit: Unit) -> str:
    if value is None:
        return "null"
    match unit:
        case "won":
            return f"{value / 1e8:,.1f}억"
        case "ratio":
            return f"{value:.4f}"
        case "iv":
            return f"{value * 100:.2f}%"
        case "iv_pt":
            return f"{value * 100:+.2f}%p"
        case "pt":
            return f"{value:.2f}"
        case "pct":
            return f"{value:+.3f}%"
        case "count" | "qty":
            return f"{value:,.0f}"


def _reasons(rec: MetricRecord | LevelRecord) -> list[str]:
    if isinstance(rec, MetricRecord):
        raw = rec.payload.get("reasons")
        return [str(x) for x in raw] if isinstance(raw, list) else []
    return list(rec.reasons)


def cell(rec: MetricRecord | LevelRecord | None, unit: Unit) -> str:
    """값 · 품질(ok 가 아니면 사유 둘까지)."""
    if rec is None:
        return "-"
    text = f"{fmt(rec.value, unit)} · {rec.quality}"
    why = _reasons(rec)
    if why and (rec.quality != "ok" or rec.value is None):
        more = f" 외 {len(why) - 2}" if len(why) > 2 else ""
        text += f" ({', '.join(why[:2])}{more})"
    return text


def _keyed(run: SnapshotRun, outputs: Sequence[str]) -> dict[tuple[str, str, str], MetricRecord]:
    return {(m.metric, m.scope, m.key): m for m in run.result.metrics if m.metric in outputs}


def metric_table(runs: Sequence[SnapshotRun], outputs: Sequence[str]) -> list[str]:
    """산출·범위·키마다 스냅샷별 값 · 품질 — 스냅샷을 열로."""
    tables = [_keyed(r, outputs) for r in runs]
    keys = sorted({k for t in tables for k in t}, key=lambda k: (outputs.index(k[0]), k[1], k[2]))
    if not keys:
        return []
    head = "| 산출 | 범위 | 키 | " + " | ".join(r.name for r in runs) + " |"
    lines = [head, "|" + "---|" * (3 + len(runs))]
    for k in keys:
        unit = UNITS.get(k[0], "ratio")
        vals = " | ".join(cell(t.get(k), unit) for t in tables)
        lines.append(f"| {k[0]} | {k[1]} | {k[2] or '-'} | {vals} |")
    return lines


def level_table(runs: Sequence[SnapshotRun]) -> list[str]:
    names = [n for s in CATALOG if s.core and s.table == "levels" for n in s.outputs]
    head = "| 레벨 | 범위 | " + " | ".join(r.name for r in runs) + " |"
    lines = [head, "|" + "---|" * (2 + len(runs))]
    for scope in ("all", "nearest", "0dte"):
        for n in names:
            vals: list[str] = []
            for r in runs:
                rec = next((x for x in r.result.levels if (x.scope, x.name) == (scope, n)), None)
                vals.append(cell(rec, UNITS.get(n, "pt")))
            lines.append(f"| {n} | {scope} | " + " | ".join(vals) + " |")
    return lines


# ── 교차 확인 ────────────────────────────────────────────────────────────────


@dataclass
class Check:
    """교차 확인 하나 — ok False 면 불일치(종료 코드 1). lines: 리포트에 싣는 본문."""

    title: str
    ok: bool
    lines: list[str] = field(default_factory=list[str])


def _close(a: float | None, b: float | None, rel: float = EXACT_REL) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=rel, abs_tol=rel * 1e-3)


def _sign(cp: str) -> float:
    return 1.0 if cp == "C" else -1.0


def _delta(cp: str, F: float, K: float, T: float, s: float) -> float:
    return greeks("c" if cp == "C" else "p", F, K, T, s).delta


def _scope_labels(run: SnapshotRun, metric: str, scope: str) -> list[str]:
    m = run.metric(metric, scope)
    raw = m.payload.get("expiries") if m is not None else None
    return [str(x) for x in raw] if isinstance(raw, list) else []


def check_vanna_charm(runs: Sequence[SnapshotRun]) -> tuple[Check, Check]:
    """§4.1·§4.2 — 종목마다 해석식 Vanna·Charm 대 vollib 델타의 중앙 차분, 범위 all 합(VEX·CEX)
    대 수치 미분으로 다시 모은 합, 시리즈마다 계약당 |CEX| 가 만기에 가까울수록 큰지(상식 점검)."""
    v_lines = [
        "| 스냅샷 | 종목 | Vanna 최대 상대오차 | 허용 밖 | VEX all engine | VEX all 수치 미분 "
        "| 차 |",
        "|---|---|---|---|---|---|---|",
    ]
    c_lines = [
        "| 스냅샷 | 종목 | Charm 최대 상대오차 | 허용 밖 | CEX all engine | CEX all 수치 미분 "
        "| 차 |",
        "|---|---|---|---|---|---|---|",
    ]
    sanity = [
        "",
        "시리즈별 계약당 익스포저(상식 점검 — 만기가 가까울수록 |CEX|/OI 가 커야 한다, §4.2):",
        "",
        "| 스냅샷 | 시리즈 | T(일) | 포함 OI | VEX/OI(원) | CEX/OI(원) |",
        "|---|---|---|---|---|---|",
    ]
    verdicts: list[str] = []  # 표 밖에 — 표 칸 안의 `|` 는 열을 깬다
    v_ok = c_ok = True
    for run in runs:
        evs = run.evals()
        in_all = set(_scope_labels(run, "vex", "all"))
        n = v_bad = c_bad = 0
        v_rel = c_rel = 0.0
        v_sum: list[float] = []
        c_sum: list[float] = []
        per: list[tuple[float, str, int, float, float]] = []
        for label, ev in sorted(evs.items()):
            F = ev.forward.F
            if F is None:
                continue
            T = ev.T
            oi_in = 0
            for o in ev.options:
                if o.greeks is None or o.iv is None or o.iv.sigma is None:
                    continue
                s, K, cp = o.iv.sigma, float(o.quote.strike), o.quote.cp
                n += 1
                oi_in += o.quote.oi
                a_v = vanna(F, K, T, s)
                hs = DERIV_H_SIGMA
                n_v = (_delta(cp, F, K, T, s + hs) - _delta(cp, F, K, T, s - hs)) / (2 * hs)
                a_c = charm(F, K, T, s)
                ht = T * DERIV_H_T
                dd = (_delta(cp, F, K, T + ht, s) - _delta(cp, F, K, T - ht, s)) / (2 * ht)
                n_c = -dd / DAYS_PER_YEAR
                if abs(a_v - n_v) > DERIV_REL * abs(a_v) + 1e-9:
                    v_bad += 1
                if abs(a_c - n_c) > DERIV_REL * abs(a_c) + 1e-9:
                    c_bad += 1
                if abs(a_v) > 1e-6:
                    v_rel = max(v_rel, abs(a_v - n_v) / abs(a_v))
                if abs(a_c) > 1e-6:
                    c_rel = max(c_rel, abs(a_c - n_c) / abs(a_c))
                if label in in_all:
                    w = _sign(cp) * o.quote.oi * OPTION_MULTIPLIER * F
                    v_sum.append(w * n_v * VANNA_SCALE)
                    c_sum.append(w * n_c)
            if oi_in:
                vx, cx = vex([ev]).value, cex([ev]).value
                if vx is not None and cx is not None:
                    per.append((T * 365, label, oi_in, vx / oi_in, cx / oi_in))
        for total, lines, name, bad, rel in (
            (v_sum, v_lines, "vex", v_bad, v_rel),
            (c_sum, c_lines, "cex", c_bad, c_rel),
        ):
            eng = run.metric(name, "all")
            ev_v = eng.value if eng is not None else None
            num = math.fsum(total) if total else None
            agree = _close(ev_v, num, DERIV_REL)
            gap = "-" if ev_v is None or num is None else f"{(ev_v - num) / 1e8:+.2e}억"
            lines.append(
                f"| {run.name} | {n} | {rel:.1e} | {bad} | {fmt(ev_v, 'won')} | "
                f"{fmt(num, 'won')} | {gap} |"
            )
            ok = bad == 0 and agree
            if name == "vex":
                v_ok = v_ok and ok
            else:
                c_ok = c_ok and ok
        per.sort()
        for t_days, label, oi_in, vpo, cpo in per:
            sanity.append(
                f"| {run.name} | {label} | {t_days:.2f} | {oi_in:,} | {vpo:,.0f} | {cpo:,.0f} |"
            )
        if per:
            nearest = per[0]
            biggest = max(per, key=lambda x: abs(x[4]))
            verdicts.append(
                f"- {run.name}: 가장 가까운 만기 {nearest[1]} 의 계약당 CEX 크기가 "
                f"{'가장 크다' if biggest is nearest else f'가장 크지 않다({biggest[1]})'}"
            )
    tol = (
        f"허용: 종목마다 |해석식 − 중앙 차분| ≤ {DERIV_REL:g}·|해석식| + 1e-9 "
        f"(σ ± {DERIV_H_SIGMA:g}, "
        f"T ± T·{DERIV_H_T:g}), 범위 합 상대 {DERIV_REL:g}. 수치 미분은 vollib 해석 델타"
        "(`core.greeks.greeks`)를 σ·T 로 흔든 것 — 해석식 `core.greeks.vanna·charm` 과 따로다"
    )
    vanna_check = Check("Vanna 해석식 대 수치 미분(∂Δ/∂σ)", v_ok, [*v_lines, "", tol])
    charm_check = Check(
        "Charm 해석식 대 수치 미분(−∂Δ/∂T ÷ 365)",
        c_ok,
        [*c_lines, "", tol, *sanity, "", *verdicts],
    )
    return vanna_check, charm_check


def check_gex_pc(runs: Sequence[SnapshotRun]) -> Check:
    """§4.3 — 범위마다 |Σ GEX_put| ÷ Σ GEX_call 을 engine 행사가별 GEX 행(strike_gex)으로 다시."""
    lines = [
        "| 스냅샷 | 범위 | Σ 콜 GEX | Σ 풋 GEX | 다시 잰 비율 | engine | 일치 |",
        "|---|---|---|---|---|---|---|",
    ]
    ok = True
    for run in runs:
        for scope in ("all", "nearest", "0dte"):
            m = run.metric("gex_pc", scope)
            labels = set(_scope_labels(run, "gex_pc", scope))
            rows = [
                r
                for r in run.result.strike_gex
                if f"{r.mrkt_cls or 'M'}:{r.expiry}" in labels and r.forward is not None
            ]
            call = math.fsum(r.gex_call for r in rows)
            put = math.fsum(r.gex_put for r in rows)
            mine = abs(put) / call if call else None
            agree = m is not None and _close(mine, m.value)  # 행이 없어도 불일치
            ok = ok and agree
            lines.append(
                f"| {run.name} | {scope} | {fmt(call, 'won')} | {fmt(put, 'won')} | "
                f"{fmt(mine, 'ratio')} | {cell(m, 'ratio')} | {'예' if agree else '**아니오**'} |"
            )
    return Check("GEX P/C 를 행사가별 GEX 행으로", ok, lines)


def manual_at_delta(points: Sequence[tuple[float, Decimal, float]], target: float) -> float | None:
    """(델타, 행사가, σ) 점들에서 target 델타의 σ — 손 보간(같은 델타면 평균, 아니면 이웃 두 점)."""
    exact = [s for d, _, s in points if d == target]
    if exact:
        return sum(exact) / len(exact)
    below = sorted(p for p in points if p[0] < target)
    above = sorted(p for p in points if p[0] > target)
    if not below or not above:
        return None
    (d0, _, s0), (d1, _, s1) = below[-1], above[0]
    return s0 + (target - d0) * (s1 - s0) / (d1 - d0)


def check_skew(runs: Sequence[SnapshotRun]) -> Check:
    """§5.3 — 시리즈마다 GEX 에 든 종목의 (자체 델타, σ)로 −25Δ 풋·+25Δ 콜 σ 를 손 보간."""
    lines = [
        "| 스냅샷 | 시리즈 | 풋 σ(−25Δ) | 콜 σ(+25Δ) | 손 보간 스큐 | engine | 일치 |",
        "|---|---|---|---|---|---|---|",
    ]
    ok = True
    for run in runs:
        for label, ev in sorted(run.evals().items()):
            m = run.metric("skew_25d", "series", label)
            if ev.forward.F is None or m is None:
                continue
            pts: dict[str, list[tuple[float, Decimal, float]]] = {"C": [], "P": []}
            for o in ev.options:
                if o.greeks is not None and o.iv is not None and o.iv.sigma is not None:
                    pts[o.quote.cp].append((o.greeks.delta, o.quote.strike, o.iv.sigma))
            put = manual_at_delta(pts["P"], -0.25)
            call = manual_at_delta(pts["C"], 0.25)
            mine = None if put is None or call is None else put - call
            agree = _close(mine, m.value)
            ok = ok and agree
            lines.append(
                f"| {run.name} | {label} | {fmt(put, 'iv')} | {fmt(call, 'iv')} | "
                f"{fmt(mine, 'iv_pt')} | {cell(m, 'iv_pt')} | {'예' if agree else '**아니오**'} |"
            )
    return Check("25Δ 스큐 손 보간", ok, lines)


def manual_atm_iv(ev: ExpiryEval) -> float | None:
    """§3.7 손계산 — F 를 사이에 두는 행사가 K1 ≤ F ≤ K2(그 만기 종목 행사가 중)의 콜·풋 σ 평균을
    F 로 선형보간. 한쪽 행사가에 σ 가 없으면 있는 쪽 값, F 가 행사가와 같으면 그 값."""
    F = ev.forward.F
    if F is None:
        return None
    ivs: dict[Decimal, list[float]] = {}
    for o in ev.options:
        ivs.setdefault(o.quote.strike, [])
        if o.iv is not None and o.iv.sigma is not None:
            ivs[o.quote.strike].append(o.iv.sigma)
    lo = [k for k in ivs if k <= F]
    hi = [k for k in ivs if k >= F]
    k1, k2 = (max(lo) if lo else None), (min(hi) if hi else None)

    def at(k: Decimal | None) -> float | None:
        v = ivs.get(k, []) if k is not None else []
        return sum(v) / len(v) if v else None

    v1, v2 = at(k1), at(k2)
    if k1 is not None and k1 == k2:
        return v1
    if v1 is not None and v2 is not None and k1 is not None and k2 is not None:
        return v1 + (F - float(k1)) * (v2 - v1) / float(k2 - k1)
    return v1 if v1 is not None else v2


def check_term(runs: Sequence[SnapshotRun]) -> Check:
    """§5.2 — 기간구조 칸마다 고른 만기의 ATM IV 를 스마일에서 손계산(§3.7)."""
    lines = [
        "| 스냅샷 | 칸 | 만기 | 손계산 ATM IV | engine | 일치 |",
        "|---|---|---|---|---|---|",
    ]
    ok = True
    for run in runs:
        evs = run.evals()
        for slot in ("0dte", "next_weekly", "monthly"):
            m = run.metric("iv_term", "all", slot)
            if m is None:
                continue
            label = str(m.payload.get("series", ""))
            ev = evs.get(label)
            mine = manual_atm_iv(ev) if ev is not None else None
            agree = _close(mine, m.value)
            ok = ok and agree
            lines.append(
                f"| {run.name} | {slot} | {label or '-'} | {fmt(mine, 'iv')} | {cell(m, 'iv')} | "
                f"{'예' if agree else '**아니오**'} |"
            )
    return Check("IV 기간구조 칸의 ATM IV 손계산", ok, lines)


def _chain_legs(run: SnapshotRun, label: str) -> dict[tuple[Decimal, str], ChainRecord]:
    """시리즈의 종목별 체인 행 — 같은 종목에 전광판·보강 행이 다 있으면 전광판(호가가 있다)."""
    cls, _, expiry = label.partition(":")
    cls = "" if cls == "M" else cls
    out: dict[tuple[Decimal, str], ChainRecord] = {}
    for r in run.inp.chain:
        if (r.mrkt_cls, r.expiry) != (cls, expiry):
            continue
        k = (r.strike, r.cp)
        if k not in out or (out[k].source != "board" and r.source == "board"):
            out[k] = r
    return out


def brute_max_pain(
    oi: Mapping[tuple[Decimal, str], int], forward: float | None
) -> tuple[Decimal | None, int]:
    """§6.6 식 그대로 — 후보(OI 행이 있는 행사가)마다 Σ 콜 OI·max(K − K′, 0) + Σ 풋 OI·max(K′ − K,
    0), 최소(동률은 F 에 가까운 쪽, 그다음 낮은 쪽). OI 가 전부 0 이면 None. (행사가, 동률 수)."""
    cands = sorted({k for k, _ in oi})
    if not cands or not any(oi.values()):
        return None, 0
    pains: dict[Decimal, Fraction] = {}
    for K in cands:
        total = Fraction(0)
        for (k2, cp), n in oi.items():
            gap = (K - k2) if cp == "C" else (k2 - K)
            if gap > 0:
                total += n * Fraction(gap)
        pains[K] = total
    best = min(pains.values())
    tied = [k for k in cands if pains[k] == best]
    if forward is None:
        return tied[0], len(tied)
    return min(tied, key=lambda k: (abs(float(k) - forward), k)), len(tied)


def check_pcr_max_pain_snapshots(runs: Sequence[SnapshotRun]) -> tuple[Check, Check]:
    """§6.5·§6.6 — 시리즈마다 체인 행(전광판 먼저)으로 PCR(OI·거래량)·맥스페인을 다시."""
    p_lines = [
        "| 스냅샷 | 시리즈 | 풋/콜 OI | 다시 잰 PCR(OI) | engine | 풋/콜 거래량 "
        "| 다시 잰 PCR(거래량) "
        "| engine | 일치 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    m_lines = [
        "| 스냅샷 | 시리즈 | 후보 | 다시 잰 맥스페인 | 동률 | engine | 일치 |",
        "|---|---|---|---|---|---|---|",
    ]
    p_ok = m_ok = True
    for run in runs:
        evs = run.evals()
        for label in sorted(evs):
            legs = _chain_legs(run, label)
            oi = {k: r.oi for k, r in legs.items() if r.oi is not None}
            po = sum(n for (_, cp), n in oi.items() if cp == "P")
            co = sum(n for (_, cp), n in oi.items() if cp == "C")
            vol = {k: r.volume for k, r in legs.items() if r.volume is not None}
            pv = sum(n for (_, cp), n in vol.items() if cp == "P")
            cv = sum(n for (_, cp), n in vol.items() if cp == "C")
            r_oi = float(Fraction(po, co)) if co else None
            r_vol = float(Fraction(pv, cv)) if cv else None
            e_oi = run.metric("pcr_oi", "series", label)
            e_vol = run.metric("pcr_volume", "series", label)
            agree = _close(r_oi, e_oi.value if e_oi else None) and _close(
                r_vol, e_vol.value if e_vol else None
            )
            p_ok = p_ok and agree
            p_lines.append(
                f"| {run.name} | {label} | {po:,}/{co:,} | {fmt(r_oi, 'ratio')} | "
                f"{cell(e_oi, 'ratio')} | {pv:,}/{cv:,} | {fmt(r_vol, 'ratio')} | "
                f"{cell(e_vol, 'ratio')} | {'예' if agree else '**아니오**'} |"
            )
            strike, tied = brute_max_pain(oi, evs[label].forward.F)
            e_mp = run.metric("max_pain", "series", label)
            mine = None if strike is None else float(strike)
            agree = _close(mine, e_mp.value if e_mp else None)
            m_ok = m_ok and agree
            m_lines.append(
                f"| {run.name} | {label} | {len({k for k, _ in oi})} | {fmt(mine, 'pt')} | "
                f"{tied} | {cell(e_mp, 'pt')} | {'예' if agree else '**아니오**'} |"
            )
    note = (
        "후보 = 체인 행이 온 행사가(스냅샷엔 마스터 상장 행사가가 없다 — engine 도 같은 입력), "
        "동률은 그 만기 F 에 가까운 쪽·낮은 쪽. 합은 정수·분수로 정확히"
    )
    return (
        Check("PCR 을 체인 행으로", p_ok, p_lines),
        Check("맥스페인을 식 그대로(전수)", m_ok, [*m_lines, "", note]),
    )


_KRX_NAME = re.compile(r"^코스피200 ([CP]) ([0-9]{6}) +([0-9,]+\.[0-9]+) \((정규|야간)\)$")


def krx_legs(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[tuple[Decimal, str], tuple[int, int]]]:
    """KRX 옵션 일별 원문을 따로 푼다(`data.krx.models` 를 쓰지 않는다) — 코스피200 월물 정규(주간)
    행만, 만기 → (행사가, 콜풋) → (OI, 거래량)."""
    out: dict[str, dict[tuple[Decimal, str], tuple[int, int]]] = {}
    for r in rows:
        if r.get("PROD_NM") != "코스피200 옵션":
            continue
        m = _KRX_NAME.match(str(r.get("ISU_NM", "")).strip())
        if m is None or m.group(4) != "정규":
            continue
        cp, expiry, strike = m.group(1), m.group(2), Decimal(m.group(3).replace(",", ""))
        oi = int(str(r.get("ACC_OPNINT_QTY") or "0").replace(",", ""))
        vol = int(str(r.get("ACC_TRDVOL") or "0").replace(",", ""))
        out.setdefault(expiry, {})[(strike, cp)] = (oi, vol)
    return out


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"{path.name}: fixture 를 읽지 못했다 ({type(e).__name__})") from None


def check_krx_pcr_max_pain(opt_daily: Mapping[str, Any]) -> tuple[Check, Check]:
    """§6.5·§6.6 — KRX 일별 fixture(2026-09-23)를 따로 푼 행으로 PCR·맥스페인(식 그대로) 대
    `data.krx.models.parse_option_rows` → `core.metrics.flow.pcr`·`max_pain`."""
    p_lines = [
        "| 거래일 | 만기 | 풋/콜 OI | 손계산 PCR(OI) | core | 풋/콜 거래량 "
        "| 손계산 PCR(거래량) | core "
        "| 일치 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    m_lines = [
        "| 거래일 | 만기 | 후보 | 손계산 맥스페인 | core | 일치 |",
        "|---|---|---|---|---|---|",
    ]
    p_ok = m_ok = True
    for day in sorted(k for k in opt_daily if k.isdigit()):
        raw = opt_daily[day]
        rows = cast(list[Mapping[str, Any]], raw if isinstance(raw, list) else [])
        mine = krx_legs(rows)
        parsed, _ = parse_option_rows(rows)
        core_rows = [r for r in parsed if r.family == "kospi200" and r.session == "day"]
        for expiry in sorted(mine):
            legs = mine[expiry]
            po = sum(oi for (_, cp), (oi, _) in legs.items() if cp == "P")
            co = sum(oi for (_, cp), (oi, _) in legs.items() if cp == "C")
            pv = sum(v for (_, cp), (_, v) in legs.items() if cp == "P")
            cv = sum(v for (_, cp), (_, v) in legs.items() if cp == "C")
            h_oi = float(Fraction(po, co)) if co else None
            h_vol = float(Fraction(pv, cv)) if cv else None
            mine_rows = [r for r in core_rows if r.expiry == expiry]
            got = pcr((r.cp, r.acc_opnint_qty, r.acc_trdvol) for r in mine_rows)
            agree = _close(h_oi, got.oi.value) and _close(h_vol, got.volume.value)
            p_ok = p_ok and agree
            p_lines.append(
                f"| {day} | {expiry} | {po:,}/{co:,} | {fmt(h_oi, 'ratio')} | "
                f"{fmt(got.oi.value, 'ratio')} · {got.oi.quality} | {pv:,}/{cv:,} | "
                f"{fmt(h_vol, 'ratio')} | {fmt(got.volume.value, 'ratio')} · {got.volume.quality} "
                f"| {'예' if agree else '**아니오**'} |"
            )
            strike, _ = brute_max_pain({k: oi for k, (oi, _) in legs.items()}, None)
            mp = max_pain(((r.strike, r.cp, r.acc_opnint_qty or 0) for r in mine_rows), None, None)
            h = None if strike is None else float(strike)
            c = None if mp.strike is None else float(mp.strike)
            agree = _close(h, c)
            m_ok = m_ok and agree
            m_lines.append(
                f"| {day} | {expiry} | {len({k for k, _ in legs})} | {fmt(h, 'pt')} | "
                f"{fmt(c, 'pt')} · {mp.quality} | {'예' if agree else '**아니오**'} |"
            )
    note = (
        "fixture = KRX `/drv/opt_bydd_trd` 원본 발췌(tests/fixtures/krx/opt_daily.json — "
        "행이 적다: "
        "값의 뜻보다 원문 → 파서 → core 경로의 대조). 손계산은 `ISU_NM` 을 정규식으로 따로 풀고 "
        "합을 분수로, 맥스페인은 후보마다 식 그대로 — F 없음(동률이면 낮은 쪽)"
    )
    return (
        Check("PCR — KRX 일별 fixture 독립 재계산", p_ok, [*p_lines, "", note]),
        Check("맥스페인 — KRX 일별 fixture 독립 재계산", m_ok, m_lines),
    )


def check_krx_daily(
    opt_daily: Mapping[str, Any], fut_daily: Mapping[str, Any], cal: TradingCalendar
) -> tuple[Check, Check, Check]:
    """§5.4·§5.5 — KRX 일별 fixture 로 일별 ATM IV(§1.3 패리티 F·§3.7 IMP_VOLT) 손계산, 그 값
    하나로는 IV 랭크가 null(too_few_days), HV20 은 fixture 에 코스피200 선물 정산가가 없어 null.
    식 대조는 SYNTHETIC 이력(아래) — 손으로 min·max·개수·표본표준편차."""
    raw = opt_daily.get("20260923")
    rows = cast(list[Mapping[str, Any]], raw if isinstance(raw, list) else [])
    parsed, _ = parse_option_rows(rows)
    krx = [
        KrxIvRow(
            r.strike,
            r.cp,
            r.tdd_clsprc,
            None if r.imp_volt is None else float(r.imp_volt),
            r.acc_trdvol or 0,
        )
        for r in parsed
        if r.family == "kospi200" and r.session == "day" and r.expiry == "202610"
    ]
    futs = cast(list[Mapping[str, Any]], fut_daily.get("rows") or [])
    settle = next(
        (
            Decimal(str(r["SETL_PRC"]))
            for r in futs
            if r.get("MKT_NM") == "정규" and "202610" in str(r.get("ISU_NM")) and r.get("SETL_PRC")
        ),
        None,
    )
    if settle is None:
        raise InputError(f"{KRX_FUT.name}: 202610 주간 정산가가 없다(ATM 기준)")
    s_ref = settle
    got = krx_atm_iv(krx, s_ref)
    traded = [r for r in krx if r.volume > 0 and r.close is not None and r.iv_pct is not None]
    # 손계산: 거래 있는 행사가의 패리티 F = K + C − P, ATM IV = F 를 사이에 두는 행사가의
    # IMP_VOLT 평균
    by_k: dict[Decimal, dict[str, KrxIvRow]] = {}
    for r in traded:
        by_k.setdefault(r.strike, {})[r.cp] = r
    pairs = {k: v for k, v in by_k.items() if "C" in v and "P" in v}
    h_f: float | None = None
    h_iv: float | None = None
    if len(pairs) == 1:
        ((k, legs),) = pairs.items()
        c, p = legs["C"].close, legs["P"].close
        if c is not None and p is not None:
            h_f = float(k + c - p)
        ivs = [x.iv_pct / 100 for x in legs.values() if x.iv_pct is not None]
        h_iv = sum(ivs) / len(ivs)
    f_ok = _close(h_f, got.forward.F)
    iv_ok = _close(h_iv, got.atm.value)
    atm_lines = [
        "| 거래일 | 만기 | 거래 있는 행 | ATM 기준 | 손계산 F | core F | 손계산 ATM IV | core "
        "| 일치 |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| 2026-09-23 | 202610 | {len(traded)} | {s_ref} | {fmt(h_f, 'pt')} | "
        f"{fmt(got.forward.F, 'pt')} · {got.forward.quality} | {fmt(h_iv, 'iv')} | "
        f"{fmt(got.atm.value, 'iv')} · {got.atm.quality} ({', '.join(got.atm.reasons) or '-'}) | "
        f"{'예' if f_ok and iv_ok else '**아니오**'} |",
        "",
        "fixture 에 거래 있는 정규 행사가가 1100 하나뿐 — F = 1100 + C − P, F 가 그 위라 §3.7 "
        "한쪽 행사가만(`one_side` estimated). ATM 기준(§1.3 ATM 선정)은 fixture 의 미니 202610 "
        "주간 "
        "정산가(코스피200 선물 행이 fixture 에 없다 — 행사가 하나라 결과는 같다)",
    ]
    atm_check = Check("KRX 일별 ATM IV 손계산(§5.4 과거 값)", f_ok and iv_ok, atm_lines)

    # IV 랭크: fixture 하루 → n = 1 < 20 null. 식 대조: SYNTHETIC 25거래일
    fx = (
        None
        if got.atm.value is None
        else iv_rank([], DailyIv(date(2026, 9, 23), got.atm.value, "krx", got.atm.quality), cal)
    )
    end = date(2026, 9, 23)
    days: list[date] = [end]
    while len(days) < 25:
        days.append(cal.prev_trading_day(days[-1]))
    days.reverse()
    values = [0.20 + 0.01 * ((7 * i) % 11) for i in range(len(days))]
    hist = [DailyIv(d, v, "self", "ok") for d, v in zip(days[:-1], values[:-1], strict=True)]
    today = DailyIv(days[-1], values[-1], "self", "ok")
    r = iv_rank(hist, today, cal)
    lo, hi, x = min(values), max(values), values[-1]
    h_rank = (x - lo) / (hi - lo)
    h_pct = len([v for v in values if v < x]) / len(values)
    rank_ok = (
        fx is not None
        and fx.rank is None
        and "too_few_days" in fx.reasons
        and _close(h_rank, r.rank)
        and _close(h_pct, r.percentile)
    )
    fx_line = (
        "- fixture(2026-09-23 KRX 값): ATM IV 가 없어 랭크를 볼 수 없다"
        if fx is None
        else f"- fixture(2026-09-23 KRX 값 하나): n = {fx.n} → 랭크·퍼센타일 "
        f"{fmt(fx.rank, 'ratio')}·{fmt(fx.percentile, 'ratio')} ({', '.join(fx.reasons)}) — "
        "명세대로 null(n < 20)"
    )
    rank_lines = [
        fx_line,
        f"- SYNTHETIC 25거래일({days[0]} ~ {days[-1]}, σ = 0.20 + 0.01·((7i) mod 11)): 손계산 "
        f"min {lo:.2f}·max {hi:.2f}·오늘 {x:.2f} → 랭크 {h_rank:.6f}·퍼센타일 {h_pct:.6f} "
        f"({len([v for v in values if v < x])}/{len(values)}), core {fmt(r.rank, 'ratio')}·"
        f"{fmt(r.percentile, 'ratio')} · {r.quality} ({', '.join(r.reasons)}) — "
        f"{'일치' if rank_ok else '**불일치**'}",
        "- 252일 실측 이력 대조는 Phase 6 백필(KRX `IMP_VOLT`) 뒤",
    ]
    rank_check = Check("IV 랭크·퍼센타일 손계산", rank_ok, rank_lines)

    # HV20: fixture 는 코스피200 선물 정산가가 없다(미니·야간 행) → no_data. 식 대조: SYNTHETIC 21일
    hv_fx = realized_vol([], {"202612": date(2026, 12, 10)}, cal, end=end)
    hv_days: list[date] = [end]
    while len(hv_days) < 21:
        hv_days.append(cal.prev_trading_day(hv_days[-1]))
    hv_days.reverse()
    rets = [0.012 * math.sin(1.7 * i) for i in range(1, len(hv_days))]
    prices = [1100.0]
    for x_ in rets:
        prices.append(prices[-1] * math.exp(x_))
    settles = [Settlement(d, "202612", p) for d, p in zip(hv_days, prices, strict=True)]
    hv = realized_vol(settles, {"202612": date(2026, 12, 10)}, cal, end=end)
    logs = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    mean = sum(logs) / len(logs)
    h_hv = math.sqrt(sum((v - mean) ** 2 for v in logs) / (len(logs) - 1)) * math.sqrt(252)
    hv_ok = (
        hv_fx.value is None
        and _close(h_hv, hv.value)
        and _close(statistics.stdev(logs) * math.sqrt(252), hv.value)
    )
    hv_lines = [
        f"- fixture(tests/fixtures/krx/fut_daily.json — 미니·야간 행뿐): HV20 "
        f"{fmt(hv_fx.value, 'iv')} ({', '.join(hv_fx.reasons)}) — 명세대로 null(20일 안 됨)",
        f"- SYNTHETIC 21거래일 정산가(202612, ln 수익률 0.012·sin(1.7i)): 손계산 "
        f"√(Σ(r − r̄)² / 19)·√252 = {h_hv:.6%}, core {fmt(hv.value, 'iv')} — "
        f"{'일치' if hv_ok else '**불일치**'}",
    ]
    hv_check = Check("HV20 손계산", hv_ok, hv_lines)
    return atm_check, rank_check, hv_check


def find_minute_outputs(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """probe 결과 JSON 들에서 분봉 조회 output1(선물 필드가 다 있는 객체)을 찾는다 — (파일, 객체).
    같은 값은 한 번."""
    need = {"futs_shrn_iscd", "futs_prpr", "basis", "kospi200_nmix", "hts_thpr"}
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, ...]] = set()

    def walk(src: str, node: object) -> None:
        if isinstance(node, dict):
            d = cast(dict[str, Any], node)
            if need <= d.keys():
                k = tuple(str(d[x]) for x in sorted(need))
                if k not in seen:
                    seen.add(k)
                    out.append((src, d))
                return
            for v in d.values():
                walk(src, v)
        elif isinstance(node, list):
            for v in cast(list[Any], node):
                walk(src, v)

    if root.is_dir():
        for p in sorted(root.rglob("*.json")):
            try:
                walk(_rel(p, root.parent), json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


def check_futures(responses: Sequence[tuple[str, dict[str, Any]]]) -> Check:
    """§7 — KIS basis 대 자체 선물가 − 지수(시장 베이시스)·이론가 − 지수(이론 베이시스)를 Decimal
    로 따로, core 교차검증(이론 베이시스 − KIS basis, 2026-09-30 실측 반영) 결과와 대조. 구현
    불일치(core 의 시장 베이시스·차·판정이 손계산과 다름)만 실패."""
    lines = [
        "| 응답 | 종목 | 선물가 | 지수 | 이론가 | KIS basis | 선물가 − 지수 | 이론가 − 지수 | "
        "KIS − 시장 | KIS − 이론 | core 교차검증(≤ 0.05) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    ok = True
    theory_like = market_like = 0
    for src, d in responses:
        fm = futures_metrics(quote_of(d))
        prpr, nmix = Decimal(str(d["futs_prpr"])), Decimal(str(d["kospi200_nmix"]))
        thpr, basis = Decimal(str(d["hts_thpr"])), Decimal(str(d["basis"]))
        market, theory = prpr - nmix, thpr - nmix
        g_m, g_t = basis - market, basis - theory
        mine_check = abs(g_t) <= BASIS_CHECK_TOLERANCE
        agree = fm.self_basis == market and fm.basis_gap == -g_t and fm.basis_check == mine_check
        ok = ok and agree
        theory_like += abs(g_t) <= Decimal("0.01")
        market_like += abs(g_m) <= BASIS_CHECK_TOLERANCE
        lines.append(
            f"| {src} | {d['futs_shrn_iscd']} | {prpr} | {nmix} | {thpr} | {basis} | {market} | "
            f"{theory} | {g_m:+} | {g_t:+} | {'통과' if fm.basis_check else '실패'}"
            f"{'' if agree else ' **(손계산과 다름)**'} |"
        )
    lines += [
        "",
        f"응답 {len(responses)}건 중 KIS basis ≈ 이론 베이시스(±0.01) {theory_like}건, ≈ 시장 "
        f"베이시스(±{BASIS_CHECK_TOLERANCE}) {market_like}건 — 그래서 §7 교차검증은 이론 "
        "베이시스와 하고 시장 베이시스는 자체 계산으로 표시한다"
        "(2026-09-30, metrics §7 [확인 필요])",
    ]
    return Check("선물 KIS basis 대 자체 베이시스", ok, lines)


def check_investor(investor: Mapping[str, Any]) -> tuple[Check, Check]:
    """§6.2·§6.3 — KIS 투자자별 fixture(2026-09-28 13:25, 7조합): 보이는 네 투자자의 순매수 =
    매수 − 매도(원문 일관성), 딜러 점검 = 증권 콜 순매수 합 > 0 이고 풋 순매수 합 < 0 을 손으로."""
    pairs = cast(dict[str, Any], investor.get("pairs") or {})
    lines = [
        "| 조합 | 외국인 | 개인 | 기관계 | 증권 | 순매수 = 매수 − 매도 |",
        "|---|---|---|---|---|---|",
    ]
    ok = True
    scrt: dict[str, int | None] = {}
    for key in sorted(pairs):
        out = cast(list[Mapping[str, Any]], pairs[key].get("output") or [])
        if not out:
            continue
        row = InvestorRow.model_validate(out[0])
        nets: list[str] = []
        consistent = True
        for inv in ("frgn", "prsn", "orgn", "scrt"):
            f = row.flow(inv)
            nets.append("-" if f.net_qty is None else f"{f.net_qty:+,}")
            raw = out[0]
            buy = int(str(raw.get(f"{inv}_shnu_vol", "0")))
            sell = int(str(raw.get(f"{inv}_seln_vol", "0")))
            consistent = consistent and f.net_qty == buy - sell
        scrt[key] = row.flow("scrt").net_qty
        ok = ok and consistent
        lines.append(
            f"| {key} | " + " | ".join(nets) + f" | {'예' if consistent else '**아니오**'} |"
        )
    calls = ("K2I/OC01", "WKM/OC05", "WKI/OC04")
    puts = ("K2I/OP01", "WKM/OP05", "WKI/OP04")
    dc = dealer_check((scrt.get(k) for k in calls), (scrt.get(k) for k in puts))
    c_known = [v for k in calls if (v := scrt.get(k)) is not None]
    p_known = [v for k in puts if (v := scrt.get(k)) is not None]
    h_call, h_put = sum(c_known), sum(p_known)
    h_ok = h_call > 0 and h_put < 0
    agree = dc.call_net == h_call and dc.put_net == h_put and dc.consistent == h_ok
    d_lines = [
        f"- 증권(딜러 프록시) 콜 순매수 {' + '.join(f'{scrt.get(k)}' for k in calls)} "
        f"= {h_call:+,}, "
        f"풋 {' + '.join(f'{scrt.get(k)}' for k in puts)} = {h_put:+,} → 손 판정 "
        f"{'일치(콜 > 0, 풋 < 0)' if h_ok else '불일치'}, core {dc.consistent} · {dc.quality} "
        f"({', '.join(dc.reasons) or '-'}) — {'같다' if agree else '**다르다**'}",
        "- 장중 한 시점(13:25) 값이라 판정의 뜻은 없다 — engine 은 그날 주간 마지막 행으로 "
        "POST_DAY 에 "
        "한 번(§6.3)",
    ]
    return (
        Check(
            "투자자별 순매수 — KIS 원문 일관성",
            ok,
            [
                *lines,
                "",
                "fixture: tests/fixtures/kis/"
                "investor.json(2026-09-28 13:25, 투자자 12종 중 보이는 넷). engine 은 값을 그대로 "
                "옮긴다",
            ],
        ),
        Check("딜러 가정 점검 손 판정", agree, d_lines),
    )


def check_oi_changes(runs: Sequence[SnapshotRun]) -> Check:
    """§6.7 — 두 스냅샷 사이 칸마다 증감 = 뒤 OI − 앞 OI(체인 행에서 따로), 이상치 짝 수."""
    if len(runs) < 2:
        return Check("OI 증감 = 두 스냅샷 OI 차", True, ["스냅샷이 한 장이라 증감이 없다"])
    a, b = runs[0], runs[-1]

    def oi_map(run: SnapshotRun) -> dict[tuple[str, str, Decimal, str], int]:
        out: dict[tuple[str, str, Decimal, str], int] = {}
        for label in run.evals():
            cls, _, expiry = label.partition(":")
            for (k, cp), r in _chain_legs(run, label).items():
                if r.oi is not None:
                    out[("" if cls == "M" else cls, expiry, k, cp)] = r.oi
        return out

    before, after = oi_map(a), oi_map(b)
    cells = [c for c in b.oi if c.change is not None]
    wrong = [
        c
        for c in cells
        if after.get((c.mrkt_cls, c.expiry, c.strike, c.cp), -1)
        - before.get((c.mrkt_cls, c.expiry, c.strike, c.cp), -1)
        != c.change
    ]
    mine_changed = sum(1 for k, v in after.items() if k in before and v != before[k])
    by_series: dict[str, list[OiChangeRecord]] = {}
    for c in cells:
        by_series.setdefault(f"{c.mrkt_cls or 'M'}:{c.expiry}", []).append(c)
    lines = [
        f"{a.name} → {b.name}: engine 칸 {len(cells)}(0 이 아닌 증감), 체인 행으로 센 바뀐 종목 "
        f"{mine_changed}, 증감이 OI 차와 다른 칸 {len(wrong)}, 이상치 "
        f"{sum(c.outlier for c in cells)}",
        "",
        "| 시리즈 | 칸 | Σ증가 | Σ감소 | 가장 큰 증감 |",
        "|---|---|---|---|---|",
    ]
    for label, cs in sorted(by_series.items()):
        up = sum(c.change or 0 for c in cs if (c.change or 0) > 0)
        down = sum(c.change or 0 for c in cs if (c.change or 0) < 0)
        big = max(cs, key=lambda c: abs(c.change or 0))
        lines.append(
            f"| {label} | {len(cs)} | {up:+,} | {down:,} | {big.strike} {big.cp} {big.change:+,} |"
        )
    ok = not wrong and len(cells) == mine_changed
    return Check("OI 증감 = 두 스냅샷 OI 차", ok, lines)


# ── [확인 필요] ───────────────────────────────────────────────────────────────

_HEAD = re.compile(r"^(#{1,3}) (?:(\d+(?:\.\d+)?)\.? )?")
_MARK = re.compile(r"\[확인 필요[^\]]*\]")


def spec_sections(text: str) -> dict[str, str]:
    """docs/metrics.md → 절 번호(§4.1·§7) → 그 절 본문(다음 머리글까지 — 하위 절은 따로)."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m and line.startswith("#"):
            cur = f"§{m.group(2)}" if m.group(2) else None
            continue
        if cur is not None:
            out.setdefault(cur, []).append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*`]", "", text)).strip()


def confirm_items(section: str, width: int = 70) -> list[str]:
    """절 본문의 [확인 필요] 표시마다 문맥 한 줄(마크다운 기호 뺌). 표시 바로 뒤가 `:` 이면(`기본값
    [확인 필요]: 목록`) 뜻이 뒤에 있어 앞 몇 글자 + 뒤 width 자, 아니면 앞 width 자(같은 줄의 앞
    표시 뒤부터)."""
    items: list[str] = []
    for line in section.splitlines():
        marks = list(_MARK.finditer(line))
        for i, m in enumerate(marks):
            start = marks[i - 1].end() if i else 0
            end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
            before = _plain(line[start : m.start()]).strip(" -—:·(,")
            after = _plain(line[m.end() : end])
            tag = f"[{m.group(0)[1:-1]}]"
            if after.startswith(":"):
                head = before[-20:].lstrip() if len(before) > 20 else before
                tail = after[1:].strip()
                tail = tail if len(tail) <= width else tail[:width].rstrip() + "…"
                items.append(f"{'…' if len(before) > 20 else ''}{head} {tag}: {tail}".strip())
                continue
            if len(before) > width:
                before = "…" + before[-width:].lstrip()
            items.append(f"{before} {tag}" if before else tag)
    return items


# ── 리포트 ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Inputs:
    snapshots: tuple[Path, ...]
    full: bool  # 로컬 전체 스냅샷(아니면 작은 발췌 fixture)
    probe_dir: Path


def resolve_inputs(probe_dir: Path) -> Inputs:
    full = tuple(probe_dir / "runs" / r / "chain_snapshot.json" for r in SNAPSHOT_RUNS)
    if all(p.is_file() for p in full):
        return Inputs(full, True, probe_dir)
    return Inputs(CORE_FIXTURES, False, probe_dir)


def _rel(p: Path, base: Path) -> str:
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        return p.name


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


CORE_GROUP = "Phase 2 핵심"
FLAG_GROUP = "기능 플래그"


@dataclass
class Report:
    """failed_tests: 지표·묶음별 실패 합(수집 실패 포함) + 매핑 파일 밖 파일 통째 실패(pytest 내부
    오류 등). missing: 시험을 돌렸는데 명세 테스트가 0 건인 지표·묶음(`CORE_GROUP`·`FLAG_GROUP`)."""

    text: str
    failed_tests: int
    mismatches: list[str]
    missing: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return self.failed_tests == 0 and not self.mismatches and not self.missing


def _check_block(c: Check) -> list[str]:
    head = f"**{c.title}** — {'일치' if c.ok else '**불일치**'}"
    return [head, "", *c.lines, ""]


def build_report(
    inputs: Inputs,
    cases: Sequence[TestCase] | None,
    *,
    cal: TradingCalendar,
    features: Features,
    metrics_md: str,
) -> Report:
    runs = evaluate_snapshots(inputs.snapshots, cal)
    opt_daily = cast(Mapping[str, Any], _load(KRX_OPT))
    fut_daily = cast(Mapping[str, Any], _load(KRX_FUT))
    minute = cast(Mapping[str, Any], _load(KIS_MINUTE))
    investor = cast(Mapping[str, Any], _load(KIS_INVESTOR))
    base = inputs.probe_dir.parent
    responses: list[tuple[str, dict[str, Any]]] = [
        (_rel(KIS_MINUTE, ROOT), cast(dict[str, Any], minute["output1"]))
    ]
    seen = {json.dumps(responses[0][1], sort_keys=True)}
    for src, d in find_minute_outputs(inputs.probe_dir):
        k = json.dumps(d, sort_keys=True)
        if k not in seen:
            seen.add(k)
            responses.append((src, d))

    vanna_c, charm_c = check_vanna_charm(runs)
    pcr_snap, mp_snap = check_pcr_max_pain_snapshots(runs)
    pcr_krx, mp_krx = check_krx_pcr_max_pain(opt_daily)
    atm_c, rank_c, hv_c = check_krx_daily(opt_daily, fut_daily, cal)
    inv_c, dealer_c = check_investor(investor)
    checks: dict[str, list[Check]] = {
        "vex": [vanna_c],
        "cex": [charm_c],
        "gex_pc": [check_gex_pc(runs)],
        "iv_term": [check_term(runs)],
        "skew_25d": [check_skew(runs)],
        "atm_iv_daily": [atm_c],
        "iv_rank": [rank_c],
        "iv_percentile": [rank_c],
        "iv_hv": [hv_c],
        "investor_flow": [inv_c],
        "dealer_check": [dealer_c],
        "pcr": [pcr_snap, pcr_krx],
        "max_pain": [mp_snap, mp_krx],
        "oi_changes": [check_oi_changes(runs)],
        "futures": [check_futures(responses)],
    }
    sections = spec_sections(metrics_md)
    tested = cases is not None
    all_cases = cases or []
    tallies = {e.flag: tally(all_cases, e.tests) for e in ENTRIES}
    core_t = tally(all_cases, CORE_TESTS)
    flag_t = tally(all_cases, FLAG_TESTS)
    groups = {**tallies, CORE_GROUP: core_t, FLAG_GROUP: flag_t}
    # 시험을 돌렸는데 0 건 — 파일을 못 모았거나(수집 실패로 pytest 가 멈춤) JUnit 이 일부뿐
    missing = [g for g, t in groups.items() if tested and t.total == 0]
    mapped = set(spec_test_files())
    stray = sorted(
        {c.label for c in all_cases if c.whole and c.outcome == "failed" and c.file not in mapped}
    )
    failed = sum(t.failed for t in groups.values()) + len(stray)
    mismatches = sorted(
        {f"{flag}: {c.title}" for flag, cs in checks.items() for c in cs if not c.ok}
    )

    def tests_cell(t: Tally) -> str:
        return "**없음**" if t.total == 0 else t.text()

    snap_kind = (
        "로컬 전체 스냅샷(probe_out — git 제외)"
        if inputs.full
        else ("git 에 있는 작은 발췌(tests/fixtures/validation — 로컬 전체 스냅샷이 없을 때)")
    )
    lines: list[str] = [
        "# 지표별 검증 리포트 (Phase 3 완료 기준)",
        "",
        f"> 자동 생성 — `{REGENERATE}`. 손으로 고치지 않는다. 설계 `docs/phase3_design.md` §5, "
        "명세 `docs/metrics.md` §4~§8, 기능 플래그 `config/features.yaml`.",
        "",
        "- 스냅샷: "
        + snap_kind
        + " — "
        + ", ".join(
            f"`{_rel(p, base if inputs.full else ROOT)}` ({_sha(p)})" for p in inputs.snapshots
        ),
        "  - engine 사이클(`services.engine.evaluate.evaluate_cycle` + engine 등록부)에 시각 "
        "순으로 — "
        "확정 베이시스·OI 추적을 다음 장에 넘긴다. 두 장 모두 2026-09-28(월) 주간, 최근접·0DTE = "
        "WKM 260904(그날 15:20 만기)",
        "- fixture: "
        + ", ".join(f"`{_rel(p, ROOT)}`" for p in (KRX_OPT, KRX_FUT, KIS_MINUTE, KIS_INVESTOR))
        + (f", 분봉 응답 {len(responses) - 1}건 더(probe_out)" if len(responses) > 1 else ""),
        "- 명세 테스트: "
        + (
            f"pytest {len(all_cases)}건 — 지표마다 그 지표의 단위·속성·engine 시험(`scripts/"
            "metric_report.py` `ENTRIES`)"
            if tested
            else "돌리지 않음(`--no-tests`)"
        ),
        f"- 판정: 명세 테스트 실패 {failed}"
        + (f" · 명세 테스트 없음 {len(missing)} ({', '.join(missing)})" if missing else "")
        + f" · 교차 확인 불일치 {len(mismatches)}"
        + (" — " + "; ".join(mismatches) if mismatches else ""),
        *[f"  - 실행 오류: `{x}`" for x in stray],
        "- 새 지표는 기본 `shadow`. `visible` 로 올리는 조건(metrics §8): 이 리포트의 교차 "
        "확인·육안 검토 통과 + ⏱ 1주 섀도 운영 무오류(`scripts.shadow_report` — 전용 앱키로 "
        "라이브 녹화가 쌓인 "
        "뒤, PLAN §12)",
        "",
        "## 요약",
        "",
        "| 플래그 | 지표 | metrics.md | 지금 | 명세 테스트 | 스냅샷 | 교차 확인 "
        "| 남은 [확인 필요] |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in ENTRIES:
        s = e.spec
        outs = s.outputs if s.table == "metrics" else ()
        has_snap = any(m.metric in outs for r in runs for m in r.result.metrics) or (
            s.table == "oi_changes" and any(r.oi for r in runs)
        )
        cs = checks.get(e.flag, [])
        c_text = "-" if not cs else ("일치" if all(c.ok for c in cs) else "**불일치**")
        n_confirm = len(confirm_items(sections.get(s.spec, "")))
        lines.append(
            f"| {e.flag} | {e.title} | {s.spec} | {features.flag(e.flag)} | "
            f"{tests_cell(tallies[e.flag]) if tested else '-'} | {'있음' if has_snap else '-'} | "
            f"{c_text} | {n_confirm} |"
        )
    lines += [
        "",
        "## Phase 2 핵심 (visible) — 스냅샷 값",
        "",
        "명세 테스트(부호 고정·GEX·레벨·골든·engine 평가): "
        + (f"{tests_cell(core_t)} ({', '.join(core_t.files)})" if tested else "-"),
        "",
        *metric_table(runs, ["net_gex", "dex", "atm_iv", "expiry_gamma"]),
        "",
        *level_table(runs),
        "",
        "값 표기: 금액 억원(원 ÷ 1e8, GEX 는 원/1%), IV 연율 %, 스큐 %p, 가격·레벨 pt. 품질 뒤 "
        "괄호는 "
        "사유(둘까지). F 없는 시리즈(M:202611·WKM:261001 — 검증 수정 4 열린 문제, 거래 없는 "
        "행사가)가 범위 all 을 invalid 로 만든다",
        "",
        "## 지표별",
        "",
    ]
    for e in ENTRIES:
        s = e.spec
        t = tallies[e.flag]
        lines += [f"### {e.flag} — {s.spec} {e.title}", ""]
        lines.append(
            "- 플래그: 기본 "
            + f"`{s.default}`, 지금 `{features.flag(e.flag)}` · 산출 "
            + ", ".join(f"`{o}`" for o in s.outputs)
            + (f" (표 `{s.table}`)" if s.table != "metrics" else "")
        )
        if tested:
            lines.append(
                f"- 명세 테스트: {tests_cell(t)} — " + (", ".join(f"`{f}`" for f in t.files) or "-")
            )
            lines += [f"  - 실패: `{f}`" for f in t.failures]
        lines.append("")
        table = metric_table(runs, list(s.outputs)) if s.table == "metrics" else []
        if table:
            lines += ["스냅샷 값:", "", *table, ""]
        elif e.flag in NO_SNAPSHOT:
            lines += [f"스냅샷 값: 없음 — {NO_SNAPSHOT[e.flag]}", ""]
        for c in checks.get(e.flag, []):
            lines += _check_block(c)
        items = confirm_items(sections.get(s.spec, ""))
        lines.append(
            f"남은 [확인 필요] ({s.spec} — {len(items)}):" if items else "남은 [확인 필요]: 없음"
        )
        lines += [f"- {x}" for x in items]
        lines.append("")
    flag_items = confirm_items(sections.get("§8", ""))
    lines += [
        "## 기능 플래그 (§8)",
        "",
        "- 명세 테스트(로더·섀도 점검): " + (tests_cell(flag_t) if tested else "-"),
        "- 지금 플래그: " + ", ".join(f"`{n}` {f}" for n, f in features.resolved().items()),
        f"- 남은 [확인 필요] ({len(flag_items)}):",
        *[f"  - {x}" for x in flag_items],
        "",
    ]
    text = "\n".join(lines).rstrip() + "\n"
    return Report(text, failed, mismatches, missing)


# 스냅샷에 입력이 없는 지표 — 무엇이 있어야 값이 나는가
NO_SNAPSHOT: dict[str, str] = {
    "atm_iv_daily": "POST_DAY 일별(그날 주간 마지막 사이클의 월물 ATM IV·KRX 일별 백필) — 아래 KRX "
    "fixture 손계산",
    "iv_rank": "일별 이력 20거래일 이상(252일 창) — Phase 6 백필 뒤. 아래 fixture·SYNTHETIC 손계산",
    "iv_percentile": "iv_rank 와 같다",
    "iv_hv": "KRX 선물 정산가 21거래일 — 아래 fixture·SYNTHETIC 손계산",
    "hiro": "ws-gateway 옵션 체결 틱(누적 매수·매도) 녹화 — 전용 앱키 뒤 라이브 재생. 식은 명세 "
    "테스트(흐름 = 순 signed × Δ × m × F 속성 포함)",
    "investor_flow": "poller investor_flow 행 — 아래 KIS fixture 로 원문 일관성",
    "dealer_check": "그날 주간 마지막 investor_flow 증권 행 — 아래 KIS fixture 손 판정",
    "block_trades": "opt_ticks 20거래일 — 그 전엔 비활성(명세). 기록 0거래일이면 "
    f"`block_thresholds([])` 활성 = {block_thresholds([]).active}",
    "futures": "scheduler 가 세션 뒤 녹화한 분봉 조회 원문 — 아래 fixture·probe 응답",
}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.metric_report", description="지표별 검증 리포트 (Phase 3 설계 §5)"
    )
    p.add_argument("--probe-dir", type=Path, default=PROBE_DIR, help="probe_out 경로(읽기만)")
    tests = p.add_mutually_exclusive_group()
    tests.add_argument("--junit", type=Path, help="이미 돌린 pytest JUnit XML")
    tests.add_argument("--no-tests", action="store_true", help="명세 테스트를 돌리지 않는다")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--write", action="store_true", help=f"{_rel(OUT, ROOT)} 에 쓴다")
    out.add_argument("--out", type=Path, help="이 경로에 쓴다")
    p.add_argument("--features", type=Path, default=FEATURES_PATH, help="플래그 파일")
    return p.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    run_tests: Callable[[Sequence[str], Path], None] = run_spec_tests,
    cal: TradingCalendar | None = None,
) -> int:
    args = parse_args(argv)
    cal = cal or TradingCalendar.default()
    try:
        features = load_features(args.features)
        cases: list[TestCase] | None = None
        if args.junit is not None:
            cases = parse_junit(args.junit.read_text(encoding="utf-8"))
        elif not args.no_tests:
            with tempfile.TemporaryDirectory() as tmp:
                xml = Path(tmp) / "junit.xml"
                run_tests(spec_test_files(), xml)
                if not xml.is_file():
                    raise InputError("pytest 가 JUnit XML 을 남기지 않았다")
                cases = parse_junit(xml.read_text(encoding="utf-8"))
        report = build_report(
            resolve_inputs(args.probe_dir),
            cases,
            cal=cal,
            features=features,
            metrics_md=METRICS_MD.read_text(encoding="utf-8"),
        )
    except (InputError, OSError, ValueError, ElementTree.ParseError) as e:
        print(f"리포트를 못 만들었다: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    target = OUT if args.write else args.out
    if target is not None:
        target.write_text(report.text, encoding="utf-8")
        missing = f"·시험 없음 {len(report.missing)}" if report.missing else ""
        print(
            f"썼다: {_rel(target, ROOT)} "
            f"(실패 {report.failed_tests}·불일치 {len(report.mismatches)}{missing})"
        )
    else:
        print(report.text, end="")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
