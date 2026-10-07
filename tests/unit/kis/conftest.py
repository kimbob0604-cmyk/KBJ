"""KIS 시험 공통 — GEXLAB 시험의 생성자 차이를 여기 한 곳에서 흡수한다(설계 §1.11).

GX 시험은 `Settings(...)`(GX 설정 — `kis_base` 속성)와 `KisClient(settings, token_provider=None, …)`
(provider 가 없으면 스스로 발급하는 클라이언트)를 쓴다. KBJ 에서는:

- 자격은 `KisCredentials`(주소는 비공개 상수 — `base_url(env)`), 설정에는 `kis_base` 가 없다 →
  `GxSettings`: KBJ `Settings` 에 GX 이름 `kis_base` 만 더한 시험용 설정.
- REST 클라이언트는 토큰을 **읽기만** 한다(`KisRestClient(creds, token_provider, rate_limiter)`) —
  발급은 auth 만(ADR 0004) → `KisClient(...)`: GX 호출 모양을 받아 KBJ 클라이언트를 만든다. provider
  를 주지 않으면 GX 가 '처음 한 번 발급하고 캐시를 나눠 쓴' 자리를 **auth 가 대신한다**: 같은
  캐시(시험마다 `tmp_path` 하나 = Redis 하나)에 토큰이 없을 때만 가짜 KIS 로 `AuthService` 를 한
  step 돌려 넣고, 클라이언트는 `reader` 로 읽는다. 그래서 GX 시험의 '발급 1회, 다음 프로세스는 캐시
  재사용' 단언이 KBJ 구조(발급은 auth, 나머지는 읽기만)에서 그대로 성립한다.
- 시계는 고정(`GX_NOW`) — 벽시계에 기대지 않는다(CLAUDE.md §4).

`from tests.unit.kis.conftest import …` 로 쓴다(`tests/unit/auth` 도 같은 설정을 쓴다).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import fakeredis
import httpx

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials, base_url
from kbj.data.private.kis.rest import KisRestClient
from kbj.data.private.kis.token import TokenProvider, reader
from kbj.data.ratelimit import RateLimiter
from kbj.services.auth.service import build_auth_service
from kbj.services.runtime import MemoryHealthSink

GX_NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST — GX 시험의 NOW


class GxSettings(Settings):
    """KBJ 설정 + GX 이름 `kis_base`(시험용). 값은 `kbj.data.private.kis.credentials.base_url`."""

    @property
    def kis_base(self) -> str:
        return base_url(self.kis_env)


# 시험(= tmp_path)마다 Redis 하나 — GX 에서 tmp_path 의 파일 캐시를 나눠 쓰던 자리
_CACHES: dict[Path, fakeredis.FakeServer] = {}


def cache_server(tmp_path: Path) -> fakeredis.FakeServer:
    return _CACHES.setdefault(tmp_path, fakeredis.FakeServer())


def auth_fills_cache(
    settings: Settings, server: fakeredis.FakeServer, transport: httpx.BaseTransport
) -> None:
    """auth 대역: 캐시가 비었으면 가짜 KIS 로 접근토큰을 한 번 발급해 넣는다(접속키는 끔)."""
    r = fakeredis.FakeRedis(server=server)
    auth = settings.model_copy(update={"service": "auth"})
    creds = KisCredentials.from_settings(auth)
    with httpx.Client(base_url=creds.base_url, transport=transport) as http:
        svc = build_auth_service(
            auth, r, http, MemoryHealthSink(), ws_key=False, now=lambda: GX_NOW
        )
        status = svc.step(GX_NOW)
    assert status["token"].action in ("fresh", "refreshed"), status


def KisClient(  # GX 클래스 이름을 그대로 받는 시험용 어댑터(함수)
    settings: Settings,
    *,
    token_provider: TokenProvider | None = None,
    rate_limiter: RateLimiter | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 20.0,
    cache_dir: Path | None = None,
) -> KisRestClient:
    """GX `KisClient(settings, …)` 모양으로 KBJ `KisRestClient` 를 만든다(위 머리말)."""
    creds = KisCredentials.from_settings(settings)
    if token_provider is None:
        if cache_dir is None or transport is None:
            raise TypeError("provider 없이 만들려면 cache_dir·transport 가 필요하다(auth 대역)")
        server = cache_server(cache_dir)
        auth_fills_cache(settings, server, transport)
        token_provider = reader(fakeredis.FakeRedis(server=server), creds, now=lambda: GX_NOW)
    return KisRestClient(creds, token_provider, rate_limiter, timeout=timeout, transport=transport)


__all__ = ["GX_NOW", "GxSettings", "KisClient", "auth_fills_cache", "cache_server"]
