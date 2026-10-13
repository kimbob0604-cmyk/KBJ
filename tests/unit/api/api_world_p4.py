"""P4 API 시험 세계 — 합성 20종목 세계(`p4_world`)를 메모리 저장소에 올리고 가짜 시계·Redis·앱을
준다.

docs/p4_design.md §1.8(A)·§8.4·§8.6. 로그인 등급(KIS·KRX) 실데이터는 쓰지 않는다 — 전부
`tests/fixtures/synthetic/p4_world.py`(고정 시드 생성기) 의 합성 값이다.

어떻게 채우나(실제 처리기·엔진을 쓴다 — 응답이 운영 경로와 같은 모양이 되게)
- 공시 목록·기업개황·잠정실적·오버행 사건·소유보고·주식총수·관심종목·종류주식: 공시 엔진 시험의 적재
  도우미(`tests/unit/engines/filings/filings_world.load` — 시각 ≤ CLOCK 인 것만, PIT)
- 시세: 세계의 KRX 일봉·스냅·유니버스·KIS 투자자(합성)
- 재무: 재무 수집 처리기 `fin.backfill`(가짜 DART 전송층 — 상세 층 = 관심종목 전체 재무제표, 그 밖은
  주요계정)
- 공시 파생(오버행 상태·요약·내부자 창·실적 일정·키워드 점수): `filings.derive` 실행 함수(sweep)
- 잠정실적 분류·알림 기록: `earnings.alerts` 실행 함수(알림 shim 대역)
- 컨센서스: 세계의 추정실적 스냅·투자의견 + 컨센서스 처리기의 파생(`derive` — 목표가·리비전)
- 리포트·부문: `reports.dart_excel` 처리기(가짜 DART)
- 밸류에이션·시나리오: `fin.valuation_band` 실행 함수(`run_valuation` — as_of = 마지막 KRX 일,
  PIT). `valuation=False` 면 넣지 않는다(밸류 행이 없을 때의 회사 단위 사유·404 경로 시험)
- 신고가 보드: 마지막 날 종목 행(종가 기준 3축 적중·창 못 채움)을 세계 일봉으로 만든 합성 산출.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import fakeredis
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbj.config.fin import FinConfig, ReportsConfig, load_fin, load_reports
from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import BoardArtifact, BoardDayRecord, Label, StockDay
from kbj.core.time import KST
from kbj.data.public.dart.client import DartClient
from kbj.reports.dart_excel import job as report_job
from kbj.services.api.app import create_app
from kbj.services.collectors import consensus as consensus_job
from kbj.services.collectors import fin as fin_job
from kbj.services.engine.earnings import run_alerts
from kbj.services.engine.filings import run_derive
from kbj.services.engine.valuation import run_valuation
from kbj.services.scheduler.claims import MemoryClaimStore
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos
from tests.fixtures.synthetic.p4_world import CLOCK, LAST_KRX_DAY, TODAY, P4World, build_world
from tests.golden.dart_report.golden import WorldDart
from tests.unit.api.api_world import BASE, PASSWORD, USER, FakeClock, make_settings
from tests.unit.engines.filings.filings_world import FakeNotify, load

ROOT: Final = Path(__file__).resolve().parents[3]
EXPECT: Final = ROOT / "tests" / "fixtures" / "synthetic" / "p4_expect.yaml"
LOADED_BY: Final = "test.api.p4"


@functools.cache
def expect() -> dict[str, Any]:
    with EXPECT.open(encoding="utf-8") as f:
        doc: dict[str, Any] = yaml.safe_load(f)
    return doc


def config_for_tests() -> tuple[FinConfig, ReportsConfig]:
    """완료 시험과 같은 설정 덮어쓰기(p4_expect.yaml config_overrides — 세계가 20종목뿐)."""
    fin = load_fin()
    fin = fin.model_copy(
        update={"consensus": fin.consensus.model_copy(update={"top_n": 0})}, deep=True
    )
    reports = load_reports().model_copy(update={"public_demo": ()})
    return fin, reports


# ── 시세·보드 ──────────────────────────────────────────────────────────────────────────


def _fill_market(repos: MemoryRepos, w: P4World, now: datetime) -> None:
    repos.market.upsert_universe(w.universe(), loaded_by=LOADED_BY)
    repos.market.upsert_snapshots(w.snaps(), loaded_by=LOADED_BY)
    repos.market.upsert_daily_bars(w.bars(), loaded_by=LOADED_BY)
    repos.flows.upsert_investor_days(w.investors(), revise=False, loaded_by=LOADED_BY, now=now)
    for ev in w.splits:
        repos.etf.put_split_event(ev, loaded_by=LOADED_BY, now=now)


def _board_axes(closes: Sequence[tuple[date, float]], day: date, tdays: Sequence[date]) -> dict:
    """종가 기준 3축 — 120 시장 거래일·달력 364일·상장 이후(엄격 '>'), 창을 못 채우면 gap None."""
    cur = closes[-1][1]
    prev = closes[:-1]
    idx = tdays.index(day)
    out_hits: dict[str, bool] = {}
    out_gap: dict[str, float | None] = {}
    first = prev[0][0] if prev else day
    w120_start = tdays[idx - 120] if idx >= 120 else None
    windows: dict[str, list[float] | None] = {
        "d120": (
            [c for d, c in prev if d >= w120_start]
            if w120_start is not None and first <= w120_start
            else None
        ),
        "w52": (
            [c for d, c in prev if d >= day - timedelta(days=364)]
            if first <= day - timedelta(days=364)
            else None
        ),
        "hist": [c for _d, c in prev] if len(prev) >= 60 else None,
    }
    for k, win in windows.items():
        if not win:
            out_hits[k] = False
            out_gap[k] = None
            continue
        ref = max(win)
        out_hits[k] = cur > ref
        out_gap[k] = round((ref - cur) / ref * 100, 2)
    label = next((k for k in ("hist", "w52", "d120") if out_hits[k]), None)
    return {"label": label, "hits": out_hits, "gap": out_gap}


def _fill_board(repos: MemoryRepos, w: P4World, now: datetime) -> None:
    day = LAST_KRX_DAY
    tdays = [d for d in w.trading_days if d <= day]
    stock_days: list[StockDay] = []
    labels: list[Label] = []
    snaps = {s.code: s for s in w.snaps(day)}
    for s in w.stocks:
        closes = [(b.date, b.close) for b in w.bars(s.code) if b.close is not None]
        if not closes or s.code not in snaps:
            continue
        basis = _board_axes(closes, closes[-1][0], tdays) if closes[-1][0] == day else None
        if basis is None:  # 정지 — 그날 봉이 없다(보드에 없음)
            continue
        sn = snaps[s.code]
        stock_days.append(
            StockDay(
                code=s.code, date=day, name=s.name, close=sn.close, chg_pct=sn.chg_pct,
                turnover=sn.turnover, turnover_is_estimate=False, mktcap=sn.mktcap,
                label=basis["label"], near_kind=None, near_gap=None, status=None, suspect=False,
                sector=None, theme=None, ret_5d=None, ret_21d=None, vol_mult=None, source="krx",
                quality=Quality.OK, extra={"close_basis": basis, "_source": "SYNTHETIC"},
            )
        )  # fmt: skip
        if basis["label"]:
            rank = ("hist", "w52", "d120").index(basis["label"])
            labels.append(Label(s.code, day, "close", basis["label"], rank, "krx", Quality.OK))
    as_of = datetime.combine(day, datetime.min.time().replace(hour=15, minute=30), tzinfo=KST)
    art = BoardArtifact(
        trade_date=day, name="newhigh", payload={"_source": "SYNTHETIC", "achieved": []},
        engine_version="test", input_digest="0" * 16, as_of=as_of, generated_at=now,
        source="krx", quality=Quality.OK,
    )  # fmt: skip
    rec = BoardDayRecord(
        trade_date=day, labels=tuple(labels), stock_days=tuple(stock_days), artifacts=(art,),
        source="krx", quality=Quality.OK,
    )  # fmt: skip
    repos.board.put_day(rec, loaded_by=LOADED_BY, now=now)


# ── 재무·리포트(실제 처리기 + 가짜 DART) ────────────────────────────────────────────────


def _ctx(job: str, now: datetime, res: dict[str, Any]) -> JobContext:
    return JobContext(
        job=job, as_of=now.astimezone(KST).strftime("%Y-%m-%dT%H:%M"), run_id=f"{job}-1",
        attempt=1, now=now, resources=res,
    )  # fmt: skip


def _fill_fin(repos: MemoryRepos, w: P4World, now: datetime, fin_cfg: FinConfig,
              reports_cfg: ReportsConfig) -> None:  # fmt: skip
    dart = WorldDart(w, now)
    client = dart.client(DartClient)
    detail = sorted(s.code for s in w.stocks if s.watch)
    base: dict[str, Any] = {
        "public_repos": repos.public(), "dart": client, "claims": MemoryClaimStore(),
        "reports_config": reports_cfg, "journal": repos.journal,
        "detail_universe": lambda _day: detail,
    }  # fmt: skip
    for tier in ("full", "main"):
        cfg = fin_cfg.model_copy(
            update={"backfill": fin_cfg.backfill.model_copy(update={"tier": tier})}
        )
        res = fin_job.backfill(_ctx("fin.backfill", now, {**base, "fin_config": cfg}))
        if res.status != "ok":
            raise RuntimeError(f"fin.backfill({tier}) 실패: {res.detail}")
    res = report_job.run(_ctx("reports.dart_excel", now, {**base, "fin_config": fin_cfg}))
    if res.status != "ok":
        raise RuntimeError(f"reports.dart_excel 실패: {res.detail}")


# ── 컨센서스(세계 스냅 + 처리기 파생) ────────────────────────────────────────────────────


def _fill_consensus(repos: MemoryRepos, w: P4World, now: datetime, cfg: FinConfig) -> date | None:
    snaps = [r for r in w.consensus if r.snap_date <= TODAY]
    ops = [o for o in w.opinions if o.opinion_date <= TODAY]
    repos.consensus.put_snapshot(snaps, loaded_by=LOADED_BY, now=now)
    repos.consensus.put_opinions(ops, loaded_by=LOADED_BY, now=now)
    if not snaps:
        return None
    day = max(r.snap_date for r in snaps)
    codes = sorted({r.code for r in snaps} | {o.code for o in ops})
    consensus_job.derive(repos, codes, day, cfg=cfg.consensus, now=now, loaded_by=LOADED_BY)
    return day


# ── 세계 ──────────────────────────────────────────────────────────────────────────────


def _fixed_version(_domain: str) -> str:
    return "v1"


@dataclass
class P4ApiWorld:
    w: P4World
    repos: MemoryRepos
    clock: FakeClock
    redis: fakeredis.FakeRedis
    fin: FinConfig
    reports: ReportsConfig
    cal: TradingCalendar
    notify: FakeNotify
    # 데이터 버전(고정) — P3 원장 캐시(`/api/flows/stock` 의 원장을 한 번만)·응답 캐시가 켜진다
    versions: Callable[[str], str] | None = field(default_factory=lambda: _fixed_version)
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

    def app(self, **kw: Any) -> FastAPI:
        args: dict[str, Any] = {
            "redis": self.redis, "repos": self.repos, "now": self.clock, "cal": self.cal,
            "markets": load_markets(), "events": load_calendar_events(), "static_dir": False,
            "fin": self.fin, "reports": self.reports, "data_versions": self.versions,
        }  # fmt: skip
        args.update(kw)
        settings = args.pop("settings", make_settings())
        return create_app(settings, **args)

    def client(self, **kw: Any) -> TestClient:
        return TestClient(self.app(**kw), base_url=BASE)

    def login(self, client: TestClient) -> None:
        r = client.post(
            "/api/auth/login",
            json={"username": USER, "password": PASSWORD},
            headers={"Origin": BASE},
        )
        assert r.status_code == 200, r.text

    def authed(self) -> TestClient:
        c = self.client()
        self.login(c)
        return c


@functools.cache
def shared() -> P4ApiWorld:
    """읽기만 하는 시험이 같이 쓰는 세계(만드는 데 몇 초 — 한 프로세스에 한 번). 고치지 않는다."""
    return build_p4_api_world()


def build_p4_api_world(
    *, valuation: bool = True, transform: Callable[[MemoryRepos], None] | None = None
) -> P4ApiWorld:
    """합성 세계를 처리기·엔진으로 메모리 저장소에 올린다(몇 초 — 모듈 범위 픽스처로 한 번)."""
    w = build_world()
    now = CLOCK
    fin_cfg, reports_cfg = config_for_tests()
    loaded = load(upto=now, w=w)
    repos = loaded.repos
    _fill_market(repos, w, now)
    _fill_board(repos, w, now)
    _fill_fin(repos, w, now, fin_cfg, reports_cfg)
    rep = run_derive(repos.public(), TODAY, now=now, cfg=fin_cfg, sweep=True)
    if rep.errors:
        raise RuntimeError(f"filings.derive 실패: {rep.errors}")
    notify = FakeNotify()
    alerts = run_alerts(repos, TODAY, now=now, cfg=fin_cfg, notify=notify)
    if alerts.errors:
        raise RuntimeError(f"earnings.alerts 실패: {alerts.errors}")
    _fill_consensus(repos, w, now, fin_cfg)
    if valuation:
        rv = run_valuation(repos, LAST_KRX_DAY, cfg=fin_cfg, now=now)
        if rv.status != "ok":
            raise RuntimeError(f"fin.valuation_band 실패: {rv.detail()}")
    if transform is not None:
        transform(repos)
    return P4ApiWorld(
        w=w, repos=repos, clock=FakeClock(now), redis=fakeredis.FakeRedis(), fin=fin_cfg,
        reports=reports_cfg, cal=TradingCalendar.default(), notify=notify,
    )  # fmt: skip
