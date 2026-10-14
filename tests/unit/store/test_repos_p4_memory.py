"""P4 메모리 저장소 규칙(docs/p4_design.md §1.1) — 같은 묶음(`repo_cases_p4.CASES`)을 통합 시험
`tests/integration/test_repos_p4_pg.py` 가 Postgres 로 돈다. 여기서는 메모리 구현으로 돌고,
공개 작업 묶음(`PublicRepos`)이 같은 메모리 표를 보는지·패키지 경계(⑬)를 더 본다.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from kbj.store.repos import Repos, memory_repos
from kbj.store.repos.memory import MemoryRepos
from kbj.store.repos.public import PublicRepos, memory_public_repos
from tests.unit.store.repo_cases_p4 import CASE_IDS, CASES, JOB, NOW, disc, rc

ROOT = Path(__file__).resolve().parents[3]


def as_repos(m: MemoryRepos) -> Repos:
    # pyright 가 메모리 구현이 Protocol(FilingsRepo 등)을 만족하는지 여기서 확인한다
    return Repos(
        market=m.market, flows=m.flows, board=m.board, etf=m.etf, filings=m.filings, fin=m.fin,
        consensus=m.consensus, journal=m.journal, macro=m.macro, macro_prv=m.macro_prv,
        market_stats=m.market_stats, us=m.us,
        program=m.program, index_review=m.index_review, trade=m.trade,
        gex=m.gex, diag=m.diag, diag_pub=m.diag_pub,
        rules=m.rules, personal=m.personal,  # P8(0021)
    )  # fmt: skip


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_memory_p4_repos_follow_the_rules(case: Callable[[Repos], None]) -> None:
    case(as_repos(memory_repos()))


def test_public_bundle_shares_the_same_tables() -> None:
    m = memory_repos()
    pub = m.public()
    as_public: PublicRepos = PublicRepos(  # Protocol 확인
        filings=pub.filings,
        fin=pub.fin,
        macro=pub.macro,
        market_stats=pub.market_stats,
        diag=pub.diag,
    )
    as_public.filings.upsert_disclosures([disc(1)], loaded_by=JOB, now=NOW)
    assert m.filings.seen([rc(1)]) == {rc(1)}  # 공개 작업이 쓴 것을 API(로그인)가 본다
    assert as_repos(m).public().filings is m.filings
    alone = memory_public_repos()
    assert alone.filings.seen([rc(1)]) == set()


def test_corp_index_reads_the_p2_table() -> None:
    m = memory_repos()
    m.filings.corp_codes["994010"] = ("99400010", "합성전자")
    assert m.filings.corp_index() == {"994010": ("99400010", "합성전자")}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_public_modules_never_import_login_repos() -> None:
    """⑬ 의 저장소 쪽 — 공개 작업이 쓰는 filings·fin·public 은 consensus·journal·패키지 이름을
    import 하지 않는다(간접 import 로 로그인 저장소를 끌어오지 않게 — 설계 §2)."""
    base = ROOT / "kbj" / "store" / "repos"
    for name in ("filings.py", "fin.py", "public.py", "_table.py", "_common.py"):
        mods = _imports(base / name)
        assert not {m for m in mods if m.startswith(("kbj.store.repos.consensus",
                                                     "kbj.store.repos.journal",
                                                     "kbj.store.repos.memory"))}, name  # fmt: skip
        assert "kbj.store.repos" not in mods, name
