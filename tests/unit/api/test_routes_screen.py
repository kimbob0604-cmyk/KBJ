"""`/api/screen/cis` 라우트 — 합성 세계(메모리 저장소).

판정 규칙은 엔진 시험(tests/unit/engines/screen)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from kbj.config.screen import load_screen
from tests.unit.api.api_world import World
from tests.unit.api.web_fixtures_p8.screen import SPIKE_CODE, WICK_CODE, add_spikes

CFG = load_screen().cis


def test_로그인_없으면_401(client: TestClient) -> None:
    assert client.get("/api/screen/cis").status_code == 401


def test_모양과_불변식(world: World, authed: TestClient) -> None:
    day = add_spikes(world)
    r = authed.get("/api/screen/cis")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["date"] == day.isoformat()
    assert d["params"]["vol_mult"] == CFG.vol_mult and d["params"]["stop_pct"] == CFG.plan.stop_pct
    assert {g["market"] for g in d["regimes"]} == {"KOSPI", "KOSDAQ"}
    rows = d["rows"]
    assert d["spikes"] >= len(rows) >= 1 and d["passed"] <= d["spikes"]
    assert all(x["vol_ratio"] >= CFG.vol_mult for x in rows)
    flags = [x["passed"] for x in rows]
    assert flags == sorted(flags, reverse=True)  # 통과 먼저
    hit = next(x for x in rows if x["code"] == SPIKE_CODE)
    assert hit["breakout"] and hit["vol_ratio"] == 6.0 and hit["name"] == "합성돌파"
    assert hit["stop"] == round(10_950 * (1 - CFG.plan.stop_pct / 100))
    assert hit["passed"] == (hit["why_not"] == [])
    wick = next(x for x in rows if x["code"] == WICK_CODE)
    assert not wick["passed"] and "윗꼬리" in wick["why_not"]


def test_데이터가_없는_날은_404(authed: TestClient) -> None:
    old = date(2000, 1, 3) - timedelta(days=1)
    r = authed.get(f"/api/screen/cis?day={old.isoformat()}")
    assert r.status_code == 404 and r.json()["code"] == "no_data"
