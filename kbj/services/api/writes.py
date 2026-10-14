"""개인 쓰기 API 공용 — 세션·CSRF·본문 상한·미구현 응답·캐시 없는 읽기(docs/p8_design.md D-P8-16,
ADR 0008).

P8 묶음 FND. 상태를 바꾸는 요청(POST·PUT·PATCH·DELETE)은 전부 `WriteSession` 의존을 단다:
1. 로그인 세션(`require_session` — 없으면 401)
2. CSRF(`require_csrf` — `X-KBJ-CSRF` == 세션 토큰, Origin 일치, 아니면 403)
3. 본문 상한 64 KiB(넘으면 413 — 헤더와 실제 본문 둘 다)
검증 오류 422 는 위치·종류만(입력값 없음 — app 처리기). 로그에 본문을 남기지 않는다.

즉석 백테스트(`POST /api/backtest`)·규칙 검증(`POST /api/rules/validate`)은 상태를 바꾸지 않지만
POST 라 같은 문지기를 단다(다른 출처의 계산 요청을 막는다).

개인 기록(규칙·일지·매매)은 작업이 아니라 쓰기 모듈이 DB 에 직접 쓴다 — 데이터 버전이 바뀌지
않으므로
그 읽기는 응답 캐시 없이 매번 만든다(`fresh_json` — ETag 는 붙인다, 관심종목 라우트와 같은 꼴).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Final, NoReturn

from fastapi import Depends, Request, Response
from pydantic import BaseModel

from kbj.services.api.auth import SessionData
from kbj.services.api.cache import etag_of
from kbj.services.api.deps import PRIVATE, ApiError, require_csrf, require_session
from kbj.services.api.models.common import ErrorBody

__all__ = [
    "MAX_BODY_BYTES",
    "WRITE_RESPONSES",
    "WriteSession",
    "fresh_json",
    "not_ready",
    "write_guard",
]

MAX_BODY_BYTES: Final = 64 * 1024

WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    403: {"model": ErrorBody, "description": "CSRF·Origin 불일치"},
    413: {"model": ErrorBody, "description": "본문 64 KiB 초과"},
    501: {"model": ErrorBody, "description": "아직 구현 전(P8 웨이브 2)"},
}


async def write_guard(
    request: Request, session: Annotated[SessionData, Depends(require_session)]
) -> SessionData:
    require_csrf(request, session)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ApiError(413, "body_too_large", "본문이 64 KiB 를 넘는다")
    if len(await request.body()) > MAX_BODY_BYTES:
        raise ApiError(413, "body_too_large", "본문이 64 KiB 를 넘는다")
    return session


WriteSession = Annotated[SessionData, Depends(write_guard)]


def not_ready(what: str) -> NoReturn:
    """웨이브 1 의 쓰기 라우트 자리 — 501(본문은 웨이브 2 묶음)."""
    raise ApiError(501, "not_implemented", f"{what} — 준비 중")


def fresh_json(request: Request, build: Callable[[], BaseModel]) -> Response:
    """응답 캐시 없이 만든 봉투 + ETag(같으면 304)."""
    body = build().model_dump_json().encode("utf-8")
    etag = etag_of(body)
    headers = {"Cache-Control": PRIVATE, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
