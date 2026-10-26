"""`/api/screen/*` — 종목 스크리너(페이지 11 '스크리너'). 로그인 전용(모든 데이터 라우트와 같다)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.screen import ScreenCis
from kbj.services.api.readers.screen import screen_cis_read
from kbj.services.api.routes._common import data_router

router = data_router("screen")


@router.get("/cis", response_model=Envelope[ScreenCis])
def cis(request: Request, day: Annotated[date | None, Query()] = None) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain="screen",
        ttl_s=TTL_DEFAULT_S,
        build=lambda: screen_cis_read(st.read, day=day),
    )
