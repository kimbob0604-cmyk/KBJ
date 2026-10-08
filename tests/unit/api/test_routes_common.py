"""모든 데이터 라우트 공통 — 세션 없이 401, 봉투(source·as_of·quality) 필수, 쿼리 422
(docs/p3_design.md §8.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from kbj.services.api.models.board import BoardEvents, BoardNewhigh, BoardRankings, BoardSectors
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.etf import EtfFlows, EtfHoldingChanges, EtfPremium, EtfTypes
from kbj.services.api.models.flows import (
    IntradayFlows,
    InvestorTotals,
    ScreenResponse,
    StockFlowDetail,
)
from kbj.services.api.models.market import MarketSummary, Ribbon, SectorHeat
from tests.unit.api.api_world import World

# (경로, 데이터 모델, 장중 시계가 필요한가)
DATA_ROUTES: list[tuple[str, type[BaseModel], bool]] = [
    ("/api/market/summary", MarketSummary, False),
    ("/api/market/sectors?period=5", SectorHeat, False),
    ("/api/market/ribbon", Ribbon, False),
    ("/api/board/newhigh", BoardNewhigh, False),
    ("/api/board/sectors", BoardSectors, False),
    ("/api/board/events", BoardEvents, False),
    ("/api/board/rankings", BoardRankings, False),
    ("/api/flows/screen?mode=value&min_avg_turnover=0", ScreenResponse, False),
    ("/api/flows/investors", InvestorTotals, False),
    ("/api/flows/stock/Q00001", StockFlowDetail, False),
    ("/api/flows/intraday", IntradayFlows, True),
    ("/api/etf/flows", EtfFlows, False),
    ("/api/etf/types", EtfTypes, False),
    ("/api/etf/premium", EtfPremium, False),
    ("/api/etf/premium?basis=inav", EtfPremium, True),
    ("/api/etf/holdings/changes", EtfHoldingChanges, False),
]


@pytest.mark.parametrize("path", [p for p, _, _ in DATA_ROUTES])
def test_no_session_is_401(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


@pytest.mark.parametrize(("path", "model", "live"), DATA_ROUTES)
def test_envelope(
    world: World, authed: TestClient, path: str, model: type[BaseModel], live: bool
) -> None:
    if live:
        world.clock.set(world.slot.replace(minute=5))
    r = authed.get(path)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    for k in ("source", "as_of", "quality", "notes", "generated_at", "data"):
        assert k in body
    assert body["source"].strip()
    assert body["quality"] in ("ok", "stale", "estimated", "invalid")
    as_of = datetime.fromisoformat(body["as_of"])
    assert as_of.tzinfo is not None
    Envelope[model].model_validate(body)  # 응답 모델과 같은 모양


@pytest.mark.parametrize(
    "path",
    [
        "/api/market/sectors?period=3",
        "/api/board/newhigh?basis=open",
        "/api/board/newhigh?kind=w26",
        "/api/board/newhigh?date=2026-13-01",
        "/api/flows/screen",  # mode 필수
        "/api/flows/screen?mode=hot",
        "/api/flows/screen?mode=value&market=KONEX",
        "/api/flows/screen?mode=value&period=10",
        "/api/flows/screen?mode=value&limit=201",
        "/api/flows/screen?mode=value&min_avg_turnover=-1",
        "/api/flows/stock/5930",
        "/api/flows/stock/abcdef",
        "/api/flows/stock/Q00001?days=61",
        "/api/etf/flows?mode=up",
        "/api/etf/flows?type=crypto",
        "/api/etf/premium?basis=x",
        "/api/etf/holdings/changes?kind=MOVE",
    ],
)
def test_bad_query_is_422(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code in (404, 422), r.text
    if r.status_code == 422:
        assert r.json()["code"] == "invalid_request"
    else:  # 경로 형식이 틀리면 라우트가 없다(정적 파일 없음 — 404)
        assert path.startswith("/api/flows/stock/")


def test_no_data_is_404_with_message(world: World) -> None:
    from kbj.store.repos import memory_repos

    world.repos = memory_repos()
    c = world.client()
    world.login(c)
    for path in ("/api/flows/screen?mode=value", "/api/board/newhigh", "/api/etf/flows"):
        r = c.get(path)
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
