"""`filings.dart_feed` 처리기 — 가짜 DART(합성 20종목 세계) + 메모리 저장소 + 메모리 선점 장부(묶음
F).

docs/p4_design.md §1.3·§4.1~§4.4·§8.2: 증분 멈춤, 07:00 다시 훑기(하루 한 번), 정정 공시는 새 행,
같은 분 두 실행에 선점 중복 0, 한 건 실패 격리(3회면 na), 020 이면 그날 중단·`failed` 사유, 전송
오류는 시도 수를 쓰지 않는다, 결정 공시 구조화 + 원문 보충, 조정·청구의 대상 잇기(옛 결정은 구조화로
찾아 넣는다), 소유보고 회사 전체에서 새 접수번호만 + 원문 변동 사유. 선점 키는
`MemoryClaimStore`(설계 §1.3 끝 — 자원 `claims`).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from kbj.config.fin import load_fin
from kbj.core.fin_rows import Instrument, PrelimRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.public.dart.client import DartClient
from kbj.data.spec import DataKey
from kbj.services.collectors import dart_feed as F
from kbj.services.scheduler.claims import MemoryClaimStore
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import MemoryPublicRepos, PublicRepos, memory_public_repos
from tests.fakes.dart_server import KEY, DartWorld, FakeDart
from tests.fixtures.synthetic.p4_world import P4World, build_world
from tests.unit.data.public._support import RecordingLimiter

PRELIM_FIELDS = (
    "rcept_no", "corp_code", "rcept_dt", "scope", "fiscal_year", "fiscal_q", "is_annual",
    "period_end", "period_inferred", "unit_mult", "revenue_cur", "revenue_prev_q",
    "revenue_prev_y", "op_cur", "op_prev_q", "op_prev_y", "ni_cur", "ni_owner_cur",
    "yoy_reported", "quality",
)  # fmt: skip


@dataclass
class Patched:
    """세계 응답 일부를 바꾼 가짜 서버용 세계(나머지는 그대로)."""

    world: P4World
    bad_docs: set[str] = field(default_factory=set[str])
    empty_json: set[str] = field(default_factory=set[str])
    thin_docs: set[str] = field(default_factory=set[str])  # 정정 공시처럼 바뀐 칸(만기)만 있는 원문

    def dart_json(
        self, endpoint: str, params: Mapping[str, str], *, now: datetime
    ) -> dict[str, Any]:
        if endpoint in self.empty_json:
            return {"status": "013", "message": "조회된 데이타가 없습니다."}
        return self.world.dart_json(endpoint, params, now=now)

    def dart_bytes(
        self, endpoint: str, params: Mapping[str, str], *, now: datetime
    ) -> bytes | None:
        if endpoint == "document.xml" and params.get("rcept_no") in self.bad_docs:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("x.xml", "<html><body><p>표가 없는 원문</p></body></html>")
            return buf.getvalue()
        if endpoint == "document.xml" and params.get("rcept_no") in self.thin_docs:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("x.xml", "<table><tr><td>5. 사채만기일</td><td>2031년 11월 13일</td>"
                                     "</tr></table>")  # fmt: skip
            return buf.getvalue()
        return self.world.dart_bytes(endpoint, params, now=now)


class Day:
    """하루 운영 흉내 — 시각을 옮기며 처리기를 부른다."""

    def __init__(self, world: DartWorld | None = None, *, profiles: bool = True) -> None:
        self.w = build_world()
        self.now = datetime(2026, 11, 16, 7, 0, tzinfo=KST)
        self.fake = FakeDart(lambda: self.now, world=world or self.w)
        self.client = DartClient(
            KEY, transport=self.fake.transport("feed"), limiter=RecordingLimiter(), retries=1,
            backoff_s=0, now=lambda: self.now,
        )  # fmt: skip
        self.repos: MemoryPublicRepos = memory_public_repos()
        for c in self.w.corps:
            if c.stock_code:
                self.repos.filings.corp_codes[c.stock_code] = (c.corp_code, c.corp_name)
        if profiles:
            self.repos.filings.upsert_profiles(
                list(self.w.profiles), loaded_by="test", now=self.now
            )
        self.claims = MemoryClaimStore()
        self.cfg = load_fin()

    def ctx(self, as_of: str, keys: tuple[DataKey, ...] | None = None) -> JobContext:
        return JobContext(
            job=F.JOB,
            as_of=as_of,
            run_id=f"{F.JOB}:{as_of}:1",
            attempt=1,
            now=self.now,
            keys=keys if keys is not None else (DataKey("DART", "list", as_of),),
            resources={
                "public_repos": self.repos,
                "claims": self.claims,
                "dart": self.client,
                "fin_config": self.cfg,
            },
        )

    def at(self, day: date, hhmm: str) -> JobResult:
        h, m = (int(x) for x in hhmm.split(":"))
        self.now = datetime(day.year, day.month, day.day, h, m, tzinfo=KST)
        return F.run(self.ctx(f"{day.isoformat()}T{hhmm}"))

    def today(self, hhmm: str) -> JobResult:
        return self.at(date(2026, 11, 16), hhmm)

    def row(self, ident: str) -> Any:
        return self.repos.filings.disclosure(self.w.rcept(ident))


TIMES = ("07:00", "09:13", "10:41", "11:21", "13:31", "14:11", "14:46", "15:21", "15:36",
         "16:06", "16:21", "19:59")  # fmt: skip


def test_rescan_runs_once_per_day_then_increments_stop_at_seen() -> None:
    d = Day()
    first = d.today("07:00")
    assert first.status == "ok" and first.collected == (
        DataKey("DART", "list", "2026-11-16T07:00"),
    )
    rescan = first.detail["rescan"]
    assert rescan["complete"] and rescan["pages"] == 2 and rescan["new"] > 140  # 11-13 금 140건 + α
    key = DataKey("DART", "list", "2026-11-13~2026-11-15")
    live = d.claims.live(key)
    assert live is not None and live.status == "done"
    calls = len(d.fake.calls("list.json"))
    second = d.today("07:01")
    assert "rescan" not in second.detail and second.detail["pages"] == 1
    assert len(d.fake.calls("list.json")) == calls + 1
    # 다시 훑기 범위 — 전 거래일(금)부터 어제(일)까지
    p = d.fake.calls("list.json")[0].params
    assert (p["bgn_de"], p["end_de"]) == ("20261113", "20261115")


def test_no_rescan_before_rescan_at() -> None:
    d = Day()
    got = d.today("06:59")
    assert got.status == "ok" and "rescan" not in got.detail


def test_a_full_day_parses_every_world_event_once() -> None:
    d = Day()
    for t in TIMES:
        r = d.today(t)
        assert r.status == "ok", (t, r.detail)
    f = d.repos.filings
    # 잠정실적 — 그날 5건, 참값과 칸마다 같다
    today = {p.rcept_no: p for p in d.w.prelims if p.rcept_dt == date(2026, 11, 16)}
    day = date(2026, 11, 16)
    stored: dict[str, PrelimRow] = {r.rcept_no: r for r in f.prelims(start=day, end=day)}
    assert set(stored) == set(today) and len(stored) == 5
    for rc, row in stored.items():
        for name in PRELIM_FIELDS:
            assert getattr(row, name) == getattr(today[rc], name), (rc, name)
    # 정정 공시는 새 행 — 원 공시 행과 따로
    fix = d.row("e15.prelim_fix")
    assert fix.is_correction and fix.base_title == "연결재무제표기준영업(잠정)실적(공정공시)"
    assert not d.row("e15.prelim").is_correction
    # 결산실적공시예고 — 표 대신 parse_note
    pre = d.row("e1.preann")
    assert (pre.kind, pre.parse_state) == ("pre_announce", "done")
    assert pre.parse_note.startswith("planned=2027-01-28;time=16:00")
    # 실적등에대한전망 — 잠정실적이 아니다(파싱 안 함)
    outlook = d.row("e1.outlook")
    assert (outlook.kind, outlook.parse_state) == ("earnings", "none")
    # 비상장 잡음 공시 — 파싱 대상 아님
    noise = [r for r in f.disclosures(start=date(2026, 11, 16), end=date(2026, 11, 16))
             if r.stock_code is None]  # fmt: skip
    assert noise and all(r.parse_state == "none" for r in noise)
    # 조정·행사 — 대상 결정 공시로 이어졌다(옛 결정은 구조화 API 로 찾아 넣었다)
    truth = {a.rcept_no: a for a in d.w.overhang_adjusts}
    for ident in ("e14.adj2", "e14.bwex"):
        a = f.adjust_t.get((d.w.rcept(ident),))
        assert a is not None and a.quality is Quality.OK
        t = truth[a.rcept_no]
        assert (a.target_rcept_no, a.kind, a.new_price, a.amount, a.shares, a.effective_date) == (
            t.target_rcept_no, t.kind, t.new_price, t.amount, t.shares, t.effective_date,
        )  # fmt: skip
    events = {e.rcept_no for e in f.overhang_events(d.w.by_no(14).company_corp)}
    assert events == {d.w.rcept(i) for i in ("e14.cb1", "e14.cb2", "e14.cb3", "e14.bw1")}
    # 선점: 접수번호 키마다 done 한 번, 거절 0
    done = [k for k in d.claims.done_keys() if k.dataset != "list"]
    assert len(done) == len(set(done)) == 9  # 잠정 5·예고 1·조정 1·행사 1·소유보고 1
    assert d.claims.refused == []
    assert all(r.parse_state in ("none", "done") for r in f.disclosure_t.all())


def test_same_minute_twice_fetches_nothing_new() -> None:
    d = Day()
    d.today("14:46")
    calls = len(d.fake.seen)
    again = F.run(d.ctx("2026-11-16T14:46"))
    assert again.status == "ok" and again.detail["new"] == 0 and again.detail["parsed"] == 0
    assert [s.endpoint for s in d.fake.seen[calls:]] == ["list.json"]  # 1쪽만 — 원문 다시 안 받음
    assert d.claims.refused == []


def test_insider_history_is_stored_once_with_reason_only_for_the_new_report() -> None:
    d = Day()
    d.today("11:21")
    f = d.repos.filings
    rows = {r.rcept_no: r for r in f.insider_reports(d.w.by_no(15).company_corp)}
    truth = {r.rcept_no: r for r in d.w.insider_reports}
    assert set(rows) == set(truth)  # 회사 보고 전체(과거 5건 + 오늘 1건)
    new = d.w.rcept("e15.ins_ceo2")
    assert rows[new] == truth[new]  # 원문 변동 사유까지
    assert all(rows[rc].reason is None for rc in rows if rc != new)  # 과거 행은 사유 모름
    assert d.row("e15.ins_ceo2").parse_state == "done"
    assert len(d.fake.calls("elestock.json")) == 1


def test_paid_in_decision_is_supplemented_from_the_document() -> None:
    d = Day()
    r = d.at(date(2026, 6, 10), "16:01")
    assert r.status == "ok"
    ev = d.repos.filings.event_t.get((d.w.rcept("e14.paid"),))
    t = next(e for e in d.w.overhang_events if e.instrument is Instrument.PAID_IN)
    assert ev is not None
    assert (ev.price, ev.listing_date, ev.shares, ev.alloc_method, ev.parse_source) == (
        t.price, t.listing_date, t.shares, t.alloc_method, "api",
    )  # fmt: skip
    assert ev.notes == ("원문 보충: listing_date·price",) and ev.quality is Quality.OK
    assert len(d.fake.calls("document.xml")) == 1


def test_decision_absent_from_the_structured_reply_is_read_from_the_document() -> None:
    d = Day(Patched(build_world(), empty_json={"cvbdIsDecsn.json"}))
    d.at(date(2026, 9, 1), "16:01")
    ev = d.repos.filings.event_t.get((d.w.rcept("e14.cb3"),))
    t = next(e for e in d.w.overhang_events if e.rcept_no == d.w.rcept("e14.cb3"))
    assert ev is not None and ev.parse_source == "document"
    for name in ("face_value", "price", "shares", "exercise_start", "exercise_end", "maturity",
                 "refix_floor", "seq", "alloc_method"):  # fmt: skip
        assert getattr(ev, name) == getattr(t, name), name


def test_decision_absent_from_structured_with_thin_document_waits_instead_of_blank_row() -> None:
    """구조화 응답은 늦게 붙는다 — 정정 공시처럼 원문에 필수 칸이 없으면 빈 행을 쓰지 않고 다시
    시도한다(실측 2026-10-20: 빈 오버행 행 21/23). 마지막 시도에만 원문으로 읽은 만큼 쓴다."""
    w = build_world()
    rc = w.rcept("e14.cb3")
    d = Day(Patched(w, empty_json={"cvbdIsDecsn.json"}, thin_docs={rc}))
    d.at(date(2026, 9, 1), "16:01")
    assert d.repos.filings.event_t.get((rc,)) is None
    row = d.repos.filings.disclosure(rc)
    assert row is not None and (row.parse_state, row.parse_tries) == ("failed", 1)
    assert "반영 지연" in (row.parse_note or "")
    # 마지막 시도(6번째) — 원문으로 읽은 만큼(만기) 쓰고 끝낸다
    stored = d.repos.filings.disclosure_t.rows[(rc,)]
    stored.row = replace(stored.row, parse_tries=5)
    d.at(date(2026, 9, 4), "16:01")
    ev = d.repos.filings.event_t.get((rc,))
    assert ev is not None and ev.parse_source == "document" and ev.face_value is None
    assert ev.maturity == date(2031, 11, 13)
    done = d.repos.filings.disclosure(rc)
    assert done is not None and done.parse_state == "done"


def test_buyback_decision_on_its_day() -> None:
    d = Day()
    d.at(date(2026, 8, 20), "16:31")
    (b,) = d.repos.filings.buybacks(d.w.by_no(1).company_corp)
    t = d.w.buybacks[0]
    assert (b.rcept_no, b.kind, b.shares_planned, b.amount_planned, b.start, b.end) == (
        t.rcept_no, t.kind, t.shares_planned, t.amount_planned, t.start, t.end,
    )  # fmt: skip


def test_unlinked_adjust_is_stored_invalid_with_a_reason() -> None:
    d = Day(Patched(build_world(), empty_json={"cvbdIsDecsn.json"}))
    d.today("10:41")
    a = d.repos.filings.adjust_t.get((d.w.rcept("e14.adj2"),))
    assert a is not None and a.quality is Quality.INVALID and a.target_rcept_no is None
    assert a.note is not None and "cb 회차 2" in a.note
    assert d.row("e14.adj2").parse_state == "done"  # 행은 남는다(연결 실패는 품질로)


def test_one_bad_document_fails_alone_and_retries_with_waits_then_na() -> None:
    """DART 원문·구조화 응답은 늦게 붙는다 — 실패한 건은 처음 본 뒤 10분·1시간·6시간·1일·2일에 다시
    시도하고(config/fin.yaml retry_wait_min), 6번째 실패에 na. 매분 다시 부르지 않는다."""
    w = build_world()
    bad = w.rcept("e18.prelim")
    d = Day(Patched(w, bad_docs={bad}))
    r = d.today("14:46")
    assert r.status == "ok" and r.detail["failed"] == 1
    row = d.row("e18.prelim")
    assert (row.parse_state, row.parse_tries) == ("failed", 1)
    assert "ParseError" in (row.parse_note or "")
    live = d.claims.live(DataKey("DART", "document", bad))
    assert live is None  # 실패한 키는 다시 잡을 수 있다
    assert d.today("14:47").detail["failed"] == 0  # 10분 전 — 아직 다시 부르지 않는다
    assert d.row("e18.prelim").parse_tries == 1
    first = row.first_seen_at
    tries = [(10, 2), (60, 3), (360, 4), (1440, 5)]
    for wait, want in tries:
        t = first + timedelta(minutes=wait)
        d.at(t.date(), f"{t:%H:%M}")
        assert d.row("e18.prelim").parse_tries == want
    d.today("15:21")  # 다른 공시는 계속 처리된다
    assert d.row("e11.sub_prelim").parse_state == "done"
    t = first + timedelta(minutes=2880)
    assert d.at(t.date(), f"{t:%H:%M}").detail["na"] == 1
    row = d.row("e18.prelim")
    assert (row.parse_state, row.parse_tries) == ("na", 6)
    assert str(row.parse_note).startswith("6회 실패")
    t += timedelta(minutes=1)
    assert d.at(t.date(), f"{t:%H:%M}").detail["failed"] == 0  # na 는 더 시도하지 않는다


def test_quota_020_closes_the_day_with_a_reason_and_keeps_pending() -> None:
    d = Day()
    d.fake.inject("020", 1, endpoint="document.xml")
    r = d.today("14:46")
    assert r.status == "failed" and r.collected == ()
    assert "한도" in r.detail["reason"] and r.detail["new"] > 0  # 목록은 넣었다
    waiting = d.repos.filings.pending(kinds=F.PARSE_KINDS)
    assert len(waiting) == 5 and all(x.parse_tries == 0 for x in waiting)  # 시도 수를 쓰지 않는다
    assert r.detail["parsed"] == 0  # 첫 원문(소유보고 사유)에서 멈췄다
    assert all(k.status != "claimed" for k in d.claims.rows)  # 잡은 키는 풀었다(실패)
    # 다음 실행 — 이 시험의 클라이언트에는 예산 저장소가 없어 다시 시도된다(운영은 예산이 닫혀 그날
    # 멈춘다)
    assert d.today("14:47").detail["parsed"] == 5


def test_transient_error_stops_the_run_without_burning_tries() -> None:
    d = Day()
    d.fake.inject("800", 1, endpoint="document.xml")
    r = d.today("14:46")
    assert r.status == "failed" and "전송·점검" in r.detail["reason"]
    assert d.row("e18.prelim").parse_tries == 0
    ok = d.today("14:47")
    assert ok.status == "ok" and d.row("e18.prelim").parse_state == "done"


def test_unknown_fiscal_month_is_estimated() -> None:
    d = Day(profiles=False)
    d.today("14:46")
    (p,) = d.repos.filings.prelims(start=date(2026, 11, 16), end=date(2026, 11, 16))
    assert p.quality is Quality.ESTIMATED and any("결산월 모름" in n for n in p.notes)


def test_run_once_keys_are_returned_as_collected() -> None:
    d = Day()
    as_of = "2026-11-16T14:46"
    keys = (DataKey("DART", "list", as_of), DataKey("DART", "document", as_of),
            DataKey("DART", "elestock", as_of))  # fmt: skip
    d.now = datetime(2026, 11, 16, 14, 46, tzinfo=KST)
    got = F.run(d.ctx(as_of, keys))
    assert got.status == "ok" and got.collected == keys


def test_missing_claims_resource_is_an_error() -> None:
    d = Day()
    ctx = d.ctx("2026-11-16T07:00")
    bare = JobContext(ctx.job, ctx.as_of, ctx.run_id, 1, ctx.now, ctx.keys,
                      resources={"public_repos": d.repos, "dart": d.client})  # fmt: skip
    with pytest.raises(KeyError, match="claims"):
        F.run(bare)


@pytest.mark.parametrize(
    ("kind", "title", "want"),
    [
        ("prelim", "영업(잠정)실적(공정공시)", ("document",)),
        ("prelim", "결산실적공시예고", ()),
        ("pre_announce", "결산실적공시예고", ("document",)),
        ("overhang", "주요사항보고서(유무상증자결정)", ("piicDecsn", "fricDecsn")),
        ("buyback", "주요사항보고서(자기주식처분결정)", ("tsstkDpDecsn",)),
        ("buyback", "자기주식취득신탁계약체결결정", ()),
        ("insider", "임원ㆍ주요주주특정증권등소유상황보고서", ("elestock",)),
        ("major_holder", "주식등의대량보유상황보고서(일반)", ("majorstock",)),
        ("periodic", "분기보고서 (2026.09)", ()),
        ("other", "기타시장안내", ()),
        ("price_adjust", "전환가액의조정 (제9회차)", ("document",)),
        ("overhang_exercise", "전환청구권행사", ("document",)),
        ("price_adjust", "전환주식의전환가액조정 (전환우선주)", ()),  # 사채가 아니다
        ("overhang_exercise", "전환주식의전환청구권행사 (상환전환우선주)", ()),
    ],
)
def test_datasets_for(kind: str, title: str, want: tuple[str, ...]) -> None:
    assert F.datasets_for(kind, title) == want


def test_every_claimed_dataset_is_collected_by_the_feed_job() -> None:
    """처리기가 직접 잡는 데이터셋은 등록부 filings.dart_feed 의 collects(event)에 있어야 한다."""
    from kbj.config.files import config_path
    from kbj.services.scheduler.registry import Registry

    job = Registry.load(config_path("jobs.yaml")).by_name(F.JOB)
    declared = {c.dataset for c in job.collects}
    used = {"list", "document", "elestock", "majorstock", *F.structured.ENDPOINT_OF.values()}
    assert used <= declared
    tables = set(job.writes)
    assert {"pub_filings.disclosure", "pub_filings.earnings_prelim", "pub_filings.overhang_event",
            "pub_filings.overhang_adjust", "pub_filings.insider_report",
            "pub_filings.major_holder_report", "pub_filings.buyback_event"} <= tables  # fmt: skip


# ── 검증 보강(묶음 F 검증 — 격리·동시 실행·다시 훑기 실패·분기말을 넘긴 정정) ─────────────────


def test_unexpected_error_in_one_parse_fails_only_that_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """해석기의 예상 밖 예외(ValueError 계열이 아닌 것)도 그 건만 failed — 같은 실행의 다른 공시는
    계속 처리되고(격리 — 절대 규칙 4), 실행은 ok, 사유는 parse_note 에 남는다(삼키지 않음)."""
    from kbj.data.public.dart import prelim_doc

    real = prelim_doc.parse
    calls = {"n": 0}

    def flaky(html: str) -> prelim_doc.PrelimParse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("합성 해석기 버그")
        return real(html)

    monkeypatch.setattr(prelim_doc, "parse", flaky)
    d = Day()
    r = d.today("14:46")
    assert r.status == "ok" and r.detail["failed"] == 1 and r.detail["parsed"] >= 1
    rows = [x for x in d.repos.filings.disclosure_t.all() if x.kind == "prelim"
            and x.parse_state != "none"]  # fmt: skip
    bad = [x for x in rows if x.parse_state == "failed"]
    assert len(bad) == 1 and "RuntimeError" in (bad[0].parse_note or "")
    assert all(x.parse_state == "done" for x in rows if x is not bad[0])
    assert all(k.status != "claimed" for k in d.claims.rows)  # 잡은 키는 실패로 놓았다


def test_a_disclosure_held_by_a_still_running_earlier_minute_is_not_taken() -> None:
    """실행기는 as_of 가 다른 실행을 동시에 돌린다 — 앞 분 실행이 쥔 접수번호 키(같은 작업이라
    claim 은 True 를 준다)를 다음 분 실행이 빼앗아 두 번 받지 않는다. 오래된 선점(죽은 실행)은 이어
    잡는다."""
    w = build_world()
    rc = w.rcept("e18.prelim")
    d = Day(w)
    key = DataKey("DART", "document", rc)
    earlier = f"{F.JOB}:2026-11-16T14:45:1"
    assert d.claims.claim(key, F.JOB, earlier, datetime(2026, 11, 16, 14, 45, tzinfo=KST))
    r = d.today("14:46")
    assert r.status == "ok" and r.detail["claim_skipped"] == 1
    live = d.claims.live(key)
    assert live is not None and (live.status, live.run_id) == ("claimed", earlier)
    assert d.row("e18.prelim").parse_state == "pending"
    assert d.today("14:50").detail.get("claim_skipped") == 1  # 아직 10분 안
    later = d.today("14:56")  # 11분 — 죽은 실행의 선점으로 보고 이어 잡는다
    assert later.status == "ok" and d.row("e18.prelim").parse_state == "done"
    live = d.claims.live(key)
    assert live is not None and live.status == "done"


def test_rescan_failure_does_not_stop_the_live_feed() -> None:
    """07:00 다시 훑기의 실패는 그 실행을 실패(사유)로 남기되 그날 증분·파싱은 돈다. 키는 실패로
    놓여 다음 분 실행이 다시 훑는다."""
    d = Day()
    d.fake.inject("800", 1, endpoint="list.json")  # 첫 list.json = 다시 훑기
    r = d.at(date(2026, 11, 16), "07:00")
    assert r.status == "failed" and "다시 훑기" in r.detail["reason"]
    assert "rescan" not in r.detail and r.detail["pages"] == 1  # 증분은 읽었다
    key = DataKey("DART", "list", "2026-11-13~2026-11-15")
    assert d.claims.live(key) is None  # 실패로 놓았다
    again = d.today("07:01")
    assert again.status == "ok" and again.detail["rescan"]["complete"]
    live = d.claims.live(key)
    assert live is not None and live.status == "done"


def test_correction_after_the_quarter_end_keeps_the_original_period() -> None:
    """2분기 잠정실적을 10월에 정정하면 접수일 추정은 3분기를 가리킨다 — 원 공시(같은 회사·범위의
    앞 잠정실적)의 기간을 따른다(알림 대상 `회사:기간:범위` 가 갈라져 두 번 울리지 않게). 92일보다
    오래된 잠정실적은 원 공시로 보지 않는다."""
    from dataclasses import replace

    from kbj.core.calendar import TradingCalendar
    from kbj.data.public.dart.disclosures import Disclosure

    d = Day()
    truth = next(p for p in d.w.prelims if p.period_inferred)
    orig = replace(truth, rcept_no="20260810900001", rcept_dt=date(2026, 8, 10), fiscal_year=2026,
                   fiscal_q=2, is_annual=False, period_end=date(2026, 6, 30))  # fmt: skip
    d.repos.filings.put_prelims([orig], loaded_by="test", now=d.now)
    fix = replace(orig, rcept_no="20261005900001", rcept_dt=date(2026, 10, 5), fiscal_q=3,
                  period_end=date(2026, 9, 30), notes=())  # fmt: skip
    disc = F.to_row(
        Disclosure(
            corp_code=fix.corp_code, corp_name="가상", stock_code="990010", corp_cls="Y",
            title="[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", rcept_no=fix.rcept_no,
            filer=None, date="2026-10-05", remark="",
        ),
        now=d.now, prefixes=d.cfg.filings.correction_prefixes,
    )  # fmt: skip
    assert disc.is_correction
    repos = PublicRepos(
        filings=d.repos.filings,
        fin=d.repos.fin,
        macro=d.repos.macro,
        market_stats=d.repos.market_stats,
        diag=d.repos.diag,
    )  # P5 FND 가 공개 묶음에 macro·market_stats 를, P7 FND-B 가 diag 를 더했다
    env = F._Env(repos, d.claims, d.client, d.cfg, TradingCalendar.default(), d.now, "t")  # pyright: ignore[reportPrivateUsage]
    got = F._period_of_original(env, disc, fix)  # pyright: ignore[reportPrivateUsage]
    assert (got.fiscal_year, got.fiscal_q, got.period_end) == (2026, 2, date(2026, 6, 30))
    assert any("원 공시(20260810900001)" in n for n in got.notes)
    # 원 공시가 92일보다 오래됐으면(다른 분기의 실적일 가능성) 접수일 추정 그대로
    late = replace(fix, rcept_no="20261120900001", rcept_dt=date(2026, 11, 20))
    late_disc = replace(disc, rcept_no=late.rcept_no, rcept_dt=late.rcept_dt)
    kept = F._period_of_original(env, late_disc, late)  # pyright: ignore[reportPrivateUsage]
    assert (kept.fiscal_q, kept.period_end) == (3, date(2026, 9, 30))


def test_incremental_cut_at_max_pages_reads_the_rest_of_the_day_in_the_same_run() -> None:
    """장애 뒤 첫 실행처럼 그날 목록이 `max_pages` 를 넘으면 같은 실행에서 그날 목록 전체를 이어
    읽는다 — 다음 실행의 증분은 1쪽에서 멈추므로 끊긴 뒤쪽(그날 앞선 접수분)이 다음 날까지 빠지지
    않게(설계 §4.1 '다음 분에 이어서')."""
    d = Day()
    feed = d.cfg.filings.feed.model_copy(update={"max_pages": 1})
    d.cfg = d.cfg.model_copy(update={"filings": d.cfg.filings.model_copy(update={"feed": feed})})
    r = d.today("19:59")
    assert r.status == "ok", r.detail
    day = date(2026, 11, 16)
    want = {x.rcept_no for x in d.w.disclosures if x.rcept_dt == day}
    got = {x.rcept_no for x in d.repos.filings.disclosures(start=day, end=day)}
    assert len(want) > 100 and got == want  # 1쪽(100건)보다 많아도 빠짐 없다
    assert r.detail["complete"] and any("이어 읽었다" in w for w in r.detail["warnings"])
    again = d.today("20:00")
    assert again.detail["new"] == 0 and again.detail["pages"] == 1
