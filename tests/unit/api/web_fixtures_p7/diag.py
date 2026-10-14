"""P7 `diag` 프런트 시험 픽스처 — `web/test/fixtures/api/diag/**`(p7_design §1.1·§5·§7, 묶음 DIAG).

합성 세계(`tests/fixtures/synthetic/p7_world.py` — 고정 시드)의 진단 입력을 메모리 저장소에 싣고
`diag.public`(08:40)·`diag.daily`(16:00)를 마지막 `HISTORY_DAYS` 거래일 동안 차례로 돌려 이력을
만든 뒤(그날 2026-12-10 은 08:40 공개분만 — 시계 14:00), 앱 응답을 `{상대 경로: JSON 글자}` 로
낸다. 공개 정적 파일(`public/diag/*.json` — 공개 내보내기 `diag_json` 결과)도 같이 싣는다.

로그인 시장 지표 값은 세계 시리즈로 만든 합성 값(`world_values` — 실측 아님): 시장폭 =
breadth_ma20·new_high_ratio, 한국 대 글로벌 = 코스피 20일 % − ACWI 20일 %, 수급 = 외국인 현물 +
K200 선물 20일 합, 반도체 쏠림 = 코스피 20일 % × 0.5(합성 대용), 경기민감 대 방어 = 없음(업종
코드 [확인 필요] — 실제 설정과 같게).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.diag import DiagConfig, load_diag
from kbj.config.markets import MarketsConfig, load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.services.api.app import create_app
from kbj.services.engine import diag_pub
from kbj.services.engine.diag import MarketValues, Repos, TodayValue, run_day
from kbj.services.public_export.diag_json import build_diag
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p7_world import CLOCK, P7World, build_world
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings
from tests.unit.engines.diagnosis.diag_seed import DiagReader, seed_public

__all__ = [
    "DOMAIN",
    "FILES",
    "HISTORY_DAYS",
    "DiagApiWorld",
    "diag_api_world",
    "render",
    "world_values",
]

DOMAIN: Final = "diag"
HISTORY_DAYS: Final = 70  # min_history(60) 를 넘겨 구간이 나오게
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("summary.json", "/api/diag/summary"),
    ("history_credit_spread.json", "/api/diag/history?key=credit_spread&days=250"),
    ("history_breadth_ma20.json", "/api/diag/history?key=breadth_ma20&days=250"),
    ("history_fear_greed.json", "/api/diag/history?key=fear_greed&days=250"),
    ("events.json", "/api/diag/events"),
)
_N: Final = 20


def _series(w: P7World, key: str) -> dict[date, Decimal]:
    return {p.day: p.value for p in w.series[key] if p.value is not None}


def _tv(v: Decimal | None, why: str) -> TodayValue:
    return TodayValue(v, Quality.OK if v is not None else None, "SYNTHETIC", None if v else why)


def world_values(w: P7World) -> MarketValues:
    """세계 시리즈 → 로그인 시장 지표 값(합성). 20거래일 창이 모자라면 None + 사유."""
    breadth, nh = _series(w, "breadth_ma20"), _series(w, "new_high_ratio")
    kospi, acwi = _series(w, "kospi"), _series(w, "acwi")
    fs, ff = _series(w, "foreign_spot"), _series(w, "foreign_k200_fut")
    days = list(w.days)

    def pct(s: dict[date, Decimal], d: date) -> Decimal | None:
        i = days.index(d)
        if i < _N or d not in s or days[i - _N] not in s:
            return None
        return (s[d] / s[days[i - _N]] - 1) * 100

    def f(
        repos: Repos, day: date, markets: MarketsConfig, cfg: DiagConfig
    ) -> dict[str, TodayValue]:
        del repos, markets, cfg
        i = days.index(day)
        k, a = pct(kospi, day), pct(acwi, day)
        window = days[i - _N + 1 : i + 1] if i >= _N else []
        flow = sum((fs.get(x, Decimal(0)) + ff.get(x, Decimal(0)) for x in window), Decimal(0))
        return {
            "semis_concentration": _tv(k * Decimal("0.5") if k is not None else None, "창 부족"),
            "cyclical_defensive": TodayValue(None, None, "", "업종 지수 코드 미정 [확인 필요]"),
            "breadth_ma20": _tv(breadth.get(day), "없음"),
            "breadth_new_high": _tv(nh.get(day), "없음"),
            "kr_vs_global": _tv(k - a if k is not None and a is not None else None, "창 부족"),
            "foreign_flow": _tv(flow if window else None, "창 부족"),
        }

    return f


@dataclass
class DiagApiWorld:
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


def _public(repos: MemoryRepos, day: date, cal: TradingCalendar, cfg: DiagConfig) -> None:
    diag_pub.run(
        JobContext(
            job="diag.public", as_of=day.isoformat(), run_id=f"fx:{day}", attempt=1,
            now=datetime.combine(day, time(8, 40), tzinfo=KST),
            resources={"repos": repos, "kr": cal, "diag_config": cfg},
        )
    )  # fmt: skip


def diag_api_world(*, empty: bool = False, at: datetime = CLOCK) -> DiagApiWorld:
    repos = memory_repos()
    if empty:
        return DiagApiWorld(None, repos, FakeClock(at))
    w = build_world()
    cal, cfg, mk = TradingCalendar.default(), load_diag(), load_markets()
    seed_public(repos, w, received_at=at)
    w.fill_gex(repos.gex)
    today = at.astimezone(KST).date()
    values = world_values(w)
    for d in [x for x in w.days if x < today][-HISTORY_DAYS:]:
        _public(repos, d, cal, cfg)
        run_day(
            repos, day=d, now=datetime.combine(d, time(16, 0), tzinfo=KST), cfg=cfg,
            markets=mk, cal=cal, market_values=values,
        )  # fmt: skip
    _public(repos, today, cal, cfg)
    return DiagApiWorld(w, repos, FakeClock(at))


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    out: dict[str, str] = {}
    aw = diag_api_world()
    c = aw.authed()
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        out[name] = _dump(r.json())
    r = diag_api_world(empty=True).authed().get("/api/diag/summary")
    out["summary_empty.json"] = _dump({"status": r.status_code, **r.json()})
    now = aw.clock()
    for f in build_diag(DiagReader(aw.repos.diag_pub), now=now).files:
        out[f"public/{f.name}"] = _dump(f.envelope(now))
    return out
