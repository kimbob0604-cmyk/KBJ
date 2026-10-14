"""`/api/diag/*` — 종합 판정·공포탐욕·지표 카드·이력·이벤트(묶음 DIAG — docs/p7_design.md §5).

합성 세계(p7_world)로 `diag.public`·`diag.daily` 를 70거래일 돌린 메모리 저장소를 앱으로 본다.
- 공개(08:40)·로그인(16:00) 날짜가 다르면 각자 최근 날 + notes
- 카드 순서는 config/diag.yaml, 상태 점수는 위험 선호 방향(§D0), 값 없는 카드는 사유
- 판정 근거 셋·이벤트 꼬리표·D-n, 공포탐욕 대체 표시
- 빈 저장소는 404 `no_data`(작업 이름·시각), 모르는 지표 404
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.config.diag import load_diag
from tests.unit.api.web_fixtures_p7.diag import FILES, DiagApiWorld, diag_api_world

CFG = load_diag()


@pytest.fixture(scope="module")
def aw() -> DiagApiWorld:
    return diag_api_world()


@pytest.fixture(scope="module")
def c(aw: DiagApiWorld) -> TestClient:
    return aw.authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_route_answers_with_the_envelope(c: TestClient) -> None:
    for _, path in FILES:
        body = _get(c, path)
        src = body["source"]
        assert src.startswith("KBJ:diag") if "events" not in path else "KBJ:calendar" in src, path
        assert body["quality"] in {"ok", "stale", "estimated", "invalid"}


def test_summary_mixes_public_today_and_login_yesterday(c: TestClient) -> None:
    body = _get(c, "/api/diag/summary")
    d = body["data"]
    assert d["trade_date"] == "2026-12-10"
    assert "공공지표 2026-12-10 · 시장 진단 2026-12-09 기준" in body["notes"]
    keys = [x["key"] for x in d["indicators"]]
    assert keys == [k for k in CFG.indicators if k in keys]  # 설정 순서
    assert set(keys) == set(CFG.indicators)
    tiers = {x["key"]: x["tier"] for x in d["indicators"]}
    assert all(tiers[k] == CFG.indicators[k].tier for k in keys)
    by = {x["key"]: x for x in d["indicators"]}
    # 업종 코드 미정 → 값 없음 + 사유, 봉투 notes 에도
    cyc = by["cyclical_defensive"]
    assert cyc["value"] is None and cyc["state"] is None and cyc["reasons"]
    assert any("cyclical_defensive" in n for n in body["notes"])
    # 상태 점수 = 위험 선호 방향(신용스프레드 좁음 +1 / 넓음 −1)
    cs = by["credit_spread"]
    want = {"narrow": 1, "normal": 0, "wide": -1}.get(cs["state"])
    assert cs["score"] == want


def test_verdict_top_reasons_and_event_flags(c: TestClient) -> None:
    d = _get(c, "/api/diag/summary")["data"]
    v = d["verdict"]
    assert v["label"] in ("risk_on", "neutral", "risk_off", "hold")
    nonzero = [k for k, s in v["parts"].items() if s != 0]
    assert len(v["top"]) == min(3, len(nonzero))
    assert all(t[0] in "+−" for t in v["top"])
    # 12-09 판정의 주의 창(12-09·12-10)에 12-10 월물 만기가 든다
    assert "expiry" in v["event_flags"] and d["caution"] is True
    fg = d["fear_greed"]
    assert fg is not None and set(fg["components"]) == set(CFG.fear_greed.components)
    ev0 = d["events"][0]
    assert ev0 == {"date": "2026-12-10", "kind": "expiry", "title": "월물 만기", "days_left": 0,
                   "quality": "ok"}  # fmt: skip


def test_history_points_and_bands(c: TestClient) -> None:
    h = _get(c, "/api/diag/history?key=credit_spread&days=250")["data"]
    assert h["tier"] == "public" and h["unit"] == "%p" and h["bands"] == [20, 40, 60, 80]
    days = [p["trade_date"] for p in h["points"]]
    assert days == sorted(days) and days[-1] == "2026-12-10" and len(days) == 71
    b = _get(c, "/api/diag/history?key=breadth_ma20&days=30")["data"]
    assert b["tier"] == "login" and len(b["points"]) == 30
    assert b["points"][-1]["state"] is not None  # 이력 70 ≥ 60 — 구간이 있다
    fg = _get(c, "/api/diag/history?key=fear_greed&days=250")["data"]
    assert fg["unit"] == "점" and fg["bands"] == [25, 45, 56, 76]
    assert all(p["state"] is None for p in fg["points"])


def test_events_window_and_days_left(c: TestClient) -> None:
    body = _get(c, "/api/diag/events?from=2026-12-01&to=2026-12-31")
    d = body["data"]
    assert (d["start"], d["end"]) == ("2026-12-01", "2026-12-31")
    kinds = {(e["date"], e["kind"]) for e in d["events"]}
    assert ("2026-12-11", "earnings_cluster") in kinds
    past = [e for e in d["events"] if e["date"] < "2026-12-10"]
    assert all(e["days_left"] is None for e in past)


def test_unknown_key_and_empty_store_are_404() -> None:
    empty = diag_api_world(empty=True).authed()
    for path in ("/api/diag/summary", "/api/diag/history?key=credit_spread", "/api/diag/events"):
        r = empty.get(path)
        assert r.status_code == 404 and r.json()["code"] == "no_data", path
        assert "diag.public" in r.json()["message"]


def test_unknown_indicator_key_404(c: TestClient) -> None:
    r = c.get("/api/diag/history?key=nope")
    assert r.status_code == 404 and "모르는 진단 지표" in r.json()["message"]


def test_past_date_summary(c: TestClient) -> None:
    d = _get(c, "/api/diag/summary?date=2026-12-01")["data"]
    assert d["trade_date"] == "2026-12-01" and d["verdict"] is not None
