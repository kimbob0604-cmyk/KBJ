"""보안 헤더·캐시 헤더·ETag·정적 파일(docs/p3_design.md §5.3·§5.4·§8.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.app import CSP, PERMISSIONS_POLICY, SECURITY_HEADERS
from tests.unit.api.api_world import BASE, World


def _check_security(headers: dict[str, str] | object) -> None:
    for k, v in SECURITY_HEADERS.items():
        assert headers[k] == v  # pyright: ignore[reportIndexIssue]


def test_csp_value() -> None:
    for part in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
    ):
        assert part in CSP
    assert "unsafe" not in CSP and "tradingview" not in CSP


def test_permissions_policy_is_a_structured_dictionary() -> None:
    """`기능=()` 쌍을 쉼표로 — 맨 `()` 는 브라우저가 문법 오류로 버린다(RFC 8941 사전)."""
    items = [x.strip() for x in PERMISSIONS_POLICY.split(",")]
    assert items
    for item in items:
        name, _, value = item.partition("=")
        assert name and name == name.lower() and all(c.isalnum() or c == "-" for c in name)
        assert value == "()"
    assert {"camera", "microphone", "geolocation", "payment"} <= {i.split("=")[0] for i in items}


@pytest.mark.parametrize("path", ["/api/health", "/api/auth/me", "/api/market/summary"])
def test_security_headers_everywhere(client: TestClient, path: str) -> None:
    _check_security(client.get(path).headers)


def test_cache_control(authed: TestClient) -> None:
    assert authed.get("/api/health").headers["cache-control"] == "no-store"
    assert authed.get("/api/auth/me").headers["cache-control"] == "no-store"
    r = authed.get("/api/flows/investors")
    assert r.headers["cache-control"] == "private, no-cache"
    assert r.headers["etag"].startswith('"')
    unauth = TestClient(authed.app, base_url=BASE).get("/api/flows/investors")
    assert unauth.status_code == 401 and unauth.headers["cache-control"] == "no-store"


def test_etag_304(authed: TestClient) -> None:
    r = authed.get("/api/etf/types")
    again = authed.get("/api/etf/types", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["cache-control"] == "private, no-cache"


def test_health_needs_no_session(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "commit": "abc1234"}


def test_static_spa_same_origin(world: World, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>KBJ</title>", encoding="utf-8")
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch',()=>{})", encoding="utf-8")
    c = world.client(static_dir=tmp_path)
    r = c.get("/")
    assert r.status_code == 200 and "KBJ" in r.text
    _check_security(r.headers)
    assert c.get("/sw.js").status_code == 200
    assert c.get("/api/health").json()["ok"] is True  # /api 가 정적 파일보다 먼저
    assert c.get("/api/market/summary").status_code == 401


def test_missing_static_dir_is_not_fatal(world: World, tmp_path: Path) -> None:
    c = world.client(static_dir=tmp_path / "nope")
    assert c.get("/api/health").status_code == 200
    assert c.get("/").status_code == 404


def test_no_openapi_or_docs_routes(client: TestClient) -> None:
    for p in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(p).status_code == 404
