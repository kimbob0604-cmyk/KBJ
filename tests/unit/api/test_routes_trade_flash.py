"""`/api/trade/flash`·띠 '수출 속보' 칩(묶음 FLASH — docs/p6_design.md §5, metrics §25).

합성 세계(p6_world — 10일 잠정치 4종 10년, 정정 1건·열 이름 바뀜 1건)를 메모리 저장소에 싣고 앱으로
본다. 빈 저장소는 404 `no_data`(작업·예정 시각·실측 번호).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.engines.trade.flash import flash_chip, segments
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld, flash_api_world


@pytest.fixture(scope="module")
def aw() -> FlashApiWorld:
    return flash_api_world()


@pytest.fixture(scope="module")
def c(aw: FlashApiWorld) -> TestClient:
    return aw.authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def test_봉투_원천_품질_최신이_먼저(c: TestClient) -> None:
    body = _get(c, "/api/trade/flash?kind=exp_item&months=6")
    assert body["source"] == "관세청(공공데이터포털)" and body["quality"] == "estimated"
    d = body["data"]
    assert d["kind"] == "exp_item" and d["months"] == 6
    assert d["columns"][0] == "전체" and len(d["columns"]) == 11
    keys = [(s["ym"], s["seg"]) for s in d["segments"]]
    assert keys[0] == ("2026-12", 1) and keys == sorted(keys, reverse=True)
    assert {s["ym"] for s in d["segments"]} == {f"2026-{m:02d}" for m in range(7, 13)}
    assert all(len(s["cells"]) == 11 for s in d["segments"])
    assert any("달러" in n for n in body["notes"])


def test_정정은_발표_당시와_최신이_다르고_notes(c: TestClient) -> None:
    body = _get(c, "/api/trade/flash?kind=exp_item&months=2")
    seg = next(s for s in body["data"]["segments"] if (s["ym"], s["seg"]) == ("2026-11", 3))
    c0 = seg["cells"][0]
    assert c0["usd"] - c0["first_usd"] == 120_000_000
    assert seg["revised_at"] is not None
    assert any("발표 당시와 최신" in n for n in body["notes"])


def test_열_이름_바뀜은_YoY_없음_사유_최신_열_이름(c: TestClient) -> None:
    body = _get(c, "/api/trade/flash?kind=exp_country&months=24")
    d = body["data"]
    assert d["columns"][10] == "가상국-10"
    for s in d["segments"]:
        cell = s["cells"][10]
        if s["ym"].startswith("2025"):
            assert cell["yoy"] is None and "열 이름 바뀜" in cell["yoy_reason"]
        else:
            assert cell["yoy"] is not None


def test_엔진과_같은_값(aw: FlashApiWorld, c: TestClient) -> None:
    assert aw.world is not None
    want = segments(list(aw.world.ten_day), kind="imp_country", months=3)
    got = _get(c, "/api/trade/flash?kind=imp_country&months=3")["data"]["segments"]
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        assert (g["ym"], g["seg"]) == (w.ym, w.seg)
        for gc, wc in zip(g["cells"], w.cells, strict=True):
            assert gc["usd"] == wc.usd and gc["yoy"] == pytest.approx(wc.yoy)
            assert gc["contribution"] == pytest.approx(wc.contribution)


def test_띠_칩(aw: FlashApiWorld, c: TestClient) -> None:
    assert aw.world is not None
    chips = {x["key"]: x for x in _get(c, "/api/market/ribbon")["data"]["chips"]}
    ef = chips["export_flash"]
    want = flash_chip(list(aw.world.ten_day))
    assert want is not None and want.yoy is not None
    assert ef["phase_pending"] is None and ef["tier"] == "public"
    assert ef["value"]["value"] == pytest.approx(want.yoy)
    assert ef["value"]["quality"] == "estimated"
    assert ef["detail"]["span_label"] == "1~10일" and ef["detail"]["ym"] == "2026-12"


def test_빈_저장소는_404_no_data() -> None:
    c = flash_api_world(empty=True).authed()
    r = c.get("/api/trade/flash?kind=exp_item&months=24")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "no_data" and "trade.customs_tenday" in body["message"]
    chips = {x["key"]: x for x in c.get("/api/market/ribbon").json()["data"]["chips"]}
    assert chips["export_flash"]["value"] is None and "수집 전" in chips["export_flash"]["note"]


@pytest.mark.parametrize("q", ["kind=nope", "months=0", "months=121"])
def test_쿼리_검증(c: TestClient, q: str) -> None:
    assert c.get(f"/api/trade/flash?{q}").status_code == 422
