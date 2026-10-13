"""부문 매출(`segments`) — 합성 사업보고서 원문(#1 백만원·#12 천원)과 단위·합계 행."""

from __future__ import annotations

from kbj.core.quality import Quality
from kbj.engines.financials.segments import parse_segments
from tests.fixtures.synthetic.p4_world import build_world


def test_합성_사업보고서_부문표() -> None:
    w = build_world()
    rcept = w.rcept("r1.2025.11011")
    html = w.documents(rcept)[0][1]
    rows = parse_segments(html, corp_code="99400010", bsns_year=2025, rcept_no=rcept)
    assert [r.segment for r in rows] == ["모의부문A(전자부품)", "모의부문B(장비)", "기타"]
    cum = sum(t.revenue for t in w.quarter_truth("99400010", "CFS") if t.key[0] == 2025)
    a = rows[0].amounts["당기"]
    assert a is not None and abs(a - cum * 0.6) <= 1_000_000  # 백만원 단위 반올림
    assert set(rows[0].amounts) == {"당기", "전기"} and rows[0].quality == Quality.ESTIMATED


def test_천원_단위() -> None:
    w = build_world()
    rcept = w.rcept("r12.2025.11011")
    rows = parse_segments(w.documents(rcept)[0][1], corp_code="99400110", bsns_year=2025,
                          rcept_no=rcept)  # fmt: skip
    cum = sum(t.revenue for t in w.quarter_truth("99400110", "OFS") if t.key[0] == 2025)
    v = rows[0].amounts["당기"]
    assert v is not None and abs(v - cum * 0.6) <= 1_000


def test_표가_없으면_빈_목록_합계행은_뺀다() -> None:
    assert parse_segments("<p>본문</p><table><tr><td>a</td><td>1</td></tr></table>",
                          corp_code="99000001", bsns_year=2025, rcept_no=None) == []  # fmt: skip
    html = ("<p>제품별 매출 (단위: 억원)</p><table><tr><td>품목</td><td>당기</td></tr>"
            "<tr><td>A</td><td>1</td></tr><tr><td>합계</td><td>1</td></tr></table>")  # fmt: skip
    rows = parse_segments(html, corp_code="99000001", bsns_year=2025, rcept_no=None)
    assert [(r.segment, dict(r.amounts)) for r in rows] == [("A", {"당기": 100_000_000})]
