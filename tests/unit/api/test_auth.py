"""로그인·세션·CSRF·잠금(docs/p3_design.md §5.4 시험 표 전부)."""

from __future__ import annotations

import logging
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kbj.services.api.auth import (
    ALL_MAX_FAILS,
    COOKIE_DEV,
    COOKIE_SECURE,
    CSRF_HEADER,
    IP_MAX_FAILS,
    Credentials,
    LoginGuard,
    LoginNotConfigured,
    SessionStore,
    hash_password,
    ip_digest,
    parse_hash,
    verify_password,
)
from kbj.store.redis_keys import WEB_LOGIN_LOCK, WEB_SESSION_PREFIX
from tests.unit.api.api_world import BASE, PASSWORD, USER, World, build_world

LOGIN = "/api/auth/login"


def _login(c: TestClient, *, password: str = PASSWORD, user: str = USER, **kw: Any) -> Any:
    headers = {"Origin": BASE, **kw.pop("headers", {})}
    return c.post(LOGIN, json={"username": user, "password": password}, headers=headers, **kw)


# ── 해시 ────────────────────────────────────────────────────────────────────────────────


def test_hash_format_and_verify() -> None:
    h = hash_password("correct horse battery", n=2**10)
    assert h.startswith("scrypt$n=1024$r=8$p=1$")
    p = parse_hash(h)
    assert p is not None and len(p.salt) == 16 and len(p.dk) == 64
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert hash_password("correct horse battery", n=2**10) != h  # 솔트가 다르다


def test_default_cost_is_design_value() -> None:
    h = hash_password("x" * 12)
    assert h.startswith("scrypt$n=32768$r=8$p=1$")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "plain",
        "scrypt$n=1000$r=8$p=1$AAAA$BBBB",
        "bcrypt$2b$12$abc",
        "scrypt$n=1024$r=8$p=1$!!$??",
    ],
)
def test_bad_hash_is_rejected_without_value(bad: str) -> None:
    assert parse_hash(bad) is None
    assert not verify_password("x", bad)
    with pytest.raises(LoginNotConfigured) as ei:
        Credentials.from_settings(USER, SecretStr(bad) if bad else None)
    assert bad == "" or bad not in str(ei.value)


def test_credentials_repr_hides_values() -> None:
    c = Credentials.from_settings(USER, SecretStr(hash_password("secret-pass-123", n=2**10)))
    assert USER not in repr(c) and "scrypt" not in repr(c)
    assert c.check(USER, "secret-pass-123")
    assert not c.check("other", "secret-pass-123")
    assert not c.check(USER, "nope")


# ── 로그인 경로 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kw",
    [{"web_user": None}, {"web_password_hash": None}, {"web_password_hash": SecretStr("broken")}],
)
def test_not_configured_is_503(kw: dict[str, Any]) -> None:
    w = build_world(intraday=False, **kw)
    r = _login(w.client())
    assert r.status_code == 503
    assert r.json()["code"] == "login_not_configured"


def test_wrong_password_401_and_counter(world: World, client: TestClient) -> None:
    r = _login(client, password="wrong-password")
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"
    assert "Retry-After" not in r.headers
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in world.redis.keys("web:login_fail:*")
    ]
    assert len(keys) == 2  # IP 하나 + 전체
    assert "testclient" not in "".join(keys)  # IP 원문은 키에 없다


def test_wrong_user_same_answer(client: TestClient) -> None:
    a = _login(client, user="nobody")
    b = _login(client, password="wrong-password")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_sixth_attempt_is_locked_even_with_right_password(client: TestClient) -> None:
    answers = [_login(client, password=f"bad-{i}") for i in range(IP_MAX_FAILS)]
    assert all(a.status_code == 401 for a in answers)
    assert "Retry-After" in answers[-1].headers  # 5번째 실패에서 잠겼다
    assert all("Retry-After" not in a.headers for a in answers[:-1])
    r = _login(client)  # 맞는 비밀번호라도
    assert r.status_code == 401
    assert int(r.headers["Retry-After"]) > 0
    assert "set-cookie" not in r.headers


def test_global_lock_after_twenty_failures(world: World, client: TestClient) -> None:
    guard = LoginGuard(world.redis)
    for i in range(ALL_MAX_FAILS):
        guard.failed(ip_digest(f"10.0.0.{i}"))  # 서로 다른 IP 20곳
    assert world.redis.exists(WEB_LOGIN_LOCK)
    r = _login(client)
    assert r.status_code == 401 and int(r.headers["Retry-After"]) > 0


