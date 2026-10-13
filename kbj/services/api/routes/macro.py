"""`/api/macro/*` — 매크로 탭·시리즈·경제 캘린더(docs/p5_design.md §5). 모양은 웨이브 1(FND), 리더
본문은
MAC 묶음(`readers/macro.py`). 공개판은 정적 JSON(`macro/*` — 공개 내보내기)이라 이 라우트는 로그인
탭까지 낸다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Path, Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.macro import EconCalendar, MacroSeries, MacroSeriesSet, MacroTabs
from kbj.services.api.readers.macro import (
    macro_calendar,
    macro_series,
    macro_series_one,
    macro_tabs,
)
from kbj.services.api.routes._common import data_router

router = data_router("macro")
TAB_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
SERIES_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:^=-]{0,63}$"


@router.get("/tabs", response_model=Envelope[MacroTabs])
def tabs(request: Request) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain="macro", ttl_s=TTL_DEFAULT_S, build=lambda: macro_tabs(st.read)
    )


@router.get("/series", response_model=Envelope[MacroSeriesSet])
def series(
    request: Request,
    tab: Annotated[str, Query(pattern=TAB_PATTERN)],
    start: Annotated[date | None, Query(alias="from")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="macro",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: macro_series(st.read, tab=tab, start=start),
    )


@router.get("/series/{series_id}", response_model=Envelope[MacroSeries])
def series_one(
    request: Request,
    series_id: Annotated[str, Path(pattern=SERIES_PATTERN)],
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="macro",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: macro_series_one(st.read, series_id=series_id, start=start, end=end),
    )


@router.get("/calendar", response_model=Envelope[EconCalendar])
def calendar(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="macro",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: macro_calendar(st.read, start=start, end=end),
    )
