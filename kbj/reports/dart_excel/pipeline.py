"""dart-report 파이프라인 — 종목코드 하나 → 리포트 입력(`collect_inputs`) → 엑셀 + 화면
블록(`render`).

KBJ 정본(docs/p4_design.md §1.5·§4.6, D-P4-6) — ET `dart-report/run.py:main`(:52~216)의 순서·경고
문구를 그대로 옮겨 둘로 나눴다: 수집·계산(`collect_inputs`)과 산출(`render`). 진단 두 개(`_diagnose`
:263·`_diagnose_bridge`:219)도 옮겼다(`diagnose`). 골든 G2(`tests/golden/dart_report/
test_pipeline_golden.py`)가 legacy `run.main` 과 호출 순서·로그 줄·`build_workbook` 입력·엑셀이
같은지 본다.

바뀐 것
- `date.today()`(연도 기본값·수주 기간 끝 — :65) → 주입한 `now`(KST 날짜). 벽시계를 읽지 않는다.
- `print`(verbose) → `log` 인자(없으면 조용히 — legacy `--quiet`). 오류 줄(`[오류] … 재무제표를
  하나도 받지 못했습니다`)은 `NoStatements` 예외로(CLI 가 표준오류에 쓰고 종료 코드 2 — legacy
  그대로).
- 주석·브릿지·수주 단계의 예외는 legacy 처럼 경고 줄로 바꾸되, **수집 실패(`FetchError` — 전송·
  한도·키)와 예산 닫힘(`BudgetExhausted`)은 올려 보낸다** — 일부가 빈 리포트를 정상처럼 저장해 다음
  날까지 덮지 않게(절대 규칙 4 — 에러를 삼키지 않는다). legacy 에서는 그것도 경고였다.
- `render` 는 엑셀과 같은 입력에서 화면용 블록(`blocks.build_blocks`)을 만든다 — 화면과 엑셀의
  숫자가 갈라지지 않는다(§4.6).
"""

from __future__ import annotations

import io
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from kbj.core.time import KST
from kbj.data.budget import BudgetExhausted
from kbj.data.http import FetchError
from kbj.engines.financials.bridge import _bucketize_bridge, build_bridge, fetch_bridge_details
from kbj.engines.financials.costs import (
    BUCKET_ORDER,
    CostNote,
    DocumentSource,
    _parse_cost_table,
    _tables_with_title,
    annual_cost_structure,
    bucketize,
    cost_timeseries,
    fetch_cost_notes,
    find_periodic_reports,
)
from kbj.engines.financials.orders import fetch_orders, orders_table
from kbj.engines.financials.statements import (
    Period,
    StatementSource,
    fetch_periods,
    to_annual,
    to_quarterly,
)
from kbj.reports.dart_excel.blocks import build_blocks
from kbj.reports.dart_excel.excel import build_workbook

__all__ = [
    "EMPTY_ORDERS",
    "NoStatements",
    "ReportInputs",
    "ReportSource",
    "collect_inputs",
    "diagnose",
    "render",
]

Log = Callable[[str], None]
# 수집 실패·예산 닫힘은 경고로 바꾸지 않는다(모듈 머리말)
_INFRA_ERRORS: tuple[type[BaseException], ...] = (FetchError, BudgetExhausted)


def _empty_orders() -> dict[str, Any]:
    return {"total_eok": 0.0, "nipa_eok": 0.0, "nipa_ratio": None, "rows": []}


EMPTY_ORDERS: dict[str, Any] = _empty_orders()


class ReportSource(StatementSource, DocumentSource, Protocol):
    """리포트 한 벌이 부르는 DART 접근.

    `DartClient` 그대로 맞는다 — 수집 작업은 원시 행 캐시(`pub_fin.statement_raw`)를 끼운다.
    """

    def corp_code(self, stock_code: str) -> tuple[str, str]:
        """종목코드 → (corp_code, 회사명)."""
        ...


class NoStatements(RuntimeError):
    """재무제표를 하나도 받지 못했다(legacy run.main 종료 코드 2)."""


