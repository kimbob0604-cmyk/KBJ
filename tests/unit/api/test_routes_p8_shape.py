"""P8 라우트 모양(docs/p8_design.md §5 — 웨이브 1 FND): `/api/rules*`·`/api/backtest*`·
`/api/journal/*`·`/api/portfolio*` 가 있고, 빈 리더면 404 `no_data`, 세션이 없으면 401, 쿼리 검증
422(입력값을 되돌리지
않는다), 쓰기 라우트는 CSRF·Origin 이 없으면 403·본문 64 KiB 넘으면 413·통과하면 501(웨이브 2 전),
OpenAPI 에 응답 모델(봉투)이 실린다.

리더·쓰기 본문은 웨이브 2 묶음(EVL·BKT·JNL)이 채운다 — 그때 '언제나 404/501' 단언은 그 묶음이 자기
라우트 시험(test_routes_{rules,backtest,journal}.py)으로 옮기고 `FILLED`·`WRITTEN` 에 경로를 더한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.auth import CSRF_HEADER
from kbj.services.api.cache import DOMAIN_JOBS
from kbj.services.api.writes import MAX_BODY_BYTES
from tests.unit.api.api_world import BASE, World

RID = "r_abcdefghijkl"
# GET 경로 → 응답 모델 이름
P8_GETS: dict[str, str] = {
    "/api/rules": "RuleList",
    "/api/rules/examples": "RuleExamples",
    "/api/rules/signals?from=2026-11-02&to=2026-11-06&mode=close": "SignalList",
    "/api/rules/stats?horizon=5": "RuleStats",
    f"/api/rules/{RID}": "RuleDetail",
    "/api/backtest/runs": "BacktestRuns",
    "/api/backtest/match?from=2026-11-02&to=2026-11-06": "BacktestMatch",
    "/api/journal/analysis": "AnalysisList",
    "/api/journal/analysis/1": "AnalysisOut",
    "/api/journal/analysis/1/markdown": "AnalysisMarkdown",
    "/api/journal/analysis/prefill/005930": "AnalysisPrefill",
    "/api/journal/trades?code=005930": "TradeList",
    "/api/journal/picks": "PickList",
    "/api/portfolio": "Portfolio",
    "/api/portfolio/correlation?set=holdings&n=120": "Correlation",
}
RULE_BODY: dict[str, Any] = {"dsl": {"v": 1}}
BT_BODY: dict[str, Any] = {"rule_id": RID, "start": "2026-01-02", "end": "2026-10-30"}
TRADE_BODY: dict[str, Any] = {
    "code": "005930",
    "side": "buy",
    "trade_date": "2026-11-02",
    "price": 70000,
    "qty": 1,
}
ANALYSIS_BODY: dict[str, Any] = {"code": "005930", "analyzed_on": "2026-11-02"}
# 쓰기 (메서드, 경로, 본문)
P8_WRITES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/rules", RULE_BODY),
    ("POST", "/api/rules/validate", RULE_BODY),
    ("PUT", f"/api/rules/{RID}", RULE_BODY),
    ("POST", f"/api/rules/{RID}/enable", None),
    ("POST", f"/api/rules/{RID}/disable", None),
    ("POST", f"/api/rules/{RID}/archive", None),
    ("POST", "/api/backtest", BT_BODY),
    ("POST", "/api/journal/analysis", ANALYSIS_BODY),
    ("PATCH", "/api/journal/analysis/1", ANALYSIS_BODY),
    ("DELETE", "/api/journal/analysis/1", None),
    ("POST", "/api/journal/trades", TRADE_BODY),
    ("DELETE", "/api/journal/trades/1", None),
]
FILLED: frozenset[str] = frozenset(
    {"/api/rules/examples", f"/api/rules/{RID}"}  # EVL 본문(예시 200·없는 규칙 — test_routes_rules)
    # JNL 본문(빈 목록 200 — test_routes_journal)
    | {"/api/journal/analysis", "/api/journal/trades?code=005930", "/api/portfolio"}
)
WRITTEN: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/backtest"),  # BKT 본문
        # EVL 본문(test_routes_rules.py — 422·404·200)
        ("POST", "/api/rules"),
        ("POST", "/api/rules/validate"),
        ("PUT", f"/api/rules/{RID}"),
        ("POST", f"/api/rules/{RID}/enable"),
        ("POST", f"/api/rules/{RID}/disable"),
        ("POST", f"/api/rules/{RID}/archive"),
        # JNL 본문(test_routes_journal.py — 201·404·422)
        ("POST", "/api/journal/analysis"),
        ("PATCH", "/api/journal/analysis/1"),
        ("DELETE", "/api/journal/analysis/1"),
        ("POST", "/api/journal/trades"),
        ("DELETE", "/api/journal/trades/1"),
    }
)


def _write(
    c: TestClient, method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]
) -> Any:
    return c.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("path", sorted(set(P8_GETS) - FILLED))
def test_empty_reader_answers_404_no_data(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
    assert r.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("path", sorted(P8_GETS))
def test_routes_need_a_session(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), P8_WRITES)
def test_writes_need_session_then_csrf(
    world: World, client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    assert _write(client, method, path, body, {}).status_code == 401
    csrf = world.login(client)
    r = _write(client, method, path, body, {"Origin": BASE})  # CSRF 없음
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    r = _write(client, method, path, body, {CSRF_HEADER: csrf, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "origin_mismatch"
    if (method, path) not in WRITTEN:
        r = _write(client, method, path, body, {CSRF_HEADER: csrf, "Origin": BASE})
        assert r.status_code == 501, r.text
        assert r.json()["code"] == "not_implemented"


def test_write_body_limit_413(world: World, client: TestClient) -> None:
    csrf = world.login(client)
    big = {"dsl": {"v": 1, "name": "x" * (MAX_BODY_BYTES + 10)}}
    r = client.post("/api/rules", json=big, headers={CSRF_HEADER: csrf, "Origin": BASE})
    assert r.status_code == 413 and r.json()["code"] == "body_too_large"
    assert "xxxx" not in r.text


def test_write_validation_422_without_echo(world: World, client: TestClient) -> None:
    csrf = world.login(client)
    secret = "비밀메모-되돌리면-안됨"
    r = client.post(
        "/api/journal/trades",
        json={**TRADE_BODY, "qty": -1, "memo": secret, "oops": secret},
        headers={CSRF_HEADER: csrf, "Origin": BASE},
    )
    assert r.status_code == 422, r.text
    assert secret not in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/rules/signals?mode=weekly",
        "/api/rules/signals?rule=DROP",
        "/api/rules/R_BAD",
        "/api/rules/stats?horizon=0",
        "/api/backtest/runs?rule=nope",
        "/api/journal/analysis/0",
        "/api/journal/analysis/prefill/abc",
        "/api/journal/trades?code=evil",
        "/api/portfolio/correlation?set=everything",
        "/api/portfolio/correlation?n=7",
    ],
)
def test_bad_queries_are_422_or_404_without_echo(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code in (404, 422), r.text
    if r.status_code == 404:
        assert r.json()["code"] != "no_data"  # 경로 형식이 틀리면 리더까지 가지 않는다
    for echo in ("weekly", "DROP", "R_BAD", "nope", "evil", "everything"):
        assert echo not in r.text


def test_openapi_lists_p8_routes_with_envelopes(world: World) -> None:
    spec: dict[str, Any] = world.client().app.openapi()  # pyright: ignore[reportAttributeAccessIssue, reportFunctionMemberAccess]
    paths = spec["paths"]
    for path, model in P8_GETS.items():
        bare = path.split("?", 1)[0].replace(RID, "{rule_id}")
        bare = bare.replace("/analysis/1", "/analysis/{analysis_id}").replace("/005930", "/{code}")
        op = paths[bare]["get"]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith(f"Envelope_{model}_"), (bare, ref)
        assert "404" in op["responses"] and "401" in op["responses"]
    for method, path, _ in P8_WRITES:
        bare = path.replace(RID, "{rule_id}").replace("/analysis/1", "/analysis/{analysis_id}")
        bare = bare.replace("/trades/1", "/trades/{trade_id}")
        for action in ("enable", "disable", "archive"):
            bare = bare.replace(f"/{action}", "/{action}")
        op = paths[bare][method.lower()]
        assert {"403", "413", "501"} <= set(op["responses"]), (method, bare)
    p8 = [
        p
        for p in paths
        if p.startswith(("/api/rules", "/api/backtest", "/api/journal", "/api/portfolio"))
    ]
    assert len(p8) == 19, p8  # 규칙 7 · 백테스트 3 · 저널 7 · 포트폴리오 2


def test_p8_cache_domains() -> None:
    assert set(DOMAIN_JOBS["rules"]) == {
        "rules.intraday",
        "rules.close",
        "rules.outcome",
        "rules.backtest_nightly",
    }
    assert set(DOMAIN_JOBS["backtest"]) == {"rules.outcome", "rules.backtest_nightly"}
    assert "journal" not in DOMAIN_JOBS and "portfolio" not in DOMAIN_JOBS
