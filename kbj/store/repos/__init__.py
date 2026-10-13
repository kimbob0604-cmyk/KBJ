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
- P5(docs/p5_design.md §1.1 — 묶음 FND): 공개 `macro`(0014 pub_macro)·`market_stats`(0016),
  로그인 `macro_prv`(0014 prv_macro)·`us`(0015 + daily_bar US)·`program`(0017). 분류 사전·테마 강도
  저장소는 사용자가 따로 진행해 P5 에서 뺐다. 공개·로그인을 **모듈로** 나눴다(계약 ⑭ — 공개 작업은
  로그인 모듈을 import 하지 않는다). `flows` 에 장중 투자자 1분(prv_gex.investor_flow — F2 만 쓴다),
  `etf` 에 보유 탐색, `board` 에 미국 산출(market='US').
- P5 2차 묶음 IDX(docs/p5_index_rebalance_prompt.md §3-3): 로그인 `index_review`(0018 prv_index.* —
  구성종목·리뷰 일정·편입/편출 예측·패시브 규모).

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
from kbj.store.repos.index_review import IndexReviewRepo, PgIndexReviewRepo
from kbj.store.repos.journal import JournalRepo, PgJournalRepo
from kbj.store.repos.macro import MacroPublicRepo, PgMacroPublicRepo
from kbj.store.repos.macro_prv import MacroPrivateRepo, PgMacroPrivateRepo
from kbj.store.repos.market import MarketRepo, PgMarketRepo
from kbj.store.repos.market_stats import MarketStatsRepo, PgMarketStatsRepo
from kbj.store.repos.memory import MemoryRepos, memory_repos
from kbj.store.repos.program import PgProgramRepo, ProgramRepo
from kbj.store.repos.public import PublicRepos
from kbj.store.repos.us import PgUsRepo, UsRepo

__all__ = [
    "MARKET",
    "BoardRepo",
    "ConnFactory",
    "ConsensusRepo",
    "EtfRepo",
    "FilingsRepo",
    "FinRepo",
    "FlowsRepo",
    "IndexReviewRepo",
    "JournalRepo",
    "MacroPrivateRepo",
    "MacroPublicRepo",
    "MarketRepo",
    "MarketStatsRepo",
    "MemoryRepos",
    "ProgramRepo",
    "PublicRepos",
    "Repos",
    "RevisionReport",
    "UsRepo",
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
    # P5(묶음 FND)
    macro: MacroPublicRepo
    macro_prv: MacroPrivateRepo
    market_stats: MarketStatsRepo
    us: UsRepo
    program: ProgramRepo
    # P5 2차 묶음 IDX(0018 — 로그인)
    index_review: IndexReviewRepo

    def public(self) -> PublicRepos:
        """같은 공개 저장소(filings·fin·macro·market_stats)를 공개 작업 묶음으로."""
        return PublicRepos(
            filings=self.filings, fin=self.fin, macro=self.macro,
            market_stats=self.market_stats,
        )  # fmt: skip


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
        macro=PgMacroPublicRepo(conn_factory),
        macro_prv=PgMacroPrivateRepo(conn_factory),
        market_stats=PgMarketStatsRepo(conn_factory),
        us=PgUsRepo(conn_factory),
        program=PgProgramRepo(conn_factory),
        index_review=PgIndexReviewRepo(conn_factory),
    )
