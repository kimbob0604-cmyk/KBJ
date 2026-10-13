"""공개 작업용 저장소 묶음 — `pub_filings`·`pub_fin` 만(docs/p4_design.md §2 '⑬ 을 지키는 import
모양').

공개 작업(`kbj.services.collectors.{dart_feed,dart_company,fin}`·`kbj.services.engine.filings`)은
`kbj.store.repos` 패키지 이름(`Repos`·`pg_repos`·`memory_repos`)을 쓰지 않는다 — 그 묶음이 로그인
저장소(consensus·journal)까지 끌어와 계약 ⑬(공개 작업 → 로그인 저장소 금지)을 간접 import 로 어긴다.
대신 이 모듈의 `PublicRepos` 를 쓴다. 이 모듈은 filings·fin·_common 만 import 한다.

    from kbj.store.repos.public import PublicRepos, pg_public_repos, memory_public_repos
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kbj.store.repos._common import ConnFactory
from kbj.store.repos.filings import FilingsRepo, MemoryFilingsRepo, PgFilingsRepo
from kbj.store.repos.fin import FinRepo, MemoryFinRepo, PgFinRepo

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


@dataclass
class MemoryPublicRepos:
    """메모리판(시험이 내부 표를 들여다볼 수 있게 구체 형)."""

    filings: MemoryFilingsRepo = field(default_factory=MemoryFilingsRepo)
    fin: MemoryFinRepo = field(default_factory=MemoryFinRepo)


def pg_public_repos(conn_factory: ConnFactory) -> PublicRepos:
    return PublicRepos(filings=PgFilingsRepo(conn_factory), fin=PgFinRepo(conn_factory))


def memory_public_repos() -> MemoryPublicRepos:
    return MemoryPublicRepos()
