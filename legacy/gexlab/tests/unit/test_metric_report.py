"""지표별 검증 리포트 (scripts/metric_report.py — docs/phase3_design.md §5, metrics §8).

- 지표 목록: Phase 3 카탈로그 플래그 전부, 시험 패턴마다 그 파일에 맞는 시험이 있다
- 명세 테스트: JUnit XML 풀기(매개변수·클래스 안 시험·파일 통째 보고 — 수집 실패는 그 파일을
  쓰는 지표의 실패)·지표별 세기, 시험을 돌렸는데 0 건인 지표·묶음은 실패처럼 막는다
- [확인 필요]: metrics.md 절 나누기·표시 문맥(앞 문맥 / `기본값 [확인 필요]: 목록` 은 뒤 문맥)
- 교차 확인: 손계산 값(KRX 일별 fixture PCR·맥스페인·ATM IV, 분봉 fixture 베이시스, 투자자
  fixture), 식 그대로 맥스페인·델타 보간, engine 값을 바꾸면 불일치로 잡힌다
- 리포트: git 에 있는 작은 발췌(CI)로 만들어도 지표마다 절이 있고 불일치 0, 실패한 명세 테스트는
  종료 코드 1, 입력 오류 2, 로컬 전체 스냅샷이 있으면 그것을 쓴다
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.calendar import TradingCalendar
from core.features import CATALOG, Features
from scripts import metric_report as mr
from scripts.make_golden import CORE_FIXTURES
from services.engine.records import MetricRecord

CAL = TradingCalendar.default()
ROOT = mr.ROOT


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def small_runs() -> list[mr.SnapshotRun]:
    return mr.evaluate_snapshots(CORE_FIXTURES, CAL)


# ── 지표 목록 ──


def test_entries_are_the_phase3_flags_in_catalog_order() -> None:
    assert [e.flag for e in mr.ENTRIES] == [s.name for s in CATALOG if not s.core]
    assert all(e.spec.default == "shadow" for e in mr.ENTRIES)


def _test_names(path: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return re.findall(r"^(?:async )?def (test_\w+)", text, flags=re.M)


@pytest.mark.parametrize(
    ("path", "pattern"),
    sorted({t for e in mr.ENTRIES for t in e.tests} | {*mr.CORE_TESTS, *mr.FLAG_TESTS}),
)
def test_every_test_pattern_matches_tests_in_its_file(path: str, pattern: str) -> None:
    """시험 이름을 바꾸거나 파일을 옮기면 여기서 걸린다 — 지표의 명세 테스트가 조용히 0 이 되지
    않게."""
    names = _test_names(path)
    assert [n for n in names if re.search(pattern, n)], (path, pattern)


def test_spec_test_files_are_the_mapped_files() -> None:
    files = mr.spec_test_files()
    assert files == sorted(set(files)) and "tests/unit/test_flow.py" in files
    assert all((ROOT / f).is_file() for f in files)
    assert not any("integration" in f for f in files)  # Docker 없이 돈다


# ── 명세 테스트 (JUnit) ──

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
<testcase classname="tests.unit.test_flow" name="test_pcr_is_put_over_call[oi]" time="0.1"/>
<testcase classname="tests.unit.test_flow" name="test_pcr_is_put_over_call[vol]" time="0.1">
  <failure message="boom">assert 1 == 2</failure></testcase>
<testcase classname="tests.unit.test_flow" name="test_max_pain_hand_computed" time="0.1">
  <skipped message="x"/></testcase>
<testcase classname="tests.property.test_flow_properties.TestPcr" name="test_pcr_scale" time="0"/>
<testcase classname="tests.unit.test_flow" name="test_pcr_errors" time="0">
  <error message="setup">E</error></testcase>
</testsuite></testsuites>
"""


