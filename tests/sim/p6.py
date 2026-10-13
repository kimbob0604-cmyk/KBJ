"""P6 수출 속보일 시뮬레이션 틀 — 실제 실행기 + 실제 관세청 수집 처리기 + 가짜 관세청(합성).

근거: docs/p6_design.md §0.3 ①~⑥·§1.6 3(WRAP). `SimDay`(하루 전체 서비스)는 무겁고 관세청 작업만
보면 되므로, 같은 부품(`JobRunner`·운영 등록부 `config/jobs.yaml`·`MemoryClaimStore`·`MemoryRunLog`)
으로 **등록부의 `trade.customs_tenday` 한 줄만** 돌린다(운영에서 꺼진 작업 — 이 사본에서만 켠다,
키 수령·실측 #6·#17·#55 뒤 운영에서 켠다).

- 시계: `FakeClock` 하나(가짜 관세청의 공표 시각·실행기·처리기가 같이 본다). 1분 보폭 — cron
  `*/20 9-17` 발화를 하나도 빠뜨리지 않는다.
- 가짜 관세청: `tests/fakes/customs_world.CustomsWorld`(p6_world 합성) — 10일 구간은 다음 날
  10:47 에 열린다(`delay_ten` 으로 바꾼다), `change_columns(kind)` 로 열 구성 변경을 넣는다.
- 창 앞 이력: 창 시작 전에 공표된 10일 구간은 저장소에 미리 넣는다(전년 같은 구간 — YoY 분모).
- 확인 경로(실제 코드): 로그인 API `/api/trade/flash`·`/api/market/ribbon`(메모리 저장소로 앱),
  공개 내보내기 `build_trade_flash`(메모리 읽기 — `public.export` 11:30 회차와 같은 함수),
  16:00 마감 요약 줄 `closing_brief.export_flash_line`.

값은 전부 합성이다(U3). 실제 네트워크 없음.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import SecretStr

from kbj.config.trade import HsChapters, load_trade
from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.trade_rows import TenDayRow
from kbj.data.budget import DailyBudget
from kbj.data.catalog import all_datasets
from kbj.data.datago import DatagoTransport
from kbj.data.public.customs.client import DATASET_IDS, CustomsClient
from kbj.reports.briefs.closing_brief import export_flash_line
from kbj.services.collectors import customs
from kbj.services.public_export.manifest import BuildOut
from kbj.services.public_export.trade_flash_json import build_trade_flash
from kbj.services.runtime.health import MemoryHealthSink
from kbj.services.scheduler.claims import MemoryClaimStore, MemoryRunLog, RunRecord
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.services.scheduler.registry import Registry
from kbj.services.scheduler.runner import JobRunner, RunEvent, inline_submit
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fakes.clock import FakeClock
from tests.fakes.customs_world import CustomsWorld
from tests.fakes.datago_server import KEY, FakeDatago
from tests.unit.api.api_world import FakeClock as ApiClock
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld, memory_flash_reader
from tests.unit.reports.briefs.brief_world import ctx as brief_ctx

ROOT: Final = Path(__file__).resolve().parents[2]
JOB: Final = "trade.customs_tenday"
OWNER: Final = "kbj.services.collectors.customs:tenday"


class _NoLimit:
    """공공데이터포털 초당 리미터 대신(호출 수는 `DailyBudget` 이 센다)."""

    def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.0


def tenday_registry() -> Registry:
    """운영 등록부에서 `trade.customs_tenday` 한 줄만 — 이 사본에서만 켠다(운영은 꺼짐)."""
    data: dict[str, Any] = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text("utf-8"))
    jobs = [dict(j, enabled=True) for j in data["jobs"] if j["name"] == JOB]
    assert len(jobs) == 1, JOB
    return Registry.parse({**data, "jobs": jobs})


@dataclass
class FlashDay:
    """속보일 창 하나. `run_until` 뒤 `calls`·`events`·저장소·확인 함수로 본다."""

    start: datetime
    world: CustomsWorld = field(default_factory=CustomsWorld)
    clock: FakeClock = field(init=False)
    repos: MemoryRepos = field(init=False)
    portal: FakeDatago = field(init=False)
    runs: MemoryRunLog = field(init=False)
    health: MemoryHealthSink = field(init=False)
    events: list[RunEvent] = field(init=False, default_factory=list)
    calls: list[datetime] = field(init=False, default_factory=list)  # 처리기를 부른 시각(KST)
    results: list[tuple[datetime, JobResult]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.clock = FakeClock(self.start)
        self.portal = FakeDatago(self.clock, customs=self.world)
        t = DatagoTransport(
            SecretStr(KEY),
            limiters={d: _NoLimit() for d in DATASET_IDS},
            budgets={d: DailyBudget(None, "datago", 9500, scope=d) for d in DATASET_IDS},
            transport=self.portal.transport("scheduler"),
            retry_waits_s=(),
            now=self.clock,
            sleep=lambda _: None,
            monotonic=self.clock.monotonic,
        )
        self.repos = memory_repos()
        self._seed()
        self.runs = MemoryRunLog()
        self.health = MemoryHealthSink()
        resources: dict[str, Any] = {
            "trade_repo": self.repos.trade,
            "customs": CustomsClient(t),
            "trade_config": load_trade(),
            "trade_maps": [self.world.trade_map()],
            "trade_regions": self.world.trade_regions(),
            "hs_chapters": HsChapters(version=1),
        }
        self.runner = JobRunner(
            tenday_registry(),
            {OWNER: self._handler},
            MemoryClaimStore(),
            TradingCalendar.default(),
            us_calendar(),
            runs=self.runs,
            catalog=all_datasets(),
            health=self.health,
            submit=inline_submit,
            resources=resources,
        )
        self._now = self.start.astimezone(UTC)

    def _seed(self) -> None:
        """창 시작 전에 공표된 구간만(그 뒤 정정은 아직 모른다 — 발표 당시 값으로)."""
        rows: list[TenDayRow] = []
        for r in self.world.world.ten_day:
            pub = self.world.ten_publish_at(r.ym, r.seg)
            if pub >= self.start:
                continue
            if r.revised_at is not None and r.revised_at >= self.start:
                r = replace(r, usd=r.first_usd, revised_at=None)
            rows.append(replace(r, first_seen_at=pub, as_of=pub))
        cols = self.world.world.ten_day_columns
        self.repos.trade.upsert_ten_day_columns(cols, loaded_by="sim.seed")
        self.repos.trade.upsert_ten_day(rows, loaded_by="sim.seed", received_at=self.start)

    def _handler(self, ctx: JobContext) -> JobResult:
        self.calls.append(ctx.now.astimezone(self.start.tzinfo))
        res = customs.tenday(ctx)
        self.results.append((ctx.now.astimezone(self.start.tzinfo), res))
        return res

    def run_until(self, end: datetime) -> FlashDay:
        end_u = end.astimezone(UTC)
        while self._now <= end_u:
            self.clock.set(self._now)
            self.events += self.runner.tick(self._now)
            self._now += timedelta(minutes=1)
        return self

    # ── 확인 ─────────────────────────────────────────────────────────────────────────────

    def records(self, as_of: str) -> list[RunRecord]:
        return [r for r in self.runs.records.values() if r.job == JOB and r.as_of == as_of]

    def api(self, at: datetime, path: str) -> dict[str, Any]:
        c = FlashApiWorld(None, self.repos, ApiClock(at)).authed()
        r = c.get(path)
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        return body

    def public(self, at: datetime) -> BuildOut:
        """`public.export` 회차가 이 시각에 만드는 trade/flash.json·trade/ribbon.json."""
        return build_trade_flash(memory_flash_reader(self.repos.trade), now=at)

    def closing_line(self, at: datetime) -> str | None:
        got = export_flash_line(brief_ctx(self.repos, at))
        return None if got is None else got[0]

    def segment_rows(self, kind: str, ym: str, seg: int) -> list[TenDayRow]:
        return [
            r for r in self.repos.trade.ten_day(kind=kind, ym_from=ym, ym_to=ym) if r.seg == seg
        ]
