"""데모 이미지 입력 — 데모 서버가 import 하는 tests.* 모듈·데이터가 데모 dockerignore 허용 목록 안.

import 는 정적으로(AST — 함수 안 import 까지) 따라간다: scripts/demo_server.py 에서 시작해 tests.*
모듈의 파일을 찾고, 그 모듈이 다시 import 하는 tests.* 를 끝까지 모은다. 실행해서 모으지 않는
까닭: 합성 세계 적재는 2분이 걸리고(test_demo_offline 이 한 번 한다), 정적 추적은 조건부 import 도
놓치지 않는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import check_public_outputs as cpo

ROOT = Path(__file__).resolve().parents[3]
IGNORE = ROOT / "Dockerfile.demo.dockerignore"
SERVER = ROOT / "scripts" / "demo_server.py"


def _tests_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith("tests."):
                out.add(node.module)
                # from tests.pkg import mod — mod 가 모듈일 수 있다
                out |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.startswith("tests.")}
    return out


def _module_file(mod: str) -> Path | None:
    base = ROOT.joinpath(*mod.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None  # 이름(클래스·함수)이지 모듈이 아니다 — 또는 __init__ 없는 이름공간 폴더


def demo_test_modules() -> dict[str, Path]:
    todo = list(_tests_imports(SERVER))
    seen: dict[str, Path] = {}
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        f = _module_file(mod)
        if f is None:
            continue
        seen[mod] = f
        todo += sorted(_tests_imports(f))
        # 부모 패키지 __init__ 도 import 때 실행된다
        parts = mod.split(".")
        for i in range(2, len(parts)):
            parent = ".".join(parts[:i])
            if (pf := _module_file(parent)) is not None and parent not in seen:
                todo.append(parent)
                seen.setdefault(parent, pf)
    return seen


def _rules() -> list[cpo.IgnoreRule]:
    rules, first = cpo.parse_dockerignore(IGNORE.read_text(encoding="utf-8"))
    assert first == "*"
    return rules


def test_every_imported_test_module_is_in_the_image() -> None:
    mods = demo_test_modules()
    # 실제로 추적했는지(빈 목록으로 통과하지 않게) — 합성 세계 다섯·웹 픽스처 셋·씨앗 셋
    for must in (
        "tests.unit.api.api_world",
        "tests.fixtures.synthetic.p5_world",
        "tests.fixtures.synthetic.p7_world",
        "tests.unit.api.web_fixtures_p8.journal",
        "tests.unit.engines.gex.engine_inputs",
        "tests.fakes.ws_server",
    ):
        assert must in mods, must
    rules = _rules()
    missing = sorted(
        p.relative_to(ROOT).as_posix()
        for p in mods.values()
        if not cpo.dockerignore_allows(rules, p.relative_to(ROOT).as_posix())
    )
    assert missing == [], missing


def test_data_files_the_world_reads_are_in_the_image() -> None:
    from tests.unit.api.api_world import GOLDEN_DAY

    rel = Path(GOLDEN_DAY).resolve().relative_to(ROOT).as_posix()
    assert cpo.dockerignore_allows(_rules(), rel), rel


def test_server_runtime_inputs_in_and_secrets_out() -> None:
    rules = _rules()
    inside = (
        "scripts/demo_server.py", "kbj/__init__.py", "config/markets.yaml", "pyproject.toml",
        "uv.lock", "web/package.json", "web/src/main.ts", "tests/fixtures/synthetic/p6_world.py",
    )  # fmt: skip
    for rel in inside:
        assert cpo.dockerignore_allows(rules, rel), rel
    outside = (
        *cpo.DEMO_FORBIDDEN_PROBES, "web/node_modules/vite/package.json",
        "web/dist-login/index.html", "web/test/scripts.test.ts", "tests/unit/api/test_auth.py",
        "scripts/check_canonical.py",
        "docs/PLAN.md", ".git/config", "kbj/__pycache__/x.pyc",
    )  # fmt: skip
    for rel in outside:
        assert not cpo.dockerignore_allows(rules, rel), rel


def test_allow_list_has_no_stale_test_entries() -> None:
    """허용 목록의 tests/ 개별 파일 줄은 모두 실제 파일이다(지운 모듈이 목록에 남지 않게)."""
    for line in IGNORE.read_text(encoding="utf-8").splitlines():
        if line.startswith("!tests/") and "*" not in line:
            assert (ROOT / line[1:]).is_file(), line
