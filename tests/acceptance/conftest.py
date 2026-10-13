"""완료 시험 공용 — P4 처리기를 실제로 돌린 합성 20종목 세계(한 프로세스에 한 번).

docs/p4_design.md §0.3·§8.4. 세계를 만드는 순서·근거는 `p4_pipeline.py` 머리말.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.acceptance.p4_pipeline import P4Pipeline, built
from tests.unit.api.api_world_p4 import P4ApiWorld


@pytest.fixture(scope="session")
def pipeline() -> P4Pipeline:
    return built()


@pytest.fixture(scope="session")
def api(pipeline: P4Pipeline) -> P4ApiWorld:
    return pipeline.api()


@pytest.fixture(scope="session")
def client(api: P4ApiWorld) -> TestClient:
    return api.authed()
