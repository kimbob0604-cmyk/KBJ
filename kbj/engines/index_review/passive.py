"""예상 패시브 매매·일평균 거래대금 대비 배수(metrics §24.5).

비중 = 유동시총 ÷ 지수 유동시총 합. 편입(또는 경계 비구성종목 — '편입되면'): + 추종자금 × 비중(편입
뒤
지수 기준), 편출(또는 경계 구성종목 — '편출되면'): − 추종자금 × 비중(지금 지수 기준). 유지는
계산하지
않는다(None). 추종자금이 없거나(설정 null) 유동시총을 모르면 None(지어내지 않는다).
배수 = |예상 패시브 매매| ÷ 일평균 거래대금.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from kbj.engines.index_review.types import StockOut

__all__ = ["with_passive"]


def with_passive(stocks: Sequence[StockOut], aum_won: int | None) -> list[StockOut]:
    if aum_won is None:
        return list(stocks)
    before = [s for s in stocks if s.member]
    after = [s for s in stocks if (s.member and s.outcome != "delete") or s.outcome == "add"]
    tot_before = sum(s.ff_mcap for s in before if s.ff_mcap)
    tot_after = sum(s.ff_mcap for s in after if s.ff_mcap)
    out: list[StockOut] = []
    for s in stocks:
        flow: int | None = None
        if s.ff_mcap:
            if s.outcome == "add" and tot_after:
                flow = round(aum_won * s.ff_mcap / tot_after)
            elif s.outcome == "borderline" and not s.member and tot_after:
                flow = round(aum_won * s.ff_mcap / (tot_after + s.ff_mcap))
            elif s.outcome in ("delete", "borderline") and s.member and tot_before:
                flow = -round(aum_won * s.ff_mcap / tot_before)
        mult = None
        if flow is not None and s.adv_won:
            mult = abs(flow) / s.adv_won
        out.append(dataclasses.replace(s, passive_won=flow, adv_multiple=mult))
    return out
