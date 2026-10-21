"""`/api/gex/briefing` — 감마 국면·레벨 해설·행사가 표·옵션 포지셔닝.

합성 p7 세계(2026-10-21 요청).
"""

from __future__ import annotations

from typing import Any

import pytest

from kbj.core.gex.gex import OPTION_MULTIPLIER
from kbj.services.api.readers.gex_briefing import NEAR_STRIKES, STRATEGY_NOTE
from tests.unit.api.web_fixtures_p7.gex import GexApiWorld, gex_api_world


@pytest.fixture(scope="module")
def aw() -> GexApiWorld:
    return gex_api_world()


@pytest.fixture(scope="module")
def body(aw: GexApiWorld) -> dict[str, Any]:
    r = aw.authed().get("/api/gex/briefing?scope=all")
    assert r.status_code == 200, r.text
    return r.json()


def test_regime_follows_the_flip_and_headline_names_the_first_tests(body: dict[str, Any]) -> None:
    d = body["data"]
    by = {lv["kind"]: lv for lv in d["levels"]}
    above = d["price"] >= by["flip"]["value"]
    assert d["regime"] == ("long" if above else "short")
    assert d["headline"].startswith("롱감마 구간" if above else "숏감마 구간")
    # 레벨표는 높은 값부터, 현재가 줄이 있고 거리는 값 − 현재가
    vals = [lv["value"] for lv in d["levels"]]
    assert vals == sorted(vals, reverse=True) and "spot" in by
    for lv in d["levels"]:
        assert lv["dist_pt"] == pytest.approx(lv["value"] - d["price"], abs=0.01)
    assert by["call_wall"]["lines"][0].startswith("막히면")
    assert by["put_wall"]["lines"][0].startswith("무너지면")


def test_strike_table_is_near_the_price_and_sorted(body: dict[str, Any]) -> None:
    rows = body["data"]["strikes"]
    assert 0 < len(rows) <= NEAR_STRIKES
    assert [r["strike"] for r in rows] == sorted((r["strike"] for r in rows), reverse=True)
    for r in rows:
        assert r["net"] == pytest.approx(r["call"] + r["put"])


def test_strategies_use_the_nearest_expiry_and_carry_the_disclaimer(body: dict[str, Any]) -> None:
    d = body["data"]
    assert d["multiplier"] == OPTION_MULTIPLIER and d["expiry"] and d["days_to_expiry"] >= 0
    keys = [s["key"] for s in d["strategies"]]
    assert keys[0] == "long_straddle" and "short_strangle" in keys
    ls = d["strategies"][0]
    k = ls["legs"][0]["strike"]
    cost = -ls["net_premium"]
    assert ls["breakevens"] == pytest.approx([k - cost, k + cost], abs=0.02)
    assert STRATEGY_NOTE in body["notes"]


def test_empty_store_is_404() -> None:
    r = gex_api_world(empty=True).authed().get("/api/gex/briefing")
    assert r.status_code == 404


def test_login_required() -> None:
    assert gex_api_world().client().get("/api/gex/briefing").status_code == 401


def test_no_chain_means_no_strategies_but_levels_stay() -> None:
    aw = gex_api_world()
    aw.repos.gex.chain_latest = lambda *a, **k: []  # type: ignore[method-assign]
    d = aw.authed().get("/api/gex/briefing").json()
    assert d["data"]["strategies"] == [] and d["data"]["levels"]
    assert any("옵션 포지셔닝 없음" in n for n in d["notes"])
