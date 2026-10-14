"""공개 작업용 저장소 묶음 — `pub_filings`·`pub_fin`(P4)·`pub_macro`·`pub_market_stats`
(P5)·`pub_diag`(P7) 만(docs/p4_design.md §2 '⑬ 을 지키는 import 모양', docs/p5_design.md
§1.1 — 계약 ⑭).

공개 작업(`kbj.services.collectors.{dart_feed,dart_company,fin}`·`kbj.services.engine.filings`)은
`kbj.store.repos` 패키지 이름(`Repos`·`pg_repos`·`memory_repos`)을 쓰지 않는다 — 그 묶음이 로그인
저장소(consensus·journal)까지 끌어와 계약 ⑬(공개 작업 → 로그인 저장소 금지)을 간접 import 로 어긴다.
대신 이 모듈의 `PublicRepos` 를 쓴다. 이 모듈은 공개 저장소 모듈(filings·fin·macro·
market_stats·diag)과 _common 만 import 한다 — 로그인 매크로(macro_prv)는 모른다.

    from kbj.store.repos.public import PublicRepos, pg_public_repos, memory_public_repos
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kbj.store.repos._common import ConnFactory
from kbj.store.repos.diag import DiagPublicRepo, MemoryDiagPublicRepo, PgDiagPublicRepo
from kbj.store.repos.filings import FilingsRepo, MemoryFilingsRepo, PgFilingsRepo
from kbj.store.repos.fin import FinRepo, MemoryFinRepo, PgFinRepo
from kbj.store.repos.macro import MacroPublicRepo, MemoryMacroPublicRepo, PgMacroPublicRepo
from kbj.store.repos.market_stats import (
    MarketStatsRepo,
    MemoryMarketStatsRepo,
    PgMarketStatsRepo,
)

__all__ = [
    "MemoryPublicRepos",
    "PublicRepos",
    "memory_public_repos",
    "pg_public_repos",
]


@dataclass(frozen=True)
class PublicRepos:
    """공개 저장소 묶음 — 공개 작업 처리기가 자원 `public_repos` 로 받는다 [제안]."""

    filings: FilingsRepo
    fin: FinRepo
    # P5 — 공개 매크로·금투협 통계
    macro: MacroPublicRepo
    market_stats: MarketStatsRepo
    # P7 — 공공지표 진단·매크로 위험 게이지·이벤트(pub_diag — diag.public 이 쓴다)
    diag: DiagPublicRepo


@dataclass
class MemoryPublicRepos:
    """메모리판(시험이 내부 표를 들여다볼 수 있게 구체 형)."""

    filings: MemoryFilingsRepo = field(default_factory=MemoryFilingsRepo)
    fin: MemoryFinRepo = field(default_factory=MemoryFinRepo)
    macro: MemoryMacroPublicRepo = field(default_factory=MemoryMacroPublicRepo)
    market_stats: MemoryMarketStatsRepo = field(default_factory=MemoryMarketStatsRepo)
    diag: MemoryDiagPublicRepo = field(default_factory=MemoryDiagPublicRepo)


def pg_public_repos(conn_factory: ConnFactory) -> PublicRepos:
    return PublicRepos(
        filings=PgFilingsRepo(conn_factory), fin=PgFinRepo(conn_factory),
        macro=PgMacroPublicRepo(conn_factory),
        market_stats=PgMarketStatsRepo(conn_factory), diag=PgDiagPublicRepo(conn_factory),
    )  # fmt: skip


def memory_public_repos() -> MemoryPublicRepos:
    return MemoryPublicRepos()
