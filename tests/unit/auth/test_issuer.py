"""KIS 접근토큰 발급자 — `POST` 토큰 경로(httpx.MockTransport).

GEXLAB `tests/unit/test_auth_client.py` 의 발급자 시험 7개(:332~:415)를 옮겼다(import 경로만 바꿈 —
발급자는 `kbj/services/auth/issuer.py` 에만 있다, ADR 0004). 이 폴더의 conftest 가
`KBJ_SERVICE=auth` 를 둔다(런타임 가드).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kbj.services.auth.issuer import (
    TOKEN_PATH,
    KisTokenIssuer,
    TokenIssueError,
    TokenIssueThrottled,
)

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST


def issuer_with(handler: Callable[[httpx.Request], httpx.Response]) -> KisTokenIssuer:
    http = httpx.Client(base_url="https://kis.example", transport=httpx.MockTransport(handler))
    return KisTokenIssuer(http, SecretStr(APP_KEY), SecretStr(APP_SECRET), now=lambda: NOW)


def test_issuer_posts_credentials_and_takes_earlier_expiry() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "access_token": "eyJTOKEN",
                "token_type": "Bearer",
                "expires_in": 86400,
                "access_token_token_expired": "2026-09-29 08:00:00",  # KST = 전날 23:00 UTC
            },
        )

    got = issuer_with(handler).issue()
    assert got.access_token.get_secret_value() == "eyJTOKEN"
    assert got.expires_at == datetime(2026, 9, 28, 23, 0, tzinfo=UTC)  # 86400초 뒤보다 이르다
    assert got.issued_at == NOW
    req = seen[0]
    assert (req.method, req.url.path) == ("POST", TOKEN_PATH)
    body = json.loads(req.content)
    assert body == {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"expires_in": 3600}, NOW + timedelta(hours=1)),
        (
            {"access_token_token_expired": "2026-09-29 09:30:00"},
            datetime(2026, 9, 29, 0, 30, tzinfo=UTC),
        ),
        (
            {"access_token_token_expired": "garbage", "expires_in": 60},
            NOW + timedelta(seconds=60),
        ),
    ],
)
def test_issuer_expiry_sources(payload: dict[str, Any], expected: datetime) -> None:
    got = issuer_with(lambda _: httpx.Response(200, json={"access_token": "T", **payload})).issue()
    assert got.expires_at == expected


def test_issuer_throttle_code() -> None:
    body = {
        "error_code": "EGW00133",
        "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)",
    }
    with pytest.raises(TokenIssueThrottled):
        issuer_with(lambda _: httpx.Response(403, json=body)).issue()


def test_issuer_errors_are_redacted() -> None:
    echo = {"error_code": "EGW00103", "error_description": f"bad appkey {APP_KEY} / {APP_SECRET}"}
    with pytest.raises(TokenIssueError) as ei:
        issuer_with(lambda _: httpx.Response(403, json=echo)).issue()
    msg = str(ei.value)
    assert "EGW00103" in msg and "403" in msg
    assert APP_KEY not in msg and APP_SECRET not in msg


@pytest.mark.parametrize("secret", [APP_KEY, APP_SECRET])
@pytest.mark.parametrize("inside", [3, 10, 23])
def test_issuer_redacts_before_truncating(secret: str, inside: int) -> None:
    # 200자 자르기 선에 비밀값이 걸쳐도 앞부분이 새지 않는다 — 가린 뒤 자른다
    code = "EGW00103"
    pad = "x" * (200 - len(code) - 1 - inside)  # "코드 설명" 에서 비밀값 앞 inside 글자만 200자 안
    echo = {"error_code": code, "error_description": pad + secret + " tail"}
    with pytest.raises(TokenIssueError) as ei:
        issuer_with(lambda _: httpx.Response(403, json=echo)).issue()
    assert secret[:inside] not in str(ei.value)


@pytest.mark.parametrize(
    "payload", [{"token_type": "Bearer"}, {"access_token": "LEAKYTOKEN", "token_type": "Bearer"}]
)
def test_issuer_bad_body_never_echoes_it(payload: dict[str, Any]) -> None:
    with pytest.raises(TokenIssueError) as ei:
        issuer_with(lambda _: httpx.Response(200, json=payload)).issue()
    assert "LEAKYTOKEN" not in str(ei.value)


def test_issuer_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    with pytest.raises(TokenIssueError, match="ConnectError"):
        issuer_with(handler).issue()
