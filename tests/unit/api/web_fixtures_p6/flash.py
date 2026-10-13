"""P6 픽스처 `web/test/fixtures/api/trade_flash/**` — 묶음 FLASH 의 응답(/api/trade/flash·상단 띠)과
공개 파일(`trade/flash.json`·`trade/ribbon.json`).

합성 세계(`tests/fixtures/synthetic/p6_world.py` — 고정 시드, 10일 잠정치 4종 10년·정정 1건·열 이름
바뀜 1건)를 메모리 저장소에 싣고(`P6World.fill`), 가짜 시계(세계의 CLOCK 2026-12-11 11:30 KST)에서
앱 응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적 — 실데이터 없음). 공개 파일은 같은 저장소를 SQL 과
같은 뜻으로 읽는 메모리 읽기(`memory_flash_reader`)로 만든다 —
`tests/unit/public_export/pub_rows.py`
의 `MemReader` 도 이 함수를 쓴다(같은 뜻의 메모리 읽기는 한 곳).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.services.api.app import create_app
from kbj.services.public_export.pub_read import Query, Reader
from kbj.services.public_export.trade_flash_json import build_trade_flash
from kbj.store.repos import MemoryRepos, memory_repos
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import CLOCK, P6World, build_world
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings

__all__ = [
    "DOMAIN",
    "FILES",
    "FlashApiWorld",
    "flash_api_world",
    "memory_flash_reader",
    "render",
]

DOMAIN: Final = "flash"

# (파일, 경로) — 로그인 API
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("flash_exp_item.json", "/api/trade/flash?kind=exp_item&months=6"),
    ("flash_exp_country.json", "/api/trade/flash?kind=exp_country&months=13"),
    ("flash_imp_item.json", "/api/trade/flash?kind=imp_item&months=3"),
    ("flash_imp_country.json", "/api/trade/flash?kind=imp_country&months=3"),
    ("market_ribbon.json", "/api/market/ribbon"),
)


def memory_flash_reader(trade: MemoryTradeRepo) -> Reader:
    """`trade_flash_json` 의 Query 이름에 SQL 과 같은 뜻으로 답하는 메모리 읽기."""

    def read(q: Query, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if q.name == "trade_ten_day_last":
            return [{"ym": trade.last_ym("pub_trade.ten_day")}]
        if q.name == "trade_ten_day":
            out: list[dict[str, Any]] = []
            for r in trade.ten_day(ym_from=str(p["ym_from"])):
                d = asdict(r)
                d["quality"] = r.quality.value
                out.append(d)
            return out
        if q.name == "trade_ten_day_columns":
            return [
                {"kind": c.kind, "col_no": c.col_no, "valid_from": c.valid_from, "name": c.name}
                for c in trade.ten_day_columns()
            ]
        raise KeyError(f"모르는 Query: {q.name}")

    return read


@dataclass
class FlashApiWorld:
    world: P6World | None  # None = 빈 저장소(404 시험)
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


def flash_api_world(*, empty: bool = False) -> FlashApiWorld:
    repos = memory_repos()
    w = None if empty else build_world()
    if w is not None:
        w.fill(repos.trade)
    return FlashApiWorld(w, repos, FakeClock(CLOCK))


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    aw = flash_api_world()
    c = aw.authed()
    out: dict[str, str] = {}
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    built = build_trade_flash(memory_flash_reader(aw.repos.trade), now=CLOCK)
    for f in built.files:
        out[f"public/{f.name}"] = _dump(json.loads(f.render(CLOCK)))
    return out
