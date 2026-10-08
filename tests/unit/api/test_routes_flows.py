"""`/api/flows/*` — 메모리 저장소 + 합성 원장 결과가 엔진 직접 호출과 같다(§8.4)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from kbj.config.markets import load_markets
from kbj.core.rows import Investor
from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.flows.screen import ScreenTuning, screen
from kbj.engines.flows.totals import market_investor_totals, stock_detail
from tests.fixtures.synthetic.ledger_gen import ledger_inputs
from tests.unit.api.api_world import World


def _ledger(world: World):
    led, _ = apply_checks(build_ledger(**ledger_inputs(world.market)))  # pyright: ignore[reportArgumentType]
    return led


@pytest.mark.parametrize("mode", ["value", "foreign", "inst", "both", "streak", "spike"])
@pytest.mark.parametrize("period", [1, 5, 20])
def test_screen_matches_engine(world: World, authed: TestClient, mode: str, period: int) -> None:
    r = authed.get(f"/api/flows/screen?mode={mode}&period={period}&min_avg_turnover=0&limit=50")
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    want = screen(
        _ledger(world),
        mode=mode,  # pyright: ignore[reportArgumentType]
        period=period,
        min_avg_turnover=0,
        limit=50,
        tuning=ScreenTuning.from_config(load_markets().screen),
    )
    assert got["n_total"] == want.n_total
    assert got["n_excluded"] == dict(want.n_excluded)
    assert [x["code"] for x in got["rows"]] == [x.code for x in want.rows]
    for a, b in zip(got["rows"], want.rows, strict=True):
        assert a["turnover_sum"] == b.turnover_sum
        assert a["foreign"] == b.foreign and a["inst"] == b.inst
        assert a["streak_foreign"] == b.streak_foreign
        assert isinstance(a["turnover_avg"], int | None)  # 원 단위 정수


def test_screen_default_floor_from_config(authed: TestClient) -> None:
    r = authed.get("/api/flows/screen?mode=value")
    assert r.json()["data"]["min_avg_turnover"] == load_markets().screen.min_avg_turnover_krw


def test_screen_notes_r2_unavailable(world: World) -> None:
    """기타법인을 주지 않는 원천 → 검산 ① 불가 사유가 notes 에(0 으로 바꾸지 않는다 — R2)."""
    from kbj.store.repos import memory_repos
    from tests.unit.api.api_world import _fill_market  # pyright: ignore[reportPrivateUsage]

    m = world.market
    m2 = replace(
        m,
        investors=tuple(r for r in m.investors if r.investor is not Investor.OTHER_CORP),
        market_investors=tuple(
            r for r in m.market_investors if r.investor is not Investor.OTHER_CORP
        ),
    )
    world.repos = memory_repos()
    _fill_market(world.repos, m2, world.clock())
    c = world.client()
    world.login(c)
    body = c.get("/api/flows/screen?mode=value&min_avg_turnover=0").json()
    assert any("검산 ①" in n and "불가" in n for n in body["notes"])
    inv = c.get("/api/flows/investors").json()
    assert inv["data"]["check1_residual"] is None
    assert inv["data"]["by_investor"]["other_corp"] is None
    assert any("검산 ① 불가" in n for n in inv["notes"])


def test_investors_matches_engine(world: World, authed: TestClient) -> None:
    body = authed.get("/api/flows/investors").json()
    want = market_investor_totals(world.market.market_investors, world.market.last_day)
    assert want is not None
    d = body["data"]
    assert d["by_investor"] == dict(want.by_investor)
    assert d["check1_residual"] == want.check1_residual == 0  # 합성 원장 — 검산 ① 0 차이
    assert d["check2_residual"] == want.check2_residual == 0
    assert len(d["recent"]) == 5
    assert d["recent"][-1]["date"] == world.market.last_day.isoformat()


def test_stock_detail_matches_engine(world: World, authed: TestClient) -> None:
    code = "Q00003"
    body = authed.get(f"/api/flows/stock/{code}?days=10").json()
    led = _ledger(world)
    want = stock_detail(led, code, world.market.last_day, 10)
    got = body["data"]
    assert [x["date"] for x in got["days"]] == [x.date.isoformat() for x in want.days]
    assert [x["foreign"] for x in got["days"]] == [x.foreign for x in want.days]
    assert got["cumulative"] == dict(want.cumulative)
    assert body["as_of"].startswith(world.market.last_day.isoformat() + "T15:30")


def test_stock_unknown_code_404(authed: TestClient) -> None:
    r = authed.get("/api/flows/stock/ZZZZZZ")
    assert r.status_code == 404 and r.json()["code"] == "no_data"


def test_intraday_estimated(world: World, authed: TestClient) -> None:
    assert authed.get("/api/flows/intraday").status_code == 404  # 장 마감 뒤 그날 슬롯 없음
    world.clock.set(world.slot.replace(minute=5))
    body = authed.get("/api/flows/intraday").json()
    assert body["quality"] == "estimated"
    d = body["data"]
    assert [x["rank"] for x in d["top_foreign"]] == [1, 2, 3, 4, 5]
    assert len(d["turnover_rank"]) == 5
    assert body["as_of"].endswith("10:10:00+09:00")
