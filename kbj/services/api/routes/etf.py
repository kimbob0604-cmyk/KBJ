"""`/api/etf/*` — 자금 흐름·유형별·괴리율·구성종목 변동(docs/p3_design.md §5.3) + P5 ETF 맵·보유
탐색·
액티브 동시 편입(docs/p5_design.md §5 — 모양은 FND, 리더 본문은 ETF 묶음 `readers/etf_p5.py`)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import Path, Query, Request, Response

from kbj.core.rows import CHANGE_KINDS, EtfType
from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.etf import (
    ActiveConsensus,
    EtfFlows,
    EtfHolders,
    EtfHoldingChanges,
    EtfHoldings,
    EtfMap,
    EtfPremium,
    EtfTypes,
)
from kbj.services.api.readers.etf import etf_flows, etf_holding_changes, etf_premium, etf_types
from kbj.services.api.readers.etf_p5 import active_consensus, etf_holders, etf_holdings, etf_map
from kbj.services.api.routes._common import PeriodQ, data_router

router = data_router("etf")
HOLDINGS_TTL_S = 600
_KIND_PATTERN = "^(" + "|".join(CHANGE_KINDS) + ")$"


@router.get("/flows", response_model=Envelope[EtfFlows])
def flows(
    request: Request,
    mode: Annotated[Literal["in", "out", "value", "indiv", "foreign", "inst"], Query()] = "in",
    etf_type: Annotated[EtfType | None, Query(alias="type")] = None,
    period: PeriodQ = 5,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="etf",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: etf_flows(st.read, mode=mode, etf_type=etf_type, period=period, limit=limit),
    )


@router.get("/types", response_model=Envelope[EtfTypes])
def types(request: Request, period: PeriodQ = 5) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="etf", ttl_s=TTL_DEFAULT_S, build=lambda: etf_types(st.read, period=period)
    )


@router.get("/premium", response_model=Envelope[EtfPremium])
def premium(
    request: Request, basis: Annotated[Literal["nav", "inav"], Query()] = "nav"
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="etf", ttl_s=live_ttl(st), build=lambda: etf_premium(st.read, basis=basis)
    )


@router.get("/holdings/changes", response_model=Envelope[EtfHoldingChanges])
def holding_changes(
    request: Request,
    day: Annotated[date | None, Query(alias="date")] = None,
    kind: Annotated[str | None, Query(pattern=_KIND_PATTERN)] = None,
    issuer: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="etf",
        ttl_s=HOLDINGS_TTL_S,
        build=lambda: etf_holding_changes(st.read, day=day, kind=kind, issuer=issuer),
    )


# ── P5(docs/p5_design.md §5 — 페이지 10 남은 것) ──────────────────────────────────────────────
CODE_PATTERN = r"^[0-9A-Z]{6}$"


@router.get("/map", response_model=Envelope[EtfMap])
def etf_map_route(
    request: Request,
    period: Annotated[Literal["1D", "1W", "1M", "3M"], Query()] = "1M",
    group: Annotated[Literal["type", "theme"], Query()] = "type",
    lev: Annotated[bool, Query()] = False,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="etf",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: etf_map(st.read, period=period, group=group, lev=lev),
    )


@router.get("/fund/{code}/holdings", response_model=Envelope[EtfHoldings])
def fund_holdings(
    request: Request, code: Annotated[str, Path(pattern=r"^[0-9A-Za-z_.-]{1,32}$")]
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="etf",
        ttl_s=HOLDINGS_TTL_S,
        build=lambda: etf_holdings(st.read, code=code),
    )


@router.get("/holders/{code}", response_model=Envelope[EtfHolders])
def holders(request: Request, code: Annotated[str, Path(pattern=CODE_PATTERN)]) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="etf", ttl_s=HOLDINGS_TTL_S, build=lambda: etf_holders(st.read, code=code)
    )


@router.get("/active-consensus", response_model=Envelope[ActiveConsensus])
def active_consensus_route(
    request: Request, days: Annotated[int, Query(ge=1, le=20)] = 1
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="etf",
        ttl_s=HOLDINGS_TTL_S,
        build=lambda: active_consensus(st.read, days=days),
    )