@dataclass
class ReportInputs:
    """`build_workbook` 의 입력 전부 + 진단·저장에 필요한 것(같은 입력에서 엑셀과 화면 블록)."""

    stock: str
    corp_code: str
    corp_name: str
    years: list[int]
    fs_div: str  # 요청(CFS·OFS)
    fs_used: str  # 실제(분기마다 — 'CFS'·'OFS'·'CFS, OFS')
    periods: list[Period]
    quarters: list[dict[str, Any]]
    annual: list[dict[str, Any]]
    notes: list[CostNote]
    qcosts: list[dict[str, Any]]
    acosts: list[dict[str, Any]]
    unmapped: list[tuple[str, float]]
    bridge_steps: list[dict[str, Any]]
    bridge_label: str
    orders: dict[str, Any]
    mapping: dict[str, Any]
    warnings: list[str] = field(default_factory=list[str])

    @property
    def meta(self) -> dict[str, Any]:
        return {
            "corp_name": self.corp_name,
            "stock_code": self.stock,
            "corp_code": self.corp_code,
            "fs_div_used": self.fs_used,
            "period_label": f"{self.years[0]}–{self.years[-1]}",
        }

    def workbook_kwargs(self) -> dict[str, Any]:
        """legacy `run.main` 이 `build_workbook` 에 넘기던 키워드 그대로."""
        return {
            "meta": self.meta,
            "annual": self.annual,
            "annual_costs": self.acosts,
            "quarters": self.quarters,
            "qcosts": self.qcosts,
            "bridge_steps": self.bridge_steps,
            "bridge_label": self.bridge_label,
            "orders": self.orders,
            "mapping": self.mapping,
            "unmapped": self.unmapped,
            "warnings": self.warnings,
        }


def _say(log: Log | None) -> Log:
    return log if log is not None else (lambda _line: None)


@dataclass
class _Stage1:
    stock: str
    corp_code: str
    corp_name: str
    years: list[int]
    this_year: int
    periods: list[Period]
    warnings: list[str]


def _statements(
    client: ReportSource,
    stock: str,
    years: Sequence[int] | None,
    fs_div: str,
    *,
    now: datetime,
    log: Log | None,
) -> _Stage1:
    say = _say(log)
    warnings: list[str] = []
    stock = str(stock).zfill(6)
    corp_code, corp_name = client.corp_code(stock)
    say(f"■ {corp_name} ({stock}) / DART 고유번호 {corp_code}")

    this_year = now.astimezone(KST).year
    if years:
        ys = list(range(years[0], years[-1] + 1))
    else:
        ys = list(range(this_year - 3, this_year + 1))
    say(f"  대상 연도: {ys[0]}–{ys[-1]}")

    # ---------------------------------------------------------------- 재무제표
    say("· 재무제표 수집…")
    periods = fetch_periods(client, corp_code, ys, fs_div)
    if not periods:
        raise NoStatements(
            f"[오류] {corp_name} 의 재무제표를 하나도 받지 못했습니다. "
            "연도 범위나 fs_div(CFS/OFS)를 바꿔보세요."
        )
    return _Stage1(stock, corp_code, corp_name, ys, this_year, periods, warnings)


def _cost_notes(
    client: ReportSource, s1: _Stage1, mapping: dict[str, Any], log: Log | None
) -> list[CostNote]:
    say = _say(log)
    say("· 정기보고서 주석에서 비용 성격별 분류 파싱…")
    # 연결·별도 주석이 한 ZIP 에 섞여 있어, 정형 API 의 (매출-영업이익)을
    # 기대 총비용으로 넘겨 맞는 표를 고르게 한다 (005930 실측 근거)
    expected_costs = {
        (p.year, p.quarter): p.cumulative["revenue"] - p.cumulative["operating_income"]
        for p in s1.periods
        if p.cumulative.get("revenue") is not None
        and p.cumulative.get("operating_income") is not None
    }
    try:
        return fetch_cost_notes(
            client,
            s1.corp_code,
            s1.years,
            mapping,
            verbose=log is not None,
            expected=expected_costs,
            log=log,
        )
    except _INFRA_ERRORS:
        raise
    except Exception as exc:
        s1.warnings.append(f"주석 파싱 중 오류: {exc}")
        if log is not None:
            log(traceback.format_exc().rstrip())
        return []