def test_success_sets_cookie_and_session(world: World, client: TestClient) -> None:
    r = _login(client)
    assert r.status_code == 200
    assert set(r.json()) == {"csrf_token"}
    assert r.headers["cache-control"] == "no-store"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_SECURE}=")
    low = cookie.lower()
    for attr in ("httponly", "secure", "samesite=strict", "path=/", "max-age=43200"):
        assert attr in low
    assert "domain=" not in low
    keys = world.redis.keys(WEB_SESSION_PREFIX + "*")
    assert len(keys) == 1
    ttl = world.redis.ttl(keys[0])
    assert 43000 < ttl <= 43200  # 12시간 고정 만료
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"user": USER, "csrf_token": r.json()["csrf_token"]}
    assert me.headers["cache-control"] == "no-store"


def test_new_sid_on_every_login(world: World, client: TestClient) -> None:
    _login(client)
    first = client.cookies.get(COOKIE_SECURE)
    _login(client)
    second = client.cookies.get(COOKIE_SECURE)
    assert first and second and first != second
    assert len(world.redis.keys(WEB_SESSION_PREFIX + "*")) == 1  # 옛 세션은 지웠다


def test_login_requires_json(client: TestClient) -> None:
    r = client.post(
        LOGIN,
        data={"username": USER, "password": PASSWORD},
        headers={"Origin": BASE},
    )
    assert r.status_code == 415


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://testserver"])
def test_login_origin_checked(client: TestClient, origin: str | None) -> None:
    headers = {} if origin is None else {"Origin": origin}
    r = client.post(LOGIN, json={"username": USER, "password": PASSWORD}, headers=headers)
    assert r.status_code == 403


def test_validation_error_does_not_echo_password(client: TestClient) -> None:
    secret = "do-not-echo-this-password"
    r = client.post(
        LOGIN,
        json={"username": USER, "password": secret, "extra": secret},
        headers={"Origin": BASE},
    )
    assert r.status_code == 422
    assert secret not in r.text


def test_me_without_session_is_401(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_logout_needs_csrf_and_origin(world: World, client: TestClient) -> None:
    csrf = world.login(client)
    assert client.post("/api/auth/logout", headers={"Origin": BASE}).status_code == 403
    r = client.post(
        "/api/auth/logout", headers={CSRF_HEADER: csrf, "Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    r = client.post("/api/auth/logout", headers={CSRF_HEADER: "x" * 43, "Origin": BASE})
    assert r.status_code == 403
    assert client.get("/api/auth/me").status_code == 200  # 아직 살아 있다
    r = client.post("/api/auth/logout", headers={CSRF_HEADER: csrf, "Origin": BASE})
    assert r.status_code == 204
    assert world.redis.keys(WEB_SESSION_PREFIX + "*") == []
    assert client.get("/api/auth/me").status_code == 401


def test_dev_cookie_name_on_loopback() -> None:
    w = build_world(intraday=False, web_cookie_secure=False)
    c = TestClient(w.app(), base_url="http://testserver")
    r = c.post(
        LOGIN,
        json={"username": USER, "password": PASSWORD},
        headers={"Origin": BASE.replace("https", "http")},
    )
    # 공개 주소는 https 로 설정돼 있어 http Origin 은 거절된다 — 개발은 공개 주소를 비운다
    assert r.status_code == 403
    w2 = build_world(intraday=False, web_cookie_secure=False, public_base_url=None)
    c2 = TestClient(w2.app(), base_url="http://testserver")
    r2 = c2.post(
        LOGIN,
        json={"username": USER, "password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    assert r2.status_code == 200
    assert r2.headers["set-cookie"].startswith(f"{COOKIE_DEV}=")
    assert "secure" not in r2.headers["set-cookie"].lower()


def test_no_secret_in_responses_or_logs(
    world: World, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bodies = [_login(client, password="wrong-attempt-pw").text, _login(client).text]
    bodies.append(client.get("/api/auth/me").text)
    hashed = world.settings.web_password_hash
    assert hashed is not None
    secrets = [PASSWORD, "wrong-attempt-pw", hashed.get_secret_value(), USER, "testclient"]
    sid = client.cookies.get(COOKIE_SECURE)
    assert sid is not None
    secrets.append(sid)
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for s in secrets:
        assert s not in logs
    for s in (PASSWORD, "wrong-attempt-pw", hashed.get_secret_value(), sid):
        assert all(s not in b for b in bodies)


def test_session_store_rejects_garbage() -> None:
    r = fakeredis.FakeRedis()
    from datetime import UTC, datetime

    st = SessionStore(r, ttl_s=60, now=lambda: datetime(2026, 10, 7, tzinfo=UTC))
    assert st.get(None) is None and st.get("") is None and st.get("x" * 300) is None
    sid, data = st.create("u")
    assert st.get(sid) == data
    r.set(next(iter(r.keys(WEB_SESSION_PREFIX + "*"))), b"not-json")
    assert st.get(sid) is None
    assert r.keys(WEB_SESSION_PREFIX + "*") == []
