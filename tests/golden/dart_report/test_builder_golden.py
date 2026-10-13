"""G1 — 빌더 골든: 합성 입력 8벌을 kbj 엔진·빌더로 돌려 legacy 캡처(정규화)와 필드 단위 비교.

docs/p4_design.md §8.1·§4.6. 기대 산출은 shim 전 legacy dart-report 가 만든 것이다
(`make_golden.py` — `git archive` 커밋 고정, META.json). 비교: 중간값(분기·연간·비용·브릿지·수주)과
엑셀(시트·셀 값·서식·열 너비·병합·차트 XML)을 실수만 허용오차로. 실패 메시지는 (경로, legacy, kbj).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.golden.dart_report.golden import (
    BUILDER,
    BUILDER_CASES,
    Mods,
    read_json,
    run_builder_case,
)
from tests.oracles.xlsx_normalize import diff, normalize


@pytest.mark.parametrize("name", BUILDER_CASES)
def test_빌더는_legacy_와_같다(name: str, kbj_mods: Mods, mapping: dict[str, Any]) -> None:
    expected = read_json(BUILDER / f"{name}.json.gz")
    inter, xlsx = run_builder_case(kbj_mods, name, mapping)
    out: list[str] = []
    diff(f"{name}.intermediate", expected["intermediate"], inter, out)
    diff(f"{name}.workbook", expected["workbook"], normalize(xlsx), out)
    assert not out, "\n".join(out)


@pytest.mark.parametrize("name", BUILDER_CASES)
def test_차트_xml_도_비교한다(name: str) -> None:
    """정규화 기대값에 차트·드로잉 파트가 실제로 들어 있다(비교가 비어서 통과하지 않게)."""
    wb = read_json(BUILDER / f"{name}.json.gz")["workbook"]
    charts = [p for p in wb["parts"] if p.startswith("xl/charts/chart")]
    drawings = [p for p in wb["parts"] if p.startswith("xl/drawings/drawing")]
    # 빈 블록 사례(④)는 브릿지·분기 비용 차트가 없다 — 연간비용구조·분기실적 차트 둘은 언제나 있다
    assert len(charts) >= 2 and drawings, (name, sorted(wb["parts"]))
    assert wb["sheets"] == [
        "요약", "연간비용구조", "분기실적", "이익브릿지", "수주현황", "분기비용구조",
        "RAW_분기재무", "RAW_미매핑계정",
    ]  # fmt: skip


def test_비교기는_차트_참조_한_칸_차이를_잡는다(kbj_mods: Mods, mapping: dict[str, Any]) -> None:
    """off-by-one(DR CLAUDE.md '지뢰') — 차트 시리즈 참조가 한 행 밀리면 실패해야 한다."""
    expected = read_json(BUILDER / "c1_smoke.json.gz")["workbook"]
    _, xlsx = run_builder_case(kbj_mods, "c1_smoke", mapping)
    got = normalize(xlsx)
    part = "xl/charts/chart2.xml"  # 분기실적 — 막대 3계열 + OPM 선
    assert "'분기실적'!$A$2:$A$15" in got["parts"][part]
    got["parts"][part] = got["parts"][part].replace("$A$2:$A$15", "$A$3:$A$16", 1)
    out: list[str] = []
    diff("wb", expected, got, out)
    assert out and part in out[0]
