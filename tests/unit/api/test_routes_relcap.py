"""`/api/market/relcap` — 상대 시총·연도별 수익률."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_world import World, fill_relcap


def test_relcap_pairs_and_annual(world: World, authed: TestClient) -> None:
    fill_relcap(world.repos, world.market.last_day, world.clock())
    body = authed.get("/api/market/relcap?period=6M").json()
    d = body["data"]
    assert d["period"] == "6M" and body["source"] == "Yahoo·KIS"
    avgo = next(p for p in d["pairs"] if p["name"] == "브로드컴 / 엔비디아")
    assert avgo["error"] is None and avgo["latest"] == pytest.approx(50.0)
    assert avgo["series"] and avgo["high"] >= avgo["latest"] >= avgo["low"]
    hynix = next(p for p in d["pairs"] if p["name"] == "SK하이닉스 / 삼성전자")
    assert hynix["error"] == "000660.KS 현재 시총 없음"  # 합성 세계 스냅에 없는 종목
    sp = next(r for r in d["annual"] if r["symbol"] == "^GSPC")
    assert sp["returns"][0] == {"year": 2026, "pct": 10.0}
    assert d["years"][0] == 2026
    assert any("SK하이닉스 / 삼성전자" in n for n in body["notes"])


def test_query_and_empty(world: World, authed: TestClient) -> None:
    assert authed.get("/api/market/relcap?period=5Y").status_code == 422
    r = authed.get("/api/market/relcap")
    assert r.status_code == 404 and "global.daily" in r.json()["message"]
