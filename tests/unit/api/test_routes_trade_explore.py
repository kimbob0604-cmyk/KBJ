"""`/api/trade/items*`·`countries*`·`regions`·`surges`·`items104`(묶음 EXP — docs/p6_design.md §5,
metrics §26·§27·§29).

합성 세계(p6_world — 고정 시드, 실데이터 아님)를 메모리 저장소에 싣고 `trade.derive` 를 한 번
돌려 앱으로 본다. 빈 저장소는 404 `no_data`(작업·예정 시각·실측 번호).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.engines.trade.explore import item_index
from tests.fixtures.synthetic.p6_world import LAST_YM
from tests.unit.api.web_fixtures_p6.explore import explore_api_world
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld

SOURCE = "관세청(공공데이터포털)"


@pytest.fixture(scope="module")
def aw() -> FlashApiWorld:
    return explore_api_world()


@pytest.fixture(scope="module")
def c(aw: FlashApiWorld) -> TestClient:
    return aw.authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == SOURCE and any("달러" in n for n in body["notes"])
    return body


def test_빈_저장소는_404_no_data() -> None:
    c = explore_api_world(empty=True).authed()
    for path in (
        "/api/trade/items?level=2", "/api/trade/items/85/series", "/api/trade/items/85/countries",
        "/api/trade/countries", "/api/trade/countries/US/items", "/api/trade/regions",
        "/api/trade/surges", "/api/trade/items104",
    ):  # fmt: skip
        r = c.get(path)
        assert r.status_code == 404, path
        assert r.json()["code"] == "no_data" and "trade." in r.json()["message"], path


def test_로그인_필요() -> None:
    r = explore_api_world().client().get("/api/trade/items?level=2")
    assert r.status_code == 401


def test_HS2_트리맵_입력(c: TestClient, aw: FlashApiWorld) -> None:
    d = _get(c, "/api/trade/items?level=2&flow=exp")["data"]
    assert d["level"] == 2 and d["parent"] is None and d["ym_from"] == d["ym_to"] == LAST_YM
    assert d["hs10_login_only"] is False
    rows = d["rows"]
    assert len(rows) == 12 and [r["usd"] for r in rows] == sorted(
        (r["usd"] for r in rows), reverse=True
    )
    assert sum(r["share"] for r in rows) == pytest.approx(1.0)
    assert all(r["has_children"] and r["name"] for r in rows)
    assert aw.world is not None
    want = sum(int(r.exp_usd or 0) for r in aw.world.items if r.ym == LAST_YM and r.hs_len == 2)
    assert sum(r["usd"] for r in rows) == want


def test_층_내려가기와_HS10(c: TestClient) -> None:
    d4 = _get(c, "/api/trade/items?level=4&parent=85&flow=exp")["data"]
    assert {r["hs"] for r in d4["rows"]} == {"8501", "8502"}
    s = next(r for r in d4["rows"] if r["hs"] == "8502")
    assert s["usd"] == 120_000_000 and s["yoy"] == pytest.approx(1.0)
    d10 = _get(c, "/api/trade/items?level=10&parent=850110&flow=exp")["data"]
    assert {r["hs"] for r in d10["rows"]} == {"8501101000", "8501102000"}
    assert not any(r["has_children"] for r in d10["rows"])


def test_기간_합과_기간_YoY(c: TestClient) -> None:
    d = _get(c, "/api/trade/items?level=4&parent=85&flow=exp&from=2026-08&to=2026-10")["data"]
    s = next(r for r in d["rows"] if r["hs"] == "8502")
    assert s["usd"] == 360_000_000 and s["yoy"] == pytest.approx(1.0)


def test_잘못된_층·기간(c: TestClient) -> None:
    assert c.get("/api/trade/items?level=3").status_code == 422
    assert c.get("/api/trade/items?level=2&parent=8501").status_code == 404
    assert c.get("/api/trade/items?level=2&from=2026-10&to=2026-01").status_code == 404


def test_시계열_분해_항등식(c: TestClient, aw: FlashApiWorld) -> None:
    body = _get(c, "/api/trade/items/8502/series?flow=exp")
    d = body["data"]
    assert d["hs"] == "8502" and d["weight_unfit"] is False and len(d["points"]) == 60
    last = d["points"][-1]
    assert (
        last["ym"] == LAST_YM
        and last["yoy"] == pytest.approx(1.0)
        and last["yoy_3m"] == pytest.approx(1.0)
    )
    assert last["unit_price"] == pytest.approx(10.0)
    for p in d["points"]:
        if p["ln_price"] is not None:
            assert p["yoy"] is not None
            assert p["ln_price"] + p["ln_volume"] == pytest.approx(math.log1p(p["yoy"]), abs=1e-9)
    assert d["points"][0]["yoy"] is None  # 전년 없음
    assert aw.world is not None
    idx = item_index(aw.world.items, "exp")
    assert last["usd"] == int(idx.usd("8502", LAST_YM) or Decimal(0))


def test_중량_부적합_꼬리표(c: TestClient) -> None:
    body = _get(c, "/api/trade/items/8901/series?flow=exp")
    assert body["data"]["weight_unfit"] is True
    assert any("중량 기준 부적합" in n for n in body["notes"])


def test_중량0_단가_None(c: TestClient) -> None:
    d = _get(c, "/api/trade/items/8401100000/series?flow=exp")["data"]
    last = d["points"][-1]
    assert last["ym"] == LAST_YM and last["kg"] == 0 and last["unit_price"] is None
    assert last["ln_price"] is None and last["ln_volume"] is None


def test_품목_국가(c: TestClient) -> None:
    d = _get(c, "/api/trade/items/8502/countries?flow=exp")["data"]
    assert d["ym"] == LAST_YM and len(d["rows"]) == 10
    assert sum(r["share"] for r in d["rows"]) == pytest.approx(1.0)
    assert all(r["country_name"] and r["yoy"] == pytest.approx(1.0) for r in d["rows"])


def test_국가_총괄과_국가별_품목(c: TestClient) -> None:
    d = _get(c, "/api/trade/countries")["data"]
    assert d["ym"] == LAST_YM and len(d["rows"]) == 40
    assert sum(r["exp_share"] for r in d["rows"]) == pytest.approx(1.0)
    for r in d["rows"]:
        assert r["bal_usd"] == r["exp_usd"] - r["imp_usd"]
    ci = _get(c, "/api/trade/countries/US/items?flow=exp")["data"]
    assert ci["country"] == "US" and ci["country_name"] and len(ci["rows"]) == 24
    assert all(len(r["hs"]) == 4 for r in ci["rows"])
    assert c.get("/api/trade/countries/ZZ/items").status_code == 404


def test_지역_개편_넘어_YoY·시도_필터(c: TestClient) -> None:
    d = _get(c, "/api/trade/regions")["data"]
    moved = [r for r in d["rows"] if r["region_id"] == "KR-46-a01"]
    assert moved and all(r["sido_cd"] == "12" and r["exp_yoy"] is not None for r in moved)
    only = _get(c, "/api/trade/regions?sido=12")["data"]
    assert only["sido"] == "12" and all(r["sido_cd"] == "12" for r in only["rows"])
    hs6 = d["rows"][0]["hs6"]
    one = _get(c, f"/api/trade/regions?hs6={hs6}")["data"]
    assert {r["hs6"] for r in one["rows"]} == {hs6}
    assert sum(r["share"] for r in one["rows"]) == pytest.approx(1.0)


def test_급등·급락(c: TestClient) -> None:
    body = _get(c, "/api/trade/surges")
    d = body["data"]
    assert d["ym"] == LAST_YM and d["level"] is None and d["min_usd"] == 10_000_000
    by = {(r["level"], r["flow"], r["rule"], r["key"]): r for r in d["rows"]}
    assert by[("hs4", "exp", "surge", "8502")]["yoy"] == pytest.approx(1.0)
    assert by[("hs4", "exp", "plunge", "2701")]["name"]
    assert any("급등 =" in n for n in body["notes"])
    hs4 = _get(c, "/api/trade/surges?level=hs4")["data"]
    assert hs4["level"] == "hs4" and {r["level"] for r in hs4["rows"]} == {"hs4"}


def test_104품목(c: TestClient) -> None:
    body = _get(c, "/api/trade/items104")
    d = body["data"]
    assert d["ym"] == LAST_YM and d["mode"] == "fixed" and d["auto_selected"] is False
    assert d["rows"] and sum(r["share"] for r in d["rows"]) == pytest.approx(1.0)
    names = {r["name"] for r in d["rows"]}
    assert all(not n.startswith("HS ") for n in names)  # 고정 목록 이름
    assert any("자체 고정" in n for n in body["notes"])
