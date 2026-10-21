"""`/api/trade/stocks`·`/api/trade/stock/{code}`(묶음 MAP — docs/p6_design.md §4.6·§5, metrics §28).

합성 세계(p6_world — 고정 시드, 실데이터 아님)에 매출·지수(`trade.stock_index`)를 실어 앱으로 본다.
랭킹은 엔진(`overlay.rank_yoy`·`rank`)과 대조, 네 상태·local 우선·매핑 없음·빈 저장소 404.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.engines.trade.overlay import rank, rank_yoy
from kbj.services.api.readers.trade_map import (
    FLOW_MONTHS,
    NO_MAP,
    NO_STOCK_INDEX,
    NOT_MAPPED,
    NOTE_RAW,
)
from kbj.services.api.readers.trade_preview import NO_CONSENSUS
from kbj.services.api.readers.trade_preview import SOURCE as PREVIEW_SOURCE
from kbj.services.engine.trade_preview import DISCLAIMER
from tests.fixtures.synthetic.p6_world import build_world
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld
from tests.unit.api.web_fixtures_p6.map import EXTRA, map_api_world

SOURCE = "관세청(공공데이터포털)+DART"


@pytest.fixture(scope="module")
def aw() -> FlashApiWorld:
    return map_api_world()


@pytest.fixture(scope="module")
def c(aw: FlashApiWorld) -> TestClient:
    return aw.authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == SOURCE and any("달러" in n for n in body["notes"])
    json.dumps(body, allow_nan=False)
    return body


def test_랭킹은_엔진과_같다(c: TestClient, aw: FlashApiWorld) -> None:
    body = _get(c, "/api/trade/stocks")
    rows = body["data"]["rows"]
    w = build_world()
    assert len(rows) == len({r.code for r in w.stock_map}) + len(EXTRA)
    ex = aw.repos.trade.stock_export()
    last = max(r.period for r in ex if not r.is_quarter and r.export_usd is not None)
    vals: dict[str, float | None] = {}
    for code in {r["code"] for r in rows}:
        m = {r.period: r.export_usd for r in ex if r.code == code and not r.is_quarter}
        vals[code] = rank_yoy(m, ym=last, months=3)
    assert [r["code"] for r in rows] == [c for c, _ in rank(vals.items())]
    for r in rows:
        want = vals[r["code"]]
        assert (r["yoy_3m"] is None and want is None) or r["yoy_3m"] == pytest.approx(want)
        assert r["reviewed"] is False
    assert {r["status"] for r in rows} == {"ok", "no_trade", "no_revenue", "short_overlap"}
    assert {r["origin"] for r in rows} == {"public", "local"}  # 로그인 API 는 local 도


@pytest.mark.parametrize(
    ("code", "status"),
    [("992060", "no_trade"), ("994015", "no_revenue"), ("992059", "short_overlap")],
)
def test_심은_상태(c: TestClient, code: str, status: str) -> None:
    d = _get(c, f"/api/trade/stock/{code}")["data"]
    assert d["status"] == status
    assert set(d["lag_corr"]) == {"0", "1", "2"} and set(d["lag_corr"].values()) == {None}


def test_ok_종목_모양(c: TestClient) -> None:
    body = _get(c, "/api/trade/stock/992004")
    d = body["data"]
    assert d["status"] == "ok" and d["fx_note"] == "환율 미조정(달러)" and d["origin"] == "public"
    assert len(d["monthly"]) == 60 and d["quarterly"]
    assert all(p["revenue_yoy"] is None for p in d["monthly"])
    assert any(p["revenue_yoy"] is not None for p in d["quarterly"])
    assert any(v is not None for v in d["lag_corr"].values())
    for v in d["lag_corr"].values():
        assert v is None or (math.isfinite(v) and -1 <= v <= 1)
    assert d["lines"] and d["evidence"] and d["evidence"][0]["kind"] == "synthetic"
    region = [x for x in d["lines"] if x["region_id"]]
    assert all(len(x["hs"]) == 6 for x in region)
    assert any(x["name"] for x in d["lines"])  # hs_dim 품목명
    assert "close" not in json.dumps(body) and "price" not in json.dumps(body)


def test_local_종목도_로그인_API_에는_나온다(c: TestClient) -> None:
    w = build_world()
    code = w.map_codes("local")[0]
    d = _get(c, f"/api/trade/stock/{code}")["data"]
    assert d["origin"] == "local"


def test_매핑_없음·빈_저장소() -> None:
    c = map_api_world().authed()
    r = c.get("/api/trade/stock/994010")
    assert r.status_code == 404 and r.json() == {"code": "no_data", "message": NOT_MAPPED}
    empty = map_api_world(empty=True).authed()
    r = empty.get("/api/trade/stocks")
    assert r.status_code == 404 and r.json()["message"] == NO_STOCK_INDEX
    r = empty.get("/api/trade/stock/992004")
    assert r.status_code == 404 and r.json()["message"] == NO_MAP
    noidx = map_api_world(index=False).authed()
    for path in ("/api/trade/stocks", "/api/trade/stock/992004"):
        r = noidx.get(path)
        assert r.status_code == 404 and r.json()["message"] == NO_STOCK_INDEX


def test_코드_형식은_422(c: TestClient) -> None:
    assert c.get("/api/trade/stock/12345").status_code == 422


def test_흐름_모양(c: TestClient) -> None:
    body = _get(c, "/api/trade/stock/992004/flows")
    d = body["data"]
    assert NOTE_RAW in body["notes"] and d["code"] == "992004" and d["flows"]
    kinds = {f["kind"] for f in d["flows"]}
    assert kinds <= {"national", "sigungu"}
    for f in d["flows"]:
        assert 0 < len(f["points"]) <= FLOW_MONTHS and f["products"]
        yms = [p["ym"] for p in f["points"]]
        assert yms == sorted(yms)
        if f["kind"] == "sigungu":  # 시군구 표는 중량 없음
            assert not f["has_kg"] and all(p["kg"] is None for p in f["points"])
            assert f["regions"]  # 이름(trade_regions.yaml), 못 찾으면 통합 ID
        assert f["nature_label"] and f["last_ym"] is not None
        last = next(p for p in f["points"] if p["ym"] == f["last_ym"])
        assert last["usd"] is not None and set(last["products"]) == {x["hs"] for x in f["products"]}
    assert any(x["name"] for f in d["flows"] for x in f["products"])  # hs_dim 품목명


def test_흐름_매핑_없음은_404(c: TestClient) -> None:
    r = c.get("/api/trade/stock/994010/flows")
    assert r.status_code == 404 and r.json() == {"code": "no_data", "message": NOT_MAPPED}
    r = map_api_world(empty=True).authed().get("/api/trade/stock/992004/flows")
    assert r.status_code == 404 and r.json()["message"] == NO_MAP


def test_실적_프리뷰_모양(c: TestClient) -> None:
    """합성 세계엔 컨센서스가 없다 — 추정·이력만, 판정 없음(P10 단계 3)."""
    r = c.get("/api/trade/stock/992004/preview")
    assert r.status_code == 200, r.text
    body = r.json()
    json.dumps(body, allow_nan=False)
    d = body["data"]
    assert body["source"] == PREVIEW_SOURCE and DISCLAIMER in body["notes"]
    assert d["code"] == "992004" and d["history"] and d["history"][-1]["quarter"] == d["quarter"]
    assert d["consensus"] is None and d["verdict"] is None and d["band"] == 0.05
    assert d["reported"] or NO_CONSENSUS in body["notes"]
    assert d["confidence"] in ("높음", "보통", "낮음") and 0 < d["months_seen"] <= 3


def test_실적_프리뷰_매핑_없음은_404(c: TestClient) -> None:
    r = c.get("/api/trade/stock/994010/preview")
    assert r.status_code == 404 and r.json() == {"code": "no_data", "message": NOT_MAPPED}
    r = map_api_world(empty=True).authed().get("/api/trade/stock/992004/preview")
    assert r.status_code == 404 and r.json()["message"] == NO_MAP
