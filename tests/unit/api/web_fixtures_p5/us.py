"""P5 픽스처 `web/test/fixtures/api/us/**` — 간밤 미국 신고가(`/api/board/us`).

합성 세계(`tests/fixtures/synthetic/p5_world.py` — 미국 유니버스 12(펀드 1·이력 짧음 1)·일봉·지수 2,
간밤 신고가 심음 `US_DAY` 11-16)를 메모리 저장소에 올리고(`load_us_world`), `us.eod` 가 하는 것처럼
엔진 산출(us_summary·us_brief — `collectors.us.build_artifacts`)을 만들어 넣은 뒤, 가짜 시계 11-17
16:30 KST 에서 API 응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적). 라우트 시험
(`tests/unit/api/test_routes_board_us.py`)도 같은 적재를 쓴다. 실데이터 없음(로그인 원천 — 합성만).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.engines.us.config import load_us
from kbj.services.api.app import create_app
from kbj.services.collectors.us import build_artifacts
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p5_world import CLOCK, US_DAY, P5World, build_world
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings

__all__ = ["DOMAIN", "FILES", "LOADED_BY", "UsApiWorld", "load_us_world", "render", "us_api_world"]

DOMAIN: Final = "us"
LOADED_BY: Final = "test.us_p5"

# (파일, 경로)
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("board_close.json", "/api/board/us"),
    ("board_high.json", "/api/board/us?basis=high"),
)


def load_us_world(repos: MemoryRepos, w: P5World) -> None:
    """유니버스·일봉(종목·지수)을 넣고 us.eod 의 엔진 산출을 만든다(시계 = CLOCK)."""
    repos.us.put_universe(US_DAY, list(w.us_universe), loaded_by=LOADED_BY, received_at=CLOCK)
    syms = [u.symbol for u in w.us_universe] + ["^SYN1", "^SYN2"]
    repos.us.upsert_daily(
        [b for s in syms for b in w.us_bars(s)], loaded_by=LOADED_BY, received_at=CLOCK
    )
    uni = [u for u in w.us_universe if not u.is_fund]
    bars = repos.us.daily([u.symbol for u in uni], US_DAY, 520)
    summary, brief, _info = build_artifacts(
        day=US_DAY, uni=uni, bars=bars, cfg=load_us(), now=CLOCK
    )
    repos.board.put_us_artifacts([summary, brief], loaded_by=LOADED_BY)


@dataclass
class UsApiWorld:
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


def us_api_world(*, empty: bool = False) -> UsApiWorld:
    repos = memory_repos()
    if empty:
        return UsApiWorld(None, repos, FakeClock(CLOCK))
    w = build_world()
    load_us_world(repos, w)
    return UsApiWorld(w, repos, FakeClock(CLOCK))


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    c = us_api_world().authed()
    out: dict[str, str] = {}
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = json.dumps(r.json(), ensure_ascii=False, indent=2) + "\n"
    return out
