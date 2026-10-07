"""컨테이너 시험 마크 — `integration` 과 별칭 `docker` (tests/conftest.py).

작은 임시 프로젝트(저장소 tests/conftest.py 의 훅만 가져온다)를 `pytest --collect-only` 로 따로 돌려
`-m` 이 실제로 무엇을 고르는지 본다 — 훅이 `-m` 거르기보다 먼저 도는지까지. 컨테이너는 없다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_TESTS = """
import pytest

@pytest.mark.integration
def test_new_mark() -> None: ...

@pytest.mark.docker
def test_old_mark() -> None: ...

def test_unit() -> None: ...
"""


def _selected(tmp_path: Path, expr: str) -> set[str]:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    integration: x\n    docker: x\n", encoding="utf-8"
    )
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import pytest_collection_modifyitems  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "test_marks.py").write_text(_TESTS, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    args = ["--collect-only", "-q", "-p", "no:cacheprovider", "-m", expr, str(tmp_path)]
    out = subprocess.run(  # noqa: S603 — 고정 인자로 이 인터프리터의 pytest 만 부른다
        [sys.executable, "-m", "pytest", *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
        check=False,
    )
    assert out.returncode in (0, 5), out.stdout + out.stderr  # 5 = 고른 시험 없음
    return {line.split("::")[-1] for line in out.stdout.splitlines() if "::" in line}


@pytest.mark.parametrize(
    ("expr", "want"),
    [
        ("not docker", {"test_unit"}),  # 기존 검사 명령 — 새 마크 시험도 빠진다
        ("not integration", {"test_unit"}),  # 옛 마크 시험도 빠진다
        ("not integration and not docker", {"test_unit"}),
        ("integration", {"test_new_mark", "test_old_mark"}),
        ("docker or integration", {"test_new_mark", "test_old_mark"}),
    ],
)
def test_integration_and_docker_select_the_same_container_tests(
    tmp_path: Path, expr: str, want: set[str]
) -> None:
    assert _selected(tmp_path, expr) == want
