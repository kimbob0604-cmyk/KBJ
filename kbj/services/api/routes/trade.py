"""`/api/trade/*` — 수출입 속보·탐색·매핑 겹쳐 보기(docs/p6_design.md §5 — 로그인 VM, DB 만 읽는다,
계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND)이 고정(D-P6-5), 리더 본문은 FLASH·EXP·MAP 묶음.

캐시 영역 `trade`(cache.py — 무효화 작업 5개). 쿼리 검증 실패는 422(입력값을 되돌리지 않는다).
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from fastapi import Path, Query, Request, Response
from pydantic import BeforeValidator

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.trade import (
    TenDayKindT,
    TradeCountries,
    TradeCountryItems,
    TradeFlash,
    TradeItemCountries,
    TradeItems,
    TradeItems104,
    TradeItemSeries,
    TradeRegions,
    TradeStock,
    TradeStocks,
    TradeSurges,
)
from kbj.services.api.readers.trade_explore import (
    trade_countries,
    trade_country_items,
    trade_item_countries,
    trade_item_series,
    trade_items,
    trade_items104,
    trade_regions,
    trade_surges,
)
from kbj.services.api.readers.trade_flash import trade_flash
from kbj.services.api.readers.trade_map import trade_stock, trade_stocks
from kbj.services.api.routes._common import data_router

router = data_router("trade")
DOMAIN: Final = "trade"
HS_PATTERN: Final = r"^[0-9]{2}([0-9]{2}){0,4}$"
HS6_PATTERN: Final = r"^[0-9]{6}$"
YM_PATTERN: Final = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
CC_PATTERN: Final = r"^[A-Z0-9]{2}$"  # 관세청 특수 코드 'Z1'(실측 2026-10-20)
SIDO_PATTERN: Final = r"^[0-9]{2}$"
CODE_PATTERN: Final = r"^[0-9A-Z]{6}$"
FlowQ = Annotated[Literal["exp", "imp"], Query()]
YmQ = Annotated[str | None, Query(pattern=YM_PATTERN)]


def _digits_to_int(v: object) -> object:
    return int(v) if isinstance(v, str) and v.isdigit() and len(v) <= 2 else v


LevelQ = Annotated[Literal[2, 4, 6, 10], BeforeValidator(_digits_to_int), Query()]
SurgeLevelQ = Annotated[Literal["hs2", "hs4", "hs6", "hs10", "country"] | None, Query()]


@router.get("/flash", response_model=Envelope[TradeFlash])
def flash(
    request: Request,
    kind: Annotated[TenDayKindT, Query()] = "exp_item",
    months: Annotated[int, Query(ge=1, le=120)] = 24,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_flash(st.read, kind=kind, months=months),
    )  # fmt: skip


@router.get("/items", response_model=Envelope[TradeItems])
def items(
    request: Request,
    level: LevelQ = 2,
    parent: Annotated[str | None, Query(pattern=HS_PATTERN)] = None,
    flow: FlowQ = "exp",
    ym_from: Annotated[str | None, Query(alias="from", pattern=YM_PATTERN)] = None,
    ym_to: Annotated[str | None, Query(alias="to", pattern=YM_PATTERN)] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_items(
            st.read, level=level, parent=parent, flow=flow, ym_from=ym_from, ym_to=ym_to
        ),
    )  # fmt: skip


@router.get("/items/{hs}/series", response_model=Envelope[TradeItemSeries])
def item_series(
    request: Request,
    hs: Annotated[str, Path(pattern=HS_PATTERN)],
    flow: FlowQ = "exp",
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_item_series(st.read, hs=hs, flow=flow),
    )  # fmt: skip


@router.get("/items/{hs}/countries", response_model=Envelope[TradeItemCountries])
def item_countries(
    request: Request,
    hs: Annotated[str, Path(pattern=HS_PATTERN)],
    ym: YmQ = None,
    flow: FlowQ = "exp",
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_item_countries(st.read, hs=hs, ym=ym, flow=flow),
    )  # fmt: skip


@router.get("/countries", response_model=Envelope[TradeCountries])
def countries(request: Request, ym: YmQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_countries(st.read, ym=ym),
    )  # fmt: skip


@router.get("/countries/{cc}/items", response_model=Envelope[TradeCountryItems])
def country_items(
    request: Request,
    cc: Annotated[str, Path(pattern=CC_PATTERN)],
    ym: YmQ = None,
    flow: FlowQ = "exp",
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_country_items(st.read, country=cc, ym=ym, flow=flow),
    )  # fmt: skip


@router.get("/regions", response_model=Envelope[TradeRegions])
def regions(
    request: Request,
    hs6: Annotated[str | None, Query(pattern=HS6_PATTERN)] = None,
    sido: Annotated[str | None, Query(pattern=SIDO_PATTERN)] = None,
    ym: YmQ = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_regions(st.read, hs6=hs6, sido=sido, ym=ym),
    )  # fmt: skip


@router.get("/surges", response_model=Envelope[TradeSurges])
def surges(request: Request, ym: YmQ = None, level: SurgeLevelQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_surges(st.read, ym=ym, level=level),
    )  # fmt: skip


@router.get("/items104", response_model=Envelope[TradeItems104])
def items104(request: Request, ym: YmQ = None) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: trade_items104(st.read, ym=ym),
    )  # fmt: skip


@router.get("/stocks", response_model=Envelope[TradeStocks])
def stocks(request: Request) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S, build=lambda: trade_stocks(st.read)
    )


@router.get("/stock/{code}", response_model=Envelope[TradeStock])
def stock(request: Request, code: Annotated[str, Path(pattern=CODE_PATTERN)]) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S, build=lambda: trade_stock(st.read, code=code)
    )
