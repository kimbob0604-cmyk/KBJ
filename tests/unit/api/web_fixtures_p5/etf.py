"""P5 픽스처 `web/test/fixtures/api/etf/**` — ETF 맵·보유 탐색·동시
편입(`/api/etf/{map,fund,holders,active-consensus}` — P5 파일만).

합성 세계(`tests/fixtures/synthetic/p5_world.py` — ETF 8개 `9975xx`, 구성종목 두 기준일 11-13·11-16,
액티브 A·B 동시 편입 심음)를 메모리 저장소에 올리고(`load_etf`), `etf.collect` 가 하는 것처럼 변동을
분석해 넣은 뒤(run_date = 11-17), 가짜 시계 11-17 16:30 KST 에서 API 응답을 `{상대 경로: JSON 글자}`
로 낸다(결정적). 라우트 시험(`tests/unit/api/test_routes_etf_p5.py`)·리포트 시험도 같은 적재를 쓴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import Change, EtfMeta, Fund
from kbj.engines.etf.holdings import analyze, fund_pairs
from kbj.services.api.app import create_app
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p5_world import CLOCK, P5World, build_world
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings

__all__ = [
    "DOMAIN",
    "FILES",
    "LOADED_BY",
    "RUN_DATE",
    "EtfApiWorld",
    "etf_api_world",
    "load_etf",
    "render",
]

DOMAIN: Final = "etf"
LOADED_BY: Final = "test.etf_p5"
RUN_DATE: Final = CLOCK.date()  # etf.collect 08:00 실행일(2026-11-17)
ISSUER: Final = "합성운용"

# (파일, 경로)
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("map_type_1m.json", "/api/etf/map?period=1M&group=type"),
    ("map_theme_1w_lev.json", "/api/etf/map?period=1W&group=theme&lev=true"),
    ("map_type_3m_lev.json", "/api/etf/map?period=3M&group=type&lev=true"),
    ("fund_holdings_active_a.json", "/api/etf/fund/997560/holdings"),
    ("fund_holdings_semi.json", "/api/etf/fund/997520/holdings"),
    ("holders_robot.json", "/api/etf/holders/997190"),
    ("holders_none.json", "/api/etf/holders/997240"),
    ("active_consensus.json", "/api/etf/active-consensus?days=1"),
    ("active_consensus_5d.json", "/api/etf/active-consensus?days=5"),
)


def load_etf(repos: MemoryRepos, w: P5World, *, run_date: date = RUN_DATE) -> int:
    """세계의 ETF 메타·일별·펀드·구성종목을 넣고 변동을 분석해
    change_log 에 쓴다. 반환 = 변동 수."""
    now = CLOCK
    names = {s.code: s.name for s in w.stocks}
    repos.etf.upsert_meta(
        [
            EtfMeta(
                code=e.code, name=e.name, issuer=ISSUER, brand=None, theme=e.theme,
                etf_type=e.etf_type, leverage=e.leverage, base_index=None,
                listed_on=e.listed_on, delisted_on=None, source="krx", quality=Quality.OK,
            )
            for e in w.etfs
        ],
        loaded_by=LOADED_BY,
        now=now,
    )  # fmt: skip
    for e in w.etfs:
        repos.etf.upsert_etf_days(list(w.etf_days(e.code)), loaded_by=LOADED_BY, received_at=now)
    funds = [
        Fund(
            fund_id=e.fund_id, issuer=ISSUER, fund_key=e.code, ticker=e.code, name=e.name,
            theme=e.theme, is_active=e.active, depth="full", track=True, empty_streak=0,
            source="ETF_ISSUERS:synthetic",
        )
        for e in w.etfs
    ]  # fmt: skip
    repos.etf.upsert_funds(funds, loaded_by=LOADED_BY, now=now)
    for f in funds:
        for d in w.holding_dates:
            rows = [replace(h, name=names.get(h.code)) for h in w.holdings(f.fund_id, d)]
            if rows:
                repos.etf.put_holdings(f.fund_id, d, rows, loaded_by=LOADED_BY, received_at=now)
    changes: list[Change] = []
    for pair in fund_pairs(repos.etf.holding_dates()):
        changes += analyze(
            pair,
            repos.etf.holdings(pair.fund_id, pair.asof),
            repos.etf.holdings(pair.fund_id, pair.prev_asof),
            run_date=run_date,
        )
    repos.etf.put_changes(run_date, changes, engine_version="test", now=now)
    return len(changes)


@dataclass
class EtfApiWorld:
    world: P5World | None  # None = 빈 저장소(404 시험)
    repos: MemoryRepos
    clock: FakeClock

    def client(self) -> TestClient:
        app = create_app(
            make_settings(),
            redis=fakeredis.FakeRedis(),
            repos=self.repos,
            now=self.clock,
            cal=TradingCalendar.default(),
            markets=load_markets(),
            events=load_calendar_events(),
            data_versions=None,
            static_dir=False,
        )
        return TestClient(app, base_url=BASE)

    def authed(self) -> TestClient:
        c = self.client()
        r = c.post(
            "/api/auth/login",
            json={"username": USER, "password": PASSWORD},
            headers={"Origin": BASE},
        )
        if r.status_code != 200:
            raise RuntimeError(f"로그인 실패: HTTP {r.status_code}")
        return c


def etf_api_world() -> EtfApiWorld:
    w = build_world()
    repos = memory_repos()
    load_etf(repos, w)
    return EtfApiWorld(w, repos, FakeClock(CLOCK))


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    c = etf_api_world().authed()
    out: dict[str, str] = {}
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = json.dumps(r.json(), ensure_ascii=False, indent=2) + "\n"
    return out
