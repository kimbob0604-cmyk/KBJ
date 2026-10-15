"""P5 라우트 모양(docs/p5_design.md §5 — 웨이브 1 FND): 라우트가 있고, 빈 리더면 404 `no_data`(작업
이름·
예정 시각), 세션이 없으면 401, 쿼리 검증 422, OpenAPI 에 응답 모델(봉투)이 실린다.

리더 본문은 웨이브 2 묶음(MAC·ETF·US·F2)이 채운다 — 그때 이 시험의 '언제나 404' 단언은 그 묶음이
자기 라우트 시험(test_routes_<도메인>.py)으로 옮긴다(이 파일은 FND 몫이라 남는 라우트 모양만 본다).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_world import World

# 경로 → (응답 모델 이름, 빈 리더 문구에 든 작업 이름)
P5_ROUTES: dict[str, tuple[str, str]] = {
    "/api/macro/tabs": ("MacroTabs", "macro.morning"),
    "/api/macro/series?tab=rates": ("MacroSeriesSet", "macro.morning"),
    "/api/macro/series/FRED:DGS10": ("MacroSeries", "macro.morning"),
    "/api/macro/calendar": ("EconCalendar", "macro.morning"),
    "/api/etf/map?period=1W&group=theme&lev=true": ("EtfMap", "etf.collect"),
    "/api/etf/fund/069500/holdings": ("EtfHoldings", "etf.collect"),
    "/api/etf/holders/005930": ("EtfHolders", "etf.collect"),
    "/api/etf/active-consensus?days=5": ("ActiveConsensus", "etf.collect"),
    "/api/board/us?basis=high": ("UsBoard", "us.eod"),
    "/api/flows/market-minute?market=k200_fut&venue=KRX": ("MarketMinute", "flows.market_minute"),
    "/api/program/daily?market=Q&days=60": ("ProgramDaily", "program.daily"),
    "/api/program/top?market=K&side=sell&scope=close": ("ProgramTop", "program.stock_sweep"),
}


# 리더 본문을 채운 묶음의 라우트 — '빈 리더면 404' 는 그 묶음
# 시험이 빈 저장소로 본다(이 세계(P3 합성)에는
# 데이터가 있어 200 이다). ETF: tests/unit/api/test_routes_etf_p5.py::test_빈_저장소는_404_no_data
FILLED: frozenset[str] = frozenset(p for p in P5_ROUTES if p.startswith("/api/etf/"))


@pytest.mark.parametrize("path", sorted(set(P5_ROUTES) - FILLED))
def test_empty_reader_answers_404_no_data_with_job_and_time(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
    assert P5_ROUTES[path][1] in body["message"]
    assert r.headers.get("cache-control") == "no-store"  # 오류 응답은 캐시하지 않는다


@pytest.mark.parametrize("path", sorted(P5_ROUTES))
def test_routes_need_a_session(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/macro/series",  # tab 필수
        "/api/macro/series?tab=Rates",
        "/api/etf/map?period=6M",
        "/api/etf/holders/00593O!",
        "/api/program/daily?days=61",
        "/api/program/top?side=both",
        "/api/flows/market-minute?venue=SPOT",
        "/api/board/us?basis=open",
    ],
)
def test_bad_queries_are_422_or_404_without_echo(authed: TestClient, path: str) -> None:
    r = authed.get(path)
    assert r.status_code in (404, 422), r.text
    if r.status_code == 422:
        assert "2W" not in r.text and "SPOT" not in r.text  # 입력값을 되돌리지 않는다


def test_openapi_lists_p5_routes_with_envelopes(world: World) -> None:
    spec: dict[str, Any] = world.client().app.openapi()  # pyright: ignore[reportAttributeAccessIssue, reportFunctionMemberAccess]
    paths = spec["paths"]
    for path, (model, _) in P5_ROUTES.items():
        bare = path.split("?", 1)[0]
        template = {
            "/api/macro/series/FRED:DGS10": "/api/macro/series/{series_id}",
            "/api/etf/fund/069500/holdings": "/api/etf/fund/{code}/holdings",
            "/api/etf/holders/005930": "/api/etf/holders/{code}",
        }.get(bare, bare)
        op = paths[template]["get"]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith(f"Envelope_{model}_"), (template, ref)
        assert "404" in op["responses"] and "401" in op["responses"]
    # 테마·밸류체인(페이지 9) 라우트는 ADR 0039(2026-10-15)로 생겼다 — 분류·강도·상세 셋(봉투)
    themes = sorted(p for p in paths if p.startswith("/api/themes"))
    assert themes == ["/api/themes", "/api/themes/strength", "/api/themes/{theme_id}"]
    for p in themes:
        ref = paths[p]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert "Envelope_Theme" in ref, (p, ref)
    schemas = spec["components"]["schemas"]
    # 순위 표는 범위 꼬리표가 필수(F2 §1.4 — 검산 ⑤)
    assert "scope_label" in schemas["ProgramTop"]["required"]
    # 분 선은 빈 분을 None 으로(잇지 않는다)
    line = schemas["MinuteLine"]["properties"]["values"]["items"]
    assert {"type": "null"} in line.get("anyOf", [])