def collect_inputs(
    client: ReportSource,
    stock: str,
    years: Sequence[int] | None,
    fs_div: str,
    mapping: dict[str, Any],
    *,
    now: datetime,
    log: Log | None = None,
    orders: bool = True,
) -> ReportInputs:
    """legacy `run.main` 의 수집·계산 단계(엑셀 쓰기 앞까지) — 순서·경고 문구 그대로.

    `years` = (시작, 끝) 또는 연도 목록(끝 포함 — 처음과 마지막만 본다). 없으면 올해 포함 4개 연도.
    `orders=False` 는 legacy `--no-orders`.
    """
    if now.tzinfo is None:
        raise ValueError("now 는 시간대가 있는 시각이어야 한다")
    say = _say(log)
    s1 = _statements(client, stock, years, fs_div, now=now, log=log)
    periods = s1.periods
    warnings = s1.warnings
    quarters = to_quarterly(periods)
    annual = to_annual(periods)
    fs_used = ", ".join(sorted({p.fs_div for p in periods}))
    say(f"  보고서 {len(periods)}건 → 분기 {len(quarters)}개 ({fs_used})")

    missing_q = [q["label"] for q in quarters if q.get("revenue") is None]
    if missing_q:
        warnings.append(
            f"매출액을 산출하지 못한 분기: {', '.join(missing_q)} (직전 분기 보고서 누락)"
        )

    # ---------------------------------------------------------------- 비용 주석
    notes = _cost_notes(client, s1, mapping, log)

    qcosts, unmapped = cost_timeseries(notes, mapping)
    acosts = annual_cost_structure(notes, mapping)
    if not notes:
        warnings.append(
            "비용 성격별 주석 표를 찾지 못했습니다. "
            "config/mapping.yaml 의 note_titles 를 회사 보고서 문구에 맞게 보강하세요."
        )
    if unmapped:
        uniq = len({u[0] for u in unmapped})
        warnings.append(f"미매핑 계정 {uniq}종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인.")
    say(f"  주석 {len(notes)}건 → 분기 비용구조 {len(qcosts)}개 / 미매핑 {len(unmapped)}행")

    # 검증 포인트 2 (README): 비용합계로 역산한 영업이익 vs 손익계산서 공시 영업이익.
    # 크게 다르면 주석 표를 잘못 잡은 것이다. 러너 로그가 유일한 눈이라 매 실행 찍는다.
    ac_by_year = {a["year"]: a for a in acosts}
    if log is not None and ac_by_year:
        say("· 검증 — 역산 영업이익(매출−비용합계) vs 공시 영업이익")
        for a in annual:
            c = ac_by_year.get(a["year"])
            rev, op = a.get("revenue"), a.get("operating_income")
            if not c or rev is None or op is None or c.get("months") != a.get("months"):
                continue
            implied = rev - c["비용합계"]
            say(
                f"   {a['label']:>5}  역산 {implied:>8,.1f}억 / 공시 {op:>8,.1f}억 / "
                f"차이 {implied - op:+,.1f}억"
            )

    # ---------------------------------------------------------------- 브릿지
    say("· 이익 브릿지 구성…")
    expected_nonop = {
        (p.year, p.quarter): p.cumulative["pretax_income"] - p.cumulative["operating_income"]
        for p in periods
        if p.cumulative.get("pretax_income") is not None
        and p.cumulative.get("operating_income") is not None
    }
    try:
        details = fetch_bridge_details(
            client,
            s1.corp_code,
            s1.years,
            mapping,
            verbose=log is not None,
            expected=expected_nonop,
            log=log,
        )
    except _INFRA_ERRORS:
        raise
    except Exception as exc:
        details = {}
        warnings.append(f"브릿지 주석 파싱 중 오류: {exc}")

    full_years = [p for p in periods if p.quarter == "4Q"]
    target = full_years[-1] if full_years else periods[-1]
    bridge_steps = build_bridge(target, details.get((target.year, target.quarter)))
    bridge_label = (
        f"{target.year}년" if target.quarter == "4Q" else f"{target.year}년 {target.quarter} 누적"
    )
    residual = next((s for s in bridge_steps if s.get("note")), None)
    if residual and abs(residual["value"]) > 5:
        warnings.append(
            f"이익브릿지 잔차가 {residual['value']:.1f}억원입니다 — "
            "config/mapping.yaml 의 bridge 설정을 보강하면 줄어듭니다."
        )

    # ---------------------------------------------------------------- 수주
    if not orders:
        otable = _empty_orders()
    else:
        say("· 수주(공급계약) 공시 수집…")
        try:
            olist = fetch_orders(
                client,
                s1.corp_code,
                f"{s1.years[0]}0101",
                f"{s1.this_year}1231",
                verbose=log is not None,
                log=log,
            )
            otable = orders_table(olist)
        except _INFRA_ERRORS:
            raise
        except Exception as exc:
            otable = _empty_orders()
            warnings.append(f"수주 공시 수집 중 오류: {exc}")

    return ReportInputs(
        stock=s1.stock,
        corp_code=s1.corp_code,
        corp_name=s1.corp_name,
        years=s1.years,
        fs_div=fs_div,
        fs_used=fs_used,
        periods=periods,
        quarters=quarters,
        annual=annual,
        notes=notes,
        qcosts=qcosts,
        acosts=acosts,
        unmapped=unmapped,
        bridge_steps=bridge_steps,
        bridge_label=bridge_label,
        orders=otable,
        mapping=mapping,
        warnings=warnings,
    )


def render(inputs: ReportInputs, generated_at: datetime) -> tuple[bytes, dict[str, Any]]:
    """같은 입력 → (xlsx 바이트, 화면용 블록 JSON)."""
    buf = io.BytesIO()
    build_workbook(buf, **inputs.workbook_kwargs(), generated_at=generated_at)
    return buf.getvalue(), build_blocks(inputs, generated_at)


