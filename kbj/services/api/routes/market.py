"""`/api/market/*` — 요약·업종 히트맵·상단 띠(docs/p3_design.md §5.3)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Query, Request, Response

from kbj.services.api.deps import TTL_LIVE_S, cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.market import MarketSummary, Ribbon, SectorHeat
from kbj.services.api.readers.market import market_ribbon, market_sectors, market_summary
from kbj.services.api.routes._common import PeriodQ, data_router

router = data_router("market")
DateQ = Annotated[date | None, Query(alias="date")]


@router.get("/summary", response_model=Envelope[MarketSummary])
def summary(request: Request, day: DateQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="market",
        ttl_s=live_ttl(st),
        build=lambda: market_summary(st.read, day=day),
    )


@router.get("/sectors", response_model=Envelope[SectorHeat])
def sectors(request: Request, period: PeriodQ = 1, day: DateQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="market",
        ttl_s=live_ttl(st),
        build=lambda: market_sectors(st.read, period=period, day=day),
    )


@router.get("/ribbon", response_model=Envelope[Ribbon])
def ribbon(request: Request) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="market", ttl_s=TTL_LIVE_S, build=lambda: market_ribbon(st.read)
    )
