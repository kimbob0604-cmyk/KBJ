"""`/api/backtest*`·`/api/rules/stats`(P8 묶음 BKT — 로그인, 합성 세계 p8_world)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.config.rules_cfg import load_rules_cfg
from kbj.services.api.deps import ApiError
from kbj.services.api.models.backtest import BacktestIn
from kbj.services.api.readers._common import ReadContext
from kbj.services.api.readers.backtest import adhoc_backtest
from tests.unit.api.web_fixtures_p8.backtest import (
    ADHOC,
    E1,
    E8,
    BacktestApiWorld,
    backtest_api_world,
)
from tests.unit.services.rules_perf_seed import RATIO

Authed = tuple[BacktestApiWorld, TestClient, str]


@pytest.fixture(scope="module")
def aw() -> Authed:
    w = backtest_api_world()
    c, csrf = w.authed()
    return w, c, csrf


def test_stats_rows_and_units(aw: Authed) -> None:
    _, c, _ = aw
    r = c.get("/api/rules/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "KBJ:rules.outcome"
    assert any("비용·체결 미반영" in n for n in body["notes"])
    data = body["data"]
    assert data["horizons"] == [1, 3, 5, 10, 20]
    rows = {x["rule_id"]: x for x in data["rows"]}
    assert set(rows) == {E1, E8}
    e1 = rows[E1]
    assert e1["name"] == "RSI 과매도" and e1["version"] is None
    assert [cell["horizon"] for cell in e1["cells"]] == [1, 3, 5, 10, 20]
    c1 = e1["cells"][0]
    assert c1["n"] > 0 and 0 <= c1["win_rate"] <= 100
    assert sum(b["n"] for b in e1["dist"]) == e1["cells"][2]["n"]  # 기본 분포 = 5일


def test_stats_filters(aw: Authed) -> None:
    _, c, _ = aw
    r = c.get(f"/api/rules/stats?rule={E1}&version=1&horizon=10")
    assert r.status_code == 200
    (row,) = r.json()["data"]["rows"]
    assert row["rule_id"] == E1 and row["version"] == 1
    assert sum(b["n"] for b in row["dist"]) == row["cells"][3]["n"]
    assert c.get(f"/api/rules/stats?rule={E1}&version=9").status_code == 404


def test_runs_and_match(aw: Authed) -> None:
    _, c, _ = aw
    r = c.get("/api/backtest/runs")
    assert r.status_code == 200
    runs = r.json()["data"]["runs"]
    assert {x["rule_id"] for x in runs} == {E1, E8}
    one = next(x for x in runs if x["rule_id"] == E1)
    assert one["summary"]["trades"]["n"] >= 1 and one["mode"] == "close"
    r = c.get(f"/api/backtest/runs?rule={E8}")
    assert [x["mode"] for x in r.json()["data"]["runs"]] == ["intraday_replay"]
    m = c.get("/api/backtest/match").json()["data"]["rows"]
    assert len(m) == 2 * 20 and all(x["diffs"] == [] and x["logic_n"] == 0 for x in m)
    assert c.get("/api/backtest/match?from=2026-10-30&to=2026-10-01").status_code == 422


def test_adhoc_backtest_and_cache(aw: Authed) -> None:
    w, c, csrf = aw
    r = w.post(c, csrf, ADHOC)
    assert r.status_code == 200, r.text
    body = r.json()
    d = body["data"]
    assert d["rule_id"] == E1 and d["version"] == 1 and d["cached"] is False
    assert d["summary"]["n"] == len(d["trades"]) and d["params"]["costs"]["fee_bps"] == 1.5
    assert all(t["entry_date"] > t["signal_date"] for t in d["trades"])  # d+1 시가 체결
    assert [x["horizon"] for x in d["outcome"]] == [1, 3, 5, 10, 20]
    assert any("생존편향" in n for n in body["notes"])
    assert r.headers["cache-control"].startswith("private")
    again = w.post(c, csrf, ADHOC).json()["data"]
    assert again["cached"] is True and again["summary"] == d["summary"]


def test_adhoc_dsl_and_errors(aw: Authed) -> None:
    w, c, csrf = aw
    dsl = {"v": 1, "name": "등락 3%", "universe": {"codes": ["Q80010", "Q80017"]}, "mode": "close",
           "when": {"all": [{"ind": "change_pct", "op": ">", "value": 3}]}}  # fmt: skip
    base: dict[str, Any] = {"start": "2026-01-02", "end": "2026-10-30"}
    r = w.post(c, csrf, {**base, "dsl": dsl})
    assert r.status_code == 200 and r.json()["data"]["rule_id"] is None
    secret = "되돌리면-안되는-이름"
    bad = {**dsl, "name": secret, "when": {"all": [{"ind": "rsi", "op": "~", "value": 1}]}}
    r = w.post(c, csrf, {**base, "dsl": bad})
    assert r.status_code == 422 and secret not in r.text
    assert w.post(c, csrf, {**base, "dsl": dsl, "rule_id": E1}).status_code == 422
    assert w.post(c, csrf, base).status_code == 422
    r = w.post(c, csrf, {**base, "rule_id": "r_000000000zzz"})
    assert r.status_code == 404 and r.json()["code"] == "unknown_rule"
    r = w.post(c, csrf, {"rule_id": E1, "start": "2015-01-02", "end": "2026-10-30"})
    assert r.status_code == 413 and r.json()["code"] == "range_too_long"
    r = w.post(c, csrf, {**base, "rule_id": E1, "end": "2025-12-31", "start": "2026-01-02"})
    assert r.status_code == 422


def test_adhoc_needs_csrf(aw: Authed) -> None:
    w, c, _ = aw
    r = c.post("/api/backtest", json=ADHOC, headers={"Origin": "https://testserver"})
    assert r.status_code == 403
    assert w.repos is not None


def test_adhoc_timeout_is_503(aw: Authed) -> None:
    w, c, _ = aw
    st = c.app.state.kbj  # type: ignore[attr-defined]
    ctx: ReadContext = st.read
    ticks = iter(range(0, 10_000, 30))
    with pytest.raises(ApiError) as e:
        adhoc_backtest(ctx, BacktestIn.model_validate(ADHOC), cfg=load_rules_cfg(),
                       split_ratio=RATIO, redis=None, clock=lambda: float(next(ticks)))  # fmt: skip
    assert e.value.status == 503 and e.value.code == "timeout"
    assert w.world is not None


def test_empty_answers_404() -> None:
    c, csrf = backtest_api_world(empty=True).authed()
    for path in ("/api/rules/stats", "/api/backtest/runs", "/api/backtest/match"):
        r = c.get(path)
        assert r.status_code == 404 and r.json()["code"] == "no_data", path
    assert csrf
