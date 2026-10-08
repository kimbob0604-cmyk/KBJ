"""`/api/etf/*` — 순유입·유형별 검산 ③·괴리율·구성종목 변동(metrics §4, §8.4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kbj.core.rows import EtfType
from kbj.engines.etf.flows import period_totals, window_flows
from tests.unit.api.api_world import World


def _engine_period(world: World, period: int) -> dict[str, float]:
    m = world.market
    tds = sorted({d.date for d in m.etf_days})[-(period + 1) :]
    out: dict[str, float] = {}
    meta = {x.code: x for x in m.etf_meta}
    flows = []
    for code in sorted({d.code for d in m.etf_days}):
        rows = sorted(
            (d for d in m.etf_days if d.code == code and d.date in tds), key=lambda d: d.date
        )
        if not rows:
            continue
        flows += window_flows(
            rows,
            period,
            trading_days=tds,
            splits=[e for e in m.split_events if e.code == code],
            delisted_on=meta[code].delisted_on if code in meta else None,
        )
    for code, p in period_totals(flows).items():
        if p.days:
            out[code] = p.net_inflow
    return out


def test_flows_match_engine(world: World, authed: TestClient) -> None:
    body = authed.get("/api/etf/flows?period=5&limit=200").json()
    want = _engine_period(world, 5)
    got = {r["code"]: r["net_inflow"] for r in body["data"]["rows"] if r["net_inflow"] is not None}
    assert got == {k: round(v) for k, v in want.items()}
    assert all(isinstance(v, int) for v in got.values())
    assert any("합치지 않는다" in n for n in body["notes"])


def test_flows_sort_and_filter(authed: TestClient) -> None:
    rows = authed.get("/api/etf/flows?mode=in").json()["data"]["rows"]
    vals = [r["net_inflow"] for r in rows if r["net_inflow"] is not None]
    assert vals == sorted(vals, reverse=True)
    out = authed.get("/api/etf/flows?mode=out").json()["data"]["rows"]
    ovals = [r["net_inflow"] for r in out if r["net_inflow"] is not None]
    assert ovals == sorted(ovals)
    t = authed.get("/api/etf/flows?type=kr_index").json()["data"]
    assert t["etf_type"] == "kr_index"
    assert all(r["etf_type"] == "kr_index" for r in t["rows"])


def test_types_check3_zero_residual(authed: TestClient) -> None:
    body = authed.get("/api/etf/types?period=20").json()
    d = body["data"]
    assert [r["etf_type"] for r in d["rows"]] == [t.value for t in EtfType]
    c3 = d["check3"]
    assert c3["n_failed"] == 0  # 합성 원장 — 검산 ③ 0 차이
    assert c3["residual"] is not None and abs(c3["residual"]) <= max(1, c3["n_checked"])
    assert c3["inflow"] == sum(r["net_inflow"] for r in d["rows"]) or abs(
        c3["inflow"] - sum(r["net_inflow"] for r in d["rows"])
    ) <= len(d["rows"])


def test_premium_nav_and_inav(world: World, authed: TestClient) -> None:
    body = authed.get("/api/etf/premium").json()
    assert body["data"]["basis"] == "nav" and body["data"]["n_checked"] > 0
    world.clock.set(world.slot.replace(minute=5))
    live = authed.get("/api/etf/premium?basis=inav").json()
    assert live["quality"] == "estimated"
    rows = live["data"]["rows"]
    assert [r["premium_pct"] for r in rows] == sorted(
        (r["premium_pct"] for r in rows), reverse=True
    )
    assert all(r["warn"] and abs(r["premium_pct"]) >= r["threshold"] for r in rows)


def test_holding_changes(authed: TestClient) -> None:
    body = authed.get("/api/etf/holdings/changes").json()
    rows = body["data"]["rows"]
    assert len(rows) == 3
    assert rows[0]["is_active"] is True  # 액티브 우선
    assert {r["kind_label"] for r in rows} >= {"신규편입", "비중확대"}
    only = authed.get("/api/etf/holdings/changes?kind=NEW").json()["data"]["rows"]
    assert [r["kind"] for r in only] == ["NEW"]
    b = authed.get("/api/etf/holdings/changes?issuer=합성운용B").json()["data"]["rows"]
    assert [r["fund_id"] for r in b] == ["tiger:F2"]
