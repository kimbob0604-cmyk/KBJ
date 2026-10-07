"""import 규칙 — import-linter 계약이 실제로 위반을 잡는지, kbj 가 legacy 를 못 쓰는지.

1. kbj/ 의 모든 import 는 kbj 자신·표준 라이브러리·선언한 런타임 의존성뿐이다. legacy 의 최상위 이름
   (`core`·`db`·`board`·`server` …)은 legacy 폴더가 sys.path 에 올라와야만 import 되는데, 이 시험은
   이름 목록을 따로 들지 않고 '허용된 것만'을 본다 — legacy 를 어떤 이름으로든 끌어오면 실패한다.
2. 일곱 계약 각각에 위반을 하나씩 심은 사본에서 lint-imports 가 그 계약 하나만 깨졌다고
   하는지 본다(계약이 빈말이 아님). ④~⑦ 은 P2 묶음 I 가 더했다(docs/p2_design.md §9.6):
   ④ KIS 발급 auth 밖 import 금지, ⑤ 텔레그램 HTTP 는 notifier 안에서만(간접 포함 — shim·outbox 의
   Attachment 두 줄만 예외), ⑥ 어댑터는 서비스를 모른다, ⑦ 저장소는 어댑터·서비스를 모른다.
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
# pyproject `dependencies` 의 import 이름. P2 S0: exchange_calendars(캘린더)·defusedxml(외부 XML).
# fastapi·uvicorn 은 P3(메인 결정 D4)
RUNTIME_IMPORTS = {
    "defusedxml",
    "exchange_calendars",
    "httpx",
    "psycopg",
    "pydantic",
    "pydantic_settings",
    "redis",
    "yaml",
}
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
        # ④ — 발급자 클래스는 kbj/services/auth/issuer.py 에만(메인 결정 D1).
        #     어느 서비스든 끌어오면 실패
        ("kbj/services/scheduler/_leak.py", "import kbj.services.auth.issuer\n", "④"),
        ("kbj/services/notifier/_leak.py", "from kbj.services.auth import service\n", "④"),
        ("kbj/engines/_leak.py", "import kbj.services.auth\n", "④"),
        ("kbj/services/collectors/_leak.py", "import kbj.services.auth\n", "④"),
        # ⑤ — 텔레그램 HTTP 클라이언트를 notifier 밖에서 직접 쓰면 실패
        ("kbj/services/scheduler/_tg.py", "import kbj.services.notifier.telegram_api\n", "⑤"),
        (
            "kbj/services/ops/_tg.py",
            "from kbj.services.notifier.telegram_api import TelegramApi\n",
            "⑤",
        ),
        #     발송 루프(notifier.service)를 거쳐 간접으로 끌어와도 실패 — shim(client)만 허용
        ("kbj/services/scheduler/_svc.py", "import kbj.services.notifier.service\n", "⑤"),
        (
            "kbj/services/collectors/_svc.py",
            "from kbj.services.notifier.service import NotifierService\n",
            "⑤",
        ),
        # ⑥ — 어댑터가 서비스·엔진·리포트를 알면 실패
        ("kbj/data/_leak.py", "import kbj.services.runtime\n", "⑥"),
        ("kbj/data/public/dart/_leak.py", "import kbj.engines\n", "⑥"),
        ("kbj/data/private/_leak.py", "import kbj.reports\n", "⑥"),
        # ⑦ — 저장소가 어댑터·서비스·엔진을 알면 실패
        ("kbj/store/_leak.py", "import kbj.data.spec\n", "⑦"),
        ("kbj/store/legacy_import/_leak.py", "import kbj.services.scheduler\n", "⑦"),
        ("kbj/store/_eng.py", "import kbj.engines\n", "⑦"),
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
    assert "Contracts: 6 kept, 1 broken." in broken.stdout, broken.stdout
    _, _, section = broken.stdout.partition("Broken contracts")
    assert contract in section, broken.stdout
