"""규칙 작업 시험 공용 — 합성 P8 세계(tests/fixtures/synthetic/p8_world.py)를
메모리 저장소·가짜 KIS·
가짜 notify 에 싣는다(묶음 EVL). 합성 데이터만."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import IntradayInvestor, Investor
from kbj.data.spec import DataKey
from kbj.engines.rules.dsl import Rule
from kbj.services.personal.rules import create_rule
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import MemoryRepos
from tests.fakes.kis_server import FakeKisServer
from tests.fixtures.synthetic.p8_world import P8World, build_world
from tests.unit.collectors.p3_fakes import Clock, DirectKis

KR = TradingCalendar.default()
SPLIT_RATIO = 0.31  # config/board.yaml integrity.split_guard_ratio 와 같은 값(시험이 대조)


@dataclass(frozen=True)
class Ticket:
    ok: bool = True
    id: str | None = "t1"
    reason: str = "queued"
    duplicate: bool = False


@dataclass
class FakeNotify:
    """`notify(text, kind=…, source=…, …)` — 넣은 것을 모으고 정한 결과를 돌려준다."""

    result: Ticket = field(default_factory=Ticket)
    raise_exc: Exception | None = None
    sent: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def __call__(self, text: str, **kw: Any) -> Ticket:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.sent.append({"text": text, **kw})
        return self.result


def world() -> P8World:
    return build_world()


def repos_with_world(w: P8World | None = None) -> MemoryRepos:
    r = MemoryRepos()
    (w or world()).fill(r)
    return r


def add_rules(
    repos: MemoryRepos,
    rules: dict[str, Rule] | list[dict[str, Any]],
    *,
    cfg: RulesConfig | None = None,
    now: datetime | None = None,
) -> list[str]:
    c = cfg or load_rules_cfg()
    t = now or world().clock
    items = (
        [r.model_dump(mode="json", exclude_none=True) for r in rules.values()]
        if isinstance(rules, dict)
        else rules
    )
    out: list[str] = []
    for d in items:
        res = create_rule(
            repos, d, enabled=True, source="api", now=t, loaded_by="test", cfg=c, seed=_next_seed
        )
        out.append(res.rule_id)
    return out


_SEED = [0]


def _next_seed() -> bytes:
    _SEED[0] += 1
    return (7_000_000 + _SEED[0] * 7919).to_bytes(10, "big")


def put_live_flows(repos: MemoryRepos, w: P8World, ts: datetime) -> int:
    """그 슬롯의 장중 가집계(세계 값)를 flows.intraday 가 쓰듯 이력 표에 넣는다."""
    rows: list[IntradayInvestor] = []
    for (code, t), r in sorted(w.live.items()):
        if t != ts:
            continue
        for who, v in ((Investor.FOREIGN, r.foreign), (Investor.INSTITUTION, r.inst)):
            if v is not None:
                rows.append(
                    IntradayInvestor(
                        code, ts, who, "KRX", v, None, None, "kis.prelim", Quality.ESTIMATED
                    )
                )
    repos.flows.put_intraday(rows, loaded_by="flows.intraday", received_at=ts)
    return len(rows)


def slot_as_of(ts: datetime) -> str:
    return f"{ts:%Y-%m-%dT%H:%M}"


def job_ctx(job: str, as_of: str, *, now: datetime, repos: MemoryRepos, **res: Any) -> JobContext:
    resources: dict[str, Any] = {"repos": repos, "kr": KR, "split_ratio": SPLIT_RATIO, **res}
    keys: tuple[DataKey, ...] = ()
    if job == "rules.intraday":
        keys = (DataKey("KIS", "watch_quotes_intraday", as_of),)
    return JobContext(
        job=job, as_of=as_of, run_id="r1", attempt=1, now=now, keys=keys, resources=resources
    )


def kis_for(w: P8World, clock: Clock) -> tuple[FakeKisServer, DirectKis]:
    server = FakeKisServer(clock, quotes=w)
    return server, DirectKis(server)


def day_at(w: P8World, k: int) -> date:
    return w.days[k]
