"""공시 엔진·작업 시험의 공용 도구 — 합성 20종목 세계(`p4_world`)를 메모리 저장소에 싣는다.

수집 처리기(F)를 돌리지 않고 세계의 참값 행(공시 목록·잠정실적·오버행 사건·소유보고·주식총수·
기업개황·관심종목·종류주식)을 바로 넣는다 — 파생(E)만 시험하려고. 공시 행의 `kind` 는 수집기와 같은
`feed_kind`, 정정 표시는 `feed.is_correction` 으로 만든다(같은 규칙 하나).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import DisclosureRow
from kbj.data.public.dart.disclosures import feed_kind
from kbj.data.public.dart.feed import base_title, is_correction
from kbj.data.public.dart.prelim_doc import PreAnnounce, encode_pre_announce
from kbj.services.collectors.dart_feed import PARSE_KINDS
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.p4_world import P4World, WorldDisclosure, build_world

LOADED_BY = "test.world"

# 세계의 결산실적공시예고(e1.preann) 본문 — 수집기가 parse_note 에 남기는 모양 그대로
PRE_ANNOUNCE = PreAnnounce(date(2027, 1, 28), "16:00", "2026년 연간 및 4분기 잠정실적")


@functools.cache
def world() -> P4World:
    return build_world()


@functools.cache
def krx() -> TradingCalendar:
    return TradingCalendar.default()


def disclosure_row(
    d: WorldDisclosure,
    *,
    parse_state: str | None = None,
    first_seen_at: datetime | None = None,
) -> DisclosureRow:
    """세계 공시 → `pub_filings.disclosure` 행(수집기 `dart_feed.to_row` 와 같은 칸 규칙)."""
    kind = feed_kind(d.title)
    parse = d.stock_code is not None and kind in PARSE_KINDS
    note = encode_pre_announce(PRE_ANNOUNCE) if kind == "pre_announce" else None
    return DisclosureRow(
        rcept_no=d.rcept_no,
        corp_code=d.corp_code,
        stock_code=d.stock_code,
        corp_name=d.corp_name,
        corp_cls=d.corp_cls,
        title=d.title,
        base_title=base_title(d.title),
        filer=d.filer,
        rcept_dt=d.rcept_dt,
        remark=d.rm or None,
        kind=kind,
        is_correction=is_correction(d.title),
        first_seen_at=first_seen_at or d.posted_at,
        parse_state=parse_state or ("done" if parse else "none"),
        parse_note=note,
    )


@dataclass
class Loaded:
    """실은 세계와 저장소."""

    w: P4World
    repos: MemoryRepos = field(default_factory=MemoryRepos)

    def corp(self, no: int) -> str:
        return self.w.by_no(no).company_corp


def load(
    upto: datetime | None = None,
    *,
    parts: Iterable[str] = (
        "disclosures",
        "profiles",
        "prelims",
        "overhang",
        "ownership",
        "shares",
        "watch",
        "classes",
    ),
    w: P4World | None = None,
) -> Loaded:
    """세계를 메모리 저장소에 싣는다. `upto`(aware) 를 주면 그 시각까지 접수된 공시·행만(PIT)."""
    w = w or world()
    out = Loaded(w)
    r = out.repos
    f = r.filings
    now = upto or w.clock
    cut = now.date()
    seen = {d.rcept_no for d in w.disclosures if upto is None or d.posted_at <= upto}

    def ok(rcept_no: str) -> bool:
        return rcept_no in seen

    p = set(parts)
    f.corp_codes.update(
        {c.stock_code: (c.corp_code, c.corp_name) for c in w.corps if c.stock_code is not None}
    )
    if "disclosures" in p:
        f.upsert_disclosures(
            [disclosure_row(d) for d in w.disclosures if ok(d.rcept_no)],
            loaded_by=LOADED_BY,
            now=now,
        )
    if "profiles" in p:
        f.upsert_profiles(list(w.profiles), loaded_by=LOADED_BY, now=now)
    if "prelims" in p:
        f.put_prelims([x for x in w.prelims if ok(x.rcept_no)], loaded_by=LOADED_BY, now=now)
    if "overhang" in p:
        f.put_overhang_events(
            [e for e in w.overhang_events if ok(e.rcept_no)], loaded_by=LOADED_BY, now=now
        )
        f.put_overhang_adjusts(
            [a for a in w.overhang_adjusts if ok(a.rcept_no)], loaded_by=LOADED_BY, now=now
        )
    if "ownership" in p:
        f.put_insider_reports(
            [x for x in w.insider_reports if ok(x.rcept_no)], loaded_by=LOADED_BY, now=now
        )
        f.put_major_holder_reports(
            [x for x in w.major_reports if ok(x.rcept_no)], loaded_by=LOADED_BY, now=now
        )
    if "shares" in p:
        f.put_share_counts(
            [s for s in w.share_counts if s.rcept_dt is None or s.rcept_dt <= cut],
            loaded_by=LOADED_BY,
            now=now,
        )
    if "watch" in p:
        r.journal.replace_watchlist("main", list(w.watchlist), loaded_by=LOADED_BY, now=now)
    if "classes" in p:
        r.consensus.put_share_classes(list(w.share_classes), loaded_by=LOADED_BY, now=now)
    return out


class FakeNotify:
    """알림 shim 대역 — 받은 호출을 남기고, notifier 처럼 같은 subject 는 duplicate 로 돌려준다."""

    @dataclass(frozen=True)
    class Ticket:
        ok: bool
        id: str | None
        reason: str
        duplicate: bool = False

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self._subjects: set[tuple[str, str | None]] = set()

    def __call__(self, text: str, **kw: Any) -> FakeNotify.Ticket:
        self.calls.append({"text": text, **kw})
        if self.fail:
            return FakeNotify.Ticket(False, None, "대기열 접속 실패(시험)")
        key = (str(kw.get("kind")), kw.get("subject"))
        if key in self._subjects:
            return FakeNotify.Ticket(True, None, "중복", duplicate=True)
        self._subjects.add(key)
        return FakeNotify.Ticket(True, f"t{len(self.calls)}", "queued")

    @property
    def subjects(self) -> list[str | None]:
        return [c.get("subject") for c in self.calls]