def test_junit_cases_and_tallies() -> None:
    cases = mr.parse_junit(JUNIT)
    assert [(c.file, c.name, c.param, c.outcome) for c in cases] == [
        ("tests/unit/test_flow.py", "test_pcr_is_put_over_call", "oi", "passed"),
        ("tests/unit/test_flow.py", "test_pcr_is_put_over_call", "vol", "failed"),
        ("tests/unit/test_flow.py", "test_max_pain_hand_computed", "", "skipped"),
        ("tests/property/test_flow_properties.py", "test_pcr_scale", "", "passed"),
        ("tests/unit/test_flow.py", "test_pcr_errors", "", "failed"),
    ]
    t = mr.tally(cases, [(mr.T_FLOW, "pcr"), (mr.P_FLOW, "pcr"), (mr.T_FLOW, "put_over")])
    assert (t.passed, t.failed, t.skipped, t.total) == (2, 2, 0, 4)  # 겹친 패턴도 한 번
    assert t.files == ("tests/property/test_flow_properties.py", "tests/unit/test_flow.py")
    assert t.failures == (
        "tests/unit/test_flow.py::test_pcr_errors",
        "tests/unit/test_flow.py::test_pcr_is_put_over_call[vol]",
    )
    assert t.text() == "2 통과 · **2 실패**"
    assert mr.tally(cases, [(mr.T_FLOW, "max_pain")]).text() == "0 통과 · 1 건너뜀"
    assert mr.tally(cases, [(mr.E_FUT, ".")]).text() == "없음"


# pytest 가 파일 통째로 내는 보고 — 모듈 수집 실패는 classname 이 비고 name 이 모듈 주소,
# 클래스 수집 실패는 classname 이 모듈·name 이 클래스, 모듈 importorskip 은 수집 건너뜀,
# 내부 오류는 pytest.internal
JUNIT_WHOLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="3" tests="5">
<testcase classname="" name="tests.unit.test_vol" time="0.000">
  <error message="collection failure">ModuleNotFoundError: No module named 'x'</error></testcase>
<testcase classname="tests.unit.test_flow" name="TestPcr" time="0">
  <error message="collection failure">E</error></testcase>
<testcase classname="" name="tests.unit.test_greeks" time="0">
  <skipped message="collection skipped">no vollib</skipped></testcase>
<testcase classname="pytest" name="internal" time="0">
  <error message="internal error">INTERNALERROR</error></testcase>
