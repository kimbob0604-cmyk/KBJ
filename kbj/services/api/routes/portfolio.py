"""`/api/portfolio*` — 포지션·실현/평가 손익·월별, 상관 행렬(docs/p8_design.md §4.7·§5 — 로그인, DB
만 읽는다, 계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND)이 고정, 리더 본문은 JNL. 매매 기록은 쓰기
모듈이 DB 에 바로 쓰므로 캐시 없이 만든다(`fresh_json`).
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from fastapi import Query, Request, Response

from kbj.services.api.deps import get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.journal import Correlation, Portfolio
from kbj.services.api.readers.portfolio import correlation, portfolio
from kbj.services.api.routes._common import data_router
from kbj.services.api.writes import fresh_json

__all__ = ["DOMAIN", "router"]

DOMAIN: Final = "portfolio"

router = data_router(DOMAIN)


@router.get("", response_model=Envelope[Portfolio])
def positions(request: Request) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: portfolio(st.read))


@router.get("/correlation", response_model=Envelope[Correlation])
def corr(
    request: Request,
    set_name: Annotated[Literal["watchlist", "holdings"], Query(alias="set")] = "watchlist",
    n: Annotated[Literal["60", "120"], Query()] = "60",
) -> Response:
    st = get_state(request)
    days: Literal[60, 120] = 60 if n == "60" else 120
    return fresh_json(request, lambda: correlation(st.read, set_name=set_name, days=days))
