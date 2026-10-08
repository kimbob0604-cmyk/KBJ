"""API 는 외부 원천을 부르지 않는다(D-P3-3) — 실제 네트워크 전송·소켓 연결이 불리면 실패.

TestClient 는 앱을 프로세스 안에서 부른다(자체 전송). 그 밖의 httpx 전송·소켓 연결은 하나라도
불리면 시험이 실패한다. 저장소는 메모리, Redis 는 fakeredis 라 소켓을 쓰지 않는다.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from tests.unit.api.api_world import World
from tests.unit.api.test_routes_common import DATA_ROUTES


def test_all_routes_without_network(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def boom(*a: Any, **k: Any) -> Any:
        calls.append(repr(a)[:80])
        raise AssertionError("외부 호출 금지")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    c = world.client()
    world.login(c)
    for live in (False, True):
        if live:
            world.clock.set(world.slot.replace(minute=5))
        for path, _, _ in DATA_ROUTES:
            assert c.get(path).status_code in (200, 404)
    assert c.get("/api/health").status_code == 200
    assert calls == []
