"""계약 ⑧ 의 실행 확인 — 공개 내보내기를 import 하면 로그인 등급 코드가 하나도 같이 올라오지 않는다.

import-linter 계약 ⑧(`kbj.services.public_export` → `kbj.data.private`·`kbj.engines`·
`kbj.services.{api,collectors,engine}`·`kbj.store.repos` 금지)은 묶음 S 가 웨이브 3 에 pyproject 에
넣는다(R20). 그 전에도 깨지지 않게, 새 해석기에서 패키지를 import 한 뒤 `sys.modules` 를 본다
(간접 import 포함). 같은 금지 목록으로 import-linter 를 임시 설정에 돌려 정적으로도 확인한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = (
    "kbj.data.private",
    "kbj.engines",
    "kbj.services.api",
    "kbj.services.collectors",
    "kbj.services.engine",
    "kbj.store.repos",
    "kbj.services.auth",
)
LINT_IMPORTS = Path(sys.executable).with_name("lint-imports")


def test_runtime_import_closure_has_no_login_tier_modules() -> None:
    code = (
        "import sys, json\n"
        "import kbj.services.public_export, kbj.services.public_export.__main__\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('kbj'))))\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    loaded: list[str] = json.loads(out.stdout.strip().splitlines()[-1])
    assert "kbj.services.public_export.export" in loaded
    bad = [m for m in loaded if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]
    assert bad == []


@pytest.mark.skipif(not LINT_IMPORTS.exists(), reason="lint-imports 가 이 환경에 없다(dev 그룹)")
def test_import_linter_contract_8_holds(tmp_path: Path) -> None:
    forbidden = ", ".join(f'"{m}"' for m in FORBIDDEN)
    cfg = tmp_path / "contract8.toml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            [tool.importlinter]
            root_packages = ["kbj"]
            include_external_packages = true

            [[tool.importlinter.contracts]]
            name = "⑧ 공개 내보내기는 로그인 등급 코드를 import 하지 않는다"
            type = "forbidden"
            source_modules = ["kbj.services.public_export"]
            forbidden_modules = [{forbidden}]
            """
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(  # noqa: S603
        [str(LINT_IMPORTS), "--config", str(cfg), "--no-cache"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
