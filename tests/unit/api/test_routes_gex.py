"""`/api/gex/*` — GEX 레벨·행사가 프로필·지표 시계열·플로(묶음 ENG — docs/p7_design.md §5·§6.2).

합성 세계(p7_world — 12월 월물 만기일, 엔진 산출 하루치)를 메모리 저장소에 싣고 앱으로 본다.
- 섀도 지표는 값을 싣지 않고(플래그 'shadow' + 섀도 검증 중 notes), off 는 싣지 않는다(§10 Q3)
- invalid 행은 빼고 notes 에 수, 봉투 품질은 실은 행 중 가장 나쁜 것
- 장 중 마지막 사이클이 오래되면 'stale' + 사유, 장 밖이면 session_open false
- 투자자별 대금은 원(investor_flow 백만원 × 10⁶), 야간 투자자별은 수집 안 함
- 빈 저장소는 404 `no_data`(작업 이름)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.core.gex.records import LevelRecord, MetricRecord, StrikeGexRecord
from kbj.services.api.readers.gex import MILLION, SHADOW_NOTE
from tests.fixtures.synthetic.p7_world import CLOCK, DAY_CYCLES, TODAY, build_world
from tests.unit.api.web_fixtures_p7.gex import CLOSED, FILES, GexApiWorld, gex_api_world

LAST = datetime.combine(TODAY, DAY_CYCLES[-1], tzinfo=CLOCK.tzinfo).astimezone(UTC)


@pytest.fixture(scope="module")
def aw() -> GexApiWorld:
    return gex_api_world()


@pytest.fixture(scope="module")
def c(aw: GexApiWorld) -> TestClient:
    return aw.authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def _levels(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {lv["name"]: lv for lv in body["data"]["levels"]}


def test_every_route_answers_with_the_envelope(c: TestClient) -> None:
    for _, path in FILES:
        body = _get(c, path)
        assert body["source"].startswith("KBJ:gex.engine"), path
        assert body["quality"] in {"ok", "stale", "estimated", "invalid"}


def test_levels_carry_the_last_cycle_and_mark_a_late_engine_stale(c: TestClient) -> None:
    body = _get(c, "/api/gex/levels?scope=all")
    d = body["data"]
    assert d["cycle_at"] == LAST.isoformat().replace("+00:00", "Z")
    assert (d["trade_date"], d["session"], d["session_open"]) == (TODAY.isoformat(), "day", True)
    lv = _levels(body)
    assert (lv["call_wall"]["value"], lv["put_wall"]["value"], lv["flip"]["value"]) == (
        360.0, 340.0, 350.5,
    )  # fmt: skip
    # 14:00 에 13:30 사이클 — 장 중 30분 지연
    assert d["stale"] is True and "13:30" in d["stale_reason"] and body["quality"] == "stale"
    # 엔진 F_ref 가 없어 선물 전광판 근월물(estimated)
    assert d["price_quality"] == "estimated" and d["price"] is not None
    assert any("선물 전광판 근월물" in n for n in body["notes"])


def test_after_the_close_the_levels_are_the_last_session_not_stale() -> None:
    body = _get(gex_api_world(at=CLOSED).authed(), "/api/gex/levels?scope=all")
    assert body["data"]["session_open"] is False and body["data"]["stale"] is False
    assert body["quality"] == "ok"


def test_shadow_levels_hide_their_values_off_and_invalid_are_left_out() -> None:
    aw = gex_api_world()
    base = dict(ts=LAST, trade_date=TODAY, session="day", scope="all")
    aw.repos.gex.write_levels(
        [
            LevelRecord.model_validate(
                {**base, "name": "vex_wall", "value": 9.0, "detail": {"x": 1}, "quality": "ok",
                 "flag": "shadow"}
            ),
            LevelRecord.model_validate(
                {**base, "name": "dark", "value": 1.0, "quality": "ok", "flag": "off"}
            ),
            LevelRecord.model_validate(
                {**base, "name": "flip", "value": 351.0, "quality": "invalid", "flag": "visible",
                 "detail": {"f_ref": 352.0}}
            ),
        ]
    )  # fmt: skip
    body = _get(aw.authed(), "/api/gex/levels?scope=all")
    lv = _levels(body)
    assert lv["vex_wall"] == {
        "name": "vex_wall", "value": None, "flag": "shadow", "quality": "ok", "reasons": [],
        "detail": {},
    }  # fmt: skip
    assert "dark" not in lv and "flip" not in lv  # off 는 싣지 않고 invalid 는 뺀다
    assert SHADOW_NOTE in body["notes"]
    assert "품질 invalid 1건 뺌(레벨)" in body["notes"]


def test_the_price_is_the_engine_f_ref_when_the_flip_has_it() -> None:
    aw = gex_api_world()
    aw.repos.gex.write_levels(
        [
            LevelRecord.model_validate(
                {"ts": LAST, "trade_date": TODAY, "session": "day", "scope": "0dte",
                 "name": "flip", "value": 350.5, "quality": "ok", "flag": "visible",
                 "detail": {"f_ref": 351.25, "multi_cross": False}}
            )
        ]
    )  # fmt: skip
    d = _get(aw.authed(), "/api/gex/levels?scope=0dte")["data"]
    assert (d["price"], d["price_quality"]) == (351.25, "ok")


def test_profile_bars_and_lines_of_the_last_cycle(aw: GexApiWorld, c: TestClient) -> None:
    body = _get(c, "/api/gex/profile?scope=all")
    d = body["data"]
    assert aw.world is not None
    want = sorted((float(r.strike), r.gex) for r in aw.world.strike_gex)
    assert sorted((b["strike"], b["gex"]) for b in d["bars"]) == want
    assert (d["flip"], d["call_wall"], d["put_wall"], d["expected_move"]) == (
        350.5, 360.0, 340.0, 4.2,
    )  # fmt: skip
    assert any(
        "범위별 시리즈 표시 없음" in n for n in body["notes"]
    )  # 합성 순GEX payload 비어 있음


def test_profile_keeps_only_the_series_of_the_scope() -> None:
    aw = gex_api_world()
    stamp = dict(ts=LAST, trade_date=TODAY, session="day")
    aw.repos.gex.write_metrics(
        [
            MetricRecord.model_validate(
                {**stamp, "metric": "net_gex", "scope": "0dte", "value": 1.0,
                 "payload": {"expiries": ["M:202612"]}, "quality": "ok", "flag": "visible"}
            )
        ]
    )  # fmt: skip
    aw.repos.gex.write_strike_gex(
        [
            StrikeGexRecord.model_validate(
                {**stamp, "mrkt_cls": "WKM", "expiry": "261202", "strike": Decimal("350.00"),
                 "gex_call": 1.0, "gex_put": -1.0, "gex": 0.0, "forward": 351.0,
                 "excluded_oi_ratio": 0.0, "quality": "ok"}
            )
        ]
    )  # fmt: skip
    c = aw.authed()
    zero = _get(c, "/api/gex/profile?scope=0dte")["data"]["bars"]
    assert {(b["mrkt_cls"], b["expiry"]) for b in zero} == {("", "202612")}
    every = _get(c, "/api/gex/profile?scope=all")["data"]["bars"]
    assert ("WKM", "261202") in {(b["mrkt_cls"], b["expiry"]) for b in every}


def test_metrics_visible_carry_values_shadow_only_their_state(c: TestClient) -> None:
    net = _get(c, "/api/gex/metrics?name=net_gex")
    assert net["data"]["flag"] == "visible" and (net["data"]["scope"], net["data"]["key"]) == (
        "all", "",
    )  # fmt: skip
    assert [p["value"] for p in net["data"]["points"]] == [1.25e11, 1.15e11, 1.05e11]
    vex = _get(c, "/api/gex/metrics?name=vex")
    assert vex["data"]["flag"] == "shadow" and len(vex["data"]["points"]) == 3
    assert all(p["value"] is None for p in vex["data"]["points"])
    assert SHADOW_NOTE in vex["notes"]


def test_metrics_window_and_unknown_names(c: TestClient) -> None:
    first = DAY_CYCLES[0]
    to = datetime.combine(TODAY, first, tzinfo=CLOCK.tzinfo) + timedelta(minutes=1)
    path = f"/api/gex/metrics?name=net_gex&to={to.isoformat().replace('+', '%2B')}"
    assert len(_get(c, path)["data"]["points"]) == 1
    for bad in ("/api/gex/metrics?name=nope", f"{path}&from=2026-12-11T00:00:00Z"):
        r = c.get(bad)
        assert r.status_code == 404 and r.json()["code"] == "no_data", bad
    assert c.get("/api/gex/metrics?name=Bad-Name").status_code == 422


def test_flow_hiro_is_shadow_and_investor_values_are_in_won(aw: GexApiWorld, c: TestClient) -> None:
    body = _get(c, "/api/gex/flow")
    d = body["data"]
    assert d["hiro_flag"] == "shadow" and d["night_investor_collected"] is False
    assert len(d["hiro"]) == 3 and all(p["value"] is None for p in d["hiro"])
    assert aw.world is not None
    last = max(r.ts for r in aw.world.investor)
    want = {
        (r.market_code, r.sector_code, r.investor): r.net_value
        for r in aw.world.investor
        if r.ts == last
    }
    got = {(r["market_code"], r["sector_code"], r["investor"]): r for r in d["investors"]}
    assert set(got) == set(want) and len(got) == 7 * 12
    for k, v in want.items():
        assert got[k]["net_value_won"] == (None if v is None else v * MILLION)
    assert any("백만원 × 10⁶" in n for n in body["notes"])


def test_empty_store_answers_404_with_the_job() -> None:
    c = gex_api_world(empty=True).authed()
    for _, path in FILES:
        r = c.get(path)
        assert r.status_code == 404, path
        body = r.json()
        assert body["code"] == "no_data" and "gex.engine" in body["message"], path


def test_the_world_is_deterministic() -> None:
    assert build_world().digest() == build_world().digest()


def test_night_cycle_without_walls_falls_back_to_the_last_day_levels() -> None:
    """야간은 옵션 호가가 얇아 벽·Flip 이 모두 invalid — 주간 마지막 사이클 레벨을 stale 로 보인다
    (2026-10-20: 야간에 기대변동폭만 보이고 나머지는 사라졌다)."""
    aw = gex_api_world()
    night = LAST + timedelta(seconds=30)
    base = dict(ts=night, trade_date=TODAY, session="night", scope="all", flag="visible")
    aw.repos.gex.write_levels(
        [
            LevelRecord.model_validate({**base, "name": n, "value": 1.0, "quality": "invalid"})
            for n in ("call_wall", "put_wall", "flip")
        ]
        + [LevelRecord.model_validate({**base, "name": "expected_move_calendar", "value": 9.0,
                                       "quality": "estimated"})]
    )  # fmt: skip
    body = _get(aw.authed(), "/api/gex/levels?scope=all")
    lv = _levels(body)
    assert body["data"]["session"] == "night"
    assert lv["expected_move_calendar"]["value"] == 9.0  # 야간 값
    assert lv["call_wall"]["quality"] == "stale" and lv["call_wall"]["value"] is not None
    assert any("주간 마지막 사이클" in n for n in body["notes"])
