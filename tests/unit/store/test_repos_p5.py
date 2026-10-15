"""P5 메모리 저장소 규칙(docs/p5_design.md §1.1) — 같은 묶음(`repo_cases_p5.CASES`)을 통합 시험
`tests/integration/test_repos_p5_pg.py` 가 Postgres 로 돈다. 여기서는 메모리 구현으로 돌고, 공개
작업 묶음(`PublicRepos`)이 같은 메모리 표를 보는지·공개/로그인 모듈 경계(계약 ⑭ 의 import 모양)를
더 본다.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from kbj.store.repos import Repos, memory_repos
from kbj.store.repos.memory import MemoryRepos
from kbj.store.repos.public import PublicRepos, memory_public_repos
from tests.unit.store.repo_cases_p5 import CASE_IDS, CASES, JOB, NOW, meta

ROOT = Path(__file__).resolve().parents[3]
REPOS = ROOT / "kbj" / "store" / "repos"
# 공개 저장소 모듈 → 로그인 저장소 모듈을 import 하지 않는다(계약 ⑭ — WRAP 이 import-linter 로)
PUBLIC_MODULES = ("macro", "market_stats", "public", "_macro")
PRIVATE_MODULES = {
    "kbj.store.repos.macro_prv", "kbj.store.repos.us",
    "kbj.store.repos.program", "kbj.store.repos.consensus", "kbj.store.repos.journal",
    "kbj.store.repos.market", "kbj.store.repos.flows", "kbj.store.repos.board",
    "kbj.store.repos.etf", "kbj.store.repos.memory", "kbj.store.repos",
}  # fmt: skip


def as_repos(m: MemoryRepos) -> Repos:
    # pyright 가 메모리 구현이 Protocol 을 만족하는지 여기서 확인한다
    return Repos(
        market=m.market, flows=m.flows, board=m.board, etf=m.etf, filings=m.filings, fin=m.fin,
        consensus=m.consensus, journal=m.journal, macro=m.macro, macro_prv=m.macro_prv,
        market_stats=m.market_stats, us=m.us, program=m.program, index_review=m.index_review,
        trade=m.trade, gex=m.gex, diag=m.diag, diag_pub=m.diag_pub,
        rules=m.rules, personal=m.personal,  # P8(0021)
        theme=m.theme, theme_pub=m.theme_pub,  # 페이지 9(0023)
    )  # fmt: skip


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_memory_p5_repos_follow_the_rules(case: Callable[[Repos], None]) -> None:
    case(as_repos(memory_repos()))


def test_public_bundle_shares_the_p5_public_tables() -> None:
    m = memory_repos()
    pub = m.public()
    as_public = PublicRepos(
        filings=pub.filings, fin=pub.fin, macro=pub.macro,
        market_stats=pub.market_stats, diag=pub.diag,
    )  # fmt: skip
    as_public.macro.upsert_meta([meta("FRED:DGS10")], loaded_by=JOB, now=NOW)
    assert [x.series_id for x in m.macro.meta()] == [
        "FRED:DGS10"
    ]  # 공개 작업이 쓴 것을 API 가 본다
    assert m.macro_prv.meta() == []  # 로그인 표와는 다른 객체
    alone = memory_public_repos()
    assert alone.macro.meta() == []


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


@pytest.mark.parametrize("name", PUBLIC_MODULES)
def test_public_repo_modules_do_not_import_login_repos(name: str) -> None:
    got = _imports(REPOS / f"{name}.py")
    assert not got & PRIVATE_MODULES, (name, sorted(got & PRIVATE_MODULES))
    assert not any(m.startswith(("kbj.data", "kbj.services", "kbj.engines")) for m in got)
