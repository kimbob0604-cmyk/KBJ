"""import 규칙 — import-linter 계약이 실제로 위반을 잡는지, kbj 가 legacy 를 못 쓰는지.

1. kbj/ 의 모든 import 는 kbj 자신·표준 라이브러리·선언한 런타임 의존성뿐이다. legacy 의 최상위 이름
   (`core`·`db`·`board`·`server` …)은 legacy 폴더가 sys.path 에 올라와야만 import 되는데, 이 시험은
   이름 목록을 따로 들지 않고 '허용된 것만'을 본다 — legacy 를 어떤 이름으로든 끌어오면 실패한다.
2. 열 계약 각각에 위반을 하나씩 심은 사본에서 lint-imports 가 기대한 계약만 깨졌다고
   하는지 본다(계약이 빈말이 아님). ④~⑦ 은 P2 묶음 I 가 더했다(docs/p2_design.md §9.6):
   ④ KIS 발급 auth 밖 import 금지, ⑤ 텔레그램 HTTP 는 notifier 안에서만(간접 포함 — shim·outbox 의
   Attachment 두 줄만 예외), ⑥ 어댑터는 서비스를 모른다, ⑦ 저장소는 어댑터·서비스를 모른다.
   ⑧~⑩ 은 P3 묶음 S 가 더했다(docs/p3_design.md §2·§7.4·D-P3-3·D-P3-5): ⑧ 공개 내보내기는
   로그인 등급 코드를 모른다, ⑨ 엔진은 순수 계산(I/O 금지), ⑩ 로그인 API 는 로그인 원천
   어댑터·수집기를 모른다.
   위반 하나가 두 계약에 함께 걸리면(예: 엔진이 auth 를 import — ④·⑨) 둘 다 기대값에 적는다.
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
    "fastapi",  # P3 로그인 API(docs/p3_design.md §1.1·D-P3-3)
    "starlette",  # fastapi 가 끌어온다(정적 파일·미들웨어)
    "uvicorn",
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
        ("kbj/engines/_leak.py", "import kbj.services.auth\n", "④⑨"),  # 엔진은 ⑨ 에도 걸린다
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
        # ⑧ — 공개 내보내기가 로그인 등급 코드(원천 어댑터·엔진·API·수집기·SQL 저장소)를 알면 실패
        ("kbj/services/public_export/_leak.py", "import kbj.data.private.krx\n", "⑧"),
        ("kbj/services/public_export/_eng.py", "import kbj.engines.board\n", "⑧"),
        ("kbj/services/public_export/_repo.py", "from kbj.store.repos import Repos\n", "⑧"),
        ("kbj/services/public_export/_api.py", "import kbj.services.api\n", "⑧"),
        # ⑨ — 엔진이 저장소·어댑터·네트워크/DB 라이브러리를 알면 실패
        ("kbj/engines/flows/_leak.py", "import kbj.store.repos\n", "⑨"),
        ("kbj/engines/board/_io.py", "import httpx\n", "⑨"),
        ("kbj/engines/etf/_io.py", "import psycopg\n", "⑨"),
        ("kbj/engines/market/_svc.py", "import kbj.services.runtime\n", "⑨"),
        # ⑩ — 로그인 API 가 원천 어댑터·수집기를 끌어오면(간접 포함) 실패
        ("kbj/services/api/_leak.py", "import kbj.data.private.kis.rest\n", "⑩"),
        ("kbj/services/api/_col.py", "from kbj.services.collectors import krx_daily\n", "⑩"),
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
    n_contracts = clean.stdout.count(" KEPT")
    want = set(contract)
    assert f"Contracts: {n_contracts - len(want)} kept, {len(want)} broken." in broken.stdout, (
        broken.stdout
    )
    _, _, section = broken.stdout.partition("Broken contracts")
    assert all(c in section for c in want), broken.stdout


def test_ten_contracts_are_declared() -> None:
    """P3 묶음 S — 계약 ⑧~⑩ 이 실제로 들어 있다(이름 첫 글자 기호로)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    names = [
        line.split("=", 1)[1].strip().strip('"')
        for line in text.splitlines()
        if line.startswith("name = ")
        and line.split("=", 1)[1].strip().strip('"')[:1] in "①②③④⑤⑥⑦⑧⑨⑩"
    ]
    assert [n[0] for n in names] == list("①②③④⑤⑥⑦⑧⑨⑩")
