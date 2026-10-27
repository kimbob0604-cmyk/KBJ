"""P7 라우트 모양(docs/p7_design.md §5 — 웨이브 1 FND-B): `/api/gex/*`·`/api/diag/*` 가 있고, 빈
리더면 404 `no_data`(작업 이름·예정 시각), 세션이 없으면 401, 쿼리 검증 422(입력값을 되돌리지
않는다), OpenAPI 에 응답 모델(봉투)이 실린다.

리더 본문은 웨이브 2 묶음(ENG·DIAG)이 채운다 — 그때 '언제나 404' 단언은 그 묶음이 자기 라우트 시험
(test_routes_gex.py·test_routes_diag.py)으로 옮기고 `FILLED` 에 경로를 더한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.cache import DOMAIN_JOBS, DOMAIN_TS
from tests.unit.api.api_world import World

# 경로 → (응답 모델 이름, 빈 리더 문구에 든 작업 이름)
P7_ROUTES: dict[str, tuple[str, str]] = {
    "/api/gex/levels?scope=all": ("GexLevels", "gex.engine"),
    "/api/gex/profile?scope=0dte": ("GexProfile", "gex.engine"),
    "/api/gex/metrics?name=vex&from=2026-10-13T00:00:00Z&to=2026-10-13T07:00:00Z": (
        "GexMetricSeries", "gex.engine",
    ),
    "/api/gex/flow": ("GexFlow", "flows.market_minute"),
    "/api/gex/briefing?scope=all": ("GexBriefing", "gex.engine"),
    "/api/gex/futures-oi?days=60": ("FuturesOi", "gex.krx_derivatives"),
    "/api/diag/summary?date=2026-10-13": ("DiagSummary", "diag.daily"),
    "/api/diag/history?key=credit_spread&days=250": ("DiagHistory", "diag.public"),
    "/api/diag/events?from=2026-10-01&to=2026-10-31": ("DiagEvents", "diag.public"),
}  # fmt: skip
# 리더 본문을 채운 묶음의 경로(웨이브 2 — 그 묶음 시험이 빈 저장소 404 를 본다)
FILLED: frozenset[str] = frozenset()


@pytest.mark.parametrize("path", sorted(set(P7_ROUTES) - FILLED))
def test_empty_reader_answers_404_no_data_with_job_and_time(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
    assert P7_ROUTES[path][1] in body["message"]
    assert r.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("path", sorted(P7_ROUTES))
def test_routes_need_a_session(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/gex/levels?scope=weekly",
        "/api/gex/profile?scope=ALL",
        "/api/gex/metrics",
        "/api/gex/metrics?name=Bad-Name",
        "/api/gex/metrics?name=vex&from=yesterday",
        "/api/diag/history?key=credit_spread&days=5",
        "/api/diag/history?key=DROP",
        "/api/diag/summary?date=2026-13-01",
        "/api/diag/events?from=soon",
        "/api/gex/futures-oi?days=5",
    ],
)
def test_bad_queries_are_422_without_echo(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code == 422, r.text
    for echo in ("weekly", "Bad-Name", "yesterday", "DROP", "soon"):
        assert echo not in r.text


def test_openapi_lists_p7_routes_with_envelopes(world: World) -> None:
    spec: dict[str, Any] = world.client().app.openapi()  # pyright: ignore[reportAttributeAccessIssue, reportFunctionMemberAccess]
    paths = spec["paths"]
    for path, (model, _) in P7_ROUTES.items():
        bare = path.split("?", 1)[0]
        op = paths[bare]["get"]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith(f"Envelope_{model}_"), (bare, ref)
        assert "404" in op["responses"] and "401" in op["responses"]
    assert len([p for p in paths if p.startswith(("/api/gex/", "/api/diag/"))]) == len(P7_ROUTES)
    schemas = spec["components"]["schemas"]
    # 섀도 지표는 값 칸이 None 가능(§10 Q3), 투자자 대금은 원 단위 칸 이름
    assert "net_value_won" in schemas["GexInvestorRow"]["properties"]
    assert "flag" in schemas["GexLevel"]["properties"]
    assert {"verdict", "fear_greed", "gauge", "indicators", "events"} <= set(
        schemas["DiagSummary"]["properties"]
    )


def test_p7_cache_domains() -> None:
    assert set(DOMAIN_JOBS["diag"]) == {"diag.public", "diag.daily", "krx.vkospi"}
    # GEX 는 상시 서비스가 쓴다 — 버전에 마지막 엔진 사이클 시각이 든다
    assert "prv_gex.levels" in DOMAIN_TS["gex"] and "gex" in DOMAIN_JOBS
    # 선물 미결제약정 패널(ADR 0043) — KRX 파생 일별·장중 투자자 작업 + 전광판 최신 시각
    assert set(DOMAIN_JOBS["gex_oi"]) == {"gex.krx_derivatives", "flows.market_minute"}
    assert "prv_gex.fut_board" in DOMAIN_TS["gex_oi"]
