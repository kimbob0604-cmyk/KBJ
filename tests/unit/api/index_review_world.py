"""지수 정기변경 API 시험 세계 — 합성 index_world 를 메모리 저장소에 싣고
index.calendar·index.predict 를
돌린 뒤의 앱(P5 2차 묶음 IDX). 웹 픽스처(다음 묶음)도 이 도우미로 만든다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.services.api.app import create_app
from kbj.services.engine import index_review as svc
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.index_world import BASE, CLOCK, build_index_world, load_into
from tests.unit.api.api_world import BASE as URL_BASE
from tests.unit.api.api_world import PASSWORD, USER, FakeClock, make_settings

__all__ = ["IndexApiWorld", "index_api_world"]


@dataclass
class IndexApiWorld:
    repos: MemoryRepos
    clock: FakeClock

    def client(self) -> TestClient:
        app = create_app(
            make_settings(), redis=fakeredis.FakeRedis(), repos=self.repos, now=self.clock,
            cal=TradingCalendar.default(), markets=load_markets(), events=load_calendar_events(),
            data_versions=None, static_dir=False,
        )  # fmt: skip
        return TestClient(app, base_url=URL_BASE)

    def authed(self) -> TestClient:
        c = self.client()
        r = c.post("/api/auth/login", json={"username": USER, "password": PASSWORD},
                   headers={"Origin": URL_BASE})  # fmt: skip
        if r.status_code != 200:
            raise RuntimeError(f"로그인 실패: HTTP {r.status_code}")
        return c


def index_api_world(
    *,
    calendar: bool = True,
    members: bool = True,
    predict: bool = True,
    user_cutoff: int | None = None,
    at: datetime = CLOCK,
) -> IndexApiWorld:
    repos = memory_repos()
    w = build_index_world()
    load_into(repos, w, members=members)
    if calendar:
        svc.sync_calendar(repos, w.config, now=CLOCK, loaded_by=svc.JOB_CALENDAR)
        if user_cutoff is not None:
            repos.index_review.set_user_cutoff("msci_korea", "2026-11", user_cutoff,
                                               loaded_by="test", now=CLOCK)  # fmt: skip
    if predict:
        svc.run_predict(repos, w.config, BASE, now=CLOCK, loaded_by=svc.JOB_PREDICT)
    return IndexApiWorld(repos, FakeClock(at))