# --------------------------------------------------------------------------- 진단(legacy CLI)


def diagnose(
    client: ReportSource,
    stock: str,
    years: Sequence[int] | None,
    fs_div: str,
    mapping: dict[str, Any],
    *,
    kind: str,
    now: datetime,
    log: Log,
) -> int:
    """`--diagnose-costs`·`--diagnose-bridge` — legacy run.main 과 같은 단계까지 돌고 진단."""
    s1 = _statements(client, stock, years, fs_div, now=now, log=log)
    quarters = to_quarterly(s1.periods)
    fs_used = ", ".join(sorted({p.fs_div for p in s1.periods}))
    log(f"  보고서 {len(s1.periods)}건 → 분기 {len(quarters)}개 ({fs_used})")
    notes = _cost_notes(client, s1, mapping, log)
    if kind == "costs":
        return _diagnose(notes, mapping, log)
    if kind == "bridge":
        return _diagnose_bridge(client, s1.corp_code, mapping, s1.periods, log)
    raise ValueError(f"진단 종류는 costs·bridge: {kind!r}")


def _diagnose_bridge(
    client: ReportSource,
    corp_code: str,
    mapping: dict[str, Any],
    periods: list[Period],
    log: Log,
) -> int:
    """브릿지 대상(마지막 완결 연도) 보고서의 영업외손익 표를 파일별로 보여준다.

    잔차가 어디서 새는지(미매핑 계정, 연결·별도 이중합산)를 러너 로그로 판단하기 위한 장치.
    """
    cfg = mapping["bridge"]
    full = [p for p in periods if p.quarter == "4Q"]
    target = full[-1] if full else periods[-1]
    exp = None
    if (
        target.cumulative.get("pretax_income") is not None
        and target.cumulative.get("operating_income") is not None
    ):
        exp = target.cumulative["pretax_income"] - target.cumulative["operating_income"]
    reports = find_periodic_reports(client, corp_code, [target.year])
    rcept = reports.get((target.year, target.quarter))
    log(f"\n=== 브릿지 진단: {target.year} {target.quarter} (rcept {rcept}) ===")
    if exp is not None:
        log(f"기대 영업외손익(세전이익-영업이익): {exp / 1e8:,.1f}억")
    if not rcept:
        log("대상 보고서를 찾지 못했다.")
        return 1
    for fname, html in client.document_texts(rcept):
        rows = []
        for kw, table, scale in _tables_with_title(html, cfg["note_titles"]):
            parsed = _parse_cost_table(table, scale)
            if parsed:
                rows.append((kw, parsed))
        if not rows:
            continue
        log(f"\n[파일] {fname}")
        merged: list[tuple[str, float]] = []
        for kw, parsed in rows:
            log(f"  표제 '{kw}' · {len(parsed)}행")
            for label, value in parsed:
                log(f"     {label:<36} {value / 1e8:>12,.1f} 억")
            merged.extend(parsed)
        buckets, un = _bucketize_bridge(merged, cfg)
        d = {k: round(v / 1e8, 1) for k, v in buckets.items()}
        log(f"  → 분해 {d}")
        log(
            f"  → 버킷합 {sum(buckets.values()) / 1e8:,.1f}억 / 미매핑 {len(un)}건 "
            f"{[u[0] for u in un][:12]}"
        )
    return 0


def _diagnose(notes: list[CostNote], mapping: dict[str, Any], log: Log) -> int:
    """주석에서 실제로 무슨 계정명이 나오는지 보여준다 — 매핑 튜닝용."""
    if not notes:
        log("주석 표를 하나도 찾지 못했습니다. mapping.yaml 의 note_titles 를 넓혀보세요.")
        return 1
    log(f"\n=== 주석 진단: {len(notes)}개 보고서 ===")
    seen: dict[str, float] = {}
    for note in notes:
        buckets, unmapped = bucketize(note.rows, mapping)
        log(
            f"\n[{note.label}] 표제 '{note.source_title}' · {len(note.rows)}행 · "
            f"배율 {note.scale:g}"
        )
        for label, value in note.rows:
            log(f"     {label:<36} {value / 1e8:>12,.1f} 억")
        for b in BUCKET_ORDER:
            log(f"   {b:<10} {buckets[b] / 1e8:>12,.1f} 억")
        for label, value in unmapped:
            seen[label] = seen.get(label, 0.0) + value
    if seen:
        log("\n--- 미매핑 계정 (전부 '기타'로 감) ---")
        for label, value in sorted(seen.items(), key=lambda kv: -abs(kv[1])):
            log(f"   {label:<40} {value / 1e8:>12,.1f} 억")
        log("\n→ config/mapping.yaml 의 buckets 에 위 계정명을 추가하세요.")
    else:
        log("\n미매핑 계정 없음.")
    return 0
