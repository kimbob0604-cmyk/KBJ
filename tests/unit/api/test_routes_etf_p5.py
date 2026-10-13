"""P5 ETF 라우트(`/api/etf/{map,fund/{code}/holdings,holders/{code},active-consensus}` — 로그인).

합성 세계(p5_world)를 메모리 저장소에 올린 API(`web_fixtures_p5/etf.py`). FND 모양 시험
(`test_routes_p5_shape.py`)의 '빈 리더면 404' 단언은 데이터가 없는 저장소에서 여기로 옮겼다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.p5_world import CLOCK
from tests.unit.api.api_world import FakeClock
from tests.unit.api.web_fixtures_p5.etf import EtfApiWorld, etf_api_world


@pytest.fixture(scope="module")
def ew() -> EtfApiWorld:
    return etf_api_world()


@pytest.fixture(scope="module")
def c(ew: EtfApiWorld) -> TestClient:
    return ew.authed()


def data(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def codes(body: dict[str, Any]) -> list[str]:
    return [x["code"] for g in body["data"]["groups"] for x in g["cells"]]


def test_맵_기본은_유형_묶음_1M_레버리지_숨김(c: TestClient) -> None:
    body = data(c, "/api/etf/map")
    d = body["data"]
    assert (d["period"], d["group"], d["lev"], d["asof"]) == ("1M", "type", False, "2026-11-16")
    got = codes(body)
    assert "997540" not in got and "997550" not in got
    assert len(got) == 6
    assert body["source"] == "KRX" and body["quality"] == "ok"
    assert body["as_of"].startswith("2026-11-16T15:30")


def test_맵_레버리지_보이기와_신규상장_회색(c: TestClient) -> None:
    d = data(c, "/api/etf/map?period=3M&group=type&lev=true")["data"]
    cells = {x["code"]: x for g in d["groups"] for x in g["cells"]}
    assert {"997540", "997550"} <= set(cells)
    assert cells["997540"]["leveraged"] and cells["997550"]["leveraged"]
    new = cells["997580"]
    assert new["new_listing"] and new["ret_pct"] is None
    assert all(x["ret_pct"] is not None for k, x in cells.items() if k != "997580")
    lab = {g["key"]: g["label"] for g in d["groups"]}
    assert lab["leveraged_inverse"] == "레버리지·인버스"
    # 셀 순서 = 순자산 내림차순
    for g in d["groups"]:
        na = [x["net_asset"] for x in g["cells"]]
        assert na == sorted(na, reverse=True)


def test_맵_테마_묶음은_국내주식만(c: TestClient) -> None:
    d = data(c, "/api/etf/map?period=1W&group=theme&lev=true")["data"]
    keys = {g["key"] for g in d["groups"]}
    assert "반도체" in keys and "원전" in keys
    members = {x["code"] for g in d["groups"] for x in g["cells"]}
    assert "997540" not in members  # 레버리지는 국내주식 묶음이 아니다
    semi = next(g for g in d["groups"] if g["key"] == "반도체")
    assert {x["code"] for x in semi["cells"]} == {"997520", "997560", "997570"}


def test_구성종목_상장코드로_찾고_변동을_단다(c: TestClient) -> None:
    body = data(c, "/api/etf/fund/997560/holdings")
    d = body["data"]
    assert (d["fund_id"], d["etf_code"], d["asof"]) == ("synthetic:997560", "997560", "2026-11-16")
    ch = {r["code"]: r["change"] for r in d["rows"]}
    assert ch["997190"] == "NEW" and ch["997230"] == "NEW"
    assert ch["997010"] is None
    assert len(d["rows"]) == 6 and body["source"] == "ETF_ISSUERS"
    assert any("운용사마다" in n for n in body["notes"])


def test_담은_ETF(c: TestClient) -> None:
    d = data(c, "/api/etf/holders/997190")["data"]
    assert d["name"] == "합성로봇"
    assert [(r["etf_code"], r["is_active"]) for r in d["rows"]] == [
        ("997570", True),
        ("997560", True),
    ]  # 비중 20% > 16.67%


def test_담은_ETF_없음은_빈_목록(c: TestClient) -> None:
    body = data(c, "/api/etf/holders/997240")
    assert body["data"]["rows"] == []


def test_액티브_동시편입(c: TestClient) -> None:
    body = data(c, "/api/etf/active-consensus")
    d = body["data"]
    assert (d["days"], d["min_funds"], d["asof"]) == (1, 2, "2026-11-17")
    assert d["rows"] == [
        {
            "code": "997190",
            "name": "합성로봇",
            "funds": ["합성 액티브A", "합성 액티브B"],
            "kinds": ["NEW"],
            "n_funds": 2,
        }
    ]  # 합성부품2(997230)는 액티브 A 만 — 제외


@pytest.mark.parametrize(
    "path",
    [
        "/api/etf/map",
        "/api/etf/fund/069500/holdings",
        "/api/etf/holders/005930",
        "/api/etf/active-consensus?days=5",
    ],
)
def test_빈_저장소는_404_no_data(path: str) -> None:
    empty = EtfApiWorld(None, memory_repos(), FakeClock(CLOCK)).authed()
    r = empty.get(path)
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
    assert "etf.collect" in body["message"]


def test_모르는_펀드는_404(c: TestClient) -> None:
    r = c.get("/api/etf/fund/ZZZZZZ/holdings")
    assert r.status_code == 404 and r.json()["code"] == "no_data"


@pytest.mark.parametrize(
    "path",
    [
        "/api/etf/map?period=6M",
        "/api/etf/map?group=brand",
        "/api/etf/active-consensus?days=0",
        "/api/etf/active-consensus?days=21",
        "/api/etf/holders/00593O!",
    ],
)
def test_잘못된_쿼리(c: TestClient, path: str) -> None:
    assert c.get(path).status_code in (404, 422)


def test_세션이_없으면_401(ew: EtfApiWorld) -> None:
    assert ew.client().get("/api/etf/map").status_code == 401
