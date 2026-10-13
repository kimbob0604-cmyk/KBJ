"""저장소(SQL) 계층 — 엔진·API·수집 처리기가 쓰는 P3 표의 읽기·쓰기(docs/p3_design.md §1.1·D-P3-5).

- `market`(`MarketRepo`·`PgMarketRepo`·`MemoryMarketRepo`): 0003 daily_bar·stock_snapshot·universe,
  0008 장중 지수·업종·순위·eod_reconcile
- `flows`(`FlowsRepo`·`PgFlowsRepo`·`MemoryFlowsRepo`): 0003 stock/market_investor_daily,
  0008 investor_intraday·investor_revision·ledger_check
- `board`(`BoardRepo`·`PgBoardRepo`·`MemoryBoardRepo`): 0007 prv_board.*
- `etf`(`EtfRepo`·`PgEtfRepo`·`MemoryEtfRepo`): 0009 prv_etf.*
- P4(docs/p4_design.md §1.1 — 묶음 M): `filings`(0010 pub_filings.*)·`fin`(0011 pub_fin.*)·
  `consensus`(0012 prv_fin.*·prv_market.share_class)·`journal`(0013 prv_journal.watchlist).
  **공개 작업은 이 패키지 이름을 import 하지 않는다**(계약 ⑬ — 여기서 consensus·journal 을
  끌어온다).
  공개 작업은 `kbj.store.repos.public`(`PublicRepos`) 또는 하위 모듈에서 직접 가져온다.

- 행 모양은 `kbj.core.rows`(엔진과 공유). 이 패키지는 `kbj.core`·psycopg 만 쓴다(계약 ⑦).
- Postgres 구현은 연결 공장(`ConnFactory`)을 받아 부를 때마다 짧게 연다. 서비스는
  `pg_repos(lambda: kbj.store.db.connect(settings, service=…))`, 시험은 `memory_repos()`.
- 원장 우선순위(D-P3-7)·잠정→확정 차이 기록 규칙은 각 모듈 머리말.
- `kbj_public_export`(공개 내보내기)는 이 패키지를 쓰지 않는다(계약 ⑧ — prv_* 만 다룬다).
"""

from __future__ import annotations

from dataclasses import dataclass

from kbj.store.repos._common import MARKET, ConnFactory
from kbj.store.repos.board import BoardRepo, PgBoardRepo
from kbj.store.repos.consensus import ConsensusRepo, PgConsensusRepo
from kbj.store.repos.etf import EtfRepo, PgEtfRepo
from kbj.store.repos.filings import FilingsRepo, PgFilingsRepo
from kbj.store.repos.fin import FinRepo, PgFinRepo
from kbj.store.repos.flows import FlowsRepo, PgFlowsRepo, RevisionReport
from kbj.store.repos.journal import JournalRepo, PgJournalRepo
from kbj.store.repos.market import MarketRepo, PgMarketRepo
from kbj.store.repos.memory import MemoryRepos, memory_repos
from kbj.store.repos.public import PublicRepos

__all__ = [
    "MARKET",
    "BoardRepo",
    "ConnFactory",
    "ConsensusRepo",
    "EtfRepo",
    "FilingsRepo",
    "FinRepo",
    "FlowsRepo",
    "JournalRepo",
    "MarketRepo",
    "MemoryRepos",
    "PublicRepos",
    "Repos",
    "RevisionReport",
    "memory_repos",
    "pg_repos",
]


@dataclass(frozen=True)
class Repos:
    """저장소 묶음 — API `create_app(…, repos=…)`·서비스 처리기가 받는다."""

    market: MarketRepo
    flows: FlowsRepo
    board: BoardRepo
    etf: EtfRepo
    filings: FilingsRepo
    fin: FinRepo
    consensus: ConsensusRepo
    journal: JournalRepo

    def public(self) -> PublicRepos:
        """같은 공개 저장소(filings·fin)를 공개 작업 묶음으로."""
        return PublicRepos(filings=self.filings, fin=self.fin)


def pg_repos(conn_factory: ConnFactory) -> Repos:
    return Repos(
        market=PgMarketRepo(conn_factory),
        flows=PgFlowsRepo(conn_factory),
        board=PgBoardRepo(conn_factory),
        etf=PgEtfRepo(conn_factory),
        filings=PgFilingsRepo(conn_factory),
        fin=PgFinRepo(conn_factory),
        consensus=PgConsensusRepo(conn_factory),
        journal=PgJournalRepo(conn_factory),
    )
