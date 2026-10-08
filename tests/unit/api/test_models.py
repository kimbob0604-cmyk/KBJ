"""응답 모델 — 모든 데이터 응답 봉투에 source·as_of·quality 필수(절대 규칙 1, §5.2)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.services.api.__main__ import openapi_text
from kbj.services.api.models.common import Envelope


def _spec() -> dict[str, Any]:
    return json.loads(openapi_text())


def test_every_data_route_returns_an_envelope() -> None:
    spec = _spec()
    schemas = spec["components"]["schemas"]
    for path, ops in spec["paths"].items():
        if not path.startswith(("/api/market", "/api/board", "/api/flows", "/api/etf")):
            continue
        ref = ops["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        name = ref.rsplit("/", 1)[-1]
        assert name.startswith("Envelope_"), path
        env = schemas[name]
        assert {"source", "as_of", "quality", "data", "generated_at"} <= set(env["required"]), path
        assert env["properties"]["as_of"]["format"] == "date-time"


def test_data_routes_declare_401() -> None:
    spec = _spec()
    for path, ops in spec["paths"].items():
        if path.startswith(("/api/market", "/api/board", "/api/flows", "/api/etf")):
            assert "401" in ops["get"]["responses"], path


def test_envelope_rejects_naive_and_empty_source() -> None:
    now = datetime(2026, 10, 7, 15, 30, tzinfo=KST)
    naive = datetime(2026, 10, 7)  # noqa: DTZ001 — 일부러 시간대 없이
    ok = Envelope[int](source="KRX", as_of=now, quality=Quality.OK, generated_at=now, data=1)
    assert ok.notes == []
    with pytest.raises(ValidationError):
        Envelope[int](source="KRX", as_of=naive, quality=Quality.OK, generated_at=now, data=1)
    with pytest.raises(ValidationError):
        Envelope[int](source=" ", as_of=now, quality=Quality.OK, generated_at=now, data=1)
    with pytest.raises(ValidationError):
        Envelope[int](source="KRX", as_of=now, quality="good", generated_at=now, data=1)  # pyright: ignore[reportArgumentType]
