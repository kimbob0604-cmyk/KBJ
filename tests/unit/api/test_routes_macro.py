"""`/api/macro/*`·상단 띠 P5 칩(readers/macro.py·market.py 띠 부분 — MAC). 합성 세계(p5_world)만."""

from __future__ import annotations

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.synthetic.p5_world import CLOCK, build_world
from tests.unit.api.api_world import World
from tests.unit.api.web_fixtures_p5.macro import load_macro


@pytest.fixture
def loaded(world: World, authed: TestClient) -> TestClient:
    world.clock.set(CLOCK.astimezone(UTC))
    load_macro(world.repos, build_world())
    return authed


def test_empty_store_answers_no_data(authed: TestClient) -> None:
    for path in ("/api/macro/tabs", "/api/macro/series?tab=us", "/api/macro/calendar"):
        r = authed.get(path)
        assert r.status_code == 404 and r.json()["code"] == "no_data"


def test_tabs_follow_config_and_mark_login_and_unprobed(loaded: TestClient) -> None:
    body = loaded.get("/api/macro/tabs").json()
    tabs = body["data"]["tabs"]
    assert [t["id"] for t in tabs] == [
        "rates",
        "prices",
        "growth",
        "labor",
        "external",
        "us",
        "market",
    ]
    market = next(t for t in tabs if t["id"] == "market")
    assert market["public"] is False and all(s["tier"] == "private" for s in market["series"])
    rates = {s["series_id"]: s for s in tabs[0]["series"]}
    assert (
        rates["ECOS:817Y002:ktb3y"]["ready"] is False
        and rates["ECOS:817Y002:ktb3y"]["probe"] == "#43"
    )
    assert rates["DERIVED:credit_aa"]["dataset"] == "DERIVED"


def test_series_values_derived_and_missing(loaded: TestClient) -> None:
    body = loaded.get("/api/macro/series?tab=us").json()
    by = {s["series_id"]: s for s in body["data"]["series"]}
    d = by["DERIVED:us_10y_2y"]
    assert d["derived"] and d["tier"] == "public" and d["latest"]["value"] is not None
    gaps = [p for p in d["points"] if p["value"] is None]
    assert gaps  # DGS2 결측일은 차도 null
    assert by["TREASURY:10y"]["na_reason"] == "확인 전(#45)" and by["TREASURY:10y"]["points"] == []
    assert body["source"] and body["quality"] in ("ok", "stale", "estimated", "invalid")
    market = loaded.get("/api/macro/series?tab=market").json()["data"]["series"]
    krw = next(s for s in market if s["series_id"] == "YAHOO:KRW=X")
    assert krw["tier"] == "private" and krw["source"] == "YAHOO" and krw["points"]


def test_series_one_window_and_unknown(loaded: TestClient) -> None:
    r = loaded.get("/api/macro/series/FRED:DGS10?from=2026-11-01&to=2026-11-13")
    pts = r.json()["data"]["points"]
    assert pts and pts[0]["period"] >= "2026-11-02" and pts[-1]["period"] <= "2026-11-13"
    assert loaded.get("/api/macro/series/FRED:NOPE").status_code == 404
    assert loaded.get("/api/macro/series/DERIVED:credit_aa").json()["data"]["derived"] is True
    assert loaded.get("/api/macro/series?tab=nope").status_code == 404


def test_calendar_merges_forecast_rows_for_login(loaded: TestClient) -> None:
    body = loaded.get("/api/macro/calendar").json()
    rows = body["data"]["rows"]
    ff = [r for r in rows if r["forecast"] is not None]
    assert ff and all(r["source"].endswith("ff") for r in ff)
    assert any(r["source"] == "bok" for r in rows)
    assert body["data"]["start"] == "2026-11-10" and body["data"]["end"] == "2026-12-17"
    assert "예상치(ForexFactory)는 로그인 등급" in body["notes"]


def test_ribbon_p5_chips_replace_pending_when_values_exist(loaded: TestClient) -> None:
    chips = {c["key"]: c for c in loaded.get("/api/market/ribbon").json()["data"]["chips"]}
    sp = chips["credit_spread"]
    assert (
        sp["phase_pending"] is None
        and sp["value"]["quality"] == "stale"
        and "공표 지연" in sp["note"]
    )
    assert sp["tier"] == "public" and sp["value"]["source"] == "ECOS:817Y002"
    bal = chips["credit_balance"]
    assert bal["phase_pending"] is None and bal["value"]["value"] > 0
    assert chips["kr_vs_global"]["phase_pending"] == "P5"  # 미국 지수 입력이 없다 — 준비 중 그대로
    assert chips["gex_flip"]["phase_pending"] == "P7"
