"""KBJ P2 다리 시험 — legacy GX 는 KIS 토큰을 발급하지 않는다(설계 §1.9·§3.8 K7·K8, ADR 0004).

`KisClient(settings)` 를 토큰 제공자 없이 만들어도 발급 요청(POST)을 보내지 않고 `TokenUnavailable`
로 끝난다. auth 의 읽기 쪽 이름만 남았고(발급자 없음), 거래 캘린더·리미터·마스터·KRX·스풀은 KBJ
정본을 다시 내보낸다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

import core.calendar
import data.kis.auth_client as auth_client
import data.kis.master
import data.kis.ratelimit
import data.krx.eod
import data.spool
import services.auth.service as auth_service
from config.settings import Settings
from data.kis.rest import KisClient

NOW = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)


def test_gexlab_never_issues_and_reexports_kbj() -> None:
    sent: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(200, json={"access_token": "x", "expires_in": 86400})

    s = Settings(
        _env_file=None, kis_app_key=SecretStr("PSgexlabBridgeKey"), kis_app_secret=SecretStr("sec")
    )  # type: ignore[call-arg]
    client = KisClient(s, transport=httpx.MockTransport(handle))
    with pytest.raises(auth_client.TokenUnavailable):
        client.get("/uapi/x", "FHKST01010100", {})
    assert sent == []  # 발급 POST 도 조회 GET 도 없다

    # 읽기 전용 리더 — Redis 가 비면 TokenUnavailable(발급하지 않는다)
    r = fakeredis.FakeRedis()
    with pytest.raises(auth_client.TokenUnavailable):
        auth_service.reader(r, s, now=lambda: NOW).get()
    for gone in ("KisTokenIssuer", "FileTokenCache", "default_token_provider", "TOKEN_PATH"):
        assert not hasattr(auth_client, gone), gone
    for gone in ("AuthService", "KisApprovalKeyIssuer", "build_auth_service", "APPROVAL_PATH"):
        assert not hasattr(auth_service, gone), gone
    assert auth_service.main() == 2  # 옛 진입점은 안내만 하고 끝난다

    # 정본 다시 내보내기 — 같은 객체다(두 벌 금지)
    import kbj.core.calendar
    import kbj.data.private.kis.master
    import kbj.data.private.krx.client
    import kbj.data.ratelimit
    import kbj.store.spool

    assert core.calendar.TradingCalendar is kbj.core.calendar.TradingCalendar
    assert data.kis.ratelimit.RedisRateLimiter is kbj.data.ratelimit.RedisRateLimiter
    assert data.kis.master.parse_master is kbj.data.private.kis.master.parse_master
    assert data.spool.DiskSpool is kbj.store.spool.DiskSpool
    assert issubclass(data.krx.eod.KrxClient, kbj.data.private.krx.client.KrxClient)
    assert s.kis_base.startswith("https://")  # 주소는 KBJ credentials 에서
