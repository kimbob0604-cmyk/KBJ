"""컨센서스 수집 대상 규칙 — 보통주·코스피/코스닥만(§4.7, ADR 0015 2-6, p4_expect.yaml).

수집 처리기(`kbj.services.collectors.consensus`)와 API(묶음 A — 계약 ⑩ 으로 수집기를 import 하지
못한다)가 같은 규칙을 쓰도록 엔진에 둔다(두 벌 금지). 우선주는 부르는 쪽이 보통주(`share_class`)로
바꾼 뒤 묻는다(우선주 컨센서스 = 보통주 것).

- 코넥스 → `konex_no_consensus`
- 보통주가 아님(스팩·리츠·ETF·ETN·모름) 또는 코스피·코스닥 밖 → `not_in_consensus_universe`
- 그 밖(보통주·코스피/코스닥) → None(대상). 관심종목이거나 전 거래일 시총 상위 `consensus.top_n`
  이면 수집한다 — 대상인데 KIS 가 빈 응답이면 API 는 404 `no_data`(0 으로 채우지 않는다).
"""

from __future__ import annotations

from typing import Final

from kbj.core.fin_rows import NaReason
from kbj.core.rows import UniverseRow
from kbj.engines.board import kinds as board_kinds
from kbj.engines.board.kinds import COMMON

__all__ = ["STOCK_MARKETS", "consensus_na", "kind_of", "market_of"]

STOCK_MARKETS: Final = frozenset({"KOSPI", "KOSDAQ"})


def market_of(u: UniverseRow) -> str | None:
    """시장 이름의 첫 낱말(대문자) — 'KOSDAQ GLOBAL' → KOSDAQ. 없으면 None."""
    parts = (u.market or "").upper().split()
    return parts[0] if parts else None


def consensus_na(u: UniverseRow) -> NaReason | None:
    """컨센서스 수집 대상이 아니면 그 사유, 대상이면 None."""
    market = market_of(u)
    if market == "KONEX":
        return NaReason.KONEX_NO_CONSENSUS
    if market not in STOCK_MARKETS or kind_of(u) != COMMON:
        return NaReason.NOT_IN_CONSENSUS_UNIVERSE
    return None


def kind_of(u: UniverseRow) -> str:
    """종목 종류 — 원천이 준 것, 없으면 보드 정본 분류(`board.kinds.of` — 이름·코드). KRX 기본정보는
    종류를 비워 두는데(krx_daily `kind=None`) `!= "common"` 으로 거르면 전 종목이 빠져 컨센서스 수집
    대상이 0 이었다(2026-10-20)."""
    if u.kind is not None:
        return u.kind
    return board_kinds.of(u.code, u.name)
