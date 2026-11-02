"""`/api/etf/stocks` — 종목별 ETF 수급(ETF 기간 값 × 구성종목 비중의 종목별 합)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from kbj.engines.etf.stock_flows import EtfSide, aggregate, coverage
from tests.unit.api.api_world import World, fill_stock_holdings
from tests.unit.api.test_routes_etf import _engine_period


def _hold(world: World) -> None:
    fill_stock_holdings(world.repos, world.market.last_day, world.clock())


@pytest.fixture
def held(world: World, authed: TestClient) -> TestClient:
    _hold(world)
    return authed


def test_stock_inflow_is_weighted_sum(world: World, held: TestClient) -> None:
    body = held.get("/api/etf/stocks?period=5&limit=300").json()
    d = body["data"]
    want = _engine_period(world, 5)
    rows = {r["code"]: r for r in d["rows"]}
    win = sorted({s.date for s in world.market.snaps})[-5:]
    assert set(rows) == {"Q00001", "Q00002"}  # 비중 없는 행·ETF 행은 빠진다
    assert "E00002" not in want  # 상장폐지 — 순유입도 거래대금도 넣지 않는다
    q1 = want["E00001"] * 0.30 + want["E00003"] * 0.10
    assert rows["Q00001"]["inflow"] == round(q1)
    assert rows["Q00002"]["inflow"] == round(want["E00001"] * 0.20)
    assert rows["Q00001"]["n_etfs"] == 2 and rows["Q00002"]["n_etfs"] == 1
    top = rows["Q00001"]["top_etfs"]
    assert {t["code"] for t in top} == {"E00001", "E00003"}
    assert abs(top[0]["inflow"]) >= abs(top[1]["inflow"])
    etf_turn = {
        c: sum(x.turnover or 0 for x in world.market.etf_days if x.code == c and x.date in win)
        for c in ("E00001", "E00003")
    }
    assert rows["Q00001"]["etf_turnover"] == round(
        etf_turn["E00001"] * 0.3 + etf_turn["E00003"] * 0.1
    )
    # 주식 수·거래대금 대비 — 원장 스냅의 끝 종가·기간 거래대금
    last = world.market.last_day
    snaps = [s for s in world.market.snaps if s.code == "Q00001" and s.date in win]
    close = next(s.close for s in snaps if s.date == last)
    assert close is not None
    turn = sum(s.turnover or 0 for s in snaps)
    assert rows["Q00001"]["inflow_shares"] == round(q1 / close)
    assert rows["Q00001"]["stock_turnover"] == turn
    assert rows["Q00001"]["inflow_pct"] == pytest.approx(q1 / turn * 100, rel=1e-6)
    assert d["n_etfs_known"] == 2 and d["holdings_asof"] == (last - timedelta(days=1)).isoformat()
    assert body["quality"] == "estimated"
    assert any("구성종목 비중" in n for n in body["notes"])


def test_stock_sort(held: TestClient) -> None:
    rows = held.get("/api/etf/stocks?sort=in").json()["data"]["rows"]
    vals = [r["inflow"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    out = held.get("/api/etf/stocks?sort=out").json()["data"]["rows"]
    assert [r["inflow"] for r in out] == sorted(vals)
    val = held.get("/api/etf/stocks?sort=value").json()["data"]["rows"]
    t = [r["etf_turnover"] for r in val]
    assert t == sorted(t, reverse=True)
    assert held.get("/api/etf/stocks?sort=bogus").status_code == 422


def test_no_holdings_is_no_data(authed: TestClient) -> None:
    r = authed.get("/api/etf/stocks")
    body = r.json()
    assert body.get("data") is None or body["data"]["rows"] == []
    assert "구성종목" in str(body)


def test_aggregate_unit() -> None:
    etfs = {
        "E1": EtfSide("E1", "가", 1000.0, 5000.0, indiv=100.0),
        "E2": EtfSide("E2", "나", -400.0, None),
        "E3": EtfSide("E3", "다", None, 800.0),
    }
    hold = {
        "E1": {"A": ("에이", 50.0), "B": ("비", 10.0), "E2": ("ETF", 5.0)},
        "E2": {"A": ("에이", 25.0)},
        "E3": {"B": ("비", 50.0), "C": ("씨", 0.0)},
        "E9": {"A": ("에이", 99.0)},  # 흐름을 모르는 ETF — 빠진다
    }
    out = aggregate(etfs, hold)
    assert set(out) == {"A", "B"}
    assert out["A"].inflow == pytest.approx(1000 * 0.5 - 400 * 0.25)
    assert out["A"].turnover == pytest.approx(2500.0)  # E2 거래대금 모름 — 아는 것만
    assert out["A"].indiv == pytest.approx(50.0)
    assert out["B"].inflow == pytest.approx(100.0) and out["B"].turnover == pytest.approx(900.0)
    assert [p[0] for p in out["A"].top()] == ["E1", "E2"]
    assert aggregate(etfs, hold, stocks={"B"}).keys() == {"B"}
    n, cov = coverage(etfs, ["E1", "E9"])
    assert n == 1 and cov == pytest.approx(1000 / 1400 * 100)
