"""읽기 전용 토큰 경로는 어떤 경우에도 발급하지 않는다(설계 §3.5, ADR 0004).

- `reader()` 제공자는 발급자가 없다. Redis 가 비었거나 만료 직전이면 `TokenUnavailable` 이고 KIS 에
  아무 요청도 보내지 않는다.
- `KisRestClient.for_service` 도 같다(토큰이 없으면 GET 도 보내지 않는다).
- 모듈 편의 함수 `access_token`·`ws_approval_key` 도 읽기만 한다.
- Redis 를 못 읽으면 `TokenUnavailable`(메모리에 살아 있는 토큰이 있으면 그것).
- kbj 어디에도 KIS 발급 경로 문자열은 auth 밖에 없다(문자열 검사 — 설계 §11.4 A 완료 명령과 같은
  것).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.rest import KisRestClient
from kbj.data.private.kis.token import (
    RedisTokenCache,
    TokenRecord,
    TokenUnavailable,
    access_token,
    reader,
    ws_approval_key,
)
from kbj.data.ratelimit import limiter_key
from kbj.store.redis_keys import KIS_TOKEN, KIS_WS_KEY

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
NOW = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # 09:00 KST
ROOT = Path(__file__).resolve().parents[3]
P_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"


def settings(**kw: object) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        service="scheduler",
        **kw,  # pyright: ignore[reportArgumentType]
    )


CREDS = KisCredentials.from_settings(settings())


class CountingKis:
    """모든 요청을 센다 — 발급(POST)이든 조회(GET)든."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.method == "POST":
            return httpx.Response(200, json={"access_token": "ISSUED", "expires_in": 86400})
        return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "MCA00000"})

    @property
    def posts(self) -> int:
        return sum(r.method == "POST" for r in self.requests)


def seed(r: fakeredis.FakeRedis, value: str, left: timedelta, key: str = KIS_TOKEN) -> None:
    rec = TokenRecord(
        access_token=SecretStr(value),
        expires_at=NOW + left,
        issued_at=NOW + left - timedelta(hours=24),
        owner=CREDS.owner,
    )
    RedisTokenCache(r, key).store(rec, NOW)


def test_reader_has_no_issuer_and_empty_cache_is_unavailable() -> None:
    r = fakeredis.FakeRedis()
    p = reader(r, CREDS, now=lambda: NOW)
    assert p.read_only
    with pytest.raises(TokenUnavailable, match="auth"):
        p.get()
    assert r.keys("*") == []  # 발급 자리(issue_blocked_until)도 잡지 않는다


@pytest.mark.parametrize("left", [timedelta(minutes=59), timedelta(minutes=2)])
def test_reader_uses_a_token_near_expiry_and_never_refreshes(left: timedelta) -> None:
    r = fakeredis.FakeRedis()
    seed(r, "near-expiry", left)
    assert reader(r, CREDS, now=lambda: NOW).get() == "near-expiry"
    assert not r.exists(KIS_TOKEN + ":issue_blocked_until")


def test_reader_refuses_a_token_under_one_minute() -> None:
    r = fakeredis.FakeRedis()
    seed(r, "dying", timedelta(seconds=50))
    with pytest.raises(TokenUnavailable):
        reader(r, CREDS, now=lambda: NOW).get()


def test_rest_client_without_token_sends_nothing() -> None:
    r = fakeredis.FakeRedis()
    kis = CountingKis()
    c = KisRestClient.for_service(
        settings(), r, now=lambda: NOW, transport=httpx.MockTransport(kis)
    )
    with pytest.raises(TokenUnavailable):
        c.get(P_PRICE, "FHKST01010100", {"FID_INPUT_ISCD": "005930"})
    assert kis.requests == []  # 발급 POST 도 조회 GET 도 없다


def test_rest_client_for_service_reads_the_auth_token_and_shares_the_app_key_bucket() -> None:
    r = fakeredis.FakeRedis()
    seed(r, "FROM-AUTH", timedelta(hours=10))
    kis = CountingKis()
    c = KisRestClient.for_service(
        settings(), r, now=lambda: NOW, transport=httpx.MockTransport(kis)
    )
    assert c.get(P_PRICE, "FHKST01010100", {"FID_INPUT_ISCD": "005930"}).ok
    assert kis.posts == 0
    (req,) = kis.requests
    assert req.headers["authorization"] == "Bearer FROM-AUTH"
    assert r.exists(limiter_key(APP_KEY))  # legacy GX·auth 와 같은 앱키 버킷(rl:kis:<해시>)


@pytest.fixture
def fresh_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """프로세스당 reader·Redis 기억을 비우고 모듈 시계를 고정한다. 이 시험은 '설정에 Redis·자격이
    없을 때'를 보므로 실행 환경의 KBJ_ 값이 끼어들지 않게 지운다."""
    import kbj.data.private.kis.token as token_mod

    for name in ("KBJ_REDIS_URL", "KBJ_KIS_APP_KEY", "KBJ_KIS_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(token_mod, "_PROCESS_READERS", {})
    monkeypatch.setattr(token_mod, "_PROCESS_REDIS", {})
    monkeypatch.setattr(token_mod, "utcnow", lambda: NOW)


@pytest.mark.usefixtures("fresh_process")
def test_module_helpers_read_only() -> None:
    r = fakeredis.FakeRedis()
    s = settings()
    with pytest.raises(TokenUnavailable):
        access_token(settings=s, redis=r)
    seed(r, "TOK", timedelta(hours=10))
    seed(r, "WSKEY", timedelta(hours=10), key=KIS_WS_KEY)
    assert access_token(settings=s, redis=r) == "TOK"  # 같은 reader 를 다시 쓴다(프로세스당 하나)
    assert ws_approval_key(settings=s, redis=r) == "WSKEY"
    with pytest.raises(TokenUnavailable, match="REDIS_URL"):
        access_token(settings=s)  # Redis 를 주지도, 설정에 두지도 않았다
    with pytest.raises(TokenUnavailable, match="KBJ_KIS_APP_KEY"):
        access_token(settings=Settings(_env_file=None), redis=r)  # pyright: ignore[reportCallIssue]
    assert r.keys("*issue_blocked*") == []


def test_redis_down_is_token_unavailable_but_a_live_memory_token_survives() -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    clock = [NOW]
    p = reader(r, CREDS, now=lambda: clock[0])
    seed(r, "LIVE", timedelta(minutes=50))  # 갱신 여유(60분) 안 — 메모리만으로는 끝나지 않는다
    assert p.get() == "LIVE"
    server.connected = False
    clock[0] = NOW + timedelta(minutes=5)
    assert p.get() == "LIVE"  # Redis 가 잠깐 끊겨도 만료 전까지 메모리 값
    cold = reader(fakeredis.FakeRedis(server=server), CREDS, now=lambda: clock[0])
    with pytest.raises(TokenUnavailable, match="Redis") as ei:
        cold.get()
    assert "LIVE" not in str(ei.value)


_OAUTH = re.compile(r"oauth2/(?:tokenP|Approval)")


def test_no_issue_path_string_outside_auth() -> None:
    """설계 §11.4 A 완료 명령의 grep 과 같다(auth 밖 `kbj/**/*.py` 에 발급 경로 문자열 0건)."""
    hits = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "kbj").rglob("*.py")
        if "services/auth/" not in p.as_posix() and _OAUTH.search(p.read_text(encoding="utf-8"))
    ]
    assert hits == []
