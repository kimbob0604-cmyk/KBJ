"""스케줄러 자원 `detail_universe` — 상세 재무(재무제표·주식총수)를 받을 보통주(D-P4-5, ADR 0014).

공개 작업 `fin.quarterly`·`fin.backfill` 은 계약 ⑬ 때문에 관심종목·시세(로그인)를 못 읽어, 로그인
쪽이 이 함수(KST 날짜 → 종목코드들)를 자원으로 넘긴다. 규칙은 화면 `Lookup.detail_codes` 와 같다:
관심종목(보통주로) ∪ 전 거래일 시총 상위 `consensus.top_n` 보통주 ∪ 공개 데모(`config/fin.yaml
detail_universe` 로 켜고 끈다).

2026-10-20 점검: 이 자원이 운영 스케줄러에 연결되지 않아 상세 층이 공개 데모(빈 목록)뿐이었다 —
주식총수(`pub_filings.share_count`)가 비어 오버행·내부자 비율, 지수 정기변경 유동비율 추정이 비었다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Final, Protocol

from kbj.config.fin import FinConfig, ReportsConfig
from kbj.engines.consensus.universe import consensus_na
from kbj.store.repos.consensus import ConsensusRepo
from kbj.store.repos.journal import JournalRepo
from kbj.store.repos.market import MarketRepo

__all__ = ["WATCH_LIST", "detail_codes", "provider"]

WATCH_LIST: Final = "main"


class Repos(Protocol):
    @property
    def market(self) -> MarketRepo: ...
    @property
    def journal(self) -> JournalRepo: ...
    @property
    def consensus(self) -> ConsensusRepo: ...


def detail_codes(repos: Repos, day: date, fin: FinConfig, reports: ReportsConfig) -> set[str]:
    cfg = fin.detail_universe
    out: set[str] = set()
    if cfg.consensus_top_n and fin.consensus.top_n > 0:
        snaps, _ = repos.market.snapshot(day)
        uni = {u.code: u for u in repos.market.universe(day)}
        ranked = sorted(
            (s.mktcap, code)
            for code, s in snaps.items()
            if s.mktcap is not None and (u := uni.get(code)) is not None and consensus_na(u) is None
        )
        ranked.sort(key=lambda t: (-t[0], t[1]))
        out |= {code for _cap, code in ranked[: fin.consensus.top_n]}
    if cfg.watchlist:
        classes = repos.consensus.share_classes()
        for c in repos.journal.watch_codes(WATCH_LIST):
            sc = classes.get(c)
            out.add(sc.common_code if sc is not None else c)
    if cfg.public_demo:
        out |= set(reports.public_demo)
    return out


def provider(repos: Repos, fin: FinConfig, reports: ReportsConfig) -> Callable[[date], set[str]]:
    """스케줄러 자원 — KST 날짜 → 상세 대상 보통주 코드."""
    return lambda day: detail_codes(repos, day, fin, reports)
