"""`/api/stock/*` — 종목 상세(페이지 8) 섹션마다 라우트 하나(docs/p4_design.md §5.3, D-P4-16).

섹션이 서로를 멈추지 않게 따로 부른다(한 섹션 404·적용 불가가 다른 섹션에 번지지 않는다). 모든
라우트는 세션 필수, `{code}` 는 `^[0-9A-Z]{6}$`. 엑셀 내려받기(`report.xlsx`)만 봉투가 아니라 파일
(`private, no-cache` + ETag — 로그인 데이터, 공유 캐시 금지).
"""

from __future__ import annotations

from typing import Annotated, Final, Literal
from urllib.parse import quote

from fastapi import Path, Query, Request, Response
from pydantic import BeforeValidator

from kbj.services.api.deps import PRIVATE, TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.filings import Disclosures, Earnings, Insiders, Overhang
from kbj.services.api.models.fin import Financials, ReportView, Segments
from kbj.services.api.models.stock import (
    Consensus,
    PriceChart,
    Scenario,
    StockHeader,
    StockSearch,
    Valuation,
)
from kbj.services.api.readers.filings import (
    stock_disclosures,
    stock_earnings,
    stock_insiders,
    stock_overhang,
)
from kbj.services.api.readers.fin import (
    XLSX_MEDIA_TYPE,
    stock_financials,
    stock_report,
    stock_report_xlsx,
    stock_segments,
)
from kbj.services.api.readers.stock import (
    CHART_MAX_DAYS,
    SEARCH_MAX,
    stock_chart,
    stock_consensus,
    stock_header,
    stock_scenario,
    stock_search,
    stock_valuation,
)
from kbj.services.api.routes._common import data_router

__all__ = ["CODE_PATTERN", "DOMAIN", "TTL_HEADER_S", "TTL_VERSIONED_S", "router"]

DOMAIN: Final = "stock"
CODE_PATTERN: Final = r"^[0-9A-Z]{6}$"
KIND_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
TTL_HEADER_S: Final = 60
TTL_SHORT_S: Final = 60
TTL_VERSIONED_S: Final = 3600  # '데이터 버전' — 작업이 새 데이터를 쓰면 버전이 바뀌어 저절로 낡는다

router = data_router(DOMAIN)
CodeP = Annotated[str, Path(pattern=CODE_PATTERN)]


def _digits_to_int(v: object) -> object:
    return int(v) if isinstance(v, str) and v.isdigit() and len(v) <= 3 else v


InsiderDaysQ = Annotated[Literal[30, 90, 180], BeforeValidator(_digits_to_int), Query()]


@router.get("/search", response_model=Envelope[StockSearch])
def search(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=20)],
    limit: Annotated[int, Query(ge=1, le=SEARCH_MAX)] = SEARCH_MAX,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=600, build=lambda: stock_search(st.read, q, limit=limit)
    )


@router.get("/{code}/header", response_model=Envelope[StockHeader])
def header(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_HEADER_S, build=lambda: stock_header(st.read, code)
    )


@router.get("/{code}/chart", response_model=Envelope[PriceChart])
def chart(
    request: Request,
    code: CodeP,
    days: Annotated[int, Query(ge=1, le=CHART_MAX_DAYS)] = 250,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: stock_chart(st.read, code, days=days),
    )


@router.get("/{code}/financials", response_model=Envelope[Financials])
def financials(
    request: Request,
    code: CodeP,
    basis: Annotated[Literal["q", "a"], Query()] = "q",
    n: Annotated[int, Query(ge=1, le=20)] = 12,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_VERSIONED_S,
        build=lambda: stock_financials(st.read, code, basis=basis, n=n),
    )


@router.get("/{code}/report", response_model=Envelope[ReportView])
def report(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_VERSIONED_S, build=lambda: stock_report(st.read, code)
    )


@router.get(
    "/{code}/report.xlsx",
    response_class=Response,
    responses={
        200: {
            "content": {XLSX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            "description": "dart-report 엑셀(`<회사>_<코드>_재무리포트.xlsx`)",
        }
    },
)
def report_xlsx(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    f = stock_report_xlsx(st.read, code)
    etag = '"' + (f.sha256 or "none")[:32] + '"'
    disposition = (
        f"attachment; filename=\"report_{code}.xlsx\"; filename*=UTF-8''{quote(f.filename)}"
    )
    headers = {"Cache-Control": PRIVATE, "ETag": etag, "Content-Disposition": disposition}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=f.body, media_type=XLSX_MEDIA_TYPE, headers=headers)


@router.get("/{code}/segments", response_model=Envelope[Segments])
def segments(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_VERSIONED_S, build=lambda: stock_segments(st.read, code)
    )


@router.get("/{code}/consensus", response_model=Envelope[Consensus])
def consensus(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_VERSIONED_S,
        build=lambda: stock_consensus(st.read, code),
    )


@router.get("/{code}/valuation", response_model=Envelope[Valuation])
def valuation(
    request: Request,
    code: CodeP,
    metric: Annotated[Literal["per", "pbr", "psr", "ev_ebitda"], Query()] = "per",
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_VERSIONED_S,
        build=lambda: stock_valuation(st.read, code, metric=metric),
    )


@router.get("/{code}/scenario", response_model=Envelope[Scenario])
def scenario(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_VERSIONED_S, build=lambda: stock_scenario(st.read, code)
    )


@router.get("/{code}/overhang", response_model=Envelope[Overhang])
def overhang(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S, build=lambda: stock_overhang(st.read, code)
    )


@router.get("/{code}/insiders", response_model=Envelope[Insiders])
def insiders(request: Request, code: CodeP, days: InsiderDaysQ = 90) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: stock_insiders(st.read, code, days=days),
    )


@router.get("/{code}/disclosures", response_model=Envelope[Disclosures])
def disclosures(
    request: Request,
    code: CodeP,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    kind: Annotated[str | None, Query(pattern=KIND_PATTERN)] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_SHORT_S,
        build=lambda: stock_disclosures(st.read, code, days=days, kind=kind),
    )


@router.get("/{code}/earnings", response_model=Envelope[Earnings])
def earnings(request: Request, code: CodeP) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_SHORT_S, build=lambda: stock_earnings(st.read, code)
    )
