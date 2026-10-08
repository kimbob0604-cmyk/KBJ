"""`/api/auth/*` — 로그인(사용자 1명)·로그아웃·세션 확인(docs/p3_design.md §5.4).

- 로그인 POST: 세션 전이라 Origin 검사 + `Content-Type: application/json` 강제(단순 폼 CSRF 차단).
  잠금 확인 → 자격 확인(이름이 틀려도 같은 scrypt 비용) → 새 sid(고정 방지) + 쿠키.
  실패·잠금은 같은 401 문구(잠금이면 Retry-After). 자격이 설정되지 않았으면 503.
- 로그아웃: 세션 + `X-KBJ-CSRF` + Origin. 세션 삭제·쿠키 지움 → 204.
- me: 세션 → `{user, csrf_token}`(없으면 401). 모두 `Cache-Control: no-store`.
- 로그: 이름·IP·쿠키·비밀번호·해시를 남기지 않는다(auth 모듈 — IP 해시 앞 8자만).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from kbj.services.api.auth import SessionData, ip_digest
from kbj.services.api.deps import (
    NO_STORE,
    ApiError,
    ApiState,
    check_origin,
    get_state,
    require_csrf,
    require_session,
    session_ttl,
)
from kbj.services.api.models.auth import CsrfResponse, LoginBody, MeResponse
from kbj.services.api.models.common import ErrorBody

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_INVALID = "이름 또는 비밀번호가 맞지 않거나 잠시 잠겼다"


def _json_only(request: Request) -> None:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise ApiError(415, "json_required", "Content-Type: application/json 만 받는다")


def _login_preflight(request: Request) -> None:
    """본문 검증보다 먼저 — 출처·형식(폼 CSRF 차단)."""
    check_origin(request)
    _json_only(request)


def _set_cookie(st: ApiState, response: Response, sid: str) -> None:
    response.set_cookie(
        st.cookie_name,
        sid,
        max_age=session_ttl(st.settings),
        path="/",
        secure=st.settings.web_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.post(
    "/login",
    response_model=CsrfResponse,
    dependencies=[Depends(_login_preflight)],
    responses={
        401: {"model": ErrorBody},
        403: {"model": ErrorBody},
        415: {"model": ErrorBody},
        422: {"model": ErrorBody},
        503: {"model": ErrorBody},
    },
)
def login(request: Request, body: LoginBody, response: Response) -> CsrfResponse:
    st = get_state(request)
    response.headers["Cache-Control"] = NO_STORE
    if st.credentials is None:
        raise ApiError(503, "login_not_configured", "로그인이 설정되지 않았다")
    ipd = ip_digest(request.client.host if request.client else None)
    wait = st.guard.locked_for(ipd)
    if wait is not None:
        raise ApiError(401, "invalid_credentials", _INVALID, headers={"Retry-After": str(wait)})
    if not st.credentials.check(body.username, body.password):
        st.guard.failed(ipd)
        wait = st.guard.locked_for(ipd)
        headers = {"Retry-After": str(wait)} if wait is not None else None
        raise ApiError(401, "invalid_credentials", _INVALID, headers=headers)
    st.guard.succeeded(ipd)
    st.sessions.drop(request.cookies.get(st.cookie_name))  # 옛 세션은 버린다(고정 방지)
    sid, sess = st.sessions.create(st.credentials.user)
    _set_cookie(st, response, sid)
    log.info("로그인 성공 ip=%s…", ipd[:8])
    return CsrfResponse(csrf_token=sess.csrf)


@router.post(
    "/logout",
    status_code=204,
    responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}},
)
def logout(request: Request, session: Annotated[SessionData, Depends(require_session)]) -> Response:
    st = get_state(request)
    require_csrf(request, session)
    st.sessions.drop(request.cookies.get(st.cookie_name))
    out = Response(status_code=204, headers={"Cache-Control": NO_STORE})
    out.delete_cookie(
        st.cookie_name, path="/", secure=st.settings.web_cookie_secure, httponly=True,
        samesite="strict",
    )  # fmt: skip
    return out


@router.get("/me", response_model=MeResponse, responses={401: {"model": ErrorBody}})
def me(session: Annotated[SessionData, Depends(require_session)], response: Response) -> MeResponse:
    response.headers["Cache-Control"] = NO_STORE
    return MeResponse(user=session.user, csrf_token=session.csrf)
