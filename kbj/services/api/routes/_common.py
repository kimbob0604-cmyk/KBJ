"""라우트 공용 — 오류 응답 표(OpenAPI)·데이터 라우터 만들기."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BeforeValidator

from kbj.services.api.deps import require_session
from kbj.services.api.models.common import ErrorBody, NoDataBody

__all__ = ["DATA_RESPONSES", "PeriodQ", "data_router"]


def _digits_to_int(v: object) -> object:
    """쿼리 문자열 '5' → 5(정수 Literal 은 문자열을 바꿔 주지 않는다). 그 밖은 그대로 검증에."""
    return int(v) if isinstance(v, str) and v.isdigit() and len(v) <= 3 else v


# 기간(영업일) — 1·5·20 만(metrics §1)
PeriodQ = Annotated[Literal[1, 5, 20], BeforeValidator(_digits_to_int), Query()]

DATA_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: {"model": ErrorBody, "description": "세션 없음·만료"},
    404: {"model": NoDataBody, "description": "아직 없음(작업·예정 시각)"},
    422: {"model": ErrorBody, "description": "쿼리 검증 실패(입력값은 싣지 않는다)"},
    503: {"model": ErrorBody, "description": "저장소·Redis 장애"},
}


def data_router(domain: str) -> APIRouter:
    """`/api/<domain>` — 모든 라우트가 로그인 세션을 요구한다(공개 라우트 없음 — §5.3)."""
    return APIRouter(
        prefix=f"/api/{domain}",
        tags=[domain],
        dependencies=[Depends(require_session)],
        responses=DATA_RESPONSES,
    )
