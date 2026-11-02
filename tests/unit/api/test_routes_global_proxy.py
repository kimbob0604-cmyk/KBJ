"""`/api/market/global-proxy` — 글로벌 프록시(받아 둔 시세 + 국내 종가)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from kbj.core.quality import Quality
from kbj.core.rows import GlobalQuote
from tests.unit.api.api_world import World


def _seed(world: World) -> None:
    r = world.repos.market
    now = world.clock()
    snaps, _ = r.snapshot(world.market.last_day)
    base = next(iter(snaps.values()))
    r.upsert_snapshots(
        [
            replace(base, code="005930", close=100_000.0),
            replace(base, code="000660", close=200_000.0),
        ],
        loaded_by="test", received_at=now,
    )  # fmt: skip
    k = r.index_series(["0001"], world.market.last_day, 1)["0001"][-1]
    r.upsert_index_bars(
        [replace(k, code="코스피200", close=500.0)], loaded_by="test", received_at=now
    )

    def q(sym: str, price: float, prev: float | None = None, src: str = "yahoo") -> GlobalQuote:
        return GlobalQuote(
            symbol=sym, ts=now - timedelta(minutes=3), price=price, prev=prev,
            funding_1h=0.0001 if src == "hyperliquid" else None, vol_24h=None,
            currency="USD", source=src, quality=Quality.ESTIMATED,
        )  # fmt: skip

    rows = [
        q("KRW=X", 1000.0), q("xyz:SMSN", 110.0, 100.0, "hyperliquid"),
        q("xyz:KR200", 505.0, 500.0, "hyperliquid"), q("SKHY", 22.0, 21.0), q("EWY", 70.0, 70.0),
    ]  # fmt: skip
    r.put_global_quotes(rows, loaded_by="global.proxy", received_at=now)


def test_global_proxy_view(world: World, authed: TestClient) -> None:
    _seed(world)
    body = authed.get("/api/market/global-proxy").json()
    d = body["data"]
    assert body["source"] == "Hyperliquid·Yahoo" and body["quality"] == "estimated"
    assert d["usdkrw"] == 1000.0 and d["base_date"] == world.market.last_day.isoformat()
    smsn = next(p for p in d["perps"] if p["symbol"] == "xyz:SMSN")
    assert smsn["vs_close_pct"] == pytest.approx(10.0) and smsn["funding_8h_pct"] == pytest.approx(
        0.08
    )
    k200 = next(p for p in d["perps"] if p["symbol"] == "xyz:KR200")
    assert k200["vs_close_pct"] == pytest.approx(1.0)
    adr = next(x for x in d["listings"] if x["symbol"] == "SKHY")
    assert adr["premium_pct"] == pytest.approx(10.0)  # 20$ 환산 대비 22$
    assert next(x for x in d["us_etfs"] if x["symbol"] == "EWY")["change_pct"] == 0.0
    assert next(x for x in d["us_etfs"] if x["symbol"] == "SOXX")["error"] == "받은 값 없음"


def test_no_recent_quotes_is_no_data(world: World, authed: TestClient) -> None:
    r = authed.get("/api/market/global-proxy")
    assert r.status_code == 404 or r.json().get("error", {}).get("code") == "no_data", r.text
    assert "global.proxy" in r.text


def test_public_is_locked(client: TestClient) -> None:
    assert client.get("/api/market/global-proxy").status_code in (401, 403)
