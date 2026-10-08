"""앱 상태·의존성·공용 응답 도우미(docs/p3_design.md §5.1~§5.4).

- `ApiState`: create_app 이 한 번 만들어 `app.state.kbj` 에 둔다(설정·Redis·읽기 문맥·세션·캐시).
- `require_session`: 데이터 라우트 전부의 의존성 — 세션 쿠키가 없거나 만료면 401.
- `ApiError`: 사유 코드만 담는 오류(입력값·내부 문구를 응답에 싣지 않는다).
- `cached_json`: 봉투 응답을 (경로, 정렬된 쿼리, 데이터 버전) 키로 캐시하고 ETag·`Cache-Control:
  private, no-cache` 를 붙인다(로그인 데이터 — 공유 캐시 금지, 매번 재검증).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from fastapi import Request, Response
from pydantic import BaseModel
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.calendar import is_equity_regular_hours
from kbj.services.api.auth import (
    CSRF_HEADER,
    Credentials,
    LoginGuard,
    SessionData,
    SessionStore,
    csrf_ok,
)
from kbj.services.api.cache import DataVersions, ResponseCache, cache_key, etag_of
from kbj.services.api.readers._common import ReadContext
from kbj.services.notifier.webhook import WebhookHandler

__all__ = [
    "NO_STORE",
    "PRIVATE",
    "ApiError",
    "ApiState",
    "cached_json",
    "check_origin",
    "get_state",
    "require_csrf",
    "require_session",
    "session_ttl",
]

NO_STORE: Final = "no-store"
PRIVATE: Final = "private, no-cache"
TTL_LIVE_S: Final = 30
TTL_DEFAULT_S: Final = 300


class ApiError(Exception):
    """사유 코드가 있는 HTTP 오류 — app 의 처리기가 `ErrorBody` 로 바꾼다."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str = "",
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


@dataclass
class ApiState:
    settings: Settings
    redis: Redis
    read: ReadContext
    sessions: SessionStore
    guard: LoginGuard
    credentials: Credentials | None  # None = 로그인 미설정(503)
    cache: ResponseCache
    versions: DataVersions | None  # None = 캐시하지 않는다(시험·버전 원천 없음)
    cookie_name: str
    webhook: WebhookHandler


def get_state(request: Request) -> ApiState:
    st = getattr(request.app.state, "kbj", None)
    if not isinstance(st, ApiState):
        raise RuntimeError("create_app 으로 만든 앱이 아니다")
    return st


def require_session(request: Request) -> SessionData:
    """세션 쿠키 → 세션. 없거나 만료면 401(본문에 사유 코드만)."""
    st = get_state(request)
    s = st.sessions.get(request.cookies.get(st.cookie_name))
    if s is None:
        raise ApiError(401, "unauthorized", "로그인이 필요하다")
    return s


def _origin(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def check_origin(request: Request) -> None:
    """상태를 바꾸는 요청의 Origin 검사 — `KBJ_PUBLIC_BASE_URL` 의 출처와 같아야 한다.

    공개 주소가 설정되지 않았으면(개발) 요청 자신의 출처와 비교한다. Origin 이 없으면 403.
    """
    st = get_state(request)
    got = request.headers.get("origin")
    if not got:
        raise ApiError(403, "origin_required", "Origin 헤더가 없다")
    base = st.settings.public_base_url
    want = _origin(base) if base else _origin(str(request.base_url))
    if want is None or _origin(got) != want:
        raise ApiError(403, "origin_mismatch", "다른 출처의 요청")


def require_csrf(request: Request, session: SessionData) -> None:
    """`X-KBJ-CSRF` == 세션 토큰(상수 시간) 그리고 Origin 일치."""
    if not csrf_ok(session, request.headers.get(CSRF_HEADER)):
        raise ApiError(403, "csrf", "CSRF 토큰이 맞지 않는다")
    check_origin(request)


def session_ttl(settings: Settings) -> int:
    return settings.web_session_ttl_h * 3600


def live_ttl(st: ApiState) -> int:
    """장중이면 30초, 그 밖 300초(§5.3 캐시 표)."""
    return TTL_LIVE_S if is_equity_regular_hours(st.read.now(), st.read.cal) else TTL_DEFAULT_S


def cached_json(
    request: Request,
    *,
    domain: str,
    ttl_s: int,
    build: Callable[[], BaseModel],
) -> Response:
    """봉투 응답 — 데이터 버전 캐시 + ETag(If-None-Match 이면 304)."""
    st = get_state(request)
    key: str | None = None
    if st.versions is not None:
        version = st.versions.get(domain)
        key = cache_key(request.url.path, request.query_params.multi_items(), version)
    hit = st.cache.get(key) if key is not None else None
    if hit is not None:
        body, etag = hit.body, hit.etag
    else:
        body = build().model_dump_json().encode("utf-8")
        etag = etag_of(body)
        if key is not None:
            st.cache.put(key, body, ttl_s)
    headers = {"Cache-Control": PRIVATE, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
