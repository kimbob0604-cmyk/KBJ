"""`/api/market/credit`(readers/market_credit.py — 페이지 1 신용잔고·예탁금·펀드 자금).

합성 세계(p5_world)만."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.core.p5_rows import MarketStatRow
from kbj.core.quality import Quality
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.market import CreditPanel
from kbj.services.api.readers.market_credit import SPARK_N
from tests.fixtures.synthetic.p5_world import CLOCK, P5World, build_world
from tests.unit.api.api_world import World
from tests.unit.api.web_fixtures_p5.macro import load_macro

PATH = "/api/market/credit"


def _load(world: World, w: P5World) -> None:
    world.clock.set(CLOCK.astimezone(UTC))
    load_macro(world.repos, w)


def _lines(c: TestClient) -> dict[str, dict[str, Any]]:
    r = c.get(PATH)
    assert r.status_code == 200, r.text
    Envelope[CreditPanel].model_validate(r.json())
    return {ln["key"]: ln for ln in r.json()["data"]["lines"]}


def test_needs_session(client: TestClient) -> None:
    r = client.get(PATH)
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


def test_empty_store_is_no_data(world: World, authed: TestClient) -> None:
    world.clock.set(CLOCK.astimezone(UTC))
    r = authed.get(PATH)
    assert r.status_code == 404 and r.json()["code"] == "no_data"
    assert "market_stats.kofia" in r.json()["message"]


def test_lines_in_won_with_change_spark_and_stale(world: World, authed: TestClient) -> None:
    w = build_world()
    _load(world, w)
    body = authed.get(PATH).json()
    assert body["source"] == "DATAGO" and body["quality"] == "stale"
    by = _lines(authed)
    assert list(by) == ["credit_balance", "investor_deposit", "fund_nav"]

    # 신용잔고 = 유가+코스닥(loan_total 없음), 백만원 → 원. 하루 늦게 공표 → stale + 사유
    cb = by["credit_balance"]
    day = cb["date"]
    parts = [
        s.value for s in w.market_stats if s.stat == "credit_balance" and s.date.isoformat() == day
    ]
    assert len(parts) == 2 and all(p is not None for p in parts)
    assert cb["value"] == int(sum(p for p in parts if p is not None) * 1_000_000)
    assert cb["quality"] == "stale" and "공표 지연" in cb["note"]
    assert cb["prev_date"] < day and cb["change"] is not None
    assert len(cb["spark"]) == SPARK_N and cb["spark"][-1] == {"date": day, "value": cb["value"]}
    prev_pt = next(p for p in cb["spark"] if p["date"] == cb["prev_date"])
    assert cb["change"] == cb["value"] - prev_pt["value"]

    dep = by["investor_deposit"]
    assert dep["freq"] == "D" and dep["quality"] == "ok" and dep["note"] is None
    assert dep["value"] > 10**13  # 원 단위(55조 안팎)
    assert dep["source"] == "DATAGO:15094809/capital"

    # 펀드 순자산은 수집 전 — 값 없음(0 아님) + 사유
    fund = by["fund_nav"]
    assert fund["value"] is None and fund["change"] is None and fund["spark"] == []
    assert "#48" in fund["note"]


def test_deposit_falls_back_to_ecos_monthly(world: World, authed: TestClient) -> None:
    w = build_world()
    _load(
        world,
        replace(w, market_stats=tuple(s for s in w.market_stats if s.stat != "market_capital")),
    )
    dep = _lines(authed)["investor_deposit"]
    assert dep["freq"] == "M" and dep["source"] == "ECOS:901Y056"
    assert dep["value"] is not None and dep["value"] > 10**13  # 십억원 → 원
    assert dep["date"].endswith("-01") and "월별" in dep["note"]


def test_unknown_unit_is_blank_not_guessed(world: World, authed: TestClient) -> None:
    w = build_world()
    odd = tuple(replace(s, unit="좌") if s.stat == "market_capital" else s for s in w.market_stats)
    _load(world, replace(w, market_stats=odd))
    dep = _lines(authed)["investor_deposit"]
    # 금투협 값은 단위를 몰라 비우고 ECOS 월별로 대신 채운다
    assert dep["freq"] == "M"


def test_missing_day_is_gap_not_zero(world: World, authed: TestClient) -> None:
    w = build_world()
    days = sorted({s.date for s in w.market_stats if s.stat == "market_capital"})
    hole = days[-3]
    extra = MarketStatRow(
        "market_capital", hole, "investor_deposit", None, "백만원", "DATAGO",
        datetime.combine(hole + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
        Quality.INVALID,
    )  # fmt: skip
    stats = tuple(s for s in w.market_stats if not (s.stat == "market_capital" and s.date == hole))
    _load(world, replace(w, market_stats=(*stats, extra)))
    dep = _lines(authed)["investor_deposit"]
    gap = next(p for p in dep["spark"] if p["date"] == hole.isoformat())
    assert gap["value"] is None
    assert Decimal(dep["value"]) > 0


@pytest.mark.parametrize("hour", [9, 16])
def test_expected_day_follows_kofia_open(world: World, authed: TestClient, hour: int) -> None:
    w = build_world()
    load_macro(world.repos, w)
    world.clock.set(CLOCK.replace(hour=hour, minute=0).astimezone(UTC))
    dep = _lines(authed)["investor_deposit"]
    assert dep["quality"] == "ok"  # 예탁금은 전 거래일 값까지 있다 — 개방 전후 모두 최신
