"""import 규칙 — import-linter 계약이 실제로 위반을 잡는지, kbj 가 legacy 를 못 쓰는지.

1. kbj/ 의 모든 import 는 kbj 자신·표준 라이브러리·선언한 런타임 의존성뿐이다. legacy 의 최상위 이름
   (`core`·`db`·`board`·`server` …)은 legacy 폴더가 sys.path 에 올라와야만 import 되는데, 이 시험은
   이름 목록을 따로 들지 않고 '허용된 것만'을 본다 — legacy 를 어떤 이름으로든 끌어오면 실패한다.
2. 세 계약 각각에 위반을 하나씩 심은 사본에서 lint-imports 가 실패하는지 본다(계약이 빈말이 아님).
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_IMPORTS = {"pydantic", "pydantic_settings", "httpx", "psycopg", "redis", "yaml"}
LINT_IMPORTS = Path(sys.executable).with_name("lint-imports")


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def test_kbj_imports_only_kbj_stdlib_and_declared_deps() -> None:
    allowed = {"kbj", "__future__"} | set(sys.stdlib_module_names) | RUNTIME_IMPORTS
    bad = [
        f"{p.relative_to(ROOT)}:{line}: {name}"
        for p in sorted((ROOT / "kbj").rglob("*.py"))
        for line, name in _imports(p)
        if name.split(".")[0] not in allowed
    ]
    assert bad == []


@pytest.mark.skipif(not LINT_IMPORTS.exists(), reason="lint-imports 가 이 환경에 없다(dev 그룹)")
@pytest.mark.parametrize(
    ("module", "violation", "contract"),
    [
        ("kbj/data/public/_leak.py", "import kbj.data.private\n", "①"),
        ("kbj/engines/_leak.py", "import legacy.gexlab\n", "②"),
        ("kbj/core/_leak.py", "from kbj.services import notifier\n", "③"),
        ("kbj/core/_io.py", "import httpx\n", "③"),
    ],
)
def test_contract_catches_violation(
    tmp_path: Path, module: str, violation: str, contract: str
) -> None:
    shutil.copytree(ROOT / "kbj", tmp_path / "kbj", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    # 설치된 kbj 보다 사본이 먼저 잡히게. 넓은 폭으로 — 계약 이름이 줄바꿈되지 않게
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "COLUMNS": "400"}

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — 고정 인자
            [str(LINT_IMPORTS), "--no-cache", "--config", "pyproject.toml"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    clean = run()
    assert clean.returncode == 0, clean.stdout + clean.stderr
    (tmp_path / module).write_text(violation, encoding="utf-8")
    broken = run()
    assert broken.returncode != 0
    assert "Contracts: 2 kept, 1 broken." in broken.stdout, broken.stdout
    _, _, section = broken.stdout.partition("Broken contracts")
    assert contract in section, broken.stdout
