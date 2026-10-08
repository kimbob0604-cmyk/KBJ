"""`GET /api/health` — 살아 있음·커밋(데이터 없음, 세션 불필요 — docs/p3_design.md §5.3)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from kbj.services.api.deps import NO_STORE, get_state
from kbj.services.api.models.auth import Health

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=Health)
def health(request: Request, response: Response) -> Health:
    response.headers["Cache-Control"] = NO_STORE
    return Health(ok=True, commit=get_state(request).settings.git_commit)
