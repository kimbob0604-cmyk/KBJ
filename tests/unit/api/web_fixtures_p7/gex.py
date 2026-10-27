"""P7 `gex` 프런트 시험 픽스처 — `web/test/fixtures/api/gex/**`(p7_design §1.1·§5, 묶음 ENG).

합성 세계(`tests/fixtures/synthetic/p7_world.py` — 고정 시드, 12월 월물 만기일 2026-12-10 의 체인·
선물·투자자별 + 엔진 산출 하루치)를 메모리 저장소(`P7World.fill_gex`)에 싣고, 가짜 시계(세계의
CLOCK 14:00 KST — 장 중, 마지막 사이클 13:30 이라 '지연')에서 앱 응답을 `{상대 경로: JSON 글자}`
로 낸다(결정적 — 실데이터 없음). 장 밖(16:30 — '마지막 세션') 응답도 하나 싣는다.

선물 미결제약정 패널(`/api/gex/futures-oi` — metrics §33, ADR 0043)은 따로 합성 세계
(`tests/fixtures/synthetic/oi_world.py` — 2026-11-04 18:00, KRX 확정 11-03 까지 + 11-04
잠정)로 만든다(`OI_FILES` — GEX 엔진 응답 시험이 `FILES` 만 돈다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.services.api.app import create_app
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.oi_world import CLOCK as OI_CLOCK
from tests.fixtures.synthetic.oi_world import build_oi_world
from tests.fixtures.synthetic.p7_world import CLOCK, P7World, build_world
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings

__all__ = [
    "CLOSED",
    "DOMAIN",
    "FILES",
    "OI_FILES",
    "GexApiWorld",
    "gex_api_world",
    "oi_api_world",
    "render",
]

DOMAIN: Final = "gex"
CLOSED: Final[datetime] = CLOCK.replace(hour=16, minute=30)  # 장 밖(POST_DAY)

# (파일, 경로) — 로그인 API
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("levels_all.json", "/api/gex/levels?scope=all"),
    ("levels_0dte.json", "/api/gex/levels?scope=0dte"),
    ("profile_all.json", "/api/gex/profile?scope=all"),
    ("metrics_net_gex.json", "/api/gex/metrics?name=net_gex"),
    ("metrics_vex.json", "/api/gex/metrics?name=vex"),
    ("metrics_skew_25d.json", "/api/gex/metrics?name=skew_25d"),
    ("flow.json", "/api/gex/flow"),
    ("briefing_all.json", "/api/gex/briefing?scope=all"),
)

# (파일, 경로) — 선물 미결제약정 패널(합성 OI 세계)
OI_FILES: Final[tuple[tuple[str, str], ...]] = (("futures_oi.json", "/api/gex/futures-oi?days=60"),)


@dataclass
class GexApiWorld:
    world: P7World | None  # None = 빈 저장소(404 시험)
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


def gex_api_world(*, empty: bool = False, at: datetime = CLOCK) -> GexApiWorld:
    repos = memory_repos()
    w = None if empty else build_world()
    if w is not None:
        w.fill_gex(repos.gex)
    return GexApiWorld(w, repos, FakeClock(at))


def oi_api_world(*, empty: bool = False, at: datetime = OI_CLOCK) -> GexApiWorld:
    """합성 OI 세계(KRX 선물 일별·투자자·전광판)를 실은 저장소 — 가짜 시계 11-04 18:00."""
    repos = memory_repos()
    if not empty:
        build_oi_world().fill(repos.gex)
    return GexApiWorld(None, repos, FakeClock(at))


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    out: dict[str, str] = {}
    c = gex_api_world().authed()
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    closed = gex_api_world(at=CLOSED).authed()
    r = closed.get("/api/gex/levels?scope=all")
    if r.status_code != 200:
        raise RuntimeError(f"장 밖 levels: HTTP {r.status_code}")
    out["levels_closed.json"] = _dump(r.json())
    r = gex_api_world(empty=True).authed().get("/api/gex/levels?scope=all")
    out["levels_empty.json"] = _dump({"status": r.status_code, **r.json()})
    oi = oi_api_world().authed()
    for name, path in OI_FILES:
        r = oi.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    r = oi_api_world(empty=True).authed().get("/api/gex/futures-oi")
    out["futures_oi_empty.json"] = _dump({"status": r.status_code, **r.json()})
    return out
