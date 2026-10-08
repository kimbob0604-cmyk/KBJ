"""`web/test/fixtures/api/*.json` — 합성 응답 픽스처가 API 응답 모델과 같은 모양이고 최신인가."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from kbj.services.api.models.auth import CsrfResponse, MeResponse
from kbj.services.api.models.common import Envelope, ErrorBody, NoDataBody
from tests.unit.api.make_web_fixtures import AUTH, FIXTURES, OUT, render
from tests.unit.api.test_routes_common import DATA_ROUTES


def _base(path: str) -> str:
    b = path.split("?")[0]
    return "/api/flows/stock" if b.startswith("/api/flows/stock/") else b


_MODEL_BY_PATH: dict[str, type[BaseModel]] = {_base(p): m for p, m, _ in DATA_ROUTES}
_AUTH_MODELS: dict[str, type[BaseModel]] = {
    "auth_me.json": MeResponse,
    "auth_login.json": CsrfResponse,
    "no_data.json": NoDataBody,
    "unauthorized.json": ErrorBody,
}


def _model(path: str) -> type[BaseModel]:
    return _MODEL_BY_PATH[_base(path)]


def _load(name: str) -> Any:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "path", "live"), FIXTURES)
def test_fixture_is_valid_envelope(name: str, path: str, live: bool) -> None:
    body = _load(name)
    Envelope[_model(path)].model_validate(body)
    assert body["source"] and body["as_of"] and body["quality"]
    if live and "summary" in name:
        assert body["quality"] == "estimated"


@pytest.mark.parametrize("name", sorted(AUTH))
def test_auth_fixtures(name: str) -> None:
    _AUTH_MODELS[name].model_validate(_load(name))


def test_every_data_route_has_a_fixture() -> None:
    have = {_model(p) for _, p, _ in FIXTURES}
    assert have == set(_MODEL_BY_PATH.values())


def test_fixtures_are_fresh() -> None:
    """합성 세계로 다시 만든 글자와 같다.

    다르면 `uv run python -m tests.unit.api.make_web_fixtures` 로 다시 만든다.
    """
    for name, text in render().items():
        assert (OUT / name).read_text(encoding="utf-8") == text, name