<testcase classname="tests.unit.test_vol" name="test_iv_rank_bounds" time="0"/>
</testsuite></testsuites>
"""


def test_a_file_that_fails_to_collect_is_a_failure_of_every_pattern_of_that_file() -> None:
    """수집 실패는 그 파일의 시험이 하나도 돌지 않았다는 뜻 — 이름 패턴과 상관없이 그 파일을 쓰는
    지표마다 실패 하나(검토 F1: classname 이 비어 경로가 `.py` 가 되어 조용히 사라졌다)."""
    cases = mr.parse_junit(JUNIT_WHOLE)
    assert [(c.file, c.name, c.outcome, c.whole) for c in cases] == [
        ("tests/unit/test_vol.py", "", "failed", "collection failure"),
        ("tests/unit/test_flow.py", "", "failed", "collection failure"),
        ("tests/unit/test_greeks.py", "", "skipped", "collection skipped"),
        ("pytest", "", "failed", "internal error"),
        ("tests/unit/test_vol.py", "test_iv_rank_bounds", "passed", ""),
    ]
    rank = mr.tally(cases, [(mr.T_VOL, "iv_rank")])
    assert (rank.passed, rank.failed) == (1, 1)
    assert rank.failures == ("tests/unit/test_vol.py (수집 실패)",)
    skew = mr.tally(cases, [(mr.T_VOL, "skew"), (mr.T_FLOW, "pcr")])  # 이름이 맞는 시험 없이도
    assert (skew.passed, skew.failed) == (0, 2)
    assert mr.tally(cases, [(mr.T_GREEKS, "vanna")]).text() == "0 통과 · 1 건너뜀"
    assert cases[3].label == "pytest (pytest 내부 오류)"


# ── [확인 필요] ──


def test_sections_and_confirm_items() -> None:
    md = "\n".join(
        [
            "# 지표 명세",
            "## 4. 익스포저",
            "머리 [확인 필요]",
            "### 4.1 Vanna",
            "- 식 없음",
            "### 4.2 Charm",
            "- 주기 2분 **[확인 필요]** 그리고 5일 [확인 필요: 5일]",
            "- 구현: `core.x`. 기본값 **[확인 필요]**: 첫째 규칙, 둘째 규칙",
            "# Phase 3",
            "밖 [확인 필요]",
            "## 7. 선물 (PLAN §5.6)",
            "- 0.05pt [확인 필요: 0.05pt]",
        ]
    )
    s = mr.spec_sections(md)
    assert set(s) == {"§4", "§4.1", "§4.2", "§7"}
    assert mr.confirm_items(s["§4.1"]) == []
    assert mr.confirm_items(s["§4"]) == ["머리 [확인 필요]"]
    assert mr.confirm_items(s["§4.2"]) == [
        "주기 2분 [확인 필요]",
        "그리고 5일 [확인 필요: 5일]",
        "구현: core.x. 기본값 [확인 필요]: 첫째 규칙, 둘째 규칙",
    ]
    # 앞 문맥은 끝 width 자 — " x x x x 끝" 의 앞 공백을 뗀다
    assert mr.confirm_items("x " * 60 + "끝 [확인 필요]", width=10) == ["…x x x x 끝 [확인 필요]"]
    long_after = mr.confirm_items("기본값 [확인 필요]: " + "가" * 100, width=5)
    assert long_after == ["기본값 [확인 필요]: 가가가가가…"]


def test_confirm_items_of_the_real_spec() -> None:
    s = mr.spec_sections(mr.METRICS_MD.read_text(encoding="utf-8"))
    assert mr.confirm_items(s["§4.1"]) == []
    assert any("[확인 필요: 5일]" in x for x in mr.confirm_items(s["§6.3"]))
    assert any("[확인 필요: 0.05pt]" in x for x in mr.confirm_items(s["§7"]))
    assert any(x.startswith("…") or ":" in x for x in mr.confirm_items(s["§5.2"]))


# ── 교차 확인 (손계산) ──


def test_krx_rows_are_parsed_independently() -> None:
    legs = mr.krx_legs(_load(mr.KRX_OPT)["20260923"])
    assert legs["202610"] == {
        (Decimal("1100.0"), "C"): (2613, 72),
        (Decimal("1100.0"), "P"): (1824, 887),
        (Decimal("745.0"), "C"): (43, 0),
    }  # 야간·미니·위클리·코스닥 행은 빠진다
    assert set(legs) == {"202610", "202812"}


def test_brute_max_pain_is_the_formula() -> None:
    oi = {(Decimal(1100), "C"): 2873, (Decimal(1100), "P"): 1737, (Decimal(745), "C"): 46}
    # pain(745) = 1737·(1100 − 745) = 616,635 · pain(1100) = 46·(1100 − 745) = 16,330
    assert mr.brute_max_pain(oi, None) == (Decimal(1100), 1)
    tie = {(Decimal(100), "C"): 1, (Decimal(110), "P"): 1}  # 두 후보 모두 10
    assert mr.brute_max_pain(tie, None) == (Decimal(100), 2)
    assert mr.brute_max_pain(tie, 108.0) == (Decimal(110), 2)
    assert mr.brute_max_pain({(Decimal(100), "C"): 0}, None) == (None, 0)


def test_manual_delta_interpolation() -> None:
    pts = [(-0.4, Decimal(100), 0.30), (-0.2, Decimal(95), 0.40), (-0.1, Decimal(90), 0.50)]
    assert mr.manual_at_delta(pts, -0.25) == pytest.approx(0.375)
    assert mr.manual_at_delta(pts, -0.2) == 0.40
    assert mr.manual_at_delta(pts, -0.5) is None and mr.manual_at_delta(pts, 0.0) is None


def test_krx_fixture_pcr_and_max_pain_hand_values() -> None:
    pcr, mp = mr.check_krx_pcr_max_pain(_load(mr.KRX_OPT))
    assert pcr.ok and mp.ok
    text = "\n".join(pcr.lines + mp.lines)
    # 202610: 풋 OI 1824 / 콜 2613 + 43, 거래량 887 / 72 (합성 fixture)
    assert "| 20260923 | 202610 | 1,824/2,656 | 0.6867 |" in text
    assert f"{887 / 72:.4f}" in text
    assert "| 20260923 | 202610 | 2 | 1100.00 | 1100.00 · ok | 예 |" in text
    assert "| 20260923 | 202812 | 256/0 | null | null · ok |" in text  # 분모 0


def test_krx_daily_atm_iv_rank_and_hv_hand_values() -> None:
    atm, rank, hv = mr.check_krx_daily(_load(mr.KRX_OPT), _load(mr.KRX_FUT), CAL)
    assert atm.ok and rank.ok and hv.ok
    # F = 1100 + 44.70 − 25.30, ATM IV = (35.20 + 39.80) / 2 % — 한쪽 행사가 (합성 fixture)
    row = "| 1119.40 | 1119.40 · estimated | 37.50% | 37.50% · estimated (one_side) |"
    assert row in "\n".join(atm.lines)
    assert "n = 1" in rank.lines[0] and "too_few_days" in rank.lines[0]
    assert "no_data" in hv.lines[0]


def test_futures_basis_hand_values_and_probe_outputs(tmp_path: Path) -> None:
    out1 = _load(mr.KIS_MINUTE)["output1"]
    c = mr.check_futures([("fixture", out1)])
    assert c.ok  # 구현은 손계산과 같다
    row = c.lines[2]
    assert "| 5.85 | 0.43 | 5.85 | +5.42 | +0.00 | 통과 |" in row  # KIS basis = 이론 베이시스
    assert "KIS basis ≈ 이론 베이시스(±0.01) 1건" in c.lines[-1]
    probe = tmp_path / "probe_out"
    (probe / "runs" / "a").mkdir(parents=True)
    other = dict(out1, futs_prpr="1100.00")
    body = {"findings": {"x": [out1, {"nested": other}, out1]}}
    (probe / "runs" / "a" / "m.json").write_text(json.dumps(body), encoding="utf-8")
    (probe / "broken.json").write_text("{", encoding="utf-8")
    found = mr.find_minute_outputs(probe)
    assert [(src, d["futs_prpr"]) for src, d in found] == [
        ("probe_out/runs/a/m.json", "1098.05"),
        ("probe_out/runs/a/m.json", "1100.00"),
    ]
    assert mr.find_minute_outputs(tmp_path / "missing") == []


def test_investor_fixture_consistency_and_dealer_check() -> None:
    inv, dealer = mr.check_investor(_load(mr.KIS_INVESTOR))
    assert inv.ok and dealer.ok
    # 증권 콜 1278 − 14115 − 5, 풋 591 − 1381 + 0 → 콜 < 0 이라 불일치 (합성 fixture)
    assert "= -12,842" in dealer.lines[0] and "= -790" in dealer.lines[0]
    assert "손 판정 불일치" in dealer.lines[0] and "core False" in dealer.lines[0]


def test_snapshot_cross_checks_agree(small_runs: list[mr.SnapshotRun]) -> None:
    vanna, charm = mr.check_vanna_charm(small_runs)
    checks = [
        vanna,
        charm,
        mr.check_gex_pc(small_runs),
        mr.check_skew(small_runs),
        mr.check_term(small_runs),
        *mr.check_pcr_max_pain_snapshots(small_runs),
        mr.check_oi_changes(small_runs),
    ]
    assert [c.title for c in checks if not c.ok] == []
    assert [r.name for r in small_runs] == ["14:27", "14:52"]
    assert "가장 크다" in "\n".join(charm.lines)  # 0DTE 의 계약당 |CEX| 가 가장 크다


def _tamper(run: mr.SnapshotRun, metric: str, scope: str, key: str = "") -> mr.SnapshotRun:
    def bump(m: MetricRecord) -> MetricRecord:
        if (m.metric, m.scope, m.key) != (metric, scope, key) or m.value is None:
            return m
        return m.model_copy(update={"value": m.value * 1.01})

    result = dataclasses.replace(run.result, metrics=tuple(bump(m) for m in run.result.metrics))
    return dataclasses.replace(run, result=result)


@pytest.mark.parametrize(
    ("metric", "scope", "key", "check"),
    [
        ("vex", "all", "", lambda rs: mr.check_vanna_charm(rs)[0]),
        ("cex", "all", "", lambda rs: mr.check_vanna_charm(rs)[1]),
        ("gex_pc", "nearest", "", mr.check_gex_pc),
        ("skew_25d", "series", "WKM:260904", mr.check_skew),
        ("iv_term", "all", "monthly", mr.check_term),
        ("pcr_oi", "series", "M:202610", lambda rs: mr.check_pcr_max_pain_snapshots(rs)[0]),
        ("max_pain", "series", "M:202610", lambda rs: mr.check_pcr_max_pain_snapshots(rs)[1]),
    ],
)
def test_a_changed_engine_value_is_a_mismatch(
    small_runs: list[mr.SnapshotRun], metric: str, scope: str, key: str, check: Any
) -> None:
    runs = [_tamper(small_runs[0], metric, scope, key), small_runs[1]]
    assert check(small_runs).ok and not check(runs).ok


# ── 리포트·실행 ──


def _report(cases: Sequence[mr.TestCase] | None, tmp_path: Path) -> mr.Report:
    return mr.build_report(
        mr.resolve_inputs(tmp_path / "no_probe"),
        cases,
        cal=CAL,
        features=Features(),
        metrics_md=mr.METRICS_MD.read_text(encoding="utf-8"),
    )


def test_report_on_the_committed_small_snapshots(tmp_path: Path) -> None:
    r = _report(None, tmp_path)
    assert r.ok and r.mismatches == [] and r.failed_tests == 0
    assert (
        "git 에 있는 작은 발췌" in r.text
        and "chain_snapshot_synthetic_20260928_1427_small.json" in r.text
    )
    for e in mr.ENTRIES:
        assert f"### {e.flag} — {e.spec.spec} {e.title}" in r.text
        assert f"| {e.flag} | {e.title} | {e.spec.spec} | shadow |" in r.text
    assert "명세 테스트: 돌리지 않음" in r.text and "교차 확인 불일치 0" in r.text
    assert r.text.endswith("\n") and "/Users/" not in r.text


def test_every_table_row_has_the_columns_of_its_header(tmp_path: Path) -> None:
    """표 칸 안의 `|`(예: |CEX|)는 열을 깬다 — 표마다 모든 줄의 칸 수가 머리줄과 같다."""
    blocks: list[list[str]] = []
    prev = False
    for line in _report(None, tmp_path).text.splitlines():
        row = line.startswith("|")
        if row and not prev:
            blocks.append([])
        if row:
            blocks[-1].append(line)
        prev = row
    assert len(blocks) > 20
    for block in blocks:
        widths = {line.count("|") for line in block}
        assert len(widths) == 1, block[:3]


def test_a_failed_spec_test_fails_the_report(tmp_path: Path) -> None:
    name = "test_pcr_is_put_over_call_for_oi_and_volume"
    cases = [
        mr.TestCase("tests/unit/test_flow.py", name, "", "failed"),
        mr.TestCase("tests/unit/test_gex.py", "test_x", "", "passed"),
    ]
    r = _report(cases, tmp_path)
    assert r.failed_tests == 1 and not r.ok
    assert "- 명세 테스트: 0 통과 · **1 실패**" in r.text
    assert f"  - 실패: `tests/unit/test_flow.py::{name}`" in r.text


def test_local_full_snapshots_are_preferred(tmp_path: Path) -> None:
    probe = tmp_path / "probe_out"
    for run, src in zip(mr.SNAPSHOT_RUNS, CORE_FIXTURES, strict=True):
        (probe / "runs" / run).mkdir(parents=True)
        shutil.copy(src, probe / "runs" / run / "chain_snapshot.json")
    got = mr.resolve_inputs(probe)
    first = probe / "runs" / mr.SNAPSHOT_RUNS[0] / "chain_snapshot.json"
    assert got.full and got.snapshots[0] == first
    assert not mr.resolve_inputs(tmp_path / "none").full


def _mapped_tests() -> list[tuple[str, str]]:
    """매핑(지표·Phase 2 핵심·플래그)마다 그 패턴에 맞는 실제 시험 하나 — (파일, 시험 이름)."""
    out: set[tuple[str, str]] = set()
    for path, pattern in {t for e in mr.ENTRIES for t in e.tests} | {
        *mr.CORE_TESTS,
        *mr.FLAG_TESTS,
    }:
        out.add((path, next(n for n in _test_names(path) if re.search(pattern, n))))
    return sorted(out)


def _full_cases(outcome: mr.Outcome = "passed") -> list[mr.TestCase]:
    return [mr.TestCase(path, name, "", outcome) for path, name in _mapped_tests()]


def test_spec_tests_that_did_not_run_fail_the_report(tmp_path: Path) -> None:
    """시험을 돌렸는데 지표(또는 Phase 2 핵심·플래그 묶음)의 명세 테스트가 0 건이면 실패 — 파일을
    못 모았거나 JUnit 이 일부뿐이면 '없음' 으로 통과하지 않는다(검토 F1). pytest 내부 오류처럼
    어느 매핑 파일에도 들지 않는 파일 통째 실패도 센다."""
    full = _full_cases()
    assert _report(full, tmp_path).ok
    no_fut = [c for c in full if c.file not in (mr.T_FUT, mr.P_FUT, mr.E_FUT)]
    r = _report(no_fut, tmp_path)
    assert r.missing == ["futures"] and r.failed_tests == 0 and not r.ok
    assert "- 판정: 명세 테스트 실패 0 · 명세 테스트 없음 1 (futures) · 교차 확인" in r.text
    assert "| futures | 선물 베이시스·괴리율·OI 증감·체결강도 | §7 | shadow | **없음** |" in r.text
    no_core = [c for c in full if c.file not in {f for f, _ in mr.CORE_TESTS}]
    assert _report(no_core, tmp_path).missing == ["Phase 2 핵심"]
    broken = [*full, mr.TestCase("pytest", "", "", "failed", "internal error")]
    r = _report(broken, tmp_path)
    assert r.failed_tests == 1 and r.missing == [] and not r.ok
    assert "  - 실행 오류: `pytest (pytest 내부 오류)`" in r.text
    # 수집 실패는 그 파일을 쓰는 지표의 실패로(내부 오류처럼 따로 세지 않는다)
    vol = [*full, mr.TestCase(mr.T_VOL, "", "", "failed", "collection failure")]
    r = _report(vol, tmp_path)
    assert r.failed_tests >= 1 and not r.ok and "실행 오류" not in r.text
    assert f"  - 실패: `{mr.T_VOL} (수집 실패)`" in r.text


def _junit_file(tmp_path: Path, outcome: str = "") -> Path:
    body = f"<{outcome} message='x'/>" if outcome else ""
    cases = "".join(
        f"<testcase classname='{path[:-3].replace('/', '.')}' name='{name}'>{body}</testcase>"
        for path, name in _mapped_tests()
    )
    p = tmp_path / "junit.xml"
    p.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>", encoding="utf-8")
    return p


def test_a_junit_with_a_collection_error_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """검토 F1 재현 — 명세 시험 파일 하나가 import 에서 깨지면 pytest 는 수집 실패 하나만 남기고
    멈춘다. 그 JUnit 으로 만든 리포트는 종료 코드 1."""
    xml = tmp_path / "broken.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        '<testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">'
        '<testcase classname="" name="tests.unit.test_vol" time="0.000">'
        '<error message="collection failure">ImportError while importing test module'
        "</error></testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    out = tmp_path / "report.md"
    argv = ["--probe-dir", str(tmp_path / "none"), "--out", str(out), "--junit", str(xml)]
    assert mr.main(argv, cal=CAL) == 1
    assert "(실패 6·불일치 0·시험 없음 13)" in capsys.readouterr().out
    text = out.read_text(encoding="utf-8")
    assert f"  - 실패: `{mr.T_VOL} (수집 실패)`" in text


def test_main_exit_codes_and_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "report.md"
    base = ["--probe-dir", str(tmp_path / "none"), "--out", str(out)]
    assert mr.main([*base, "--junit", str(_junit_file(tmp_path))], cal=CAL) == 0
    assert out.read_text(encoding="utf-8").startswith("# 지표별 검증 리포트")
    assert "썼다:" in capsys.readouterr().out
    assert mr.main([*base, "--junit", str(_junit_file(tmp_path, "failure"))], cal=CAL) == 1
    assert mr.main([*base, "--junit", str(tmp_path / "missing.xml")], cal=CAL) == 2
    assert "리포트를 못 만들었다" in capsys.readouterr().err

    calls: list[tuple[list[str], Path]] = []

    def fake_run(files: Sequence[str], xml: Path) -> None:
        calls.append((list(files), xml))
        xml.write_text(_junit_file(tmp_path).read_text(encoding="utf-8"), encoding="utf-8")

    assert mr.main(base, run_tests=fake_run, cal=CAL) == 0
    assert calls and calls[0][0] == mr.spec_test_files()
    assert mr.main(base, run_tests=lambda f, x: None, cal=CAL) == 2  # XML 을 남기지 않았다
    capsys.readouterr()
    calls.clear()
    assert mr.main(["--probe-dir", str(tmp_path / "none"), "--no-tests"], run_tests=fake_run) == 0
    assert calls == [] and capsys.readouterr().out.startswith("# 지표별 검증 리포트")
