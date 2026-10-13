"""주석 비용(dart-report `costs` 승격본) — 단위·누적 열·합계행·표 고르기·버킷·분기 차분.

docs/p4_design.md §4.6·§8.3. 원문은 합성 세계(`p4_world` #1 — 본문·연결 주석·별도 주석 세 파일,
백만원)를 가짜 DART 로 받는다.
"""

from __future__ import annotations

from typing import Any

import pytest
from bs4 import BeautifulSoup

from kbj.data.public.dart.client import DartClient
from kbj.engines.financials import costs as C
from tests.fixtures.synthetic.p4_world import build_world
from tests.golden.dart_report.golden import WorldDart, world_now


@pytest.fixture(scope="module")
def mapping() -> dict[str, Any]:
    return C.load_mapping()


def _table(html: str) -> Any:
    return BeautifulSoup(html, "html.parser").find("table")


def test_단위_배율은_표에_가까운_표기를_쓴다() -> None:
    assert C._unit_scale(C._norm("(단위: 백만원) … (단위 : 천원)")) == 1e3
    assert C._unit_scale(C._norm("단위:억원")) == 1e8
    assert C._unit_scale("") == 1.0


def test_분기_표는_누적_열을_읽는다() -> None:
    t = _table(
        "<table><tr><th>구분</th><th>당분기 3개월</th><th>당분기 누적</th></tr>"
        "<tr><td>원재료 사용액</td><td>10</td><td>25</td></tr>"
        "<tr><td>종업원 급여</td><td>(3)</td><td>△7</td></tr></table>"
    )
    assert C._parse_cost_table(t, 1e6) == [("원재료사용액", 25e6), ("종업원급여", -7e6)]


def test_머리행이_없으면_첫_숫자열() -> None:
    t = _table("<table><tr><td>감가상각비</td><td>-</td><td>1,234</td></tr></table>")
    assert C._parse_cost_table(t) == [("감가상각비", 1234.0)]


def test_버킷_합계행과_라벨_잃은_합계행을_뺀다(mapping: dict[str, Any]) -> None:
    rows = [("성격별비용", 100.0), ("원재료", 60.0), ("급여", 30.0), ("광고선전비", 6.0),
            ("경상연구개발비", 4.0), ("합계(*2)", 100.0)]  # fmt: skip
    buckets, unmapped = C.bucketize(rows, mapping)
    assert buckets["원가"] == 60.0 and buckets["인건비"] == 30.0
    assert buckets["기타"] == 10.0 and unmapped == [("경상연구개발비", 4.0)]
    assert sum(buckets.values()) == 100.0


def test_정정공시는_큰_접수번호() -> None:
    class Src:
        def disclosures(
            self, corp_code: str, bgn_de: str, end_de: str, pblntf_ty: str | None = None
        ) -> list[dict[str, Any]]:
            return [
                {"report_nm": "사업보고서 (2025.12)", "rcept_no": "20260315000001"},
                {"report_nm": "[기재정정]사업보고서 (2025.12)", "rcept_no": "20260410000002"},
                {"report_nm": "분기보고서 (2026.03)", "rcept_no": "20260515000003"},
                {"report_nm": "감사보고서", "rcept_no": "20260301000004"},
            ]

        def document_texts(self, rcept_no: str) -> list[tuple[str, str]]:
            return []

    got = C.find_periodic_reports(Src(), "99000001", [2025, 2026])
    assert got == {(2025, "4Q"): "20260410000002", (2026, "1Q"): "20260515000003"}


def test_세_파일_중_기대_총비용에_맞는_표를_고른다(mapping: dict[str, Any]) -> None:
    """#1 원문 셋(본문·연결 주석·별도 주석) — 별도 표(합이 작다)를 고르지 않는다."""
    w = build_world()
    client = WorldDart(w, world_now()).client(DartClient)
    truth = {t.key: t for t in w.quarter_truth("99400010", "CFS")}
    cum1 = truth[(2025, 1)]
    expected = {(2025, "1Q"): float(cum1.revenue - cum1.op)}
    lines: list[str] = []
    notes = C.fetch_cost_notes(
        client, "99400010", [2025], mapping, verbose=True, expected=expected, log=lines.append
    )
    assert [n.label for n in notes] == ["1Q25", "2Q25", "3Q25", "4Q25"]
    q1 = notes[0]
    total = sum(v for lb, v in q1.rows if lb != "합계")
    assert total == pytest.approx(cum1.revenue - cum1.op, rel=1e-6)
    assert q1.scale == 1e6 and q1.source_title == "비용의성격별분류"
    assert lines[0].startswith("  [주석] 1Q25  '비용의성격별분류'  12행")


def test_분기_차분과_연간(mapping: dict[str, Any]) -> None:
    def note(q: str, v: float) -> C.CostNote:
        return C.CostNote(2025, q, "x", [("원재료", v), ("급여", v / 2), ("합계", v * 1.5)])

    notes = [note("1Q", 100.0), note("2Q", 260.0), note("4Q", 600.0)]
    rows, unmapped = C.cost_timeseries(notes, mapping)
    assert unmapped == []
    assert rows[1]["원가"] == pytest.approx(160 / C.EOK)
    assert rows[2]["원가"] is None and rows[2]["비용합계"] is None  # 3Q 주석 없음 → 4Q 차분 불가
    annual = C.annual_cost_structure(notes, mapping)
    assert annual[0]["label"] == "2025" and annual[0]["비용합계"] == pytest.approx(900 / C.EOK)


def test_매핑_기본_경로는_config_dart_report() -> None:
    m = C.load_mapping()
    assert set(m) >= {"buckets", "total_labels", "cost_behavior", "note_titles", "bridge"}
    assert list(m["buckets"]) == C.BUCKET_ORDER
