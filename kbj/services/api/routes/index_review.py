"""`/api/index-review*` — 패시브 지수 정기변경 예측(P5 2차 묶음 IDX, metrics §24). 로그인
전용(구성종목·
컷오프 추정 — 공개 빌드·내보내기 제외). 본문은 `readers/index_review.py`(저장된 예측만 읽는다)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.index_review import IndexReview, StockIndexBadges
from kbj.services.api.readers.index_review import index_review, stock_badges
from kbj.services.api.routes._common import data_router

router = data_router("index-review")
DOMAIN = "index_review"


@router.get("", response_model=Envelope[IndexReview])
def review(
    request: Request,
    index: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)] = None,
    review: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: index_review(st.read, index=index, review=review),
    )


@router.get("/stock/{code}", response_model=Envelope[StockIndexBadges])
def stock(
    request: Request,
    code: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: stock_badges(st.read, code),
    )
