"""`earnings.alerts`·`earnings.backfill` 서비스(kbj.services.engine.earnings).

메모리 저장소·가짜 알림으로. docs/p4_design.md §1.4('같은 subject 두 번 → 한 번(정정), 관심종목
아님 → 기록만, 알림 본문에 계산값만')·§4.2·§8.5(관심종목 잠정실적 → alert.earnings 1통, 정정 1건
주입 → 여전히 1통, 관심 밖 → 0),
`tests/fixtures/synthetic/p4_expect.yaml alerts.alert.earnings`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
import yaml

from kbj.config.fin import load_fin
from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import PrelimScope, SurpriseRow
from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.core.time import KST
from kbj.data.spec import DataKey
from kbj.services.engine import earnings as svc
from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import load_notify_config
from kbj.services.scheduler.handlers import JobContext
from tests.fakes.clock import FakeClock
from tests.unit.engines.filings.filings_world import (
    FakeNotify,
    Loaded,
    disclosure_row,
    load,
    world,
)

CFG = load_fin()
KR = TradingCalendar.default()
EXPECT = Path(__file__).resolve().parents[4] / "tests/fixtures/synthetic/p4_expect.yaml"
ALERTS: dict[str, Any] = yaml.safe_load(EXPECT.read_text(encoding="utf-8"))["alerts"]


def _run(lw: Loaded, notify: Any, *, now: datetime | None = None) -> svc.AlertReport:
    w = lw.w
    return svc.run_alerts(lw.repos, w.today, now=now or w.clock, cfg=CFG, notify=notify)


def _rows(lw: Loaded) -> dict[str, SurpriseRow]:
    return {r.rcept_no: r for r in lw.repos.consensus.surprises()}


# ── earnings.alerts ────────────────────────────────────────────────────────────────────


def test_world_day_sends_exactly_the_expected_alert() -> None:
    w = world()
    lw = load()
    note = FakeNotify()
    rep = _run(lw, note)
    exp = ALERTS["alert.earnings"]
    assert rep.errors == {}
    assert len(note.calls) == exp["count"] == 1
    call = note.calls[0]
    assert call["subject"] == exp["subject"] == "99400140:2026Q3:consolidated"
    assert (call["kind"], call["source"], call["as_of"]) == (
        "alert.earnings", "earnings.alerts", w.today,
    )  # fmt: skip
    assert "YOY_BIG_UP" in call["text"] and "+52.5%" in call["text"]
    rows = _rows(lw)
    first = rows[w.rcept("e15.prelim")]
    assert (first.signal, first.priority, first.code) == ("YOY_BIG_UP", 1, "994140")
    assert first.alerted_at == w.clock and first.notify_ticket == "t1"
    assert first.op_yoy == pytest.approx(52.5, abs=0.05) and first.rev_surprise is None
    assert first.consensus_ref is None and first.source == "DART"
    # 정정공시 — 같은 subject, 다시 보내지 않고 기록만
    fix = rows[w.rcept("e15.prelim_fix")]
    assert fix.alerted_at is None and any("이미 넣음" in n for n in fix.notes)
    assert rep.already == 1 and rep.alerts == 1


def test_world_day_records_but_does_not_alert_the_others() -> None:
    w = world()
    lw = load()
    _run(lw, FakeNotify())
    rows = _rows(lw)
    two_col = rows[w.rcept("e18.prelim")]  # 99K170 2열 폼 — 전년동기 없음
    assert (two_col.signal, two_col.priority, two_col.alerted_at) == ("NO_DATA", 5, None)
    sub = rows[w.rcept("e11.sub_prelim")]  # 자회사 잠정 — 알림 대상 아님
    assert sub.scope is PrelimScope.SUBSIDIARY and sub.alerted_at is None
    other = rows[w.rcept("e16.prelim")]  # 994150 — 관심 밖
    assert other.code == "994150" and other.alerted_at is None
    late = rows[w.rcept("e8.prelim_26q3")]  # 10-28 — 처리 기간 안이지만 늦게 봤다
    assert late.alerted_at is None
    assert w.rcept("e1.prelim_26q3") not in rows  # 10-08 — 처리 기간(30일) 밖
    inferred = rows[w.rcept("e8.prelim_26q3")]  # 본문에 기간 표기가 없다 — 결산월로 추정
    assert inferred.quality is Quality.ESTIMATED


def test_second_run_has_nothing_to_do() -> None:
    lw = load()
    note = FakeNotify()
    _run(lw, note)
    again = _run(lw, note)
    assert again.processed == 0 and len(note.calls) == 1


def test_correction_arriving_in_a_later_run_does_not_ring_again() -> None:
    w = world()
    early = w.disclosure("e15.prelim").posted_at + timedelta(minutes=5)
    lw = load(early)
    note = FakeNotify()
    _run(lw, note, now=early)
    assert note.subjects == ["99400140:2026Q3:consolidated"]
    # 정정공시가 16:20 에 들어온다 — 같은 저장소에 더 싣고 다음 실행
    later = load(w.clock)
    lw.repos.filings.put_prelims(
        [later.repos.filings.prelim(w.rcept("e15.prelim_fix"))],  # type: ignore[list-item]
        loaded_by="t",
        now=w.clock,
    )
    fixd = later.repos.filings.disclosure(w.rcept("e15.prelim_fix"))
    assert fixd is not None
    lw.repos.filings.upsert_disclosures([fixd], loaded_by="t", now=w.clock)
    _run(lw, note)
    assert len(note.calls) == 1
    assert _rows(lw)[w.rcept("e15.prelim_fix")].alerted_at is None


def test_notify_failure_keeps_the_row_unprocessed_for_the_next_run() -> None:
    w = world()
    lw = load()
    bad = FakeNotify(fail=True)
    rep = _run(lw, bad)
    assert set(rep.errors) == {w.rcept("e15.prelim"), w.rcept("e15.prelim_fix")}
    assert "알림을 넣지 못했다" in rep.errors[w.rcept("e15.prelim")]
    rows = _rows(lw)
    assert w.rcept("e15.prelim") not in rows and w.rcept("e18.prelim") in rows  # 나머지는 기록
    good = FakeNotify()
    rep2 = _run(lw, good)
    assert rep2.errors == {} and good.subjects == ["99400140:2026Q3:consolidated"]


def test_queue_duplicate_is_recorded_as_alerted() -> None:
    lw = load()
    note = FakeNotify()
    note._subjects.add(("alert.earnings", "99400140:2026Q3:consolidated"))  # 앞 실행이 넣고 죽었다
    rep = _run(lw, note)
    first = _rows(lw)[lw.w.rcept("e15.prelim")]
    assert first.alerted_at is not None and any("중복 키" in n for n in first.notes)
    assert rep.duplicates == 1 and rep.alerts == 0


def test_invalid_prelim_is_recorded_invalid_and_not_alerted() -> None:
    w = world()
    lw = load()
    src = lw.repos.filings.prelim(w.rcept("e15.prelim"))
    assert src is not None
    lw.repos.filings.put_prelims(
        [replace(src, quality=Quality.INVALID)], loaded_by="t", now=w.clock
    )
    note = FakeNotify()
    rep = _run(lw, note)
    assert rep.errors == {}
    first = _rows(lw)[w.rcept("e15.prelim")]
    # invalid 는 estimated 로 올리지 않고, 그 숫자로 알림을 내보내지 않는다
    assert first.quality is Quality.INVALID and first.alerted_at is None
    assert any("invalid" in n for n in first.notes)
    # 같은 대상의 정상 정정공시는 알린다(정정 표시) — 한 통
    assert note.subjects == ["99400140:2026Q3:consolidated"]
    assert "정정공시" in note.calls[0]["text"]


def test_missing_notify_resource_fails_only_the_alert_rows() -> None:
    lw = load()
    rep = svc.run_alerts(lw.repos, lw.w.today, now=lw.w.clock, cfg=CFG, notify=None)
    assert any("notify" in v for v in rep.errors.values())
    assert lw.w.rcept("e18.prelim") in _rows(lw)


def test_watching_the_preferred_share_alerts_the_company() -> None:
    w = world()
    lw = load()
    # 관심종목을 #2(우선주 994015)만으로 — 회사(#1)의 잠정실적을 하나 만든다
    lw.repos.journal.replace_watchlist(
        "main", [x for x in w.watchlist if x.code == "994015"], loaded_by="t", now=w.clock
    )
    src = lw.repos.filings.prelim(w.rcept("e15.prelim"))
    d15 = lw.repos.filings.disclosure(w.rcept("e15.prelim"))
    assert src is not None and d15 is not None
    rc = "20261116999901"
    lw.repos.filings.put_prelims(
        [replace(src, rcept_no=rc, corp_code=lw.corp(1))], loaded_by="t", now=w.clock
    )
    lw.repos.filings.upsert_disclosures(
        [replace(d15, rcept_no=rc, corp_code=lw.corp(1), stock_code="994010", corp_name="모의")],
        loaded_by="t",
        now=w.clock,
    )
    note = FakeNotify()
    _run(lw, note)
    assert note.subjects == [f"{lw.corp(1)}:2026Q3:consolidated"]
    assert "모의(994010)" in note.calls[0]["text"]


def test_real_notifier_dedups_the_same_subject() -> None:
    """실제 알림 shim(설정 notify.yaml alert.earnings — dedup subject)으로: 기록을 잃고 다시 돌아도
    대기열에는 한 통."""
    w = world()
    clock = FakeClock(w.clock)
    r = fakeredis.FakeRedis()
    client = NotifyClient(load_notify_config(), False, outbox=Outbox(r, now=clock), now=clock)
    lw = load()
    rep = _run(lw, client.notify)
    assert rep.errors == {} and rep.alerts == 1
    lw2 = load()  # 기록을 잃었다(같은 대기열)
    rep2 = _run(lw2, client.notify)
    assert rep2.errors == {} and rep2.alerts == 0 and rep2.duplicates == 1
    # 두 번째는 거절 기록(메시지 없음)만 남는다 — 보낼 메시지는 한 통
    sent = [e for e in Outbox(r, now=clock).read("t", 50) if e.message is not None]
    assert len(sent) == 1 and sent[0].message is not None
    assert sent[0].message.subject == "99400140:2026Q3:consolidated"


# ── earnings.backfill ──────────────────────────────────────────────────────────────────


def _bar(code: str, d: date, close: float, volume: int = 1000) -> Bar:
    return Bar(code, d, close, close, close, close, volume, volume * int(close), "krx", "KRX",
               Quality.OK)  # fmt: skip


def _alerted(lw: Loaded, at: datetime) -> SurpriseRow:
    row = SurpriseRow(
        rcept_no="20261116000777", code="994140", corp_code=lw.corp(15), period="2026Q3",
        scope=PrelimScope.CONSOLIDATED, signal="YOY_BIG_UP", priority=1, alerted_at=at,
    )  # fmt: skip
    lw.repos.consensus.put_surprises([row], loaded_by="t", now=at)
    return row


def _bars(lw: Loaded, closes: dict[date, float | None], code: str = "994140") -> None:
    bars = []
    for d, c in closes.items():
        bars.append(_bar(code, d, c) if c is not None else _bar(code, d, 1.0, volume=0))
        bars.append(_bar("994010", d, 50_000.0))  # 다른 종목 — 그날 원장이 있다
    lw.repos.market.upsert_daily_bars(bars, loaded_by="t")


TD = [date(2026, 11, d) for d in (16, 17, 18, 19, 20, 23, 24, 25)]


def test_backfill_fills_3_5_7_trading_days_after_the_close_before_the_alert() -> None:
    lw = load(parts=())
    _alerted(lw, datetime(2026, 11, 16, 16, 25, tzinfo=KST))  # 마감 뒤 → 기준 11-16 종가
    _bars(lw, dict(zip(TD, [10_000.0, 10_100.0, 10_200.0, 10_300.0, 10_400.0, 10_500.0,
                             10_600.0, 10_700.0], strict=True)))  # fmt: skip
    # 11-20(목 → 기준 + 4거래일) 06:30 — 3일째(11-19)까지만 있다
    rep = svc.run_backfill(lw.repos, date(2026, 11, 20), now=datetime(2026, 11, 20, 6, 30,
                           tzinfo=KST), cfg=CFG, calendar=KR)  # fmt: skip
    assert rep.errors == {} and rep.through == date(2026, 11, 19)
    row = lw.repos.consensus.surprise("20261116000777")
    assert row is not None
    assert row.price_at_alert == 10_000.0 and row.perf_3d == pytest.approx(3.0)
    assert (row.perf_5d, row.perf_7d, row.perf_done) == (None, None, False)
    # 11-26 06:30 — 7일째(11-25)까지
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=datetime(2026, 11, 26, 6, 30, tzinfo=KST),
                     cfg=CFG, calendar=KR)  # fmt: skip
    done = lw.repos.consensus.surprise("20261116000777")
    assert done is not None and done.perf_done
    assert done.perf_5d == pytest.approx(5.0) and done.perf_7d == pytest.approx(7.0)
    assert lw.repos.consensus.perf_pending() == []


def test_backfill_intraday_alert_uses_previous_close() -> None:
    lw = load(parts=())
    _alerted(lw, datetime(2026, 11, 16, 11, 0, tzinfo=KST))  # 장중 → 기준 11-13(금) 종가
    _bars(lw, {date(2026, 11, 13): 9_000.0, **dict.fromkeys(TD, 9_900.0)})
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=datetime(2026, 11, 26, 6, 30, tzinfo=KST),
                     cfg=CFG, calendar=KR)  # fmt: skip
    row = lw.repos.consensus.surprise("20261116000777")
    assert row is not None and row.price_at_alert == 9_000.0
    assert row.perf_3d == pytest.approx(10.0) and row.perf_done


def test_suspended_day_is_none_not_invented() -> None:
    lw = load(parts=())
    _alerted(lw, datetime(2026, 11, 16, 16, 25, tzinfo=KST))
    closes: dict[date, float | None] = dict.fromkeys(TD, 10_000.0)
    closes[date(2026, 11, 19)] = None  # 3거래일째 정지(거래량 0 행)
    _bars(lw, closes)
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=datetime(2026, 11, 26, 6, 30, tzinfo=KST),
                     cfg=CFG, calendar=KR)  # fmt: skip
    row = lw.repos.consensus.surprise("20261116000777")
    assert row is not None and row.perf_3d is None and row.perf_5d == pytest.approx(0.0)
    assert row.perf_done


def test_missing_ledger_day_waits_instead_of_none() -> None:
    lw = load(parts=())
    _alerted(lw, datetime(2026, 11, 16, 16, 25, tzinfo=KST))
    _bars(lw, {d: 10_000.0 for d in TD[:3]})  # 11-18 까지만 원장이 있다(11-19 수집 전)
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=datetime(2026, 11, 26, 6, 30, tzinfo=KST),
                     cfg=CFG, calendar=KR)  # fmt: skip
    row = lw.repos.consensus.surprise("20261116000777")
    assert row is not None and row.perf_3d is None and not row.perf_done  # 기다린다


def test_backfill_without_code_finishes_with_none() -> None:
    lw = load(parts=())
    row = replace(_alerted(lw, datetime(2026, 11, 16, 16, 25, tzinfo=KST)), code=None)
    lw.repos.consensus.put_surprises([row], loaded_by="t", now=lw.w.clock)
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=datetime(2026, 11, 26, 6, 30, tzinfo=KST),
                     cfg=CFG, calendar=KR)  # fmt: skip
    got = lw.repos.consensus.surprise(row.rcept_no)
    assert got is not None and got.perf_done and got.perf_3d is None


# ── 처리기 ─────────────────────────────────────────────────────────────────────────────


def _ctx(resources: dict[str, Any], *, job: str, as_of: str, now: datetime,
         keys: tuple[DataKey, ...] = ()) -> JobContext:  # fmt: skip
    return JobContext(job=job, as_of=as_of, run_id="r1", attempt=1, now=now, keys=keys,
                      resources=resources)  # fmt: skip


def test_alerts_handler() -> None:
    w = world()
    lw = load()
    note = FakeNotify()
    key = DataKey("DART", "x", "2026-11-16T21:30")
    res = svc.alerts(_ctx({"repos": lw.repos, "notify": note, "fin_config": CFG, "kr": KR},
                          job="earnings.alerts", as_of="2026-11-16T21:30", now=w.clock,
                          keys=(key,)))  # fmt: skip
    assert res.status == "ok" and res.collected == (key,) and res.detail["alerts"] == 1
    bad = load()
    res2 = svc.alerts(
        _ctx(
            {"repos": bad.repos, "notify": FakeNotify(fail=True), "fin_config": CFG},
            job="earnings.alerts",
            as_of="2026-11-16T21:30",
            now=w.clock,
        )
    )
    assert res2.status == "failed" and res2.detail["n_errors"] == 2


def test_backfill_handler_trade_date_as_of() -> None:
    lw = load(parts=())
    _alerted(lw, datetime(2026, 11, 16, 16, 25, tzinfo=KST))
    _bars(lw, dict.fromkeys(TD, 10_000.0))
    res = svc.backfill(_ctx({"repos": lw.repos, "fin_config": CFG, "kr": KR},
                            job="earnings.backfill", as_of="2026-11-26",
                            now=datetime(2026, 11, 26, 6, 30, tzinfo=KST)))  # fmt: skip
    assert res.status == "ok" and res.detail["completed"] == 1 and res.rows == 1


def test_freshness_is_counted_in_trading_days() -> None:
    """금요일(11-13) 접수분을 월요일(11-16)에 처음 봐도 알린다 — 목요일(11-12) 것은 기록만."""
    w = world()
    lw = load(parts=("watch", "classes"))
    src = next(p for p in w.prelims if p.rcept_no == w.rcept("e15.prelim"))
    d15 = w.disclosure("e15.prelim")
    base = disclosure_row(d15)
    for no, day, rc in ((1, date(2026, 11, 13), "20261113999901"),
                        (3, date(2026, 11, 12), "20261112999901")):  # fmt: skip
        corp, code = lw.corp(no), w.by_no(no).code
        lw.repos.filings.put_prelims([replace(src, rcept_no=rc, corp_code=corp, rcept_dt=day)],
                                     loaded_by="t", now=w.clock)  # fmt: skip
        lw.repos.filings.upsert_disclosures(
            [replace(base, rcept_no=rc, corp_code=corp, stock_code=code, rcept_dt=day)],
            loaded_by="t",
            now=w.clock,
        )
    note = FakeNotify()
    rep = svc.run_alerts(lw.repos, w.today, now=w.clock, cfg=CFG, notify=note, calendar=KR)
    assert rep.errors == {} and note.subjects == [f"{lw.corp(1)}:2026Q3:consolidated"]
    stale = lw.repos.consensus.surprise("20261112999901")
    assert stale is not None and stale.alerted_at is None
    assert any("늦게 처리" in n for n in stale.notes)


def test_missing_base_day_ledger_waits_and_suspended_base_finishes_none() -> None:
    lw = load(parts=())
    _alerted(lw, datetime(2026, 11, 16, 16, 25, tzinfo=KST))
    _bars(lw, dict.fromkeys(TD[1:], 10_000.0))  # 기준일(11-16) 원장이 아직 없다
    now = datetime(2026, 11, 26, 6, 30, tzinfo=KST)
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=now, cfg=CFG, calendar=KR)
    row = lw.repos.consensus.surprise("20261116000777")
    assert row is not None and row.price_at_alert is None and not row.perf_done
    _bars(lw, {TD[0]: None})  # 기준일 행이 왔다 — 그 종목은 정지(거래량 0)
    svc.run_backfill(lw.repos, date(2026, 11, 26), now=now, cfg=CFG, calendar=KR)
    done = lw.repos.consensus.surprise("20261116000777")
    assert done is not None and done.perf_done
    assert (done.price_at_alert, done.perf_3d, done.perf_7d) == (None, None, None)
