"""`/api/watchlist` — 관심종목 목록·쓰기(로그인·개인, docs/p4_design.md §5.3·D-P4-13,
docs/p8_design.md §5 — 쓰기는 P8 묶음 JNL).

목록은 가져오기 CLI·쓰기 API 가 DB 에 직접 쓴다(작업이 아니라 데이터 버전이 바뀌지 않는다) — 그래서
응답 캐시를 쓰지 않고 매번 만든다(ETag 는 붙인다 — 같으면 304).

쓰기(P8): `PUT /api/watchlist/{list}/{code}`(더하기 — 멱등),
`DELETE /api/watchlist/{list}/{code}`, `POST /api/watchlist/{list}/order`(본문 `{codes: [...]}` —
목록 전체 순서). 전부 `WriteSession`(세션 + CSRF + 64 KiB) 뒤 쓰기 주체 `kbj.services.watchlist`
하나를 부른다. 응답은 바뀐 목록(`no-store`).
거절은 사유 코드만(404 `not_in_list`·`unknown_code`, 422 `bad_order`·`bad_code`·`full`).
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import Body, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from kbj.services.api.cache import etag_of
from kbj.services.api.deps import PRIVATE, ApiError, get_state
from kbj.services.api.models.common import Envelope, ErrorBody
from kbj.services.api.models.stock import Watchlist
from kbj.services.api.readers.watchlist import watchlist as read_watchlist
from kbj.services.api.routes._common import data_router
from kbj.services.api.writes import WRITE_RESPONSES, WriteSession
from kbj.services.watchlist import WatchWriteError, add, remove, reorder

__all__ = ["DOMAIN", "WATCH_WRITE_RESPONSES", "WatchOrderIn", "router"]

DOMAIN: Final = "watchlist"
LIST_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
CODE: Final = r"^[0-9A-Z]{6}$"
_STATUS: Final = {
    "not_in_list": 404,
    "unknown_code": 404,
    "bad_code": 422,
    "bad_order": 422,
    "full": 422,
}

router = data_router(DOMAIN)
ListP = Annotated[str, Path(pattern=LIST_PATTERN)]
CodeP = Annotated[str, Path(pattern=CODE)]

WATCH_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **WRITE_RESPONSES,
    404: {"model": ErrorBody, "description": "목록에 없는 종목·모르는 종목코드"},
    422: {"model": ErrorBody, "description": "순서 목록 불일치·형식 오류"},
}


class WatchOrderIn(BaseModel):
    """목록 전체의 새 순서(빠짐·더함·중복 없이)."""

    model_config = ConfigDict(extra="forbid")

    codes: list[Annotated[str, Field(pattern=CODE)]] = Field(max_length=32767)


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


def _after(request: Request, list_name: str, status: int = 200) -> Response:
    st = get_state(request)
    return Response(
        content=read_watchlist(st.read, list_name=list_name).model_dump_json(),
        media_type="application/json",
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _refused(e: WatchWriteError) -> ApiError:
    return ApiError(_STATUS.get(e.reason, 422), e.reason, "관심종목 쓰기 거절")


@router.put("/{list_name}/{code}", response_model=Envelope[Watchlist],
            responses=WATCH_WRITE_RESPONSES)  # fmt: skip
def watch_add(request: Request, _: WriteSession, list_name: ListP, code: CodeP) -> Response:
    st = get_state(request)
    try:
        added = add(st.read.repos, list_name, code, now=st.read.now())[1]
    except WatchWriteError as e:
        raise _refused(e) from None
    return _after(request, list_name, 201 if added else 200)


@router.delete("/{list_name}/{code}", response_model=Envelope[Watchlist],
               responses=WATCH_WRITE_RESPONSES)  # fmt: skip
def watch_remove(request: Request, _: WriteSession, list_name: ListP, code: CodeP) -> Response:
    st = get_state(request)
    try:
        remove(st.read.repos, list_name, code, now=st.read.now())
    except WatchWriteError as e:
        raise _refused(e) from None
    return _after(request, list_name)


@router.post("/{list_name}/order", response_model=Envelope[Watchlist],
             responses=WATCH_WRITE_RESPONSES)  # fmt: skip
def watch_order(
    request: Request, _: WriteSession, list_name: ListP, body: Annotated[WatchOrderIn, Body()]
) -> Response:
    st = get_state(request)
    try:
        reorder(st.read.repos, list_name, body.codes, now=st.read.now())
    except WatchWriteError as e:
        raise _refused(e) from None
    return _after(request, list_name)
