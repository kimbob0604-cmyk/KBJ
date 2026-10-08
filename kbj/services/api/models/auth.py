"""로그인·상태 응답 모델(docs/p3_design.md §5.3·§5.4)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from kbj.services.api.models.common import ApiModel

__all__ = ["CsrfResponse", "Health", "LoginBody", "MeResponse"]


class LoginBody(BaseModel):
    """POST /api/auth/login 본문. 값은 검증 오류 문구에 싣지 않는다(app 의 422 처리기)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class CsrfResponse(ApiModel):
    csrf_token: str


class MeResponse(ApiModel):
    user: str
    csrf_token: str


class Health(ApiModel):
    ok: bool
    commit: str | None
