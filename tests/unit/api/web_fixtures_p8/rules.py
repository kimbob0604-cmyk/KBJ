"""P8 `rules` 프런트 시험 픽스처 — `web/test/fixtures/api/rules/**`(docs/p8_design.md §1.1·§1.2,
묶음 EVL).

합성 세계(`tests/fixtures/synthetic/p8_world.py` — 고정 시드)를 메모리 저장소에 싣고 규칙을 쓰기
모듈(`kbj.services.personal.rules` — API 와 같은 함수, 고정 id 씨앗)로 만든 뒤(E1·E8·E9 켬, E4 꺼짐,
E3 보관, E1 새 판), 실제 처리기 `rules.intraday`(가짜 KIS 슬롯 시세 + 장중 가집계)·`rules.close`
를 돌려 신호를 만든다. 앱 응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적 — 가짜 시계). 빈 저장소의
404 응답도 둔다(페이지 '.msg' 안내 시험).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.config.rules_cfg import load_rules_cfg
from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.api.app import create_app
from kbj.services.api.auth import CSRF_HEADER
from kbj.services.engine import rules as R
from kbj.services.personal.rules import create_rule, new_version, set_state
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p8_world import (  # pyright: ignore[reportPrivateUsage]
    P8World,
    _slot_ts,
    build_world,
)
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings
from tests.unit.collectors.p3_fakes import Clock
from tests.unit.services.rules_world import (
    FakeNotify,
    job_ctx,
    kis_for,
    put_live_flows,
    slot_as_of,
)

__all__ = ["DOMAIN", "RulesApiWorld", "render", "rules_api_world"]

DOMAIN: Final = "rules"
ENABLED: Final = ("E1", "E8", "E9")
INTRADAY_DAY: Final = 317  # 심은 칸(E8·E9 Q80015 슬롯 20)
SLOTS: Final = (19, 20, 21)
EMPTY_AT: Final = datetime(2026, 10, 30, 16, 0, tzinfo=KST)
BAD_RULE: Final[dict[str, Any]] = {
    "v": 1,
    "name": "검사 실패 예",
    "universe": {"watchlist": "main"},
    "mode": "intraday",
    "when": {
        "all": [
            {"ind": "new_high", "op": "==", "value": 1},
            {"ind": "rsi", "p": {"n": 999}, "op": "<", "value": 30},
        ]
    },
}


@dataclass
class RulesApiWorld:
    world: P8World | None
    repos: MemoryRepos
    clock: FakeClock
    ids: dict[str, str]

    def authed(self) -> tuple[TestClient, dict[str, str]]:
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
        c = TestClient(app, base_url=BASE)
        r = c.post(
            "/api/auth/login",
            json={"username": USER, "password": PASSWORD},
            headers={"Origin": BASE},
        )
        if r.status_code != 200:
            raise RuntimeError(f"로그인 실패: HTTP {r.status_code}")
        return c, {CSRF_HEADER: str(r.json()["csrf_token"]), "Origin": BASE}


def _seeds() -> Callable[[], bytes]:
    n = [0]

    def nxt() -> bytes:
        n[0] += 1
        return f"evl-fixture-{n[0]:04d}".encode()

    return nxt


def rules_api_world(*, empty: bool = False) -> RulesApiWorld:
    """규칙 다섯 + 장중 세 슬롯 + 마감 이틀을 돌린 세계(empty 면 빈 저장소)."""
    if empty:
        return RulesApiWorld(None, memory_repos(), FakeClock(EMPTY_AT), {})
    w = build_world()
    repos = memory_repos()
    w.fill(repos)
    cfg = load_rules_cfg()
    now = w.clock - timedelta(days=10)
    seed = _seeds()
    ids: dict[str, str] = {}
    for k in ("E1", "E3", "E4", "E8", "E9"):
        dsl = w.examples[k].model_dump(mode="json", exclude_none=True)
        res = create_rule(
            repos,
            dsl,
            enabled=k in ENABLED,
            source="example",
            now=now,
            loaded_by="fixture",
            cfg=cfg,
            seed=seed,
        )
        ids[k] = res.rule_id
    set_state(repos, ids["E3"], "archive", now=now)
    e1 = w.examples["E1"].model_dump(mode="json", exclude_none=True)
    e1["when"]["all"][0]["value"] = 32  # E1 새 판(문턱 30 → 32) — 판 이력 화면
    new_version(repos, ids["E1"], e1, enabled=True, now=now + timedelta(days=1), cfg=cfg)
    notify = FakeNotify()
    d = w.days[INTRADAY_DAY]
    for k in SLOTS:
        ts = _slot_ts(d, k)
        clock = Clock(ts + timedelta(seconds=30))
        put_live_flows(repos, w, ts)
        _, kis = kis_for(w, clock)
        res = R.intraday(
            job_ctx(
                "rules.intraday", slot_as_of(ts), now=clock(), repos=repos, kis=kis, notify=notify
            )
        )
        if res.status != "ok":
            raise RuntimeError(f"rules.intraday {k}: {res.status}")
    for day in (w.days[-2], w.days[-1]):
        at = datetime.combine(day, time(16, 0), tzinfo=KST)
        res = R.close(job_ctx("rules.close", day.isoformat(), now=at, repos=repos, notify=notify))
        if res.status != "ok":
            raise RuntimeError(f"rules.close {day}: {res.status}")
    return RulesApiWorld(w, repos, FakeClock(w.clock), ids)


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적)."""
    out: dict[str, str] = {}
    aw = rules_api_world()
    w = aw.world
    if w is None:  # pragma: no cover
        raise RuntimeError("세계가 없다")
    c, h = aw.authed()
    d0, d1 = w.days[INTRADAY_DAY].isoformat(), w.days[-1].isoformat()
    gets = (
        ("list.json", "/api/rules"),
        ("detail.json", f"/api/rules/{aw.ids['E1']}"),
        ("examples.json", "/api/rules/examples"),
        ("signals.json", f"/api/rules/signals?from={d0}&to={d1}"),
    )
    for name, path in gets:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        out[name] = _dump(r.json())
    e9 = w.examples["E9"].model_dump(mode="json", exclude_none=True)
    for name, body in (("validate_ok.json", {"dsl": e9}), ("validate_bad.json", {"dsl": BAD_RULE})):
        r = c.post("/api/rules/validate", json=body, headers=h)
        if r.status_code != 200:
            raise RuntimeError(f"validate: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    r = c.post("/api/rules", json={"dsl": BAD_RULE}, headers=h)
    out["create_422.json"] = _dump({"status": r.status_code, **r.json()})
    ec, _ = rules_api_world(empty=True).authed()
    for name, path in (
        ("list_empty.json", "/api/rules"),
        ("signals_empty.json", "/api/rules/signals"),
    ):
        r = ec.get(path)
        out[name] = _dump({"status": r.status_code, **r.json()})
    return out
