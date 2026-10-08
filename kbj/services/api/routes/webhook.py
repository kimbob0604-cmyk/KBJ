"""`POST /telegram/webhook` — P2 `WebhookHandler.handle` 를 감싼다(docs/p3_design.md §5.5).

- 세션·CSRF·Origin 면제(텔레그램이 부른다). 인증은 헤더 `X-Telegram-Bot-Api-Secret-Token` 을
  처리기가 상수 시간으로 비교한다. 비밀이 설정되지 않았으면 **늘 403**(§5.5 — 인증 없는 수신은
  없다. P2 처리기는 이때 503 을 주지만 HTTP 경로에서는 설계대로 403 으로 끊고 처리기를
  부르지 않는다).
- 본문은 1 MiB 상한으로 읽는다(넘으면 413 — 처리기를 부르지 않는다).
- 응답은 처리기의 `as_response()` 그대로(본문 `{"ok": bool}` — 사유 없음).
"""

from __future__ import annotations

import json
from typing import Final

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from kbj.services.api.deps import ApiError, get_state
from kbj.services.api.models.common import ErrorBody
from kbj.services.notifier.webhook import WEBHOOK_PATH

MAX_BODY: Final = 1024 * 1024

router = APIRouter(tags=["telegram"])


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY:
                raise ApiError(413, "too_large")
        except ValueError:
            raise ApiError(400, "bad_length") from None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(413, "too_large")
    return bytes(buf)


@router.post(
    WEBHOOK_PATH,
    responses={403: {"model": ErrorBody}, 413: {"model": ErrorBody}, 503: {"model": ErrorBody}},
)
async def telegram_webhook(request: Request) -> Response:
    st = get_state(request)
    secret = st.settings.telegram_webhook_secret
    if secret is None or not secret.get_secret_value():
        return Response(
            content=json.dumps({"ok": False}),
            status_code=403,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    body = await _read_limited(request)
    req = {
        "method": "POST",
        "path": WEBHOOK_PATH,
        "headers": dict(request.headers),
        "body": body,
    }
    res = await run_in_threadpool(st.webhook.handle, req)
    out = res.as_response()
    return Response(
        content=out["body"],
        status_code=int(out["status"]),
        headers={**out["headers"], "Cache-Control": "no-store"},
    )
