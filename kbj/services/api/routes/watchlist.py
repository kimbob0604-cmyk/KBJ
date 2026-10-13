"""`/api/watchlist` — 관심종목 목록(로그인·개인, docs/p4_design.md §5.3·D-P4-13).

목록은 가져오기 CLI 가 DB 에 직접 쓴다(작업이 아니라 데이터 버전이 바뀌지 않는다) — 그래서 응답
캐시를 쓰지 않고 매번 만든다(ETag 는 붙인다 — 같으면 304). 쓰기 API 는 P8.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Query, Request, Response

from kbj.services.api.cache import etag_of
from kbj.services.api.deps import PRIVATE, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.stock import Watchlist
from kbj.services.api.readers.watchlist import watchlist as read_watchlist
from kbj.services.api.routes._common import data_router

__all__ = ["DOMAIN", "router"]

DOMAIN: Final = "watchlist"
LIST_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"

router = data_router(DOMAIN)


@router.get("", response_model=Envelope[Watchlist])
def watchlist(
    request: Request,
    list_name: Annotated[str, Query(alias="list", pattern=LIST_PATTERN)] = "main",
) -> Response:
    st = get_state(request)
    body = read_watchlist(st.read, list_name=list_name).model_dump_json().encode("utf-8")
    etag = etag_of(body)
    headers = {"Cache-Control": PRIVATE, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
