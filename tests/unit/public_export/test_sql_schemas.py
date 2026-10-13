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

from kbj.services.public_export.pub_read import Query

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
    # __init__·__main__·export·calendar_json·events_json·manifest·push + P4 corp_index·filings_json·
    # fin_json·pub_read
    assert len(_files()) >= 11


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


# ── P4 — 이름 붙은 SQL(Query) 상수 ─────────────────────────────────────────────────────────


def _queries() -> list[tuple[str, Query]]:
    import importlib

    out: list[tuple[str, Query]] = []
    for mod in ("corp_index", "filings_json", "fin_json"):
        m = importlib.import_module(f"kbj.services.public_export.{mod}")
        out += [(f"{mod}.{k}", v) for k, v in vars(m).items() if isinstance(v, Query)]
    return out


_CTE = re.compile(r"\b(?:WITH|,)\s*([a-z_][a-z0-9_]*)\s+AS\s*\(", re.I)
_TARGET = re.compile(r"\b(?:FROM|JOIN)\s+(?:LATERAL\s+)?(?!\(|LATERAL\b)([A-Za-z_][\w.]*)", re.I)


def unqualified_tables(sql: str) -> list[str]:
    """FROM·JOIN 대상 중 스키마가 없고 같은 질의의 CTE 도 아닌 이름(하위 질의 `(` 는 뺀다)."""
    ctes = {c.lower() for c in _CTE.findall(sql)}
    return [t for t in _TARGET.findall(sql) if "." not in t and t.lower() not in ctes]


def test_unqualified_table_checker_is_not_empty() -> None:
    assert unqualified_tables("SELECT a FROM disclosure") == ["disclosure"]
    assert unqualified_tables("SELECT a FROM pub_fin.x JOIN LATERAL foo f ON true") == ["foo"]
    ok = (
        "WITH days AS (SELECT d FROM pub_filings.disclosure) SELECT x FROM pub_filings.disclosure"
        " t JOIN days ON true LEFT JOIN LATERAL (SELECT 1 FROM pub_fin.ratio) s ON true"
    )
    assert unqualified_tables(ok) == []


def test_p4_queries_read_only_pub_tables() -> None:
    qs = _queries()
    assert len(qs) >= 12
    for where, q in qs:
        sql = q.sql
        assert violations(f"Q = {sql!r}\n") == [], where
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)\.([a-z_]+)", sql, re.I))
        assert tables, where
        assert {s for s, _ in tables} <= {"pub_filings", "pub_fin"}, where
        # 스키마 없는 표 이름도 없다(search_path 로 public.* 를 읽을 수 있다) — 하위 질의·CTE 예외
        assert unqualified_tables(sql) == [], where
        # 읽기만 — 쓰기·DDL 문 없음
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT)\b", sql, re.I
        )
        assert ";" not in sql, where


def test_p4_query_names_unique_and_answered_by_memory_reader() -> None:
    from tests.unit.public_export.pub_rows import MemReader

    names = [q.name for _, q in _queries()]
    # 같은 Query 객체를 다른 모듈이 다시 내보낸 것은 한 번으로 센다
    assert len(set(names)) == len({id(q) for _, q in _queries()})
    for n in set(names):
        assert hasattr(MemReader, f"q_{n}"), f"시험용 메모리 읽기에 질의 {n} 이 없다"
