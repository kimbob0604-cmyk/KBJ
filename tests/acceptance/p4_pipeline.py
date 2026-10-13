"""P4 완료 시험의 세계 — 합성 20종목(`p4_world`)에 P4 **실제 처리기**를 등록부 cron 순서대로 돌린다.

docs/p4_design.md §0.3·§8.4(묶음 S). 운영 경로와 같은 모양이 되게 처리기를 직접 부른다(실행기 의미 —
실행 as_of·선점 — 는 하루 운영 시뮬레이션 `tests/sim/test_one_day_p4.py` 가 따로 본다).

창과 순서
- **과거(씨앗, T0 = 2026-11-12 22:00 KST 까지)**: 하루치 피드로는 들어오지 않는 이력은 세계 참값
  행을 넣는다 —
  공시·잠정실적·오버행·소유보고·주식총수·관심종목·종류주식(`filings_world.load(upto=T0)`), 시세(KRX
  일봉·스냅·유니버스·분할 ≤ 11-12), KIS 투자자 일별, 컨센서스 스냅·의견(< 11-13 — 세계는 2026-09-01
  부터). 고유번호(`filings.corp_code`)·기업개황(`filings.company_profile`)·**재무 5년
  백필(`fin.backfill` — 상세 층 full 뒤 주요계정 main)**은 T0 에 실제 처리기로 받는다.
- **11-13(금) 00:00 ~ 11-16(월) 21:30**: 등록부의 cron(`config/jobs.yaml`)을 그대로 펼쳐 시각 순서로
  처리기를 부른다 — `filings.corp_code`·`filings.company_profile`·`filings.dart_feed`(매분)·
  `fin.quarterly`·`filings.derive`·`earnings.alerts`·`earnings.backfill`·`krx.daily`(P3 — 가짜
  KRX 에 그날 세계 행)·`fin.valuation_band`·`consensus.snapshot`(등록부에서는 꺼져 있어도
  처리기를 직접 — 설계 §8.4)·`reports.dart_excel`. 같은 시각이면 등록부 순서. 실행 as_of 는
  실행기와 같은 규칙(`resolve_as_of`), 조건(`when`)이 맞지 않는 날은 건너뛴다.
- 장 마감 수집(`market.close_collect` — P3, 검증된 처리기)의 11-13 산출(KIS 스냅 — 상태 배지·투자자
  일별)은 15:40 에 세계 참값으로 넣는다(KIS 일별 가짜 응답이 20종목 세계를 모른다).
- 신고가 보드(P3 board 엔진 — 다시 계산 금지, D-P4-23)는 11-16 08:40 확정분을 API 시험 세계와 같은
  합성 산출로 넣는다.

설정 덮어쓰기는 `p4_expect.yaml config_overrides`(consensus.top_n=0·public_demo 비움) 그대로.
키·토큰은 가짜 값이다. 로그인 등급 응답은 합성(U3).
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import fakeredis

from kbj.config.fin import FinConfig, ReportsConfig
from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.time import KST
from kbj.data.catalog import all_datasets, keys_for
from kbj.data.public.dart.client import DartClient
from kbj.data.spec import DataKey
from kbj.reports.dart_excel import job as report_job
from kbj.services.collectors import consensus as consensus_job
from kbj.services.collectors import corp_code as corp_code_job
from kbj.services.collectors import dart_company, dart_feed, krx_daily
from kbj.services.collectors import fin as fin_job
from kbj.services.collectors.corp_code import MemoryCorpCodeStore
from kbj.services.collectors.krx_daily import Coverage
from kbj.services.engine import earnings as earnings_job
from kbj.services.engine import filings as filings_job
from kbj.services.engine import valuation as valuation_job
from kbj.services.scheduler.claims import MemoryClaimStore
from kbj.services.scheduler.conditions import AsOfUnavailable, holds, resolve_as_of
from kbj.services.scheduler.handlers import Handler, JobContext, JobResult
from kbj.services.scheduler.registry import JobSpec, Registry
from kbj.store.repos import MemoryRepos
from tests.fakes.dart_server import KEY as DART_KEY
from tests.fakes.dart_server import FakeDart
from tests.fakes.kis_server import FakeKisServer
from tests.fakes.krx_server import FakeKrx
from tests.fixtures.synthetic.p4_world import CLOCK, LAST_KRX_DAY, P4World, build_world
from tests.golden.dart_report.golden import NoLimit
from tests.unit.api.api_world import FakeClock
from tests.unit.api.api_world_p4 import P4ApiWorld, _fill_board, config_for_tests
from tests.unit.collectors.p3_fakes import DirectKis, krx_client, load_cfg
from tests.unit.engines.filings.filings_world import FakeNotify, load

ROOT: Final = Path(__file__).resolve().parents[2]
T0: Final = datetime(2026, 11, 12, 22, 0, tzinfo=KST)  # 과거 씨앗의 끝(목 밤)
START: Final = datetime(2026, 11, 13, 0, 0, tzinfo=KST)
END: Final = CLOCK  # 2026-11-16 21:30 — p4_expect.yaml clock
SEEDED_UNTIL: Final = date(2026, 11, 12)  # 시세 씨앗의 마지막 날(11-13 은 krx.daily 가 받는다)
CLOSE_COLLECT_AT: Final = datetime(2026, 11, 13, 15, 40, tzinfo=KST)
BOARD_CONFIRM_AT: Final = datetime(2026, 11, 16, 8, 40, tzinfo=KST)
LOADED_BY: Final = "acceptance.seed"

# 창 안에서 등록부 cron 대로 돌리는 작업(실제 처리기). 같은 시각이면 이 순서
PIPELINE: Final[dict[str, Handler]] = {
    "filings.corp_code": corp_code_job.run,
    "filings.company_profile": dart_company.run,
    "earnings.backfill": earnings_job.backfill,
    "filings.dart_feed": dart_feed.run,
    "krx.daily": krx_daily.run,
    "fin.valuation_band": valuation_job.band,
    "fin.quarterly": fin_job.quarterly,
    "filings.derive": filings_job.derive,
    "earnings.alerts": earnings_job.alerts,
    "consensus.snapshot": consensus_job.run,
    "reports.dart_excel": report_job.run,
}
SEED_HANDLERS: Final[dict[str, Handler]] = {"fin.backfill": fin_job.backfill}


@dataclass(frozen=True)
class Run:
    job: str
    as_of: str
    at: datetime
    status: str
    detail: dict[str, Any]
    wall_s: float


class TracebackLog(logging.Handler):
    """처리기가 남긴 로그 중 traceback(exc_info)이 붙은 것 — 완료 시험 ⑤."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info:
            self.records.append(record)


