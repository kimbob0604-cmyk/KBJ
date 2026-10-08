"""API 시험 세계 — 합성 시장(고정 시드)을 메모리 저장소에 채운 것과 가짜 시계·Redis·앱.

로그인 등급(KIS·KRX·운용사) 실데이터는 쓰지 않는다 — `tests/fixtures/synthetic/ledger_gen.py`(합성
원장, 시드 고정)와 신고가 골든 입력(합성 — `tests/golden/board/expected`)만 쓴다. 시험용 비밀번호
해시는 낮은 비용(n=2**10)으로 시험 안에서 만든다(레포에 해시 상수 없음).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kbj.config.markets import CalendarEvents, MarketsConfig, load_calendar_events, load_markets
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import (
    BoardArtifact,
    BoardDayRecord,
    Change,
    EtfQuote,
    Fund,
    IndexQuote,
    IntradayInvestor,
    Investor,
    Label,
    RankRow,
    SectorQuote,
)
from kbj.core.time import KST
from kbj.services.api.app import create_app
from kbj.services.api.auth import hash_password
from kbj.services.api.cache import DataVersionSource
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.ledger_gen import SyntheticMarket, generate

ROOT: Final = Path(__file__).resolve().parents[3]
GOLDEN_DAY: Final = ROOT / "tests" / "golden" / "board" / "expected" / "day_29.json.gz"
SEED: Final = 20261007
USER: Final = "kbj-tester"
PASSWORD: Final = "합성-비밀번호-for-tests-only"
BASE: Final = "https://testserver"
LOADED_BY: Final = "test.api"
HISTORY_FROM: Final = "2021-10-01"  # 시장 일봉 첫날(진단 — 화면·notes 에 내세우지 않는다)
SOURCE_FLOOR: Final = "2010-01-04"  # 원천 바닥(합성)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.t = now

    def __call__(self) -> datetime:
        return self.t

    def set(self, t: datetime) -> None:
        self.t = t

    def advance(self, **kw: float) -> None:
        self.t = self.t + timedelta(**kw)


def kst(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=KST)


def low_cost_hash(password: str = PASSWORD) -> str:
    return hash_password(password, n=2**10)


def make_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "web_user": USER,
        "web_password_hash": SecretStr(low_cost_hash()),
        "public_base_url": BASE,
        "telegram_webhook_secret": SecretStr("webhook-secret-for-tests"),
        "git_commit": "abc1234",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # pyright: ignore[reportCallIssue]


def _fill_market(repos: MemoryRepos, m: SyntheticMarket, now: datetime) -> None:
    repos.market.upsert_universe(list(m.universe), loaded_by=LOADED_BY)
    repos.market.upsert_snapshots(list(m.snaps), loaded_by=LOADED_BY)
    repos.market.upsert_daily_bars(list(m.bars), loaded_by=LOADED_BY)
    repos.market.upsert_index_bars(list(m.index_bars), loaded_by=LOADED_BY)
    repos.flows.upsert_investor_days(list(m.investors), revise=False, loaded_by=LOADED_BY, now=now)
    repos.flows.upsert_market_days(list(m.market_investors), loaded_by=LOADED_BY)
    repos.etf.upsert_etf_days(list(m.etf_days), loaded_by=LOADED_BY)
    repos.etf.upsert_meta(list(m.etf_meta), loaded_by=LOADED_BY, now=now)
    for ev in m.split_events:
        repos.etf.put_split_event(ev, loaded_by=LOADED_BY, now=now)


def golden_payloads() -> dict[str, dict[str, Any]]:
    with gzip.open(GOLDEN_DAY, "rt", encoding="utf-8") as f:
        return json.load(f)


def _fill_board(repos: MemoryRepos, day: date, now: datetime, quality: Quality) -> None:
    g = golden_payloads()
    nh = dict(g["newhigh"])
    nh["hist_scope"] = {
        "history_from": HISTORY_FROM,
        "n_before_listing": 3,
        "n_listing_unknown": 1,
        "source_floor": SOURCE_FLOOR,
        "n_since_floor": 5,
    }
    payloads = {
        "universe_meta": {k: v for k, v in g["universe"].items() if k != "stocks"},
        "newhigh": nh,
        "sectors": g["sectors"],
        "events": g["events"],
        "rankings": g["rankings"],
    }
    as_of = kst(day, 15, 30)
    arts = tuple(
        BoardArtifact(
            trade_date=day,
            name=name,  # pyright: ignore[reportArgumentType]
            payload={**p, "as_of": day.isoformat()},
            engine_version="test",
            input_digest="0" * 16,
            as_of=as_of,
            generated_at=now,
            source="kis",
            quality=quality,
        )
        for name, p in payloads.items()
    )
    labels: list[Label] = []
    for row in nh["achieved"]:
        for basis in ("close", "high"):
            lab = (row.get(f"{basis}_basis") or {}).get("label")
            if lab:
                labels.append(
                    Label(
                        code=row["code"],
                        date=day,
                        basis=basis,  # pyright: ignore[reportArgumentType]
                        kind=lab,
                        rank=("hist", "w52", "d120").index(lab),
                        source="kis",
                        quality=quality,
                    )
                )
    rec = BoardDayRecord(
        trade_date=day,
        labels=tuple(labels),
        stock_days=(),
        artifacts=arts,
        source="kis",
        quality=quality,
    )
    repos.board.put_day(rec, loaded_by=LOADED_BY, now=now)


def _fill_intraday(repos: MemoryRepos, m: SyntheticMarket, slot: datetime) -> None:
    """다음 영업일 장중 한 슬롯(잠정) — 지수·업종·가집계·순위·ETF 시세."""
    q = Quality.ESTIMATED
    repos.market.put_index_quotes(
        [
            IndexQuote("0001", slot, "코스피", 2650.5, 0.42, 4_100_000_000_000, 210_000_000,
                       "kis", q),
            IndexQuote("1001", slot, "코스닥", 780.2, -0.31, 3_200_000_000_000, 400_000_000,
                       "kis", q),
        ],
        loaded_by=LOADED_BY,
    )  # fmt: skip
    repos.market.put_sector_quotes(
        [
            SectorQuote(
                "KOSPI", c, slot, f"합성업종{c}", 1000.0 + i, 0.5 - i * 0.4, 10**11, "kis", q
            )
            for i, c in enumerate(("S0001", "S0002"))
        ],
        loaded_by=LOADED_BY,
    )
    stocks = [s.code for s in m.stocks if s.kind == "common"][:5]
    inv: list[IntradayInvestor] = []
    for i, code in enumerate(stocks, start=1):
        inv.append(
            IntradayInvestor(
                code, slot, Investor.FOREIGN, "KRX", 10**9 * (6 - i), 1000 * i, i, "kis.prelim", q
            )
        )
        inv.append(IntradayInvestor(code, slot, Investor.INSTITUTION, "KRX", 10**8 * (6 - i), 500,
                                    i, "kis.prelim", q))  # fmt: skip
    repos.flows.put_intraday(inv, loaded_by=LOADED_BY)
    repos.market.put_rank_rows(
        [
            RankRow("KOSPI", slot, i, "KRX", code, f"합성{code}", 10**10 * (6 - i), 1.2, "kis", q)
            for i, code in enumerate(stocks, start=1)
        ],
        loaded_by=LOADED_BY,
    )
    etfs = sorted({d.code for d in m.etf_days})[:3]
    repos.etf.put_quotes(
        [
            EtfQuote(code, slot, 10_100.0 + i * 10, 10_000.0, None, 10**9, 10**5, "kis", q)
            for i, code in enumerate(etfs)
        ],
        loaded_by=LOADED_BY,
    )


def _fill_holdings(repos: MemoryRepos, run: date, now: datetime) -> None:
    funds = [
        Fund("kodex:F1", "합성운용A", "F1", "E00001", "합성ETF01", "AI반도체", True, "full",
             True, 0, "ETF_ISSUERS:합성운용A"),
        Fund("tiger:F2", "합성운용B", "F2", "E00002", "합성ETF02", "시장대표", False, "top10", True,
             0, "ETF_ISSUERS:합성운용B"),
    ]  # fmt: skip
    repos.etf.upsert_funds(funds, loaded_by=LOADED_BY, now=now)
    prev = run - timedelta(days=1)
    rows = [
        Change(run, "kodex:F1", "Q00001", "NEW", "합성종목01", run, prev, 1, None, 1000.0, None,
               3.1, None, None),
        Change(run, "kodex:F1", "Q00002", "ADD", "합성종목02", run, prev, 1, 500.0, 600.0, 2.0,
               2.4, 20.0, 18.5),
        Change(run, "tiger:F2", "Q00003", "OUT10", "합성종목03", run, prev, 1, 800.0, 790.0, 4.0,
               3.9, -1.25, None),
    ]  # fmt: skip
    repos.etf.put_changes(run, rows, engine_version="test", now=now)


@dataclass
class World:
    market: SyntheticMarket
    repos: MemoryRepos
    clock: FakeClock
    redis: fakeredis.FakeRedis
    markets: MarketsConfig
    events: CalendarEvents
    cal: TradingCalendar
    settings: Settings
    next_day: date
    slot: datetime
    versions: DataVersionSource | None = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

    def app(self, **kw: Any) -> FastAPI:
        args: dict[str, Any] = {
            "redis": self.redis,
            "repos": self.repos,
            "now": self.clock,
            "cal": self.cal,
            "markets": self.markets,
            "events": self.events,
            "data_versions": self.versions,
            "static_dir": False,
        }
        args.update(kw)
        settings = args.pop("settings", self.settings)
        return create_app(settings, **args)

    def client(self, **kw: Any) -> TestClient:
        return TestClient(self.app(**kw), base_url=BASE)

    def login(self, client: TestClient, password: str = PASSWORD) -> str:
        r = client.post(
            "/api/auth/login",
            json={"username": USER, "password": password},
            headers={"Origin": BASE},
        )
        assert r.status_code == 200, r.text
        return r.json()["csrf_token"]


def populate(
    repos: Any,
    m: SyntheticMarket,
    cal: TradingCalendar,
    *,
    board_quality: Quality = Quality.ESTIMATED,
    intraday: bool = True,
) -> tuple[date, datetime]:
    """합성 시장을 저장소에 채운다 — 메모리 저장소(시험)·Pg 저장소(`scripts/demo_p3_server.py --db`)
    둘 다 같은 메서드라 같은 데이터가 들어간다. 반환: (다음 거래일, 장중 슬롯)."""
    last = m.last_day
    after_close = kst(last, 18, 0)
    _fill_market(repos, m, after_close)
    _fill_board(repos, last, after_close, board_quality)
    _fill_holdings(repos, last, after_close)
    nxt = cal.next_trading_day(last)
    slot = kst(nxt, 10, 0)
    if intraday:
        _fill_intraday(repos, m, slot)
    return nxt, slot


def build_world(
    *,
    seed: int = SEED,
    board_quality: Quality = Quality.ESTIMATED,
    intraday: bool = True,
    transform: Callable[[SyntheticMarket], SyntheticMarket] | None = None,
    **settings_kw: Any,
) -> World:
    """`transform`: 합성 시장을 채우기 전에 바꾼다(데모 금액 규모 — scripts/demo_p3_server.py)."""
    m = generate(seed)
    if transform is not None:
        m = transform(m)
    repos = memory_repos()
    cal = TradingCalendar.default()
    after_close = kst(m.last_day, 18, 0)
    nxt, slot = populate(repos, m, cal, board_quality=board_quality, intraday=intraday)
    markets = load_markets()
    markets = markets.model_copy(
        update={
            "market": markets.market.model_copy(update={"sector_indices": ("S0001", "S0002")}),
        }
    )
    return World(
        market=m,
        repos=repos,
        clock=FakeClock(after_close),
        redis=fakeredis.FakeRedis(),
        markets=markets,
        events=load_calendar_events(),
        cal=cal,
        settings=make_settings(**settings_kw),
        next_day=nxt,
        slot=slot,
    )
