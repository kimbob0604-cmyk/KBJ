"""`/api/filings/*` — 페이지 5 공시·일정 패널(docs/p4_design.md §5.3·§6.4).

공시 피드는 하루치(기본 오늘), 잠정실적은 기간(최대 31일), 새 오버행·내부자 큰 변동은 최근 N일, 실적
일정은 오늘부터 N일. 일반 공시는 텔레그램으로 보내지 않는다(D-P4-14 — 화면에서만 본다).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Final, Literal

from fastapi import Query, Request, Response

from kbj.services.api.deps import ApiError, cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.filings import (
    EarningsList,
    EarningsSchedule,
    FilingsFeed,
    InsiderRecent,
    OverhangRecent,
)
from kbj.services.api.readers.filings import (
    filings_earnings,
    filings_feed,
    filings_insiders,
    filings_overhang,
    filings_schedule,
)
from kbj.services.api.routes._common import data_router

__all__ = ["DOMAIN", "MAX_RANGE_DAYS", "router"]

DOMAIN: Final = "filings"
MAX_RANGE_DAYS: Final = 31
KIND_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
DateQ = Annotated[date | None, Query(alias="date")]

router = data_router(DOMAIN)


@router.get("/feed", response_model=Envelope[FilingsFeed])
def feed(
    request: Request,
    day: DateQ = None,
    kind: Annotated[str | None, Query(pattern=KIND_PATTERN)] = None,
    importance: Annotated[Literal["critical", "high", "medium", "low"] | None, Query()] = None,
    market: Annotated[Literal["KOSPI", "KOSDAQ", "KONEX"] | None, Query()] = None,
    watch_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=live_ttl(st),
        build=lambda: filings_feed(
            st.read,
            day=day,
            kind=kind,
            level=importance,
            market=market,
            watch_only=watch_only,
            limit=limit,
        ),
    )


@router.get("/earnings", response_model=Envelope[EarningsList])
def earnings(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    st = get_state(request)
    to = end or st.read.today()
    frm = start or (to - timedelta(days=6))
    if frm > to or (to - frm).days + 1 > MAX_RANGE_DAYS:
        raise ApiError(422, "invalid_request", f"기간은 from ≤ to, {MAX_RANGE_DAYS}일 이하")
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=60,
        build=lambda: filings_earnings(st.read, start=frm, end=to),
    )


@router.get("/overhang", response_model=Envelope[OverhangRecent])
def overhang(
    request: Request,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    instrument: Annotated[Literal["cb", "bw", "eb", "paid_in", "bonus"] | None, Query()] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=300,
        build=lambda: filings_overhang(st.read, days=days, instrument=instrument),
    )


@router.get("/insiders", response_model=Envelope[InsiderRecent])
def insiders(
    request: Request,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    min_ratio_pct: Annotated[float | None, Query(ge=0, le=100)] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=300,
        build=lambda: filings_insiders(st.read, days=days, min_ratio_pct=min_ratio_pct),
    )


@router.get("/schedule", response_model=Envelope[EarningsSchedule])
def schedule(request: Request, days: Annotated[int, Query(ge=1, le=60)] = 14) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=600, build=lambda: filings_schedule(st.read, days=days)
    )
