"""`/api/diag/*` — 종합 판정·공포탐욕·지표 카드·이력·이벤트(docs/p7_design.md §5 — 로그인 VM, DB 만
읽는다, 계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND-B)이 고정, 리더 본문은 DIAG 묶음.

캐시 영역 `diag`(cache.py — diag.public·diag.daily·krx.vkospi 완료가 버전).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Final

from fastapi import Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.diag import DiagEvents, DiagHistory, DiagSummary
from kbj.services.api.readers.diag import diag_events, diag_history, diag_summary
from kbj.services.api.routes._common import data_router

router = data_router("diag")
DOMAIN: Final = "diag"
KEY_PATTERN: Final = r"^[a-z][a-z0-9_]{0,40}$"


@router.get("/summary", response_model=Envelope[DiagSummary])
def summary(request: Request, day: Annotated[date | None, Query(alias="date")] = None) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: diag_summary(st.read, day=day),
    )  # fmt: skip


@router.get("/history", response_model=Envelope[DiagHistory])
def history(
    request: Request,
    key: Annotated[str, Query(pattern=KEY_PATTERN)],
    days: Annotated[int, Query(ge=20, le=1250)] = 250,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: diag_history(st.read, key=key, days=days),
    )  # fmt: skip


@router.get("/events", response_model=Envelope[DiagEvents])
def events(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: diag_events(st.read, start=start, end=end),
    )  # fmt: skip
