"""KIS 토큰 캐시·읽기 — KBJ P2: 정본은 `kbj.data.private.kis.token`·`credentials`
(설계 §1.2·§3.8 K7).

이 모듈에는 **발급 코드가 없다**(ADR 0004 — 발급은 KBJ auth 서비스 한 곳). legacy GX 가 쓰는 읽기
쪽 이름만 KBJ 에서 다시 내보낸다: 토큰 기록·Redis 캐시·캐시 제공자·소유 해시(`token_owner` —
GX 와 같은 값이라 전환 기간 같은 Redis 토큰을 읽는다).

지운 것(MIGRATION.md P2): `KisTokenIssuer`·발급 경로 상수(→ kbj/services/auth/issuer.py),
`FileTokenCache`·`FallbackTokenCache`·`default_token_provider`(토큰은 Redis 에만 — 파일 캐시 폐지).
승격한 시험: `tests/unit/test_auth_client.py` → kbj `tests/unit/kis`·`tests/unit/auth`.
"""

from kbj.data.private.kis.credentials import token_owner
from kbj.data.private.kis.token import (
    ISSUE_MIN_GAP,
    MIN_VALID,
    REFRESH_MARGIN,
    CachedTokenProvider,
    IssuedToken,
    RedisTokenCache,
    TokenCache,
    TokenError,
    TokenProvider,
    TokenRecord,
    TokenUnavailable,
    utcnow,
)

__all__ = [
    "ISSUE_MIN_GAP",
    "MIN_VALID",
    "REFRESH_MARGIN",
    "CachedTokenProvider",
    "IssuedToken",
    "RedisTokenCache",
    "TokenCache",
    "TokenError",
    "TokenProvider",
    "TokenRecord",
    "TokenUnavailable",
    "token_owner",
    "utcnow",
]
