"""P5 픽스처 `web/test/fixtures/api/program/**` — 장중 투자자 수급·프로그램매매
(`/api/flows/market-minute`·`/api/program/{daily,top}` — P5 묶음 F2).

합성 세계(`tests/fixtures/synthetic/p5_world.py` — 1분 누적 5조합 × 12구분 × 391분(백만원), 프로그램
일별 60일·장중 391분·종목 장중 훑기 38슬롯·마감 훑기)를 메모리 저장소에 올리고(`load_f2_world`),
가짜 시계에서 API 응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적). 라우트 시험
(`tests/unit/api/test_routes_program.py`)도 같은 적재를 쓴다. 실데이터 없음(KIS — 로그인 원천,
합성만).

- 현물 조합(코스피 KSP·0001·코스닥 KSQ·1001)은 운영 설정에서 켜져 있다. 합성 세계의 1분 행은
  자리표시 코드(`SYNK`·`SYN0`)라 설정 사본(`f2_markets`)에서 **코스피를** 그 코드로 바꾸고,
  **코스닥은 꺼서** '확인 전' 화면을 남긴다(시험 전용 — 운영 값 아님). 코스피는 10:00~10:04 다섯
  분을 빼서 넣는다(빈 분 — 화면이 점선으로 잇는다).
- 겹칠 지수: 코스피(0001)·코스피200(2001) 장중 10분 값을 합성으로 넣는다(`INDEX_BASE` — 실데이터
  아님).
- 시계: 장중 13:00(오늘 장중 잠정), 마감 뒤 16:30(픽스처 기본), 마감 훑기 뒤 18:40, 장 시작 전
  08:30, 휴장일(토) 10:00. 파일마다 그 시각까지 있었을 행만 넣은 세계다.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Any, Final

import fakeredis
from fastapi.testclient import TestClient

from kbj.config.markets import MarketsConfig, load_calendar_events, load_markets, parse_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.p5_rows import InvestorMinute
from kbj.core.quality import Quality
from kbj.core.rows import IndexQuote, UniverseRow
from kbj.core.time import KST
from kbj.services.api.app import create_app
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p5_world import CLOCK, TODAY, P5World, build_world
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings

__all__ = [
    "DOMAIN",
    "DROP_KOSPI",
    "FILES",
    "LOADED_BY",
    "ProgramApiWorld",
    "f2_markets",
    "load_f2_world",
    "program_api_world",
    "render",
]

DOMAIN: Final = "program"
LOADED_BY: Final = "test.program_p5"
# 코스피 조합에서 뺀 분(끊긴 선 — 수집 실패 흉내)
DROP_KOSPI: Final[tuple[time, ...]] = tuple(time(10, m) for m in range(5))
# 늦게 받은 분(슬롯 + 45초 넘음 — estimated). 나머지 분은 제때(ok)
LATE: Final[frozenset[time]] = frozenset({time(11, 0), time(11, 1)})
INTRADAY: Final = datetime.combine(TODAY, time(13, 0), tzinfo=KST)
BEFORE_OPEN: Final = datetime.combine(TODAY, time(8, 30), tzinfo=KST)
SWEPT: Final = datetime.combine(TODAY, time(18, 40), tzinfo=KST)  # 마감 훑기(18:10) 뒤
HOLIDAY: Final = datetime(2026, 11, 21, 10, 0, tzinfo=KST)  # 토요일
# 합성 장중 지수(10분 슬롯) — (코드, 이름, 전일 종가, 슬롯마다 더하는 값)
INDEX_BASE: Final[tuple[tuple[str, str, float, float], ...]] = (
    ("0001", "코스피", 2600.0, 0.75),
    ("2001", "코스피200", 350.0, 0.11),
)

# (파일, 경로, 시계)
FILES: Final[tuple[tuple[str, str, datetime], ...]] = (
    ("minute_k200_fut.json", "/api/flows/market-minute?market=k200_fut", INTRADAY),
    ("minute_kospi.json", "/api/flows/market-minute?market=kospi", INTRADAY),
    ("minute_kospi_close.json", "/api/flows/market-minute?market=kospi", CLOCK),
    ("minute_kosdaq_off.json", "/api/flows/market-minute?market=kosdaq", INTRADAY),
    ("minute_before_open.json", "/api/flows/market-minute?market=k200_fut", BEFORE_OPEN),
    ("minute_holiday.json", "/api/flows/market-minute?market=k200_fut", HOLIDAY),
    ("daily_K.json", "/api/program/daily?market=K&days=60", CLOCK),
    ("daily_Q.json", "/api/program/daily?market=Q&days=60", CLOCK),
    ("top_K_buy_intraday.json", "/api/program/top?market=K&side=buy&scope=intraday", INTRADAY),
    ("top_K_sell_intraday.json", "/api/program/top?market=K&side=sell&scope=intraday", INTRADAY),
    ("top_K_buy_close.json", "/api/program/top?market=K&side=buy&scope=close", SWEPT),
    ("top_Q_sell_close.json", "/api/program/top?market=Q&side=sell&scope=close", SWEPT),
)


def f2_markets() -> MarketsConfig:
    """운영 설정 사본 — 코스피 조합만 합성 자리표시 코드로 켠다(시험 전용, 운영 값 아님)."""
    data: dict[str, Any] = load_markets().model_dump(mode="json")
    for p in data["flows"]["minute"]["pairs"]:
        if p["id"] == "kospi":
            p.update(market="SYNK", sector="SYN0", enabled=True, note="합성 자리표시")
        if p["id"] == "kosdaq":
            p.update(enabled=False, note="시험 — '확인 전' 화면")
    return parse_markets(data)


def _index_quotes(upto: datetime | None) -> list[IndexQuote]:
    """장중 10분 지수(09:00~15:30 슬롯, 그 시각까지) — 슬롯마다 조금씩 오르내리는 합성 값."""
    out: list[IndexQuote] = []
    t = datetime.combine(TODAY, time(9, 0), tzinfo=KST)
    end = datetime.combine(TODAY, time(15, 30), tzinfo=KST)
    k = 0
    while t <= end and (upto is None or t <= upto):
        for code, name, prev, step in INDEX_BASE:
            v = round(prev + step * ((k % 7) - 2) + step * k / 3, 2)
            out.append(
                IndexQuote(
                    code, t, name, v, round((v / prev - 1) * 100, 2), None, None, "kis",
                    Quality.ESTIMATED,
                )
            )  # fmt: skip
        t += timedelta(minutes=10)
        k += 1
    return out


def _universe(w: P5World) -> list[UniverseRow]:
    return [
        UniverseRow(s.code, TODAY, s.name, s.market, "common", None, "krx", Quality.OK)
        for s in w.stocks
    ]


def _minute_rows(w: P5World, pid: str, upto: datetime | None) -> list[InvestorMinute]:
    drop = DROP_KOSPI if pid == "kospi" else ()
    out: list[InvestorMinute] = []
    for m in w.minutes(pid, drop=drop):
        if upto is not None and m.ts > upto:
            continue
        # 제때 받은 분은 ok, LATE 분만 늦게 받은 값(estimated — 슬롯 + 45초 넘음)
        out.append(replace(m, quality=Quality.ESTIMATED if m.ts.time() in LATE else Quality.OK))
    return out


def load_f2_world(repos: MemoryRepos, w: P5World, *, upto: datetime | None = None) -> None:
    """그 시각(upto)까지 있었을 행을 넣는다 — 1분 누적(코스피는 DROP_KOSPI 분을 뺀다)·프로그램
    일별(오늘 것은 마감 뒤에만)·장중·투자자별·종목 장중(슬롯 ≤ upto)·마감 훑기(마감 뒤에만)·
    유니버스."""

    def seen(ts: datetime | None) -> bool:
        return upto is None or ts is None or ts <= upto

    after_close = upto is None or upto >= datetime.combine(TODAY, time(18, 10), tzinfo=KST)
    for pid in ("kospi", "kosdaq", "k200_fut", "k200_call", "k200_put"):
        repos.flows.put_investor_minutes(_minute_rows(w, pid, upto), loaded_by=LOADED_BY)
    days = [
        r
        for r in w.program_days
        if r.trade_date < TODAY or upto is None or upto.time() >= time(15, 50)
    ]
    repos.program.put_market_days(days, loaded_by=LOADED_BY, received_at=CLOCK)
    repos.program.put_market_minutes(
        [r for r in w.program_intraday if seen(r.ts)], loaded_by=LOADED_BY, received_at=CLOCK
    )
    repos.program.put_investor_days(
        list(w.program_investors), loaded_by=LOADED_BY, received_at=CLOCK
    )
    scan = [r for r in w.program_stocks if r.scope == "scan" and seen(r.ts)]
    repos.program.put_stock_scan(scan, loaded_by=LOADED_BY, received_at=CLOCK)
    if after_close:
        sweep = [r for r in w.program_stocks if r.scope == "sweep"]
        repos.program.put_stock_days(sweep, loaded_by=LOADED_BY, received_at=CLOCK)
    repos.market.upsert_universe(_universe(w), loaded_by=LOADED_BY, received_at=CLOCK)
    repos.market.put_index_quotes(_index_quotes(upto), loaded_by=LOADED_BY, received_at=CLOCK)


@dataclass
class ProgramApiWorld:
    world: P5World | None  # None = 빈 저장소(404 시험)
    repos: MemoryRepos
    clock: FakeClock
    markets: MarketsConfig

    def client(self) -> TestClient:
        app = create_app(
            make_settings(),
            redis=fakeredis.FakeRedis(),
            repos=self.repos,
            now=self.clock,
            cal=TradingCalendar.default(),
            markets=self.markets,
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


def program_api_world(
    *, empty: bool = False, at: datetime = CLOCK, markets: MarketsConfig | None = None
) -> ProgramApiWorld:
    """그 시각(at)의 세계 — 그때까지 있었을 행만 넣는다(장중 13:00 이면 13:00 까지)."""
    repos = memory_repos()
    cfg = markets if markets is not None else f2_markets()
    if empty:
        return ProgramApiWorld(None, repos, FakeClock(at), cfg)
    w = _world()
    load_f2_world(repos, w, upto=at)
    return ProgramApiWorld(w, repos, FakeClock(at), cfg)


@functools.cache
def _world() -> P5World:
    return build_world()


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드). 파일마다 그 시각의
    세계."""
    out: dict[str, str] = {}
    for name, path, at in FILES:
        r = program_api_world(at=at).authed().get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        body = r.json()
        if name.startswith("minute_"):  # 분 축 401칸 × 12선 — 한 줄로(파일 크기)
            out[name] = json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n"
        else:
            out[name] = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    return out
