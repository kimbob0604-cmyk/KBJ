"""합성 세계(`p4_world`)를 메모리 공개 저장소에 올리는 시험 도우미 — 재무 수집·리포트 작업 시험
공용.

`dart_feed`(묶음 F)가 할 일(공시 목록·기업개황·고유번호 표)을 세계의 참값으로 미리 넣는다 — 이
시험들은 재무·리포트 작업만 본다. DART 응답은 가짜 전송층(`WorldDart` — 시각 고정)으로.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from kbj.config.fin import FinConfig, ReportsConfig, load_fin, load_reports
from kbj.core.fin_rows import DisclosureRow
from kbj.data.public.dart.client import DartClient
from kbj.services.scheduler.claims import MemoryClaimStore
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.journal import MemoryJournalRepo
from kbj.store.repos.public import MemoryPublicRepos, memory_public_repos
from tests.fixtures.synthetic.p4_world import CLOCK, P4World, build_world
from tests.golden.dart_report.golden import WorldDart


def disclosure_row(d: Any) -> DisclosureRow:
    title: str = d.title
    base = title.split("]", 1)[1] if title.startswith("[") else title
    return DisclosureRow(
        rcept_no=d.rcept_no, corp_code=d.corp_code, stock_code=d.stock_code,
        corp_name=d.corp_name, corp_cls=d.corp_cls, title=title, base_title=base,
        filer=d.filer, rcept_dt=d.rcept_dt, remark=d.rm or None,
        kind="periodic" if d.kind == "periodic" else "other",
        is_correction=title.startswith("["), first_seen_at=d.posted_at,
    )  # fmt: skip


@dataclass
class WorldState:
    world: P4World
    now: datetime
    repos: MemoryPublicRepos
    journal: MemoryJournalRepo
    claims: MemoryClaimStore
    dart: WorldDart
    client: DartClient
    fin_cfg: FinConfig
    reports_cfg: ReportsConfig
    detail: set[str] = field(default_factory=set[str])
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

    def ctx(self, job: str, *, run_id: str = "run-1", now: datetime | None = None,
            keys: tuple[Any, ...] = ()) -> JobContext:  # fmt: skip
        when = now or self.now
        res: dict[str, Any] = {
            "public_repos": self.repos, "dart": self.client, "claims": self.claims,
            "fin_config": self.fin_cfg, "reports_config": self.reports_cfg,
            "journal": self.journal,
            "detail_universe": lambda day: sorted(self.detail),
            **self.extra,
        }  # fmt: skip
        return JobContext(job=job, as_of=when.strftime("%Y-%m-%dT%H:%M"), run_id=run_id,
                          attempt=1, now=when, keys=keys, resources=res)  # fmt: skip


def make_state(
    *,
    now: datetime = CLOCK,
    upto: date | None = None,
    corps: Iterable[str] | None = None,
    detail: Iterable[str] | None = None,
    transform: Callable[[DisclosureRow], DisclosureRow] | None = None,
) -> WorldState:
    """세계 공시(정기보고서 등 — now 까지 접수)·기업개황·고유번호 표를 넣은 메모리 상태."""
    w = build_world()
    repos = memory_public_repos()
    keep = set(corps) if corps is not None else None
    rows = [
        disclosure_row(d)
        for d in w.disclosures
        if d.posted_at <= now
        and (upto is None or d.rcept_dt <= upto)
        and (keep is None or d.corp_code in keep)
    ]
    if transform is not None:
        rows = [transform(r) for r in rows]
    repos.filings.upsert_disclosures(rows, loaded_by="test", now=now)
    repos.filings.upsert_profiles(list(w.profiles), loaded_by="test", now=now)
    repos.filings.corp_codes.update(
        {c.stock_code: (c.corp_code, c.corp_name) for c in w.corps if c.stock_code}
    )
    journal = MemoryJournalRepo()
    journal.replace_watchlist("main", list(w.watchlist), loaded_by="test", now=now)
    dart = WorldDart(w, now)
    watch = {s.code for s in w.stocks if s.watch} if detail is None else set(detail)
    return WorldState(
        world=w, now=now, repos=repos, journal=journal, claims=MemoryClaimStore(), dart=dart,
        client=dart.client(DartClient), fin_cfg=load_fin(), reports_cfg=load_reports(),
        detail=watch,
    )  # fmt: skip
