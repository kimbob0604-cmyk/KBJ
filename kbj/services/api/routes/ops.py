"""`/api/ops/*` — 페이지 13 '운영' 최소판(보기 전용 표 셋 — docs/p9_design.md §6.2, §11 Q5,
ADR 0037). 로그인 전용·읽기만. 캐시는 짧게(15초 — 데이터 버전 대신 시간만).

- `GET /api/ops/status` — 서비스 하트비트·점검 항목(`python -m kbj.services.ops check` 와 같은 함수)
- `GET /api/ops/jobs?date=` — 그날 작업(마지막 시도)
- `GET /api/ops/notify?date=` — 그날 발송 기록(본문 없음)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Final

from fastapi import Query, Request, Response
from pydantic import BaseModel

from kbj.services.api.cache import cache_key
from kbj.services.api.deps import PRIVATE, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.ops import OpsJobs, OpsNotify, OpsStatus
from kbj.services.api.readers.ops import OpsContext, ops_context, ops_jobs, ops_notify, ops_status
from kbj.services.api.routes._common import data_router

router = data_router("ops")
TTL_OPS_S: Final = 15
_VERSION: Final = "ops"


def _build_context(request: Request) -> Callable[[], OpsContext | None]:
    def build() -> OpsContext | None:  # pragma: no cover — 운영(Postgres) 경로
        st = get_state(request)
        if st.settings.database_url is None:
            return None
        from kbj.config.files import config_path
        from kbj.services.ops.source import PgOpsSource, load_expect
        from kbj.services.runtime.heartbeat import heartbeat_age
        from kbj.store.db import connect

        settings = st.settings
        redis = st.redis

        def conn() -> Any:  # psycopg 연결
            return connect(settings, service="api")

        def age(service: str, now: datetime) -> float | None:
            return heartbeat_age(redis, service, now)

        return OpsContext(
            PgOpsSource(conn), load_expect(config_path("jobs.yaml", settings=settings)), age
        )

    return build


def _ctx(request: Request) -> OpsContext:
    return ops_context(request.app.state, _build_context(request))


def _short_cached(request: Request, build: Callable[[], BaseModel]) -> Response:
    """15초 프로세스 캐시 + ETag(If-None-Match 이면 304)."""
    st = get_state(request)
    key = cache_key(request.url.path, request.query_params.multi_items(), _VERSION)
    hit = st.cache.get(key)
    if hit is not None:
        body, etag = hit.body, hit.etag
    else:
        body = build().model_dump_json().encode("utf-8")
        etag = st.cache.put(key, body, TTL_OPS_S).etag
    headers = {"Cache-Control": PRIVATE, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/status", response_model=Envelope[OpsStatus])
def status(request: Request) -> Response:
    st = get_state(request)
    return _short_cached(request, lambda: ops_status(st.read, _ctx(request)))


@router.get("/jobs", response_model=Envelope[OpsJobs])
def jobs(request: Request, day: Annotated[date | None, Query(alias="date")] = None) -> Response:
    st = get_state(request)
    return _short_cached(request, lambda: ops_jobs(st.read, _ctx(request), day))


@router.get("/notify", response_model=Envelope[OpsNotify])
def notify(request: Request, day: Annotated[date | None, Query(alias="date")] = None) -> Response:
    st = get_state(request)
    return _short_cached(request, lambda: ops_notify(st.read, _ctx(request), day))
