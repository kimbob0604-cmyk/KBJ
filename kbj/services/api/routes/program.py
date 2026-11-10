"""`/api/program/*` — 프로그램매매 일별·상위(docs/p5_design.md §5, F2 §3.5). 모양은 웨이브 1(FND),
리더
본문은 F2 묶음(`readers/program.py`). 순위는 범위 꼬리표(`scope_label`)를 반드시 싣는다(F2 §1.4).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.program import ProgramDaily, ProgramIntraday, ProgramTop
from kbj.services.api.readers.program import program_daily, program_intraday, program_top
from kbj.services.api.routes._common import data_router

router = data_router("program")
MarketT = Literal["K", "Q"]


@router.get("/daily", response_model=Envelope[ProgramDaily])
def daily(
    request: Request,
    market: Annotated[MarketT, Query()] = "K",
    days: Annotated[int, Query(ge=1, le=60)] = 60,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="program",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: program_daily(st.read, market=market, days=days),
    )


@router.get("/intraday", response_model=Envelope[ProgramIntraday])
def intraday(request: Request, market: Annotated[MarketT, Query()] = "K") -> Response:
    """장중 시장 프로그램 누적(매분 그날 누적 — metrics §22). 장 뒤엔 마지막 분 = 마감 누적."""
    st = get_state(request)
    return cached_json(
        request,
        domain="program",
        ttl_s=live_ttl(st),
        build=lambda: program_intraday(st.read, market=market),
    )


@router.get("/top", response_model=Envelope[ProgramTop])
def top(
    request: Request,
    market: Annotated[MarketT, Query()] = "K",
    side: Annotated[Literal["buy", "sell"], Query()] = "buy",
    scope: Annotated[Literal["intraday", "close"], Query()] = "intraday",
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="program",
        ttl_s=live_ttl(st),
        build=lambda: program_top(st.read, market=market, side=side, scope=scope),
    )
