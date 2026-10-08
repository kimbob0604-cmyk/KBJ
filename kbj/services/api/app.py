"""FastAPI 앱 — 로그인 화면의 API·텔레그램 웹훅·로그인 SPA 정적 파일을 같은 출처로
(docs/p3_design.md §5, D-P3-3·4, ADR 0008 초안).

- `create_app(settings, *, redis, repos, now)`: 라우터(`/api/*`·`/telegram/webhook`)·미들웨어(보안
  헤더)·오류 처리기·정적 파일(`settings.web_dist_dir`, `html=True` — `/api`·`/telegram` 이 먼저).
- API 는 **외부 원천을 부르지 않는다**(DB·Redis 읽기만 — 계약 ⑩: `kbj.data.private`·
  `kbj.services.collectors` import 금지). 화면 요청이 KIS 한도를 쓰지 않는다.
- 오류는 삼키지 않는다: 저장소·Redis 장애는 로그(종류만)와 503, 데이터 없음은 404 `no_data`,
  쿼리 검증 실패는 422(입력값을 싣지 않는다 — 비밀번호가 응답에 되돌아오지 않게).
- 시계는 `now` 주입(시험은 가짜 시계). 거래 캘린더·시장 설정·금통위 일정도 주입할 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Final

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis
from redis.exceptions import RedisError

from kbj.config.files import REPO_ROOT
from kbj.config.markets import (
    CalendarEvents,
    MarketsConfig,
    load_calendar_events,
    load_markets,
)
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.time import utcnow
from kbj.services.api.auth import (
    COOKIE_DEV,
    COOKIE_SECURE,
    Credentials,
    LoginGuard,
    LoginNotConfigured,
    SessionStore,
)
from kbj.services.api.cache import DataVersions, DataVersionSource, ResponseCache
from kbj.services.api.deps import ApiError, ApiState, session_ttl
from kbj.services.api.models.common import ErrorBody, NoDataBody
from kbj.services.api.readers._common import ApiRepos, LedgerCache, NoData, ReadContext
from kbj.services.api.routes import auth, board, etf, flows, health, market, webhook
from kbj.services.notifier.webhook import WebhookHandler
from kbj.store.db import StoreError

__all__ = ["API_VERSION", "SECURITY_HEADERS", "create_app"]

log = logging.getLogger(__name__)

API_VERSION: Final = "0.3.0"
# §5.4 보안 헤더. sw.js·manifest 는 같은 출처('self')라 worker-src·manifest-src 를
# 밝혀 둔다(묶음 W 요청)
CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)
# 쓰지 않는 강력한 기능을 모두 끈다. 값은 구조화 헤더 사전(`기능=()`) — 맨 `()` 는 브라우저가
# 문법 오류로 버리고 콘솔에 오류를 남긴다(P3 데모 화면 점검에서 찾음).
PERMISSIONS_POLICY: Final = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
    "microphone=(), payment=(), usb=()"
)
SECURITY_HEADERS: Final[dict[str, str]] = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": PERMISSIONS_POLICY,
    "X-Frame-Options": "DENY",
}


def _error(status: int, code: str, message: str = "", headers: dict[str, str] | None = None):
    body = ErrorBody(code=code, message=message).model_dump(mode="json")
    return JSONResponse(
        body, status_code=status, headers={"Cache-Control": "no-store", **(headers or {})}
    )


def _credentials(settings: Settings) -> Credentials | None:
    try:
        return Credentials.from_settings(settings.web_user, settings.web_password_hash)
    except LoginNotConfigured as e:
        log.warning("웹 로그인 비활성: %s", e)  # 문구에 값 없음
        return None


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(NoData)
    async def _no_data(_: Request, exc: NoData) -> JSONResponse:
        body = NoDataBody(message=exc.message).model_dump(mode="json")
        return JSONResponse(body, status_code=404, headers={"Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 입력값(input)·문맥(ctx)은 싣지 않는다 — 위치와 종류만(비밀번호가 되돌아오지 않게)
        errors = [
            {"loc": [str(p) for p in e.get("loc", ())], "type": str(e.get("type", ""))}
            for e in exc.errors()
        ]
        body = ErrorBody(code="invalid_request", detail={"errors": errors}).model_dump(mode="json")
        return JSONResponse(body, status_code=422, headers={"Cache-Control": "no-store"})

    @app.exception_handler(RedisError)
    async def _redis(_: Request, exc: RedisError) -> JSONResponse:
        log.error("Redis 오류: %s", type(exc).__name__)
        return _error(503, "unavailable", "세션 저장소 장애")

    @app.exception_handler(StoreError)
    async def _store(_: Request, exc: StoreError) -> JSONResponse:
        log.error("저장소 오류: %s", exc.cause or type(exc).__name__)  # 문구는 접속 정보를 가린 것
        return _error(503, "unavailable", "저장소 장애")


def create_app(
    settings: Settings,
    *,
    redis: Redis,
    repos: ApiRepos,
    now: Callable[[], datetime] = utcnow,
    cal: TradingCalendar | None = None,
    markets: MarketsConfig | None = None,
    events: CalendarEvents | None = None,
    data_versions: DataVersionSource | None = None,
    static_dir: Any = None,
) -> FastAPI:
    """앱 하나. `data_versions` 가 없으면 응답을 캐시하지 않는다(ETag 는 늘 붙인다).

    `static_dir`: 로그인 SPA 빌드 폴더(기본 `settings.web_dist_dir` — 상대 경로는 레포 루트 기준).
    `False` 면 정적 파일을 내보내지 않는다(openapi 생성·시험).
    """
    versions = None if data_versions is None else DataVersions(redis, data_versions)

    def ledger_token() -> str | None:
        if versions is None:
            return None
        return versions.get("market") + "|" + versions.get("flows")

    read = ReadContext(
        ledgers=LedgerCache(ledger_token),
        repos=repos,
        now=now,
        cal=cal if cal is not None else TradingCalendar.default(),
        markets=markets if markets is not None else load_markets(settings=settings),
        events=events if events is not None else load_calendar_events(settings=settings),
    )
    state = ApiState(
        settings=settings,
        redis=redis,
        read=read,
        sessions=SessionStore(redis, ttl_s=session_ttl(settings), now=now),
        guard=LoginGuard(redis),
        credentials=_credentials(settings),
        cache=ResponseCache(now=now),
        versions=versions,
        cookie_name=COOKIE_SECURE if settings.web_cookie_secure else COOKIE_DEV,
        webhook=WebhookHandler(settings.telegram_webhook_secret, redis, now=now),
    )
    app = FastAPI(
        title="KBJ API",
        version=API_VERSION,
        description=(
            "KBJ 로그인 등급 API(사용자 1명). 모든 데이터 응답은 봉투(source·as_of·quality·notes·"
            "generated_at·data), 금액은 원 단위 정수. 외부 원천을 부르지 않는다(DB·Redis 읽기만)."
        ),
        docs_url=None,
        redoc_url=None,
        # 운영에서 스키마를 내보내지 않는다 — `python -m kbj.services.api openapi`
        openapi_url=None,
    )
    app.state.kbj = state
    _install_handlers(app)

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if request.url.path.startswith(("/api/", "/telegram/")):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    for r in (health, auth, market, board, flows, etf, webhook):
        app.include_router(r.router)

    if static_dir is not False:
        d = static_dir if static_dir is not None else settings.web_dist_dir
        path = d if d.is_absolute() else REPO_ROOT / d
        if path.is_dir():
            app.mount("/", StaticFiles(directory=path, html=True), name="spa")
        else:
            log.warning("로그인 SPA 빌드 폴더가 없다 — 정적 파일 없이 API 만 연다")
    return app
