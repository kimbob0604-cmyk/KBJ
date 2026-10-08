"""정적 시험 — 공개 내보내기 패키지 안 SQL 문자열의 스키마 이름이 전부 `pub_` 로 시작(§7.4).

docstring 은 설명 글이라 빼고, 나머지 문자열 상수에서 SQL 처럼 보이는 것(SELECT·FROM·JOIN …)의
`스키마.표` 참조를 모은다. 로그인 등급 스키마(`prv_*`)·운영(`ops`)·기본 스키마(`public`)는 SQL 이
아니어도 문자열에 나오면 실패한다. 검사가 빈말이 아님을 심은 위반으로 확인한다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "kbj" / "services" / "public_export"
_SQL_WORD = re.compile(r"\b(SELECT|FROM|JOIN|INSERT|UPDATE|DELETE|COPY|TABLE|INTO)\b", re.I)
_QUALIFIED = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|COPY)\s+\"?([a-z_][a-z0-9_]*)\"?\s*\.", re.I
)
_LOGIN_SCHEMA = re.compile(r"\bprv_[a-z]|\bops\.[a-z]|\bpublic\.[a-z]")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def string_constants(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    docs = _docstring_nodes(tree)
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]


def violations(source: str) -> list[str]:
    bad: list[str] = []
    for line, s in string_constants(source):
        if _LOGIN_SCHEMA.search(s):
            bad.append(f"{line}: 로그인 등급·운영 스키마 이름")
        if _SQL_WORD.search(s):
            bad += [
                f"{line}: 스키마 {m}"
                for m in _QUALIFIED.findall(s)
                if not m.lower().startswith("pub_")
            ]
    return bad


def _files() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def test_package_exists() -> None:
    assert len(_files()) >= 7  # __init__·__main__·export·calendar_json·events_json·manifest·push


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_sql_strings_only_pub_schemas(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "planted",
    [
        'Q = "SELECT * FROM prv_market.daily_bar"\n',
        'Q = "SELECT x FROM pub_trade.a JOIN ops.job_run r ON true"\n',
        'Q = "select * from public.t"\n',
        'Q = "INSERT INTO market.x VALUES (1)"\n',
        'SCHEMA = "prv_flows"\n',
    ],
)
def test_checker_catches_planted_violation(planted: str) -> None:
    assert violations(planted)


def test_checker_allows_pub_and_docstrings() -> None:
    ok = (
        '"""prv_market 은 읽지 않는다(설명)."""\n'
        'Q = "SELECT a FROM pub_trade.monthly JOIN pub_macro.x USING (d)"\n'
    )
    assert violations(ok) == []