def seed_history(
    repos: MemoryRepos, w: P4World, *, until: date, now: datetime, loaded_by: str = LOADED_BY
) -> None:
    """하루치 수집으로는 들어오지 않는 이력 — 유니버스·분할(세계 전체), 시세·KIS 투자자 일별·
    컨센서스 스냅·투자의견(`until` 이하). 완료 시험과 하루 운영 시뮬레이션
    (`tests/sim/test_one_day_p4.py`)이 같이 쓴다(두 벌 금지)."""
    r = repos
    r.market.upsert_universe(w.universe(), loaded_by=loaded_by)
    r.market.upsert_snapshots([s for s in w.snaps() if s.date <= until], loaded_by=loaded_by)
    r.market.upsert_daily_bars([b for b in w.bars() if b.date <= until], loaded_by=loaded_by)
    r.flows.upsert_investor_days(
        [i for i in w.investors() if i.date <= until], revise=False, loaded_by=loaded_by, now=now
    )
    for ev in w.splits:
        r.etf.put_split_event(ev, loaded_by=loaded_by, now=now)
    r.consensus.put_snapshot(
        [x for x in w.consensus if x.snap_date <= until], loaded_by=loaded_by, now=now
    )
    r.consensus.put_opinions(
        [o for o in w.opinions if o.opinion_date <= until], loaded_by=loaded_by, now=now
    )


def detail_codes(repos: MemoryRepos) -> list[str]:
    """상세 층 대상(보통주 코드) = 관심종목(저장소에서 읽는다) — 설정 덮어쓰기 consensus.top_n=0·
    공개 데모 없음(p4_expect.yaml). 운영 스케줄러에는 이 자원(`detail_universe`)이 아직 없다
    (S 요청)."""
    return sorted(i.code for i in repos.journal.watchlist("main"))


