"""`/api/gex/*` — GEX 레벨·행사가 프로필·지표 시계열·플로(docs/p7_design.md §5 — 로그인 VM, DB 만
읽는다, 계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND-B)이 고정, 리더 본문은 ENG 묶음.

캐시 영역 `gex`(cache.py — 마지막 엔진 사이클 시각이 버전). 장중 30초·그 밖 300초.
`/futures-oi`(선물 미결제약정 — metrics §33, ADR 0043)는 영역 `gex_oi`(KRX 파생 일별·장중
투자자 작업 + 선물 전광판 최신 시각 — 장 밖에 KRX 일별이 들어와도 버전이 바뀐다).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final

from fastapi import Query, Request, Response

from kbj.services.api.deps import cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex import (
    GexBriefing,
    GexFlow,
    GexLevels,
    GexMetricSeries,
    GexProfile,
    GexScopeT,
)
from kbj.services.api.models.gex_oi import FuturesOi
from kbj.services.api.readers.gex import gex_flow, gex_levels, gex_metrics, gex_profile
from kbj.services.api.readers.gex_briefing import gex_briefing
from kbj.services.api.readers.gex_oi import DEFAULT_DAYS, MAX_DAYS, MIN_DAYS, futures_oi
from kbj.services.api.routes._common import data_router

router = data_router("gex")
DOMAIN: Final = "gex"
OI_DOMAIN: Final = "gex_oi"
NAME_PATTERN: Final = r"^[a-z][a-z0-9_]{0,40}$"
ScopeQ = Annotated[GexScopeT, Query()]


@router.get("/levels", response_model=Envelope[GexLevels])
def levels(request: Request, scope: ScopeQ = "all") -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=live_ttl(st),
        build=lambda: gex_levels(st.read, scope=scope),
    )  # fmt: skip


@router.get("/profile", response_model=Envelope[GexProfile])
def profile(request: Request, scope: ScopeQ = "all") -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=live_ttl(st),
        build=lambda: gex_profile(st.read, scope=scope),
    )  # fmt: skip


@router.get("/metrics", response_model=Envelope[GexMetricSeries])
def metrics(
    request: Request,
    name: Annotated[str, Query(pattern=NAME_PATTERN)],
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=live_ttl(st),
        build=lambda: gex_metrics(st.read, name=name, start=start, end=end),
    )  # fmt: skip


@router.get("/briefing", response_model=Envelope[GexBriefing])
def briefing(request: Request, scope: ScopeQ = "all") -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=live_ttl(st),
        build=lambda: gex_briefing(st.read, scope=scope),
    )  # fmt: skip


@router.get("/flow", response_model=Envelope[GexFlow])
def flow(request: Request) -> Response:
    st = get_state(request)
    return cached_json(request, domain=DOMAIN, ttl_s=live_ttl(st), build=lambda: gex_flow(st.read))


@router.get("/futures-oi", response_model=Envelope[FuturesOi])
def futures_oi_route(
    request: Request,
    days: Annotated[int, Query(ge=MIN_DAYS, le=MAX_DAYS)] = DEFAULT_DAYS,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=OI_DOMAIN, ttl_s=live_ttl(st),
        build=lambda: futures_oi(st.read, days=days),
    )  # fmt: skip
