"""`/api/market/*` — 요약·업종 히트맵·상단 띠(docs/p3_design.md §5.3)·신용잔고 패널(`credit`)·
글로벌 프록시(`global-proxy`)·상대 시총(`relcap`)·급등·급락(`movers` — metrics §6.8)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import Query, Request, Response

from kbj.engines.market.movers import MIN_MKTCAP_WON
from kbj.services.api.deps import TTL_DEFAULT_S, TTL_LIVE_S, cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.market import (
    CreditPanel,
    GlobalProxy,
    MarketMovers,
    MarketSummary,
    Ribbon,
    SectorHeat,
)
from kbj.services.api.models.relcap import RelCap
from kbj.services.api.readers.global_proxy import global_proxy
from kbj.services.api.readers.market import (
    market_movers,
    market_ribbon,
    market_sectors,
    market_summary,
)
from kbj.services.api.readers.market_credit import market_credit
from kbj.services.api.readers.relcap import relcap
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


@router.get("/movers", response_model=Envelope[MarketMovers])
def movers(
    request: Request,
    min_mktcap: Annotated[int, Query(ge=0, le=10**16)] = MIN_MKTCAP_WON,
) -> Response:
    """급등·급락 — 시총 하한(원, 기본 3,000억) 이상 보통주의 마지막 마감 등락률 상위·하위 10."""
    st = get_state(request)
    return cached_json(
        request,
        domain="market",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: market_movers(st.read, min_mktcap=min_mktcap),
    )


@router.get("/ribbon", response_model=Envelope[Ribbon])
def ribbon(request: Request) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="market", ttl_s=TTL_LIVE_S, build=lambda: market_ribbon(st.read)
    )


@router.get("/credit", response_model=Envelope[CreditPanel])
def credit(request: Request) -> Response:
    """페이지 1 신용잔고·예탁금·펀드 자금(금투협 — 수집이 바뀌면 macro 영역 캐시가 비워진다)."""
    st = get_state(request)
    return cached_json(
        request, domain="macro", ttl_s=TTL_DEFAULT_S, build=lambda: market_credit(st.read)
    )


@router.get("/global-proxy", response_model=Envelope[GlobalProxy])
def global_proxy_route(request: Request) -> Response:
    """페이지 1 글로벌 프록시 — 24시간 선물·해외 상장 괴리율·지수선물·매크로·미국 ETF(5분 수집)."""
    st = get_state(request)
    return cached_json(
        request, domain="global", ttl_s=TTL_LIVE_S, build=lambda: global_proxy(st.read)
    )


@router.get("/relcap", response_model=Envelope[RelCap])
def relcap_route(
    request: Request, period: Annotated[Literal["6M", "1Y", "2Y"], Query()] = "1Y"
) -> Response:
    """페이지 3 상대 시총(종목 간 시총 비율 추이)·주요 지수 연도별 수익률(global.daily 07:25)."""
    st = get_state(request)
    return cached_json(
        request, domain="global", ttl_s=TTL_DEFAULT_S, build=lambda: relcap(st.read, period=period)
    )
