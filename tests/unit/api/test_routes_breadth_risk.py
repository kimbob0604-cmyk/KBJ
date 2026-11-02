"""`/api/diag/breadth-risk` — 시장 체력(ADR·이격도)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kbj.engines.market.adr import adr_series, daily_counts, disparity
from tests.unit.api.api_world import World


def test_adr_matches_engine(world: World, authed: TestClient) -> None:
    body = authed.get("/api/diag/breadth-risk?period=3M&ma=25").json()
    d = body["data"]
    assert d["period"] == "3M" and d["ma"] == 25 and d["window"] == 20
    r = world.repos.market
    end = r.trading_days(world.market.last_day, 1)[0]
    tds = sorted(r.trading_days(end, 84))
    snaps, _ = r.snapshot(end)
    counts = daily_counts(r.series(None, end, 84), {c: s.market for c, s in snaps.items()}, tds)
    want = adr_series(counts["KOSPI"])[-1]
    kospi = next(a for a in d["adr"] if a["market"] == "KOSPI")
    assert kospi["latest"] == pytest.approx(want.adr)
    assert kospi["up"] == want.up and kospi["down"] == want.down
    assert kospi["zone"] in ("과열", "중립", "바닥권")
    idx = r.index_series(["0001"], end, 200)["0001"]
    dis = [v for _, v in disparity([(b.date, b.close) for b in idx], 25) if v is not None]
    kd = next(x for x in d["disparity"] if x["market"] == "KOSPI")
    assert kd["latest"] == pytest.approx(dis[-1])
    assert body["source"] == "KRX" and any("ADR" in n for n in body["notes"])


def test_query_validation(authed: TestClient) -> None:
    assert authed.get("/api/diag/breadth-risk?period=5Y").status_code == 422
    assert authed.get("/api/diag/breadth-risk?ma=10").status_code == 422
    assert authed.get("/api/diag/breadth-risk?ma=30").json()["data"]["ma"] == 50  # 25·50 만
