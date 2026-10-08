"""스크리너 응답 시간 — 2,700종목 × 25영업일 합성 원장(docs/p3_design.md §6.9·§8.4 [제안]).

측정(2026-10-07, 이 개발 기계): 미적중 약 13초 = 메모리 저장소 읽기 약 6.4초(`snapshots`·`days` 가
표 전체를 정렬 — 시험용 구현) + 원장 만들기 약 5.3초(`build_ledger` 의 `pick_best`·`row_rank`
190만 회) + 검산·스크리닝 0.5초. 설계 목표(미적중 3초 [제안])에 **못 미친다** — 묶음 M·E2 에 요청을
넘겼다(보고 참조). API 는 같은 데이터 버전 안에서 원장을 한 번만 만든다(`LedgerCache`) — 적중은
수십 ms. 그래서 이 시험의 미적중 상한은 크게 퇴행했을 때만 잡는 값(40초)이고, 적중 상한은 0.5초.
"""

from __future__ import annotations

import time

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.services.api.app import create_app
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.ledger_gen import generate
from tests.unit.api.api_world import (
    BASE,
    FakeClock,
    _fill_market,  # pyright: ignore[reportPrivateUsage]
    kst,
    make_settings,
)

LIMIT_S = 40.0  # 퇴행 감시용 — 목표 3초는 [확인 필요](M·E2 최적화 뒤 낮춘다)


def test_screen_2700_stocks_uncached() -> None:
    m = generate(11, days=25, n_stocks=2700, n_etfs=12)
    repos = memory_repos()
    clock = FakeClock(kst(m.last_day, 18))
    _fill_market(repos, m, clock())
    versions = {"flows": "v1"}
    app = create_app(
        make_settings(),
        redis=fakeredis.FakeRedis(),
        repos=repos,
        now=clock,
        cal=TradingCalendar.default(),
        markets=load_markets(),
        events=load_calendar_events(),
        data_versions=lambda d: versions.get(d, "none"),
        static_dir=False,
    )
    c = TestClient(app, base_url=BASE)
    from tests.unit.api.api_world import PASSWORD, USER

    assert (
        c.post(
            "/api/auth/login",
            json={"username": USER, "password": PASSWORD},
            headers={"Origin": BASE},
        ).status_code
        == 200
    )
    t0 = time.perf_counter()
    r = c.get("/api/flows/screen?mode=value&period=20&min_avg_turnover=0&limit=200")
    miss = time.perf_counter() - t0
    assert r.status_code == 200
    assert r.json()["data"]["n_total"] > 1000
    t1 = time.perf_counter()
    assert (
        c.get("/api/flows/screen?mode=value&period=20&min_avg_turnover=0&limit=200").json()
        == r.json()
    )
    hit = time.perf_counter() - t1
    print(f"screen 2700종목: 미적중 {miss:.2f}s, 적중 {hit * 1000:.1f}ms")
    assert miss < LIMIT_S, f"미적중 {miss:.2f}s ≥ {LIMIT_S}s"
    assert hit < 0.5, f"적중 {hit:.3f}s"
