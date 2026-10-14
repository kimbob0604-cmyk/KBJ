"""P8 `backtest` 프런트 시험 픽스처 — `web/test/fixtures/api/backtest/**`(docs/p8_design.md §1.1·
§1.3, 묶음 BKT).

합성 세계(`tests/fixtures/synthetic/p8_world.py` — 고정 시드)를 메모리 저장소에 싣고 예시 규칙
E1(마감)·E8(장중)을 켠 뒤, 같은 평가기로 만든 '실시간' 신호(마지막 60 거래일 — 상태 dry)에
`rules.outcome`·`rules.backtest_nightly` 처리기를 돌려 성과·야간 실행·일치 감시를 만든다. 앱 응답을
`{상대 경로: JSON 글자}` 로 낸다(결정적 — 가짜 시계). 페이지 11 ④ 성과(`/api/rules/stats`)도 이
폴더에 둔다(BKT 소유).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.config.rules_cfg import RulesConfig
from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.api.app import create_app
from kbj.services.api.auth import CSRF_HEADER
from kbj.services.engine.rules_perf import nightly, outcome
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p8_world import P8World
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings
from tests.unit.services.rules_perf_seed import RATIO, rule_id_of, seeded

__all__ = ["ADHOC", "DOMAIN", "FILES", "BacktestApiWorld", "backtest_api_world", "render"]

DOMAIN: Final = "backtest"
E1: Final = rule_id_of("E1")
E8: Final = rule_id_of("E8")
EMPTY_AT: Final = datetime(2026, 10, 30, 16, 0, tzinfo=KST)
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("stats.json", "/api/rules/stats"),
    ("stats_e1.json", f"/api/rules/stats?rule={E1}&horizon=10"),
    ("runs.json", "/api/backtest/runs"),
    ("runs_e1.json", f"/api/backtest/runs?rule={E1}"),
    ("match.json", "/api/backtest/match"),
)
ADHOC: Final[dict[str, Any]] = {
    "rule_id": E1, "start": "2025-07-10", "end": "2026-10-30", "mode": "close", "hold": 5,
    "stop_pct": -7, "target_pct": 15,
}  # fmt: skip


@dataclass
class BacktestApiWorld:
    world: P8World | None  # None = 빈 저장소(404 시험)
    repos: MemoryRepos
    clock: FakeClock
    cfg: RulesConfig | None
    redis: fakeredis.FakeRedis

    def client(self) -> TestClient:
        app = create_app(
            make_settings(), redis=self.redis, repos=self.repos, now=self.clock,
            cal=TradingCalendar.default(), markets=load_markets(), events=load_calendar_events(),
            data_versions=None, static_dir=False,
        )  # fmt: skip
        return TestClient(app, base_url=BASE)

    def authed(self) -> tuple[TestClient, str]:
        c = self.client()
        r = c.post(
            "/api/auth/login",
            json={"username": USER, "password": PASSWORD},
            headers={"Origin": BASE},
        )
        if r.status_code != 200:
            raise RuntimeError(f"로그인 실패: HTTP {r.status_code}")
        return c, str(r.json()["csrf_token"])

    def post(self, c: TestClient, csrf: str, body: dict[str, Any]) -> Any:
        return c.post("/api/backtest", json=body, headers={CSRF_HEADER: csrf, "Origin": BASE})


def _job(
    job: str, repos: MemoryRepos, w: P8World, cfg: RulesConfig, cal: TradingCalendar
) -> JobResult:
    day = w.days[-1]
    ctx = JobContext(
        job=job, as_of=day.isoformat(), run_id=f"fx:{job}", attempt=1,
        now=datetime.combine(day, time(8, 45), tzinfo=KST),
        resources={"repos": repos, "rules_config": cfg, "kr": cal, "split_ratio": RATIO},
    )  # fmt: skip
    res = outcome(ctx) if job == "rules.outcome" else nightly(ctx)
    if res.status != "ok":
        raise RuntimeError(f"{job}: {res.status} {res.detail}")
    return res


def backtest_api_world(*, empty: bool = False) -> BacktestApiWorld:
    if empty:
        return BacktestApiWorld(
            None, memory_repos(), FakeClock(EMPTY_AT), None, fakeredis.FakeRedis()
        )
    repos, w, _, cfg, cal = seeded(("E1", "E8"), live_days=60)
    _job("rules.outcome", repos, w, cfg, cal)
    _job("rules.backtest_nightly", repos, w, cfg, cal)
    return BacktestApiWorld(w, repos, FakeClock(w.clock), cfg, fakeredis.FakeRedis())


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    out: dict[str, str] = {}
    aw = backtest_api_world()
    c, csrf = aw.authed()
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        out[name] = _dump(r.json())
    r = aw.post(c, csrf, ADHOC)
    if r.status_code != 200:
        raise RuntimeError(f"POST /api/backtest: HTTP {r.status_code} {r.text[:200]}")
    body = r.json()
    body["data"]["trades"] = body["data"]["trades"][-40:]  # 픽스처 크기(화면 표는 최근 것)
    body["data"]["signals"] = body["data"]["signals"][-40:]
    out["adhoc.json"] = _dump(body)
    ec, _ = backtest_api_world(empty=True).authed()
    for name, path in (("stats_empty.json", "/api/rules/stats"),
                       ("runs_empty.json", "/api/backtest/runs")):  # fmt: skip
        r = ec.get(path)
        out[name] = _dump({"status": r.status_code, **r.json()})
    return out
