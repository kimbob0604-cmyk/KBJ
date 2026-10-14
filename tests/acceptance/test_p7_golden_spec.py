"""P7 완료 시험 — GEXLAB 골든·명세가 KBJ 에서 녹색이고, 조용히 덜 모이지 않는다(docs/p7_design.md
§0.3·§1.7-2, WRAP).

- 수: 골든(코어 22·체인 부분집합 14·raw 32 — 같아야 한다)과 명세(GX 단위 13파일 1,457 + 부호 9 =
  `tests/unit/core/gex` 1,466, 속성 46 — 이 수 이상)를 `pytest --collect-only` 로 센다. 옮긴 수는
  `docs/migration/gexlab.md`(P9 WRAP — 옛 legacy/gexlab/MIGRATION.md) 'P7' 표와 같다
  (FND-C 1,548 = 1,466 + 46 + 36). 엔진 승격 146 + 새 3,
  진단 명세 87 + 속성 5 도 같은 방식으로 아래쪽 한계를 둔다.
- 녹색: 골든 비교 시험 셋을 실제로 돌리고(허용오차 `TOLERANCES` 그대로 — 넓히지 않는다), 골든 CLI
  (`gex_golden core`·`recorder.golden raw-parse`)가 재생성 없이 기존 파일과 같다며 종료 0.
- 골든 JSON 은 `git mv` 로 옮겼다. WRAP 확인(2026-10-13 `git diff -M`): raw 녹화(jsonl)는 바이트까지
  같고, 코어·raw-parse 기대값 JSON 은 메타 문자열(`_about`·`_regenerate`)과 입력 경로 문자열만
  KBJ 경로로 바뀌었다(숫자·fixture sha256 그대로).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# (경로, 기대 수) — 골든은 같아야 한다(기대값 파일 수정 금지), 명세는 아래쪽 한계
GOLDEN = (
    ("tests/golden/gex/test_core_golden.py", 22),
    ("tests/golden/gex/test_chain_subset.py", 14),
    ("tests/golden/gex/test_raw_golden.py", 32),
)
SPEC = (
    ("tests/unit/core/gex", 1466),  # 단위 13파일 1,457 + 부호 9
    ("tests/unit/core/gex/test_sign_conventions.py", 9),
    ("tests/property/gex", 46),
    ("tests/unit/engines/gex", 146),  # ENG 승격 9파일(+ 새 시험)
    ("tests/unit/engines/diagnosis", 80),  # metrics §D1~§D9 명세(test_spec_*)·작업
    ("tests/property/test_diag_props.py", 5),
)
_COLLECTED = re.compile(r"(\d+) tests? collected")


def _collected(path: str) -> int:
    out = subprocess.run(  # noqa: S603 — 고정 인자
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", "-W", "ignore",
         path],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    m = _COLLECTED.search(out.stdout)
    assert out.returncode == 0 and m, out.stdout[-2000:] + out.stderr[-2000:]
    return int(m.group(1))


@pytest.mark.parametrize(("path", "n"), GOLDEN)
def test_golden_counts_are_exact(path: str, n: int) -> None:
    assert _collected(path) == n


@pytest.mark.parametrize(("path", "n"), SPEC)
def test_spec_counts_do_not_shrink(path: str, n: int) -> None:
    assert _collected(path) >= n


def test_fnd_c_moved_count_matches_migration() -> None:
    """FND-C 옮긴 수 1,548 = 명세 단위·부호 1,466 + 속성 46 + 골든 코어·부분집합 36."""
    total = sum(_collected(p) for p in ("tests/unit/core/gex", "tests/property/gex"))
    total += 22 + 14
    assert total >= 1548
    text = (ROOT / "docs" / "migration" / "gexlab.md").read_text(encoding="utf-8")
    assert "FND-C 소계 | **1,548**" in text


def test_golden_tests_are_green() -> None:
    out = subprocess.run(  # noqa: S603 — 고정 인자
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore",
         *(p for p, _ in GOLDEN)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stdout[-3000:]
    assert out.stdout.count(".") >= 68 and "F" not in out.stdout.split("[")[0], out.stdout[-500:]


@pytest.mark.parametrize(
    "cmd",
    [
        ("kbj.services.engine.gex_golden", "core"),
        ("kbj.services.recorder.golden", "raw-parse"),
    ],
)
def test_golden_cli_matches_files_without_regenerating(cmd: tuple[str, str]) -> None:
    out = subprocess.run(  # noqa: S603 — 고정 인자
        [sys.executable, "-m", *cmd], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
