"""API 시험 공용 픽스처 — 합성 세계(메모리 저장소·가짜 시계·가짜 Redis)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_world import World, build_world


@pytest.fixture
def world() -> World:
    return build_world()


@pytest.fixture
def client(world: World) -> TestClient:
    return world.client()


@pytest.fixture
def authed(world: World, client: TestClient) -> TestClient:
    world.login(client)
    return client
