"""`/api/rules*` 본문(묶음 EVL — docs/p8_design.md §5): 규칙 쓰기(만들기·새 판·상태 — 쓰기 모듈
하나)·422 는 위치·사유만(입력값 없음)·검증 + 오늘 원장 시험 평가(저장 없음)·예시·신호 이력.

합성 P8 세계(메모리 저장소)를 API 세계에 끼운다. 세션·CSRF·64 KiB 는 test_routes_p8_shape.py."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.auth import CSRF_HEADER
from kbj.services.engine import rules as R
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.p8_world import _slot_ts  # pyright: ignore[reportPrivateUsage]
from tests.unit.api.api_world import BASE, FakeClock, World
from tests.unit.collectors.p3_fakes import Clock
from tests.unit.services.rules_world import (
    job_ctx,
    kis_for,
    put_live_flows,
    repos_with_world,
    slot_as_of,
    world,
)

W = world()
RULE: dict[str, Any] = W.examples["E1"].model_dump(mode="json", exclude_none=True)


@pytest.fixture
def p8() -> MemoryRepos:
    return repos_with_world(W)


@pytest.fixture
def api(world: World, p8: MemoryRepos) -> tuple[TestClient, dict[str, str]]:
    c = world.client(repos=p8, now=FakeClock(W.clock))
    csrf = world.login(c)
    return c, {CSRF_HEADER: csrf, "Origin": BASE}


def test_create_list_detail_new_version_and_states(api: tuple[TestClient, dict[str, str]]) -> None:
    c, h = api
    r = c.post("/api/rules", json={"dsl": RULE, "enabled": True}, headers=h)
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    d = r.json()["data"]
    rid = d["rule_id"]
    assert d["version"] == 1 and d["enabled"] is True and d["archived"] is False
    lst = c.get("/api/rules").json()["data"]["rules"]
    assert [x["rule_id"] for x in lst] == [rid]
    assert lst[0]["mode"] == "close" and lst[0]["cooldown_min"] == 1440
    assert lst[0]["universe"] == {"watchlist": "main"} and lst[0]["signals_30d"] == 0
    changed = {**RULE, "when": {"all": [{"ind": "rsi", "op": "<", "value": 25}]}}
    r = c.put(f"/api/rules/{rid}", json={"dsl": changed, "enabled": True}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["version"] == 2
    same = c.put(f"/api/rules/{rid}", json={"dsl": changed, "enabled": True}, headers=h)
    assert same.json()["data"]["version"] == 2  # 같은 해시 — 판을 늘리지 않는다
    det = c.get(f"/api/rules/{rid}").json()["data"]
    assert [v["version"] for v in det["versions"]] == [1, 2]
    assert det["versions"][0]["dsl"]["when"]["all"][0]["value"] == 30
    assert c.post(f"/api/rules/{rid}/disable", headers=h).json()["data"]["enabled"] is False
    a = c.post(f"/api/rules/{rid}/archive", headers=h)
    assert a.status_code == 200 and a.json()["data"]["archived"] is True
    r = c.post(f"/api/rules/{rid}/enable", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "rule_state"
    r = c.put(f"/api/rules/{rid}", json={"dsl": RULE}, headers=h)
    assert r.status_code == 409


def test_invalid_rule_422_has_pointer_and_no_echo(api: tuple[TestClient, dict[str, str]]) -> None:
    c, h = api
    secret = "비밀-규칙이름-되돌리면-안됨"
    bad = {
        **RULE,
        "name": secret,
        "mode": "weekly",
        "when": {"all": [{"ind": "rsi", "op": "<", "value": 30, "p": {"n": 999}}]},
    }
    r = c.post("/api/rules", json={"dsl": bad}, headers=h)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "invalid_rule"
    assert {"pointer": "/mode", "reason": "literal_error"} in body["detail"]["issues"]
    assert secret not in r.text and "weekly" not in r.text and "999" not in r.text


def test_unknown_rule_404(api: tuple[TestClient, dict[str, str]]) -> None:
    c, h = api
    r = c.put("/api/rules/r_zzzzzzzzzzzz", json={"dsl": RULE}, headers=h)
    assert r.status_code == 404 and r.json()["code"] == "unknown_rule"
    r = c.post("/api/rules/r_zzzzzzzzzzzz/enable", headers=h)
    assert r.status_code == 404
    assert c.get("/api/rules/r_zzzzzzzzzzzz").status_code == 404


def test_validate_bad_is_200_with_issues(api: tuple[TestClient, dict[str, str]]) -> None:
    c, h = api
    r = c.post("/api/rules/validate", json={"dsl": {**RULE, "v": 2}}, headers=h)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["ok"] is False and d["issues"][0]["pointer"] == "/v" and d["trial"] == []


def test_validate_good_runs_trial_without_storing(
    api: tuple[TestClient, dict[str, str]], p8: MemoryRepos
) -> None:
    c, h = api
    r = c.post("/api/rules/validate", json={"dsl": RULE}, headers=h)
    assert r.status_code == 200, r.text
    env = r.json()
    d = env["data"]
    assert d["ok"] is True and len(d["dsl_hash"]) == 64
    assert d["normalized"]["when"]["all"][0]["p"] == {"n": 14}
    trial = {t["code"]: t for t in d["trial"]}
    assert len(trial) == 20 and trial["Q80002"]["state"] == "skipped"
    assert trial["Q80002"]["skipped_reason"] == "split_suspect"
    assert {t["trade_date"] for t in d["trial"]} == {W.days[-1].isoformat()}
    assert all(t["conds"][0]["ind"] == "rsi" for t in d["trial"] if t["state"] != "skipped")
    assert p8.rules.rules(include_archived=True) == []  # 저장하지 않는다
    assert env["as_of"].startswith(W.days[-1].isoformat())


def test_examples_list_ten(api: tuple[TestClient, dict[str, str]]) -> None:
    c, _ = api
    ex = c.get("/api/rules/examples").json()["data"]["examples"]
    assert [e["id"] for e in ex] == [f"E{i}" for i in range(1, 11)]
    assert all(len(e["dsl_hash"]) == 64 for e in ex)


def test_signals_history_after_intraday_run(
    api: tuple[TestClient, dict[str, str]], p8: MemoryRepos
) -> None:
    c, h = api
    for k in ("E8", "E9"):
        dsl = W.examples[k].model_dump(mode="json", exclude_none=True)
        assert (
            c.post("/api/rules", json={"dsl": dsl, "enabled": True}, headers=h).status_code == 201
        )
    ts = _slot_ts(W.days[317], 20)
    clock = Clock(ts + timedelta(seconds=30))
    put_live_flows(p8, W, ts)
    _, kis = kis_for(W, clock)
    R.intraday(job_ctx("rules.intraday", slot_as_of(ts), now=clock(), repos=p8, kis=kis))
    d = W.days[317].isoformat()
    r = c.get(f"/api/rules/signals?from={d}&to={d}&mode=intraday")
    assert r.status_code == 200, r.text
    env = r.json()
    rows = env["data"]["signals"]
    dry = [s for s in rows if s["status"] == "dry"]
    assert {s["code"] for s in dry} == {"Q80015"} and len(dry) == 2
    assert {s["rule_name"] for s in dry} == {"거래량 급증 양봉", "장중 급등"}
    assert dry[0]["evidence"]["partial"] is True and dry[0]["quality"] == "estimated"
    assert env["quality"] == "estimated" and any("notify_log" in n for n in env["notes"])
    assert rows == sorted(rows, key=lambda s: s["eval_ts"], reverse=True)
    close = c.get(f"/api/rules/signals?from={d}&to={d}&mode=close")
    assert close.status_code == 404 and close.json()["code"] == "no_data"
    # 목록의 30일 신호 수는 세계 시계(10-30) 기준 30일 안 신호
    lst = c.get("/api/rules").json()["data"]["rules"]
    assert sorted(x["signals_30d"] for x in lst) == [1, 1]
