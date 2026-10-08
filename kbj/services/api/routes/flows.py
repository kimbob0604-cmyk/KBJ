"""`/api/flows/*` — 스크리닝·시장 투자자·종목 상세·장중 잠정(docs/p3_design.md §5.3)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import Path, Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, TTL_LIVE_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.flows import (
    IntradayFlows,
    InvestorTotals,
    ScreenResponse,
    StockFlowDetail,
)
from kbj.services.api.readers.flows import (
    flows_intraday,
    flows_investors,
    flows_screen,
    flows_stock,
)
from kbj.services.api.routes._common import PeriodQ, data_router

router = data_router("flows")
CODE_PATTERN = r"^[0-9A-Z]{6}$"


@router.get("/screen", response_model=Envelope[ScreenResponse])
def screen(
    request: Request,
    mode: Annotated[Literal["value", "foreign", "inst", "both", "streak", "spike"], Query()],
    market: Annotated[Literal["all", "KOSPI", "KOSDAQ"], Query()] = "all",
    period: PeriodQ = 5,
    min_avg_turnover: Annotated[int | None, Query(ge=0, le=10**15)] = None,
    include_flagged: Annotated[bool, Query()] = False,
    share_class: Annotated[Literal["common", "pref"], Query()] = "common",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="flows",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: flows_screen(
            st.read,
            mode=mode,
            market=market,
            period=period,
            min_avg_turnover=min_avg_turnover,
            include_flagged=include_flagged,
            limit=limit,
            share_class=share_class,
            day=day,
        ),
    )


@router.get("/investors", response_model=Envelope[InvestorTotals])
def investors(
    request: Request, day: Annotated[date | None, Query(alias="date")] = None
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="flows",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: flows_investors(st.read, day=day),
    )


@router.get("/stock/{code}", response_model=Envelope[StockFlowDetail])
def stock(
    request: Request,
    code: Annotated[str, Path(pattern=CODE_PATTERN)],
    days: Annotated[int, Query(ge=1, le=60)] = 20,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="flows",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: flows_stock(st.read, code, days=days),
    )


@router.get("/intraday", response_model=Envelope[IntradayFlows])
def intraday(request: Request) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="flows", ttl_s=TTL_LIVE_S, build=lambda: flows_intraday(st.read)
    )
