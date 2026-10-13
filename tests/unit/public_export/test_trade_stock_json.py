"""공개 `trade/stocks.json`·`trade/stock/<코드>.json`(묶음 MAP — docs/p6_design.md §7, metrics §28).

이 패키지는 엔진을 import 하지 않아(계약 ⑧) 랭킹 정의를 따로 적는다 — 여기서 로그인 API(엔진)와
대조한다. 관심종목 흔적 0: `origin: local`(관심종목 매핑) 종목 파일 0·`local` 글자 0·시세 칸 0.
합성 세계(p6_world — 고정 시드, 실데이터 아님).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from kbj.config.trade import load_trade
from kbj.services.public_export.manifest import check_payload, file_cap, trade_problems
from kbj.services.public_export.trade_stock_json import (
    Q_EXPORT,
    Q_MAP,
    RANK_MONTHS,
    STOCKS_NAME,
    build_trade_stock,
)
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.p6_world import CLOCK, build_world
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld
from tests.unit.api.web_fixtures_p6.map import map_api_world, memory_stock_reader


@pytest.fixture(scope="module")
def aw() -> FlashApiWorld:
    return map_api_world()


@pytest.fixture(scope="module")
def files(aw: FlashApiWorld) -> dict[str, Any]:
    out = build_trade_stock(memory_stock_reader(aw.repos), now=CLOCK)
    assert out.skipped == ()
    return {f.name: f for f in out.files}


def _data(f: Any) -> dict[str, Any]:
    return json.loads(f.render(CLOCK))["data"]


def test_빈_표는_파일_0_이유_0() -> None:
    out = build_trade_stock(memory_stock_reader(memory_repos()), now=CLOCK)
    assert out.files == () and out.skipped == ()


def test_지수_전이면_이유_한_줄() -> None:
    out = build_trade_stock(memory_stock_reader(map_api_world(index=False).repos), now=CLOCK)
    assert out.files == () and len(out.skipped) == 1


def test_SQL_은_pub_trade_만·공개_줄만() -> None:
    for q in (Q_MAP, Q_EXPORT):
        assert "pub_trade." in q.sql and "prv_" not in q.sql
        assert "origin = 'public'" in q.sql


def test_랭킹_상수는_설정과_같다() -> None:
    assert load_trade().overlay.rank_months == RANK_MONTHS


def test_공개_종목만·관심종목_흔적_0(files: dict[str, Any]) -> None:
    w = build_world()
    public = set(w.map_codes("public")) | {"994015", "992059"}
    local = set(w.map_codes("local"))
    got = {n.split("/")[-1][:-5] for n in files if n.startswith("trade/stock/")}
    assert got == public and not (got & local)
    for name, f in files.items():
        text = f.render(CLOCK)
        cap = file_cap(name)
        assert cap is not None and len(text.encode()) <= cap, name
        assert check_payload(name, text) == [], name
        assert trade_problems(name, json.loads(text)) == [], name
        assert '"local"' not in text and not any(c in text for c in local), name


def test_랭킹은_로그인_API_와_같다(files: dict[str, Any], aw: FlashApiWorld) -> None:
    rows = _data(files[STOCKS_NAME])["rows"]
    api = aw.authed().get("/api/trade/stocks").json()["data"]["rows"]
    want = [(r["code"], r["yoy_3m"], r["status"]) for r in api if r["origin"] == "public"]
    got = [(r["code"], r["yoy_3m"], r["status"]) for r in rows]
    assert [g[0] for g in got] == [x[0] for x in want]
    for g, x in zip(got, want, strict=True):
        assert (g[1] is None and x[1] is None) or g[1] == pytest.approx(x[1])
        assert g[2] == x[2]


def test_종목_파일은_로그인_API_와_같은_모양(files: dict[str, Any], aw: FlashApiWorld) -> None:
    c = aw.authed()
    for code in ("992004", "992060", "994015", "992059"):
        d = _data(files[f"trade/stock/{code}.json"])
        api = c.get(f"/api/trade/stock/{code}").json()["data"]
        for k in ("status", "reviewed", "map_version", "fx_note", "evidence", "lag_corr"):
            assert d[k] == api[k], (code, k)
        assert [p["period"] for p in d["monthly"]] == [p["period"] for p in api["monthly"]]
        assert [p["export_usd"] for p in d["quarterly"]] == [
            p["export_usd"] for p in api["quarterly"]
        ]
        assert [(x["hs"], x["region_id"], x["weight"]) for x in d["lines"]] == [
            (x["hs"], x["region_id"], x["weight"]) for x in api["lines"]
        ]
        assert "origin" not in d
