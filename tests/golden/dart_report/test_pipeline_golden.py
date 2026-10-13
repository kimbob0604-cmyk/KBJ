"""G2 — 파이프라인 골든: 합성 세계 두 회사의 DART 응답을 kbj `collect_inputs`·`render` 에 흘려
legacy `run.main` 캡처와 비교한다(docs/p4_design.md §4.6·§8.1).

비교하는 것: ① DART 호출 순서(엔드포인트·인자 — 키 제외) ② 로그 줄(legacy verbose 출력 — 끝의 CLI
꼬리는 뺀다) ③ `build_workbook` 입력(분기·연간·비용·브릿지·수주·미매핑·경고) ④ 엑셀(정규화 —
차트 XML 포함). 회사: #1 994010(연결·별도·본문 주석 셋 — 이중 합산 함정, 백만원, 2025 사업보고서
정정본, 공급계약 + 정정), #12 994110(OFS 만 — CFS 요청이 OFS 로 폴백, 천원, 공급계약 1건).
"""

from __future__ import annotations

from typing import Any

import pytest

from kbj.data.public.dart.client import DartClient
from kbj.reports.dart_excel.pipeline import collect_inputs, render
from tests.fixtures.synthetic.p4_world import build_world
from tests.golden.dart_report.golden import (
    GENERATED_AT,
    PIPELINE,
    PIPELINE_COMPANIES,
    PIPELINE_YEARS,
    WorldDart,
    jsonable,
    read_json,
    world_now,
)
from tests.oracles import formula_eval
from tests.oracles.xlsx_normalize import diff, normalize


@pytest.mark.parametrize("stock", PIPELINE_COMPANIES)
def test_파이프라인은_legacy_run_main_과_같다(stock: str, mapping: dict[str, Any]) -> None:
    exp = read_json(PIPELINE / f"{stock}.json.gz")
    wd = WorldDart(build_world(), world_now())
    lines: list[str] = []
    inputs = collect_inputs(
        wd.client(DartClient), stock, PIPELINE_YEARS, "CFS", mapping,
        now=world_now(), log=lines.append,
    )  # fmt: skip
    got_inputs = {k: jsonable(v) for k, v in inputs.workbook_kwargs().items() if k != "mapping"}
    xlsx, _payload = render(inputs, GENERATED_AT)
    out: list[str] = []
    diff(f"{stock}.calls", exp["calls"], wd.calls, out)
    diff(f"{stock}.log", exp["log"], lines, out)
    diff(f"{stock}.inputs", exp["inputs"], got_inputs, out)
    diff(f"{stock}.workbook", exp["workbook"], normalize(xlsx), out)
    assert not out, "\n".join(out)
    assert formula_eval.formula_count(xlsx) == exp["formulas"]
    assert formula_eval.errors(formula_eval.evaluate(xlsx)) == []


def test_g2_는_함정을_실제로_밟는다() -> None:
    """골든이 노리는 사례가 기대 산출에 남아 있다(빈 비교로 통과하지 않게)."""
    a = read_json(PIPELINE / "994010.json.gz")
    b = read_json(PIPELINE / "994110.json.gz")
    assert a["inputs"]["meta"]["fs_div_used"] == "CFS"
    assert b["inputs"]["meta"]["fs_div_used"] == "OFS"  # CFS 요청 → OFS 폴백
    assert len(a["inputs"]["quarters"]) == 15 and len(a["inputs"]["qcosts"]) == 15
    # 수주: #1 은 원 계약 + 정정이 두 행(legacy 는 정정을 거르지 않는다), #12 는 NIPA 1건
    assert [r["정정"] for r in a["inputs"]["orders"]["rows"]] == ["", "정정"]
    assert b["inputs"]["orders"]["nipa_ratio"] == 1.0
    # 브릿지: '당기손익-공정가치측정…평가이익' 이 '손' 때문에 음수로(legacy 버그 — 고정)
    fvpl = next(s for s in a["inputs"]["bridge_steps"] if s["name"] == "FVPL 평가이익")
    assert fvpl["value"] < 0
    # 주석 표: 연결·별도·본문 세 파일 중 기대 총비용에 맞는 한 표(합 = 기대)
    assert any("합 40,367억 / 기대 40,367억" in line for line in a["log"])
    # 정정본(2025 사업보고서)이 있는 연도 — 큰 접수번호를 썼다
    assert any(c[0] == "document.xml" for c in a["calls"])
