"""G3 — 실데이터 골든(키 수령 뒤 #30). fixture 가 없으면 건너뛴다(docs/p4_design.md §4.6·§8.8).

DART 는 공개 등급이라 실응답 fixture 를 커밋할 수 있다(`tests/fixtures/public/dart/report/**`).
캡처는 `make_golden.py --legacy-commit 607b761` 과 같은 방식(legacy 는 `git archive` 로 shim 전
커밋에서)으로 하고, 비교는 G2 와 같다. 지금은 fixture 가 없다 — 사유를 남기고 건너뛴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parents[2] / "fixtures" / "public" / "dart" / "report"
GOLDEN = Path(__file__).resolve().parent / "real"


def test_실데이터_골든() -> None:
    cases = sorted(GOLDEN.glob("*.json.gz")) if GOLDEN.is_dir() else []
    if not REAL.is_dir() or not cases:
        pytest.skip("실데이터 골든 없음 — DART 키 수령 후 #30(docs/probe_results.md §7)")
    raise AssertionError("G3 비교는 fixture 를 넣을 때 G2 하네스(WorldDart 대신 기록 재생)로 쓴다")
