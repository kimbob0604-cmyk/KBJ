"""공개 수출입 탐색 파일(묶음 EXP — docs/p6_design.md §7, metrics §26·§27·§29).

이 패키지는 엔진을 import 하지 않아(계약 ⑧) 정의를 따로 적는다 — 여기서 엔진
(`kbj.engines.trade.explore`)·로그인 API 응답과 값을 대조한다. 합성 세계(p6_world)만
쓴다(실데이터 없음).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.engines.trade.explore import item_index, yoy
from kbj.services.public_export.manifest import check_payload, file_cap, trade_problems
from kbj.services.public_export.trade_explore_json import (
    PUBLIC_MONTHS,
    QUERIES,
    build_trade_explore,
)
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import CLOCK, LAST_YM, P6World
from tests.unit.api.web_fixtures_p6.explore import explore_api_world, memory_explore_reader
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld


@pytest.fixture(scope="module")
def aw() -> FlashApiWorld:
    return explore_api_world()


@pytest.fixture(scope="module")
def world(aw: FlashApiWorld) -> P6World:
    assert aw.world is not None
    return aw.world


@pytest.fixture(scope="module")
def files(aw: FlashApiWorld) -> dict[str, Any]:
    out = build_trade_explore(memory_explore_reader(aw.repos.trade), now=CLOCK)
    assert out.skipped == ()
    return {f.name: f for f in out.files}


@pytest.fixture(scope="module")
def c(aw: FlashApiWorld) -> TestClient:
    return aw.authed()


def _data(f: Any) -> dict[str, Any]:
    return json.loads(f.render(CLOCK))["data"]


def test_빈_표는_파일_0_이유_0() -> None:
    out = build_trade_explore(memory_explore_reader(MemoryTradeRepo()), now=CLOCK)
    assert out.files == () and out.skipped == ()


def test_SQL_은_pub_trade_만() -> None:
    for q in QUERIES:
        assert "pub_trade." in q.sql and "prv_" not in q.sql, q.name


def test_파일_이름·상한·공개_검사(files: dict[str, Any], world: P6World) -> None:
    chapters = {r.hs for r in world.items if r.hs_len == 2}
    want = {"trade/hs2.json", "trade/countries.json", "trade/surge.json", "trade/items104.json",
            "trade/monthly.json"}  # fmt: skip
    want |= {f"trade/hs/{ch}.json" for ch in chapters}
    assert want <= set(files)
    assert {n for n in files if n.startswith("trade/country/")}
    assert {n for n in files if n.startswith("trade/region/")}
    for name, f in files.items():
        text = f.render(CLOCK)
        cap = file_cap(name)
        assert cap is not None and len(text.encode()) <= cap, name
        assert check_payload(name, text) == [], name
        assert trade_problems(name, json.loads(text)) == [], name
        assert f.source == "관세청(공공데이터포털)"


def test_HS2_배열은_엔진과_같다(files: dict[str, Any], world: P6World) -> None:
    d = _data(files["trade/hs2.json"])
    assert len(d["months"]) == PUBLIC_MONTHS and d["months"][-1] == LAST_YM
    assert d["hs10_login_only"] is True
    idx = item_index(world.items, "exp")
    for code in d["codes"]:
        hs = code["hs"]
        assert len(hs) == 2 and code["has_children"] and code["name"]
        for i, ym in enumerate(d["months"]):
            u = idx.usd(hs, ym)
            assert code["exp"]["usd"][i] == (None if u is None else int(u))
            want = yoy(u, idx.usd(hs, f"{int(ym[:4]) - 1}{ym[4:]}"))
            got = code["exp"]["yoy"][i]
            assert (got is None and want is None) or got == pytest.approx(want)


def test_장_파일_HS4·HS6·상위_국가(files: dict[str, Any]) -> None:
    d = _data(files["trade/hs/85.json"])
    codes = {c["hs"]: c for c in d["codes"]}
    assert set(codes) == {"8501", "8502", "850110", "850120", "850210", "850220"}
    assert all(len(h) in (4, 6) for h in codes)  # HS10 은 로그인만
    s = codes["8502"]["exp"]
    assert s["usd"][-1] == 120_000_000 and s["yoy"][-1] == pytest.approx(1.0)
    top = d["countries"]["8502"]
    assert len(top) == 10 and sum(t["share"] for t in top) == pytest.approx(1.0)
    assert all(t["yoy"] == pytest.approx(1.0) for t in top)
    assert set(d["countries"]) <= {"8501", "8502"}


def test_누락_달은_상위_행(files: dict[str, Any], world: P6World) -> None:
    d = _data(files["trade/hs/39.json"])
    code = next(c for c in d["codes"] if c["hs"] == "3901")
    i = d["months"].index("2026-06")
    whole = next(r for r in world.items if r.hs == "3901" and r.ym == "2026-06")
    assert code["exp"]["usd"][i] == int(whole.exp_usd or 0)
    miss = next(c for c in d["codes"] if c["hs"] == "390120")
    assert miss["exp"]["usd"][i] is None


def test_월별_추이_공개_파일은_로그인_API_와_같다(files: dict[str, Any], c: TestClient) -> None:
    api = c.get("/api/trade/monthly").json()["data"]
    pub = _data(files["trade/monthly.json"])
    assert pub == api


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("trade/countries.json", "/api/trade/countries"),
        ("trade/country/US.json", "/api/trade/countries/US/items?flow=exp"),
        ("trade/surge.json", "/api/trade/surges"),
        ("trade/items104.json", "/api/trade/items104"),
    ],
)
def test_로그인_API_와_같은_모양_같은_값(
    files: dict[str, Any], c: TestClient, name: str, path: str
) -> None:
    api = c.get(path).json()["data"]
    pub = _data(files[name])
    assert set(pub) == set(api)
    assert len(pub["rows"]) == len(api["rows"])
    for a, b in zip(pub["rows"], api["rows"], strict=True):
        assert set(a) == set(b)
        for k, v in a.items():
            assert (v == pytest.approx(b[k])) if isinstance(v, float) else v == b[k], (name, k)


def test_지역_파일은_API_와_같다(files: dict[str, Any], c: TestClient) -> None:
    names = sorted(n for n in files if n.startswith("trade/region/"))
    for name in names:
        hs6 = name.rsplit("/", 1)[1][:-5]
        api = c.get(f"/api/trade/regions?hs6={hs6}").json()["data"]
        pub = _data(files[name])
        assert pub["hs6"] == api["hs6"] and pub["ym"] == api["ym"]
        assert [(r["sido_cd"], r["sgg_nm"], r["exp_usd"]) for r in pub["rows"]] == [
            (r["sido_cd"], r["sgg_nm"], r["exp_usd"]) for r in api["rows"]
        ]
        for a, b in zip(pub["rows"], api["rows"], strict=True):
            assert (a["exp_yoy"] is None) == (b["exp_yoy"] is None)
            if a["exp_yoy"] is not None:
                assert a["exp_yoy"] == pytest.approx(b["exp_yoy"])
