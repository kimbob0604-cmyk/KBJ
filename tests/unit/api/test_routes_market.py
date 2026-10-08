"""`/api/market/*` — 요약·업종·띠(docs/p3_design.md §4.4·§5.3)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.market.turnover import market_turnover
from tests.fixtures.synthetic.ledger_gen import ledger_inputs
from tests.unit.api.api_world import World


def test_summary_after_close(world: World, authed: TestClient) -> None:
    body = authed.get("/api/market/summary").json()
    d = body["data"]
    last = world.market.last_day
    assert d["date"] == last.isoformat()
    led, _ = apply_checks(build_ledger(**ledger_inputs(world.market)))  # pyright: ignore[reportArgumentType]
    want = market_turnover(led, last)
    assert want is not None
    t = d["turnover"]
    assert t["today"]["value"] == want.value
    assert t["basis"] == "close" and "마감" in t["tags"] and "NXT 미포함" in t["tags"]
    assert len(t["series"]) == 20
    codes = {x["code"] for x in d["indices"]}
    assert {"0001", "1001"} <= codes
    assert all(not x["live"] for x in d["indices"])
    assert any("NXT" in n for n in body["notes"])
    assert d["breadth"]["total"] > 0
    assert d["investors"]["check1_residual"] == 0


def test_summary_intraday_uses_index_turnover(world: World, authed: TestClient) -> None:
    world.clock.set(world.slot.replace(minute=5))
    body = authed.get("/api/market/summary").json()
    t = body["data"]["turnover"]
    assert t["basis"] == "intraday_index"
    assert t["today"]["quality"] == "estimated"
    assert t["today"]["value"] == 4_100_000_000_000 + 3_200_000_000_000
    assert "장중(지수 기준)" in t["tags"]
    kospi = next(x for x in body["data"]["indices"] if x["code"] == "0001")
    assert kospi["live"] and kospi["value"] == 2650.5 and kospi["quality"] == "estimated"
    assert body["quality"] == "estimated"


def test_sectors(world: World, authed: TestClient) -> None:
    body = authed.get("/api/market/sectors?period=5").json()
    cells = body["data"]["cells"]
    assert [c["code"] for c in cells] == ["S0001", "S0002"]
    assert all(c["chg_pct"] is not None for c in cells)
    world.clock.set(world.slot.replace(minute=5))
    live = authed.get("/api/market/sectors?period=1").json()
    assert live["quality"] == "estimated"
    assert live["data"]["cells"][0]["chg_pct"] == 0.5


def test_sectors_pending_without_codes(world: World) -> None:
    world.markets = world.markets.model_copy(
        update={"market": world.markets.market.model_copy(update={"sector_indices": ()})}
    )
    c = world.client()
    world.login(c)
    r = c.get("/api/market/sectors")
    assert r.status_code == 404
    assert "준비 중" in r.json()["message"]


def test_ribbon_chips(world: World, authed: TestClient) -> None:
    body = authed.get("/api/market/ribbon").json()
    chips = {c["key"]: c for c in body["data"]["chips"]}
    assert chips["session"]["value"]["value"] == "야간장"
    assert chips["market_turnover"]["value"]["quality"] in ("ok", "estimated")
    assert chips["newhigh_count"]["value"]["value"] > 0
    assert chips["cyc_def"]["value"] is None and chips["cyc_def"]["note"]
    for k in ("credit_spread", "credit_balance", "export_flash", "kr_vs_global", "gex_flip"):
        assert chips[k]["phase_pending"] and chips[k]["value"] is None
    mpc = chips["bok_mpc"]
    if mpc["value"] is not None:
        assert mpc["value"]["value"] >= 0


def test_summary_and_sectors_with_past_date(world: World, authed: TestClient) -> None:
    """`date?`(§5.3) — 지난 날을 주면 그날 이하 확정값만(장중 슬롯은 쓰지 않는다)."""
    world.clock.set(world.slot.replace(minute=5))  # 장중이라도
    days = sorted({b.date for b in world.market.bars})
    past = days[-3]
    body = authed.get(f"/api/market/summary?date={past.isoformat()}").json()
    d = body["data"]
    assert d["date"] == past.isoformat()
    assert all(not x["live"] for x in d["indices"])
    assert d["turnover"]["basis"] == "close"
    assert body["as_of"] <= f"{past.isoformat()}T15:30:00+09:00"
    sec = authed.get(f"/api/market/sectors?period=1&date={past.isoformat()}").json()
    assert all(
        c["as_of"] is None or c["as_of"][:10] <= past.isoformat() for c in sec["data"]["cells"]
    )
    assert authed.get("/api/market/summary?date=2026-13-01").status_code == 422
