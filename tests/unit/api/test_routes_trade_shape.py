"""P6 라우트 모양(docs/p6_design.md §5 — 웨이브 1 FND): `/api/trade/*` 가 있고, 빈 리더면 404
`no_data`(작업 이름·예정 시각), 세션이 없으면 401, 쿼리 검증 422(입력값을 되돌리지 않는다),
OpenAPI 에 응답 모델(봉투)이 실린다.

리더 본문은 웨이브 2 묶음(FLASH·EXP·MAP)이 채운다 — 그때 '언제나 404' 단언은 그 묶음이 자기 라우트
시험(test_routes_trade_<도메인>.py)으로 옮기고 `FILLED` 에 경로를 더한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.cache import DOMAIN_JOBS
from tests.unit.api.api_world import World

# 경로 → (응답 모델 이름, 빈 리더 문구에 든 작업 이름)
P6_ROUTES: dict[str, tuple[str, str]] = {
    "/api/trade/flash?kind=exp_item&months=24": ("TradeFlash", "trade.customs_tenday"),
    "/api/trade/items?level=4&parent=85&flow=exp&from=2025-10&to=2026-09": (
        "TradeItems", "trade.customs_monthly",
    ),
    "/api/trade/items/8542/series?flow=imp": ("TradeItemSeries", "trade.customs_monthly"),
    "/api/trade/items/854232/countries?ym=2026-09": (
        "TradeItemCountries", "trade.customs_monthly",
    ),
    "/api/trade/countries?ym=2026-09": ("TradeCountries", "trade.customs_monthly"),
    "/api/trade/countries/US/items?ym=2026-09": ("TradeCountryItems", "trade.customs_monthly"),
    "/api/trade/regions?hs6=854232&sido=41": ("TradeRegions", "trade.customs_monthly"),
    "/api/trade/surges?ym=2026-09&level=hs4": ("TradeSurges", "trade.derive"),
    "/api/trade/items104?ym=2026-09": ("TradeItems104", "trade.derive"),
    "/api/trade/stocks": ("TradeStocks", "trade.stock_index"),
    "/api/trade/stock/000660": ("TradeStock", "trade.map_load"),
}  # fmt: skip
TEMPLATES = {
    "/api/trade/items/8542/series": "/api/trade/items/{hs}/series",
    "/api/trade/items/854232/countries": "/api/trade/items/{hs}/countries",
    "/api/trade/countries/US/items": "/api/trade/countries/{cc}/items",
    "/api/trade/stock/000660": "/api/trade/stock/{code}",
}
# 리더 본문을 채운 묶음의 경로(웨이브 2 — 그 묶음 시험이 빈 저장소 404 를 본다)
FILLED: frozenset[str] = frozenset(
    {"/api/trade/flash?kind=exp_item&months=24"}  # FLASH — test_routes_trade_flash.py
)


@pytest.mark.parametrize("path", sorted(set(P6_ROUTES) - FILLED))
def test_empty_reader_answers_404_no_data_with_job_and_time(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
    assert P6_ROUTES[path][1] in body["message"]
    assert r.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("path", sorted(P6_ROUTES))
def test_routes_need_a_session(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/trade/flash?kind=exp",
        "/api/trade/flash?months=121",
        "/api/trade/items?level=3",
        "/api/trade/items?parent=854",
        "/api/trade/items?from=2026-13",
        "/api/trade/items?flow=both",
        "/api/trade/items/8X42/series",
        "/api/trade/countries/usa/items",
        "/api/trade/regions?hs6=8542",
        "/api/trade/surges?level=hs8",
        "/api/trade/stock/00066!",
    ],
)
def test_bad_queries_are_422_or_404_without_echo(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code in (404, 422), r.text
    if r.status_code == 422:
        assert "usa" not in r.text and "both" not in r.text and "hs8" not in r.text


def test_openapi_lists_p6_routes_with_envelopes(world: World) -> None:
    spec: dict[str, Any] = world.client().app.openapi()  # pyright: ignore[reportAttributeAccessIssue, reportFunctionMemberAccess]
    paths = spec["paths"]
    for path, (model, _) in P6_ROUTES.items():
        bare = path.split("?", 1)[0]
        op = paths[TEMPLATES.get(bare, bare)]["get"]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith(f"Envelope_{model}_"), (bare, ref)
        assert "404" in op["responses"] and "401" in op["responses"]
    assert len([p for p in paths if p.startswith("/api/trade/")]) == len(P6_ROUTES)
    schemas = spec["components"]["schemas"]
    # 공개 등급 응답 — 시세 칸이 없다(로그인 종가 줄은 /api/stock/{code}/chart — §4.6)
    for name in ("TradeStock", "TradeStockPoint", "TradeStockRank"):
        props = set(schemas[name]["properties"])
        assert not props & {"close", "price", "chg_pct", "mktcap"}, name
    assert "fx_note" in schemas["TradeStock"]["properties"]  # 환율 미조정(Q8)


def test_trade_cache_domain_invalidates_on_the_five_jobs() -> None:
    assert set(DOMAIN_JOBS["trade"]) == {
        "trade.customs_tenday", "trade.customs_monthly", "trade.derive", "trade.map_load",
        "trade.stock_index",
    }  # fmt: skip
    assert "trade.customs_tenday" in DOMAIN_JOBS["market"]  # 상단 띠 '수출 속보' 칩