class P4Pipeline:
    """합성 세계 + 메모리 저장소 + 가짜 서버(DART·KIS·KRX) + 처리기 실행 기록."""

    def __init__(self, *, dart_every_min: int = 1) -> None:
        self.w: P4World = build_world()
        self.registry = Registry.load(ROOT / "config" / "jobs.yaml")
        self.catalog = all_datasets()
        self.kr = TradingCalendar.default()
        self.us = us_calendar()
        self.clock = FakeClock(T0)
        self.fin_cfg: FinConfig
        self.reports_cfg: ReportsConfig
        self.fin_cfg, self.reports_cfg = config_for_tests()
        self.dart_every_min = dart_every_min
        loaded = load(
            T0,
            parts=("disclosures", "prelims", "overhang", "ownership", "shares", "watch", "classes"),
            w=self.w,
        )
        self.repos: MemoryRepos = loaded.repos
        self.repos.filings.corp_codes.clear()  # 고유번호 표는 filings.corp_code 처리기가 채운다
        self.corp_store = MemoryCorpCodeStore()
        self.claims = MemoryClaimStore()
        self.notify = FakeNotify()
        self.dart_fake = FakeDart(self.clock, world=self.w)
        self.dart = DartClient(
            DART_KEY, transport=self.dart_fake.transport("acceptance"), limiter=NoLimit(),
            retries=1, backoff_s=0, now=self.clock, sleep=lambda _s: None,
        )  # fmt: skip
        self.kis = DirectKis(FakeKisServer(self.clock, consensus=self.w))
        self.krx_fake = FakeKrx(self.clock)
        self.krx = krx_client(self.krx_fake)
        self.runs: list[Run] = []
        self.tracebacks = TracebackLog()

    # ── 자원 ────────────────────────────────────────────────────────────────────────────
    def detail_codes(self, _day: date) -> list[str]:
        return detail_codes(self.repos)

    def resources(self) -> dict[str, Any]:
        return {
            "repos": self.repos,
            "public_repos": self.repos.public(),
            "journal": self.repos.journal,
            "claims": self.claims,
            "dart": self.dart,
            "kis": self.kis,
            "krx": self.krx,
            "markets": load_cfg(),
            "krx_coverage": Coverage(min_rows={}),
            "kr": self.kr,
            "us": self.us,
            "fin_config": self.fin_cfg,
            "reports_config": self.reports_cfg,
            "detail_universe": self.detail_codes,
            "notify": self.notify,
            "corp_code_store": self.corp_store,
            "corp_code_fetch": lambda: self.dart.corp_codes(refresh=True),
            "redis": fakeredis.FakeRedis(),
        }

    # ── 실행 ────────────────────────────────────────────────────────────────────────────
    def keys(self, spec: JobSpec, as_of: str) -> tuple[DataKey, ...]:
        """실행기가 미리 잡는 키(event 가 아닌 collects 를 펼친다 — runner._keys 와 같은 뜻)."""
        out: list[DataKey] = []
        for c in spec.collects:
            if c.as_of == "event":
                continue
            out += keys_for(self.catalog[c.dataset_id], as_of, c.venues or None)
        return tuple(out)

    def call(self, name: str, as_of: str, *, keys: tuple[DataKey, ...] | None = None) -> JobResult:
        spec = self.registry.by_name(name)
        handler = PIPELINE.get(name) or SEED_HANDLERS[name]
        ks = self.keys(spec, as_of) if keys is None else keys
        now = self.clock()
        ctx = JobContext(
            job=name, as_of=as_of, run_id=f"{name}:{as_of}:1", attempt=1, now=now, keys=ks,
            resources=self.resources(),
        )  # fmt: skip
        if name == "krx.daily":
            self._serve_krx(date.fromisoformat(as_of))
        t = time.monotonic()
        res = handler(ctx)
        self.runs.append(Run(name, as_of, now, res.status, dict(res.detail), time.monotonic() - t))
        return res

    def _serve_krx(self, day: date) -> None:
        """가짜 KRX 가 그날 세계 행을 낸다(KRX 는 D+1 08:00 공표 — 시험은 부르는 순간 공표)."""
        self.krx_fake.extra = {
            ep: [dict(r) for r in rows]
            for ep, rows in (self.w.krx_daily_rows(day) | self.w.krx_base_rows(day)).items()
        }
        self.krx_fake.publish(day)

    def fires(self) -> Iterator[tuple[datetime, str, str]]:
        """창 안 (시각, 작업, 실행 as_of) — 등록부 cron·when·as_of 그대로, 시각·등록 순서."""
        out: list[tuple[datetime, int, str, str]] = []
        order = {n: i for i, n in enumerate(PIPELINE)}
        for name in PIPELINE:
            spec = self.registry.by_name(name)
            cron = spec.schedule.cron_spec()
            assert cron is not None, name
            d = START.date()
            while d <= END.date():
                if holds(spec.schedule.when, d, self.kr, self.us):
                    for t in cron.times_on(d):
                        if name == "filings.dart_feed" and t.minute % self.dart_every_min:
                            continue
                        at = datetime.combine(d, t, tzinfo=KST)
                        if not START <= at <= END:
                            continue
                        try:
                            as_of = resolve_as_of(spec.run_as_of(), at, self.kr, self.us)
                        except AsOfUnavailable:
                            continue
                        out.append((at, order[name], name, as_of))
                d += timedelta(days=1)
        for at, _o, name, as_of in sorted(out):
            yield at, name, as_of

    # ── 씨앗(과거) ──────────────────────────────────────────────────────────────────────
    def seed(self) -> None:
        # 시세·컨센서스 이력(≤ 11-12) — 11-13·11-16 은 krx.daily·consensus.snapshot 처리기가 받는다
        seed_history(self.repos, self.w, until=SEEDED_UNTIL, now=T0)
        # 고유번호·기업개황·재무 5년 백필 — 실제 처리기(T0)
        day = T0.date().isoformat()
        self._must(self.call("filings.corp_code", day))
        self._bridge_corp_codes()
        self._must(self.call("filings.company_profile", day))
        for tier in ("full", "main"):
            self.fin_cfg = self.fin_cfg.model_copy(
                update={"backfill": self.fin_cfg.backfill.model_copy(update={"tier": tier})}
            )
            self._must(self.call("fin.backfill", T0.strftime("%Y-%m-%dT%H:%M"), keys=()))
        self.fin_cfg = config_for_tests()[0]

    def _bridge_corp_codes(self) -> None:
        """운영은 같은 표(`pub_filings.corp_code`)다 — 메모리판은 고유번호 저장소와 공시 저장소가
        따로라
        처리기가 쓴 행을 공시 저장소의 색인으로 옮긴다(상장사만)."""
        idx = self.repos.filings.corp_codes
        idx.clear()
        for c, _at in self.corp_store.rows.values():
            if c.stock_code:
                idx[c.stock_code] = (c.corp_code, c.corp_name)

    @staticmethod
    def _must(res: JobResult) -> None:
        if res.status != "ok":
            raise RuntimeError(f"처리기 실패: {res.status} {dict(res.detail)}")

    # ── 창 안 사건 ──────────────────────────────────────────────────────────────────────
    def _close_collect(self) -> None:
        """11-13 장 마감 수집(P3 market.close_collect)의 KIS 스냅·투자자 일별 — 세계 참값."""
        day = LAST_KRX_DAY
        kis = [replace(s, source="kis") for s in self.w.snaps(day)]
        self.repos.market.upsert_snapshots(kis, loaded_by="acceptance.close_collect")
        self.repos.flows.upsert_investor_days(
            [i for i in self.w.investors() if i.date == day],
            revise=False,
            loaded_by="acceptance.close_collect",
            now=self.clock(),
        )

    def build(self) -> P4Pipeline:
        root = logging.getLogger("kbj")
        root.addHandler(self.tracebacks)
        try:
            self.seed()
            hooks: list[tuple[datetime, Callable[[], None]]] = [
                (CLOSE_COLLECT_AT, self._close_collect),
                (BOARD_CONFIRM_AT, lambda: _fill_board(self.repos, self.w, self.clock())),
            ]
            for at, name, as_of in self.fires():
                while hooks and hooks[0][0] <= at:
                    hat, fn = hooks.pop(0)
                    self.clock.set(hat)
                    fn()
                self.clock.set(at)
                self.call(name, as_of)
                if name == "filings.corp_code":
                    self._bridge_corp_codes()
            self.clock.set(END)
        finally:
            root.removeHandler(self.tracebacks)
        return self

    # ── 결과 보기 ───────────────────────────────────────────────────────────────────────
    def runs_of(self, job: str) -> list[Run]:
        return [r for r in self.runs if r.job == job]

    def api(self) -> P4ApiWorld:
        """완료 시각(21:30)의 로그인 API — 같은 메모리 저장소를 읽는다."""
        return P4ApiWorld(
            w=self.w, repos=self.repos, clock=FakeClock(END), redis=fakeredis.FakeRedis(),
            fin=self.fin_cfg, reports=self.reports_cfg, cal=self.kr, notify=self.notify,
        )  # fmt: skip


@functools.cache
def built() -> P4Pipeline:
    """한 프로세스에 한 번(몇십 초) — 읽기만 하는 시험이 같이 쓴다."""
    return P4Pipeline().build()


__all__ = [
    "END", "PIPELINE", "T0", "P4Pipeline", "Run", "built", "detail_codes", "seed_history",
]  # fmt: skip
