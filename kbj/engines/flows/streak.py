"""연속 순매수·순매수 일수(docs/metrics.md §2 — 정본).

대체되는 세 벌: SD `server.py:_flow_streak`:14918, ET `monitor/kr/flows.py:windows`:84(종목 휴장일을
건너뜀 — 여기서는 끊는다), ET `monitor/flow/analyze.py:buy_days`:68.

- 연속 순매수: 오늘부터 거꾸로 세어 순매수 > 0 인 연속 영업일 수. **0원·순매도·거래정지·값 없음·
  invalid·그날 행 없음**에서 끊긴다. 오늘이 거래정지면 0.
- 순매수 일수: 순매수 > 0 인 날의 수. 보합(0)·값 없는 날은 세지 않는다(ET buy_days 그대로).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from kbj.engines.flows.ledger import LedgerRow, Who

__all__ = ["buy_days", "streak"]


def streak(rows_desc: Sequence[LedgerRow | None], who: Who) -> int:
    """`rows_desc` 는 최신 영업일부터(`Ledger.rows_desc`) — 그날 행이 없으면 None."""
    n = 0
    for r in rows_desc:
        if r is None or not r.usable or not r.traded:
            break
        v = r.value(who)
        if v is None or v <= 0:
            break
        n += 1
    return n


def buy_days(rows: Iterable[LedgerRow | None], who: Who) -> int:
    """순매수 > 0 인 날의 수(invalid·값 없는 날 제외)."""
    return sum(
        1 for r in rows if r is not None and r.usable and (v := r.value(who)) is not None and v > 0
    )
