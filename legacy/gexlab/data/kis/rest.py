"""KIS REST 클라이언트 — KBJ P2: 정본은 `kbj.data.private.kis.rest.KisRestClient`
(설계 §1.2·§3.8 K7).

토큰은 **읽기만** 한다 — 발급은 KBJ auth 서비스 한 곳(ADR 0004). GX 의 `KisClient(settings,
token_provider=None, …)` 가 provider 없이 스스로 발급하던 경로(`default_token_provider`)는 지웠다:
provider 를 주지 않으면 토큰이 없는 제공자(`TokenUnavailable` — 발급 없음)를 끼운다. 운영 경로는
`reader(redis, settings)` 를 넘긴다(poller·scheduler 분봉).

승격한 시험: `tests/unit/test_kis_rest.py` → kbj `tests/unit/kis/test_kis_rest.py`.
"""

from __future__ import annotations

import httpx
from kbj.data.private.kis.rest import (
    MINUTE_PATH,
    MINUTE_TR,
    RATE_LIMIT_CODE,
    TOKEN_REJECTED_CODES,
    KisResponse,
    KisRestClient,
    minute_chart_params,
    redact,
)
from kbj.data.private.kis.token import TokenProvider, TokenUnavailable
from kbj.data.ratelimit import RateLimiter

from config.settings import Settings

__all__ = [
    "MINUTE_PATH",
    "MINUTE_TR",
    "RATE_LIMIT_CODE",
    "TOKEN_REJECTED_CODES",
    "KisClient",
    "KisResponse",
    "KisRestClient",
    "NoTokenProvider",
    "minute_chart_params",
    "redact",
]


class NoTokenProvider:
    """토큰이 없는 제공자 — 부르면 `TokenUnavailable`(발급하지 않는다)."""

    def get(self) -> str:
        raise TokenUnavailable(
            "토큰 제공자가 없다 — reader(redis, settings) 를 넘긴다(발급은 KBJ auth)"
        )

    def invalidate(self) -> None:
        return None


def KisClient(  # GX 클래스 이름·호출 모양을 받는 생성 함수
    settings: Settings,
    timeout: float = 20.0,
    token_provider: TokenProvider | None = None,
    rate_limiter: RateLimiter | None = None,
    transport: httpx.BaseTransport | None = None,
) -> KisRestClient:
    """GX `KisClient(settings, …)` 모양으로 KBJ `KisRestClient` 를 만든다
    (앱키·시크릿은 GX 설정에서)."""
    provider: TokenProvider = token_provider if token_provider is not None else NoTokenProvider()
    return KisRestClient(
        settings.kis_credentials(), provider, rate_limiter, timeout=timeout, transport=transport
    )
