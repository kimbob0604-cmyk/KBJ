"""골든 자체를 지킨다 — legacy 커밋 고정·파일 체크섬 고정·매핑 이동이 내용을 바꾸지 않았는지.

docs/p4_design.md §4.6 '순서 규칙'·§8.1: 골든은 shim(D-P4-6 ④) **전** legacy 로 캡처했다
(`make_golden.py` 가 그 커밋을 `git archive` 로 풀어 돌린다). 골든 파일은 그 뒤 고치지 않는다 —
바꿀 일은 새 골든 세트 + ADR(D-P4-8). openpyxl 판이 바뀌면(R8) legacy 커밋에서 다시 캡처한다.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import version

from kbj.config.files import config_path
from tests.golden.dart_report.golden import (
    BUILDER_CASES,
    HERE,
    PIPELINE_COMPANIES,
    PIPELINE_YEARS,
    load_meta,
)

LEGACY_COMMIT = "607b761"  # P3 완료(ADR 0018) 커밋 — dart-report 는 P2 재배선 그대로, shim 전


def test_legacy_커밋이_shim_전으로_고정돼_있다() -> None:
    meta = load_meta()
    assert meta["legacy_commit"] == LEGACY_COMMIT
    assert "shim 전" in meta["legacy_source"] and "git archive" in meta["legacy_source"]
    assert meta["builder_cases"] == list(BUILDER_CASES) and len(BUILDER_CASES) == 8
    p = meta["pipeline"]
    assert p["companies"] == list(PIPELINE_COMPANIES) and p["years"] == list(PIPELINE_YEARS)
    assert p["world_seed"] == 20261007 and p["world_clock"] == "2026-11-16T21:30:00+09:00"
    assert meta["compare"]["rel_tol"] == 1e-9 and meta["compare"]["abs_tol"] == 1e-9


def test_골든_파일_체크섬이_그대로다() -> None:
    meta = load_meta()
    want: dict[str, str] = meta["checksums"]
    files = [*HERE.glob("inputs/*.json"), *HERE.glob("builder/*.json.gz"),
             *HERE.glob("pipeline/*.json.gz")]  # fmt: skip
    got = {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert got == want
    assert len(want) == 2 * len(BUILDER_CASES) + len(PIPELINE_COMPANIES)


def test_매핑은_옮겨도_내용이_같다() -> None:
    """DR config/mapping.yaml → config/dart_report/mapping.yaml(D-P4-6 ④ — 경로만)."""
    data = config_path("dart_report/mapping.yaml").read_bytes()
    assert hashlib.sha256(data).hexdigest() == load_meta()["mapping_sha256"]


def test_openpyxl_판이_캡처_때와_같다() -> None:
    """차트 XML 직렬화는 openpyxl 판에 민감하다(R8) — 다르면 legacy 커밋에서 다시 캡처."""
    cap = load_meta()["openpyxl"].split(".")[:2]
    assert version("openpyxl").split(".")[:2] == cap
