"""`/api/board/*` — 산출 JSON 그대로 + 거르기 + 원장 순매수·역사적 신고가 범위(R8)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kbj.core.quality import Quality
from tests.unit.api.api_world import HISTORY_FROM, World, build_world, golden_payloads


def test_newhigh_passthrough_and_scope(world: World, authed: TestClient) -> None:
    body = authed.get("/api/board/newhigh").json()
    d = body["data"]
    g = golden_payloads()["newhigh"]
    assert d["counts_close"] == g["counts_close"]
    assert d["history_from"] == HISTORY_FROM
    assert any(HISTORY_FROM in n for n in body["notes"])
    assert any("억원" in n for n in body["notes"])
    assert body["quality"] == "estimated"
    assert d["filter"]["n_before_filter"] == len(g["achieved"])
    assert all(r["close_basis"]["label"] for r in d["achieved"])


def test_newhigh_filters(authed: TestClient) -> None:
    allr = authed.get("/api/board/newhigh").json()["data"]["achieved"]
    hist = authed.get("/api/board/newhigh?kind=hist").json()["data"]["achieved"]
    assert hist and all(r["close_basis"]["label"] == "hist" for r in hist)
    assert len(hist) < len(allr)
    high = authed.get("/api/board/newhigh?basis=high").json()["data"]["achieved"]
    assert all(r["high_basis"]["label"] for r in high)
    big = authed.get("/api/board/newhigh?min_turnover_eok=5000").json()["data"]["achieved"]
    assert all(r["turnover"] >= 5000 for r in big)
    assert len(big) < len(allr)


def test_newhigh_confirmed_quality_ok() -> None:
    w = build_world(intraday=False, board_quality=Quality.OK)
    c = w.client()
    w.login(c)
    body = c.get("/api/board/newhigh").json()
    assert body["quality"] == "ok"
    assert body["notes"][0] == "KRX 확정"


def test_other_artifacts(authed: TestClient) -> None:
    g = golden_payloads()
    s = authed.get("/api/board/sectors").json()["data"]
    assert len(s["sectors"]) == len(g["sectors"]["sectors"])
    e = authed.get("/api/board/events").json()["data"]
    assert e["events"] == g["events"]["events"]
    r = authed.get("/api/board/rankings").json()["data"]
    assert r["cross_codes"] == g["rankings"]["cross_codes"]


def test_date_without_board_is_404(authed: TestClient) -> None:
    r = authed.get("/api/board/newhigh?date=2020-01-02")
    assert r.status_code == 404 and r.json()["code"] == "no_data"
