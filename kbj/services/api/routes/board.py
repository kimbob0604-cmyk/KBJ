"""`/api/board/*` — 신고가 보드 산출(docs/p3_design.md §5.3)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.board import BoardEvents, BoardNewhigh, BoardRankings, BoardSectors
from kbj.services.api.models.common import Envelope
from kbj.services.api.readers.board import (
    board_events,
    board_newhigh,
    board_rankings,
    board_sectors,
)
from kbj.services.api.routes._common import data_router

router = data_router("board")
DateQ = Annotated[date | None, Query(alias="date")]


@router.get("/newhigh", response_model=Envelope[BoardNewhigh])
def newhigh(
    request: Request,
    day: DateQ = None,
    basis: Annotated[Literal["close", "high"], Query()] = "close",
    kind: Annotated[Literal["hist", "w52", "d60"] | None, Query()] = None,
    min_turnover_eok: Annotated[float | None, Query(ge=0, le=1_000_000)] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="board",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: board_newhigh(
            st.read, day=day, basis=basis, kind=kind, min_turnover_eok=min_turnover_eok
        ),
    )


@router.get("/sectors", response_model=Envelope[BoardSectors])
def sectors(request: Request, day: DateQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="board", ttl_s=TTL_DEFAULT_S, build=lambda: board_sectors(st.read, day=day)
    )


@router.get("/events", response_model=Envelope[BoardEvents])
def events(request: Request, day: DateQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="board", ttl_s=TTL_DEFAULT_S, build=lambda: board_events(st.read, day=day)
    )


@router.get("/rankings", response_model=Envelope[BoardRankings])
def rankings(request: Request, day: DateQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="board",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: board_rankings(st.read, day=day),
    )
