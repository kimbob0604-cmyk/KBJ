"""KIS 토큰 auth — KBJ P2: 발급 서비스는 KBJ 로 옮겼다(설계 §3.8 K8·K9, ADR 0004).

토큰·웹소켓 접속키 **발급**은 `python -m kbj.services.auth` 한 곳만 한다. 이 모듈에는 발급 코드가
없고, legacy GX(poller·ws-gateway·scheduler 분봉)가 쓰는 읽기 쪽 이름만 남긴다:

- `TOKEN_KEY`·`WS_KEY_KEY` — Redis 키(KBJ `kbj.data.private.kis.token.CACHE_KEYS` —
  `kis:token`·`kis:ws_key`)
- `reader(redis, settings, name="token", *, now)` — 읽기 전용 제공자(발급하지 않는다). GX 설정을
  받아 KBJ `kbj.data.private.kis.token.reader` 로 넘긴다(같은 소유 해시)
- `redact` — 값 가리기(KBJ `kbj.core.masking.redact`)

승격한 시험: `tests/unit/test_auth_service.py` → kbj `tests/unit/auth/test_auth_service.py`
(health 3개는 kbj `tests/unit/runtime/test_runtime.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from kbj.core.masking import redact
from kbj.data.private.kis.token import CACHE_KEYS, CachedTokenProvider, TokenUnavailable, utcnow
from kbj.data.private.kis.token import reader as _kbj_reader
from redis import Redis

from config.settings import Settings

__all__ = ["TOKEN_KEY", "WS_KEY_KEY", "CredentialName", "main", "reader", "redact"]

TOKEN_KEY = CACHE_KEYS["token"]
WS_KEY_KEY = CACHE_KEYS["ws_key"]
CredentialName = Literal["token", "ws_key"]


def reader(
    redis: Redis,
    settings: Settings,
    name: CredentialName = "token",
    *,
    now: Callable[[], datetime] = utcnow,
) -> CachedTokenProvider:
    """다른 서비스용 읽기 전용 제공자 — 발급하지 않는다(발급은 KBJ auth 만, ADR 0004).

    `reader(r, s).get()` 은 접근토큰, `reader(r, s, "ws_key").get()` 은 웹소켓 접속키. 만료
    임박이어도 만료 1분 전까지 돌려주고, 없으면 `TokenUnavailable`. KIS 가 거절하면 `invalidate()`
    (거절 신고만 — 새 값은 auth 가 넣는다).
    """
    try:
        creds = settings.kis_credentials()
    except ValueError:
        raise TokenUnavailable("KIS_APP_KEY 가 없다") from None
    return _kbj_reader(redis, creds, name, now=now, by="legacy-gexlab")


def main() -> int:  # pragma: no cover — 옛 compose 진입점
    """옛 `python -m services.auth` — 발급은 KBJ 로 옮겼다. 실행하면 안내하고 2 로 끝난다."""
    import sys

    print(
        "services.auth 는 KBJ 로 옮겼다 — `python -m kbj.services.auth`(KBJ_SERVICE=auth)로 띄운다"
        " (ADR 0004, legacy/gexlab/MIGRATION.md P2)",
        file=sys.stderr,
    )
    return 2
