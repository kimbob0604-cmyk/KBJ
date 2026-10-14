"""P8 여러 날 시뮬레이션 틀 — 실제 실행기 + 실제 규칙 처리기, 합성 P8 세계
(docs/p8_design.md §0.3·§1.6-3).

`SimDay`(하루 전체 서비스)는 무겁고 규칙 성과만 보면 되므로 p6(`FlashDay`)과 같은 부품
(`JobRunner`·운영 등록부 `config/jobs.yaml`·`MemoryClaimStore`·`MemoryRunLog`)으로 등록부의
**다섯 줄**만 돌린다: 실제 처리기 `rules.close`(16:00)·`rules.outcome`(08:30)·
`rules.backtest_nightly`(08:45) + 재료 작업 `market.close_collect`(15:35)·`krx.daily`(08:05)
는 합성 세계의 그날 값을 저장소에 '공표'하는 대역(가짜 KIS·KRX 를 거치는 수집기 자체는 P3 시험·
하루 sim 이 본다 — 여기서는 원장이 날마다 자라는 것만 흉내).

- 창: 합성 세계 `p8_world` 의 마지막 `WINDOW` 거래일. 설계서 본문의 2026-11~12 창은 세계가
  10-30 에 끝나 이 세계의 마지막 25 거래일로 바꿨다(추석 연휴 — 정본 캘린더 휴장 — 가 창
  안에 있어 '휴장일 실행 0' 도 본다).
- 창 앞 원장: 창 첫날 전까지의 일봉·지수·확정 수급만 미리 넣는다. 창 안 날의 값은 그날 15:35
  `market.close_collect` 대역이 넣는다(16:00 `rules.close` 는 그날까지만 본다 — 미래 값이
  저장소에 없다).
- 장중(`rules.intraday` — 2026-10-14 사용자 요청으로 켬, ADR 0031 §6)은 이 틀에 넣지 않는다 —
  장중 일치는 완료 시험(`tests/acceptance/test_rules_parity.py`)이 본다.
- 시계: `FakeClock` 하나. 08:00~09:00·15:30~16:30 KST 만 1분 보폭(그 밖에는 이 다섯 작업의
  발화가 없다).
- 발송: 가짜 notify(`kind`·접수 시각만 모은다 — 실제 텔레그램 없음).

값은 전부 합성이다(U3). 실제 네트워크 없음. 실주문 없음.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, Final

import yaml

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.time import KST
from kbj.data.catalog import all_datasets
from kbj.services.engine import rules as R
from kbj.services.engine import rules_perf as RP
from kbj.services.runtime.health import MemoryHealthSink
from kbj.services.scheduler.claims import MemoryClaimStore, MemoryRunLog, RunRecord
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.services.scheduler.registry import Registry
from kbj.services.scheduler.runner import JobRunner, RunEvent, inline_submit
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fakes.clock import FakeClock
from tests.fixtures.synthetic.p8_world import P8World, build_world
from tests.unit.services.rules_world import SPLIT_RATIO, add_rules

ROOT: Final = Path(__file__).resolve().parents[2]
WINDOW: Final = 25
JOBS: Final = ("market.close_collect", "krx.daily", "rules.close", "rules.outcome",
               "rules.backtest_nightly")  # fmt: skip
REAL: Final = ("rules.close", "rules.outcome", "rules.backtest_nightly")
CLOSE_RULES: Final = ("E1", "E2", "E3", "E4", "E5", "E6", "E7")
ACTIVE: Final = ((dtime(8, 0), dtime(9, 0)), (dtime(15, 30), dtime(16, 30)))


def rules_registry() -> Registry:
    """운영 등록부에서 다섯 줄만 — 전부 켠 사본(운영의 켬·끔과 무관하게 같은 시각·의존·as_of)."""
    data: dict[str, Any] = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text("utf-8"))
    jobs = [dict(j, enabled=True) for j in data["jobs"] if j["name"] in JOBS]
    assert sorted(j["name"] for j in jobs) == sorted(JOBS)
    return Registry.parse({**data, "jobs": jobs, "services": []})


@dataclass
class Sent:
    at: datetime  # 접수 시각(KST)
    kind: str
    as_of: Any


@dataclass
class RecordingNotify:
    clock: FakeClock
    sent: list[Sent] = field(default_factory=list[Sent])

    def __call__(self, text: str, **kw: Any) -> Any:
        from tests.unit.services.rules_world import Ticket

        self.sent.append(Sent(self.clock().astimezone(KST), str(kw.get("kind")), kw.get("as_of")))
        return Ticket()


@dataclass
class RulesDays:
    """여러 날 창 하나. `run()` 뒤 저장소·실행 기록·발송으로 본다."""

    world: P8World = field(default_factory=build_world)
    window: int = WINDOW
    cfg: RulesConfig = field(default_factory=load_rules_cfg)
    kr: TradingCalendar = field(default_factory=TradingCalendar.default)
    repos: MemoryRepos = field(init=False)
    clock: FakeClock = field(init=False)
    runs: MemoryRunLog = field(init=False)
    health: MemoryHealthSink = field(init=False)
    notify: RecordingNotify = field(init=False)
    events: list[RunEvent] = field(init=False, default_factory=list[RunEvent])
    calls: list[tuple[str, str, datetime]] = field(init=False, default_factory=list)
    # 아침(rules.outcome 뒤) 성과 행 수 — as_of → 행 수
    outcome_rows: dict[date, int] = field(init=False, default_factory=dict)
    rule_ids: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        w = self.world
        self.days = list(w.days[-self.window :])
        first = self.days[0]
        self.start = datetime.combine(first, dtime(7, 0), tzinfo=KST)
        self.end = datetime.combine(self.days[-1] + timedelta(days=3), dtime(9, 0), tzinfo=KST)
        self.clock = FakeClock(self.start)
        self.repos = memory_repos()
        self._seed_before(first)
        ids = add_rules(self.repos, {k: w.examples[k] for k in CLOSE_RULES}, now=self.start)
        self.rule_ids = dict(zip(CLOSE_RULES, ids, strict=True))
        self.runs = MemoryRunLog()
        self.health = MemoryHealthSink()
        self.notify = RecordingNotify(self.clock)
        handlers = {
            "kbj.services.engine.rules:close": self._wrap("rules.close", R.close),
            "kbj.services.engine.rules_perf:outcome": self._wrap("rules.outcome", self._outcome),
            "kbj.services.engine.rules_perf:nightly": self._wrap("rules.backtest_nightly",
                                                                 RP.nightly),
        }  # fmt: skip
        reg = rules_registry()
        for j in reg.jobs:
            if j.name not in REAL:
                handlers[j.owner] = self._wrap(j.name, self._material(j.name))
        self.runner = JobRunner(
            reg,
            handlers,
            MemoryClaimStore(),
            self.kr,
            us_calendar(),
            runs=self.runs,
            catalog=all_datasets(),
            health=self.health,
            submit=inline_submit,
            resources={
                "repos": self.repos,
                "kr": self.kr,
                "split_ratio": SPLIT_RATIO,
                "rules_config": self.cfg,
                "notify": self.notify,
            },
        )

    # ── 원장 ───────────────────────────────────────────────────────────────────────────────
    def _seed_before(self, first: date) -> None:
        w = self.world
        r = self.repos
        r.market.upsert_universe(list(w.universe), loaded_by="sim.seed")
        r.journal.replace_watchlist("main", list(w.watchlist), loaded_by="sim.seed", now=self.start)
        for t in w.trades:
            r.personal.add_trade_personal(t)
        self._publish(lambda d: d < first)

    def _publish(self, keep: Any) -> None:
        w = self.world
        r = self.repos
        r.market.upsert_daily_bars(
            [b for bs in w.bars.values() for b in bs if keep(b.date)], loaded_by="sim.close"
        )
        r.market.upsert_index_bars(
            [b for bs in w.index_bars.values() for b in bs if keep(b.date)], loaded_by="sim.close"
        )
        inv = [x for x in w.investor_days() if keep(x.date)]
        if inv:
            r.flows.upsert_investor_days(inv, revise=False, loaded_by="sim.close", now=self.clock())

    def _material(self, job: str) -> Any:
        def run(ctx: JobContext) -> JobResult:
            if job == "market.close_collect":
                d = date.fromisoformat(ctx.as_of[:10])
                self._publish(lambda x: x == d)  # 그날 마감 값(합성 — 확정과 같다)
            return JobResult("ok", tuple(ctx.keys), detail={"sim": True})

        return run

    def _outcome(self, ctx: JobContext) -> JobResult:
        res = RP.outcome(ctx)
        d = date.fromisoformat(ctx.as_of[:10])
        self.outcome_rows[d] = len(self.all_outcomes())
        return res

    def _wrap(self, name: str, fn: Any) -> Any:
        def run(ctx: JobContext) -> JobResult:
            self.calls.append((name, ctx.as_of, ctx.now.astimezone(KST)))
            res: JobResult = fn(ctx)
            return res

        return run

    # ── 시계 ───────────────────────────────────────────────────────────────────────────────
    def _ticks(self) -> Iterator[datetime]:
        d = self.start.date()
        while d <= self.end.date():
            for a, b in ACTIVE:
                t = datetime.combine(d, a, tzinfo=KST)
                stop = datetime.combine(d, b, tzinfo=KST)
                while t <= stop:
                    if self.start <= t <= self.end:
                        yield t
                    t += timedelta(minutes=1)
            d += timedelta(days=1)

    def run(self) -> RulesDays:
        for t in self._ticks():
            self.clock.set(t)
            self.events += self.runner.tick(t.astimezone(UTC))
        return self

    # ── 확인 ───────────────────────────────────────────────────────────────────────────────
    def records(self, job: str) -> list[RunRecord]:
        return sorted((r for r in self.runs.records.values() if r.job == job),
                      key=lambda r: r.as_of)  # fmt: skip

    def all_signals(self) -> list[Any]:
        return self.repos.rules.signals(self.days[0], self.days[-1], mode="close")

    def all_outcomes(self) -> list[Any]:
        sigs = self.repos.rules.signals(self.world.days[0], self.days[-1])
        return self.repos.rules.outcomes([s.signal_id for s in sigs])

    def trading_index(self, d: date) -> int:
        return self.world.days.index(d)
