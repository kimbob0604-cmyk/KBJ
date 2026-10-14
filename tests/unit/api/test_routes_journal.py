"""`/api/journal/*`·`/api/portfolio*`·`/api/watchlist` 쓰기 본문(묶음 JNL — docs/p8_design.md
§4.7·§5).

API 세계(메모리 저장소)에 합성 종목 셋(990001~990003 — 260 거래일 일봉)을 더한다. 쓰기는 세션 +
CSRF(모양·64 KiB 는 test_routes_p8_shape.py), 거절은 사유 코드만(입력값을 되돌리지 않는다)."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.core.quality import Quality
from kbj.core.rows import Bar, UniverseRow
from kbj.core.rules_rows import PickRow
from kbj.services.api.auth import CSRF_HEADER
from tests.unit.api.api_world import BASE, World

CODES = ("990001", "990002", "990003")
H = dict[str, str]


def _weekdays(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


@pytest.fixture
def api(world: World) -> tuple[TestClient, H, World, list[date]]:
    end = world.market.last_day
    days = _weekdays(end, 260)
    rows: list[Bar] = []
    for k, code in enumerate(CODES):
        r = random.Random(k)  # noqa: S311 — 합성 값
        px = 10_000.0 * (k + 1)
        for d in days:
            px *= 1 + r.gauss(0, 0.02)
            c = round(px, 0)
            rows.append(Bar(code, d, c, c, c, c, 1000, int(c * 1000), "KRX", "KRX", Quality.OK))
    world.repos.market.upsert_daily_bars(rows, loaded_by="test")
    world.repos.market.upsert_universe(
        [
            UniverseRow(c, end, f"합성{c[-1]}", "KOSPI", "common", None, "KRX", Quality.OK)
            for c in CODES
        ],
        loaded_by="test",
    )
    c = world.client()
    csrf = world.login(c)
    return c, {CSRF_HEADER: csrf, "Origin": BASE}, world, days


ANALYSIS: dict[str, Any] = {
    "code": "990001",
    "analyzed_on": "2026-09-15",
    "price": 12345.5,
    "eps": {"bear": 1000, "base": 1200.5, "bull": None},
    "tp": {"base": 15000},
    "tags": ["합성"],
    "memo": "메모",
    "value_sources": {"price": "kbj"},
}


def test_empty_reads(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, _, _, _ = api
    a = c.get("/api/journal/analysis")
    assert a.status_code == 200 and a.json()["data"]["items"] == []
    assert a.json()["notes"] and a.json()["source"] == "KBJ"
    assert c.get("/api/journal/trades").json()["data"]["items"] == []
    p = c.get("/api/portfolio").json()
    assert p["data"]["positions"] == [] and p["data"]["trades_n"] == 0
    assert c.get("/api/journal/picks").status_code == 404
    assert c.get("/api/portfolio/correlation").status_code == 404
    assert c.get("/api/journal/analysis/5").status_code == 404


def test_writes_need_csrf(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, h, _, _ = api
    assert c.post("/api/journal/analysis", json=ANALYSIS).status_code == 403
    assert c.post("/api/journal/trades", json={}, headers={"Origin": BASE}).status_code == 403
    assert c.put("/api/watchlist/main/990001").status_code == 403
    assert (
        c.delete("/api/journal/trades/1", headers={**h, "Origin": "https://evil"}).status_code
        == 403
    )


def test_analysis_crud_and_markdown(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, h, _, _ = api
    r = c.post("/api/journal/analysis", json=ANALYSIS, headers=h)
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    d = r.json()["data"]
    assert d["id"] == 1 and d["source"] == "api" and d["price"] == 12345.5
    assert d["eps"] == {"bear": 1000.0, "base": 1200.5, "bull": None}
    assert d["chain_ids"] == [None, None, None]
    lst = c.get("/api/journal/analysis", params={"code": "990001"}).json()["data"]["items"]
    assert [x["id"] for x in lst] == [1]
    assert c.get("/api/journal/analysis", params={"code": "990002"}).json()["data"]["items"] == []
    r = c.patch("/api/journal/analysis/1", json={**ANALYSIS, "conclusion": "보유"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["conclusion"] == "보유"
    md = c.get("/api/journal/analysis/1/markdown").json()["data"]["markdown"]
    assert md.startswith("# 분석 일지 — 합성1 (990001)")
    assert "12,345.50 (KBJ 계산)" in md and "보유" in md
    assert c.patch("/api/journal/analysis/9", json=ANALYSIS, headers=h).status_code == 404
    r = c.delete("/api/journal/analysis/1", headers=h)
    assert r.status_code == 200 and r.json()["data"] == {"id": 1}
    assert c.get("/api/journal/analysis/1").status_code == 404
    assert c.delete("/api/journal/analysis/1", headers=h).json()["code"] == "unknown_analysis"


def test_analysis_422_no_echo(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, h, _, _ = api
    secret = "비밀메모-되돌리면-안됨"
    r = c.post(
        "/api/journal/analysis", json={**ANALYSIS, "code": "bad!", "memo": secret}, headers=h
    )
    assert r.status_code == 422 and secret not in r.text and "bad!" not in r.text


def test_trades_portfolio_and_negative(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, h, w, days = api
    buy = {"code": "990001", "side": "buy", "trade_date": days[-30].isoformat(), "price": 10000,
           "qty": 10, "fee": 15}  # fmt: skip
    r = c.post("/api/journal/trades", json=buy, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["fee"] == 15.0 and r.json()["data"]["source"] == "api"
    sell = {**buy, "side": "sell", "trade_date": days[-10].isoformat(), "price": 11000, "qty": 4}
    assert c.post("/api/journal/trades", json=sell, headers=h).status_code == 201
    over = {**sell, "qty": 777}
    r = c.post("/api/journal/trades", json=over, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "negative_holding"
    assert "777" not in r.text
    items = c.get("/api/journal/trades").json()["data"]["items"]
    assert [x["side"] for x in items] == ["sell", "buy"]  # 최근 먼저
    p = c.get("/api/portfolio").json()
    pos = p["data"]["positions"][0]
    last = w.repos.market.series(["990001"], days[-1], 1)["990001"][-1].close
    avg = (10000 * 10 + 15) / 10
    assert pos["qty"] == 6 and pos["avg_cost"] == pytest.approx(avg)
    assert pos["last_close"] == last and pos["close_date"] == days[-1].isoformat()
    assert pos["realized"] == round((11000 - avg) * 4 - 15)  # 매도 수수료 15
    assert last is not None and pos["unrealized"] == round((last - avg) * 6)
    assert pos["name"] == "합성1" and pos["quality"] == "ok"
    assert p["data"]["realized_total"] == pos["realized"] and p["data"]["trades_n"] == 2
    assert p["data"]["monthly"] and all("month" in m for m in p["data"]["monthly"])
    # 매수를 지우면 매도가 보유보다 많아진다 → 422
    tid = items[1]["id"]
    assert c.delete(f"/api/journal/trades/{tid}", headers=h).status_code == 422
    assert c.delete("/api/journal/trades/99", headers=h).json()["code"] == "unknown_trade"
    # 보유 1종목 — 상관 404
    assert c.get("/api/portfolio/correlation", params={"set": "holdings"}).status_code == 404


def test_watchlist_writes_and_correlation(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, h, _, _ = api
    for code in CODES:
        r = c.put(f"/api/watchlist/main/{code}", headers=h)
        assert r.status_code == 201, r.text
    again = c.put("/api/watchlist/main/990001", headers=h)
    assert again.status_code == 200 and again.headers["cache-control"] == "no-store"
    assert [x["code"] for x in again.json()["data"]["items"]] == list(CODES)
    assert c.put("/api/watchlist/main/990009", headers=h).json()["code"] == "unknown_code"
    r = c.post("/api/watchlist/main/order", json={"codes": ["990003", "990001"]}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "bad_order"
    r = c.post("/api/watchlist/main/order", json={"codes": ["990003", "990001", "990002"]},
               headers=h)  # fmt: skip
    assert [x["code"] for x in r.json()["data"]["items"]] == ["990003", "990001", "990002"]
    corr = c.get("/api/portfolio/correlation", params={"set": "watchlist", "n": "120"}).json()
    data = corr["data"]
    assert data["window_days"] == 120 and data["codes"] == ["990003", "990001", "990002"]
    m = data["matrix"]
    assert [m[i][i] for i in range(3)] == [1.0, 1.0, 1.0]
    assert m[0][1] == m[1][0] and -1 <= m[0][1] <= 1
    assert data["names"] == ["합성3", "합성1", "합성2"] and corr["quality"] == "ok"
    r = c.delete("/api/watchlist/main/990003", headers=h)
    assert [x["code"] for x in r.json()["data"]["items"]] == ["990001", "990002"]
    assert c.delete("/api/watchlist/main/990003", headers=h).status_code == 404


def test_prefill_from_bars_and_nothing(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, _, w, days = api
    r = c.get("/api/journal/analysis/prefill/990002")
    assert r.status_code == 200, r.text
    f = r.json()["data"]["fields"]
    bars = w.repos.market.series(["990002"], days[-1], 250)["990002"]
    assert f["price"] == bars[-1].close
    assert f["ret_52w"] == pytest.approx((bars[-1].close / bars[0].close - 1) * 100)  # type: ignore[operator]
    assert f["value_sources"] == {"price": "kbj", "ret_52w": "kbj"}
    assert f["chain_ids"] == [None, None, None] and f["tp_source"] is None
    assert c.get("/api/journal/analysis/prefill/990009").status_code == 404


def test_picks_after_returns(api: tuple[TestClient, H, World, list[date]]) -> None:
    c, _, w, days = api
    d0 = days[-20]
    w.repos.personal.put_picks_legacy_import(
        [PickRow(d0, "합성추천", 1, "990001", "합성1", "kr", 0.9, Decimal(10000))],
        loaded_by="import:sd",
    )
    r = c.get("/api/journal/picks")
    assert r.status_code == 200, r.text
    it = r.json()["data"]["items"][0]
    bars = {b.date: b.close for b in w.repos.market.series(["990001"], days[-1], 30)["990001"]}
    cal = sorted(w.repos.market.trading_days(days[-1], 40))
    nxt = [d for d in cal if d > d0]
    assert it["rets"]["d1"] == pytest.approx((bars[nxt[0]] / 10000 - 1) * 100)  # type: ignore[operator]
    assert it["rets"]["d7"] == pytest.approx((bars[nxt[6]] / 10000 - 1) * 100)  # type: ignore[operator]
