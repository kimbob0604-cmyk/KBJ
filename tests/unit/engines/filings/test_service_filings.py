"""`filings.derive` 서비스(kbj.services.engine.filings) — 메모리 저장소로 합성 세계의 하루.

docs/p4_design.md §1.4·§3.2·§4.1~§4.4: 공개 파생(오버행 상태·요약, 내부자 창, 실적 일정, 키워드
점수)을 `pub_filings.*` 에 미리 쓴다. 훑기(그날 첫 실행·밤 마무리)와 증분(최근에 본 공시의 회사),
같은 as_of 재실행 멱등, 회사 하나의 실패 격리, 처리기 자원 모양을 본다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.fin import load_fin
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.spec import DataKey
from kbj.services.engine import filings as svc
from kbj.services.scheduler.handlers import JobContext
from tests.unit.engines.filings.filings_world import load, world

CFG = load_fin()
KR = TradingCalendar.default()
EXPECT = Path(__file__).resolve().parents[4] / "tests/fixtures/synthetic/p4_expect.yaml"
HAND: dict[str, Any] = yaml.safe_load(EXPECT.read_text(encoding="utf-8"))["hand"]


def _kst(h: int, m: int, d: date | None = None) -> datetime:
    day = d or world().today
    return datetime(day.year, day.month, day.day, h, m, tzinfo=KST)


def test_night_sweep_writes_overhang_insider_schedule_and_scores() -> None:
    w = world()
    lw = load()
    f = lw.repos.filings
    rep = svc.run_derive(lw.repos.public(), w.today, now=w.clock, cfg=CFG, calendar=KR)
    assert rep.errors == {} and rep.sweep is True
    # 오버행 #14 = 손계산
    c14 = lw.corp(14)
    s = f.overhang_summary(c14, w.today)
    hand = HAND["overhang_994130"]
    assert s is not None
    assert (s.potential_new_shares, s.exercisable_shares, s.denominator) == (
        hand["potential_new_shares"], hand["exercisable_shares"], hand["denominator"],
    )  # fmt: skip
    assert s.ratio_pct == pytest.approx(hand["ratio_pct"], abs=5e-5)
    assert len(f.overhang_state(c14, w.today)) == 5
    # 내부자 #15 = 손계산(정정은 늦은 접수번호만 — 공시 행의 정정 표시로 짝을 찾았다)
    c15 = lw.corp(15)
    got = {x.days: x for x in f.insider_windows(c15, w.today)}
    for days, exp in HAND["insider_994140"].items():
        g = got[int(days)]
        assert (g.net_shares, g.buy_shares, g.sell_shares, g.n_reports, g.n_reporters) == (
            exp["net_shares"], exp["buy_shares"], exp["sell_shares"], exp["n_reports"],
            exp["n_reporters"],
        )  # fmt: skip
        assert g.ratio_pct == pytest.approx(exp["ratio_pct"], abs=5e-5)
    # 실적 일정 — #1 의 결산실적공시예고 행 = 세계 참값, 3분기는 이미 냈다(11-13) → 법정 기한 없음
    rows = f.schedule(start=date(2026, 1, 1), end=date(2027, 12, 31), corp_code=lw.corp(1))
    assert [r for r in rows if r.kind == "pre_announce"] == list(w.schedule)
    stat = [r for r in rows if r.kind == "statutory"]
    assert [(r.period_end, r.planned_date) for r in stat] == [
        (date(2026, 12, 31), date(2027, 3, 31))
    ]
    # 키워드 점수 — 최근 7일 공시는 모두, 그 전은 손대지 않는다
    recent = f.disclosures(start=date(2026, 11, 9), end=w.today)
    assert recent and all(d.kw_score is not None for d in recent)
    halt = f.disclosure(w.rcept("e4.halt"))  # 11-06 — 7일 밖
    assert halt is not None and halt.kw_score is None
    split = f.disclosure(w.rcept("e20.delist"))  # 10-30 — 밖
    assert split is not None and split.kw_score is None
    cb_adj = f.disclosure(w.rcept("e14.adj2"))
    assert cb_adj is not None and cb_adj.kw_score == 0  # '전환가액의조정' — 표에 없음
    bwex = f.disclosure(w.rcept("e14.bwex"))
    assert bwex is not None and bwex.kw_score == 8 and bwex.kw_matched == ("신주인수권",)


def test_stale_score_is_rescored_when_keyword_table_changes() -> None:
    """키워드 표를 바꾸면(config/fin.yaml) 최근 기간의 옛 점수도 다음 실행에 새 점수로 바뀐다."""
    w = world()
    lw = load()
    pub = lw.repos.public()
    svc.run_derive(pub, w.today, now=w.clock, cfg=CFG, calendar=KR)
    rc = w.rcept("e14.bwex")
    stored = lw.repos.filings.disclosure_t.rows[(rc,)]
    stored.row = replace(stored.row, kw_score=0, kw_matched=())  # 옛 표로 매긴 점수
    again = svc.run_derive(pub, w.today, now=w.clock, cfg=CFG, calendar=KR)
    assert again.n_scored == 1  # 그 한 줄만 — 같은 점수인 줄은 다시 쓰지 않는다
    bwex = lw.repos.filings.disclosure(rc)
    assert bwex is not None and bwex.kw_score == 8 and bwex.kw_matched == ("신주인수권",)


def test_same_as_of_rerun_is_idempotent() -> None:
    w = world()
    lw = load()
    pub = lw.repos.public()
    first = svc.run_derive(pub, w.today, now=w.clock, cfg=CFG, calendar=KR)
    snap = (
        dict(lw.repos.filings.state_t.rows),
        dict(lw.repos.filings.summary_t.rows),
        dict(lw.repos.filings.window_t.rows),
        {k: v.row for k, v in lw.repos.filings.schedule_t.rows.items()},
    )
    again = svc.run_derive(pub, w.today, now=w.clock, cfg=CFG, calendar=KR)
    assert again.errors == {} and again.n_scored == 0  # 점수는 한 번만
    assert again.n_overhang == first.n_overhang and again.n_insider == first.n_insider
    assert set(lw.repos.filings.state_t.rows) == set(snap[0])
    assert set(lw.repos.filings.summary_t.rows) == set(snap[1])
    assert set(lw.repos.filings.window_t.rows) == set(snap[2])
    assert {k: v.row for k, v in lw.repos.filings.schedule_t.rows.items()} == snap[3]


def test_incremental_run_touches_only_recently_seen_companies() -> None:
    w = world()
    now = _kst(10, 45)  # e14.adj2(10:40) 까지 접수 — #15 의 오늘 보고(11:20)는 아직
    lw = load(now)
    rep = svc.run_derive(lw.repos.public(), w.today, now=now, cfg=CFG, calendar=KR, sweep=False)
    assert rep.errors == {} and rep.sweep is False
    f = lw.repos.filings
    assert f.overhang_summary(lw.corp(14), w.today) is not None  # 10:40 리픽싱 — 바뀐 회사
    assert f.insider_windows(lw.corp(15)) == []  # 바뀌지 않았다 — 훑기 때 쓴다
    assert f.overhang_summaries(w.today).keys() == {lw.corp(14)}


def test_should_sweep_first_run_of_the_day_and_night() -> None:
    w = world()
    lw = load()
    f = lw.repos.filings
    assert svc.should_sweep(f, w.today, _kst(7, 10)) is True  # 그날 파생 행이 없다
    svc.run_derive(lw.repos.public(), w.today, now=_kst(7, 10), cfg=CFG, calendar=KR)
    assert svc.should_sweep(f, w.today, _kst(10, 10)) is False
    assert svc.should_sweep(f, w.today, _kst(21, 10)) is True  # 밤 마무리
    assert svc.should_sweep(f, date(2026, 11, 17), _kst(7, 10, date(2026, 11, 17))) is True


def test_unparsed_claim_makes_the_state_an_estimated_upper_bound() -> None:
    w = world()
    lw = load()
    f = lw.repos.filings
    bwex = f.disclosure(w.rcept("e14.bwex"))
    assert bwex is not None
    f.mark_parsed(bwex.rcept_no, "failed", note="표를 못 읽었다(시험)", now=w.clock)
    svc.run_derive(lw.repos.public(), w.today, now=w.clock, cfg=CFG, calendar=KR)
    bw = next(s for s in f.overhang_state(lw.corp(14), w.today) if s.rcept_no == w.rcept("e14.bw1"))
    assert bw.quality is Quality.ESTIMATED and any("청구분 미반영" in n for n in bw.notes)
    cb1 = next(
        s for s in f.overhang_state(lw.corp(14), w.today) if s.rcept_no == w.rcept("e14.cb1")
    )
    assert cb1.quality is Quality.OK  # 신주인수권 행사 — CB 에는 걸리지 않는다
    summary = f.overhang_summary(lw.corp(14), w.today)
    assert summary is not None and summary.quality is Quality.ESTIMATED


def test_unparsed_insider_report_marks_windows_estimated() -> None:
    w = world()
    lw = load()
    f = lw.repos.filings
    f.mark_parsed(w.rcept("e15.ins_ceo2"), "failed", note="응답에 없다(시험)", now=w.clock)
    svc.run_derive(lw.repos.public(), w.today, now=w.clock, cfg=CFG, calendar=KR)
    assert all(x.quality is Quality.ESTIMATED for x in f.insider_windows(lw.corp(15), w.today))


def test_one_company_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    w = world()
    lw = load()
    f = lw.repos.filings
    real = f.overhang_adjusts
    bad = lw.corp(14)

    def boom(corp: str) -> Any:
        if corp == bad:
            raise RuntimeError("디스크가 가득 찼다(시험)")
        return real(corp)

    monkeypatch.setattr(f, "overhang_adjusts", boom)
    rep = svc.run_derive(lw.repos.public(), w.today, now=w.clock, cfg=CFG, calendar=KR)
    assert (
        set(rep.errors) == {f"overhang:{bad}"} and "RuntimeError" in rep.errors[f"overhang:{bad}"]
    )
    assert f.overhang_summary(bad, w.today) is None
    assert f.insider_windows(lw.corp(15), w.today)  # 다른 파생은 썼다
    assert rep.detail()["n_errors"] == 1


def test_schedule_replaces_old_rows_of_unlisted_and_writes_statutory_for_missing_reports() -> None:
    w = world()
    lw = load()
    f = lw.repos.filings
    svc.run_derive(lw.repos.public(), w.today, now=w.clock, cfg=CFG, calendar=KR)
    # #9(재무 미제출·관리종목) — 3분기 보고서가 없어 법정 기한 행이 있다(기한 = 11-16, 오늘)
    rows9 = f.schedule(start=date(2026, 1, 1), end=date(2027, 12, 31), corp_code=lw.corp(9))
    assert any(r.period_end == date(2026, 9, 30) and r.kind == "statutory" for r in rows9)
    # 상장폐지된 회사의 옛 일정은 다음 훑기에서 지워진다
    prof = lw.repos.filings.profiles([lw.corp(20)])[lw.corp(20)]
    f.upsert_profiles([replace(prof, stock_code=None)], loaded_by="t", now=w.clock)
    for k in [k for k, v in f.disclosure_t.rows.items() if v.row.corp_code == lw.corp(20)]:
        del f.disclosure_t.rows[k]
    svc.run_derive(lw.repos.public(), w.today, now=w.clock, cfg=CFG, calendar=KR)
    assert f.schedule(start=date(2026, 1, 1), end=date(2027, 12, 31), corp_code=lw.corp(20)) == []


# ── 처리기 ─────────────────────────────────────────────────────────────────────────────


def _ctx(
    resources: dict[str, Any], *, as_of: str = "2026-11-16T21:40", keys: tuple[DataKey, ...] = ()
) -> JobContext:
    return JobContext(
        job="filings.derive",
        as_of=as_of,
        run_id="r1",
        attempt=1,
        now=world().clock,
        keys=keys,
        resources=resources,
    )


def test_handler_with_public_repos_resource() -> None:
    lw = load()
    key = DataKey("DART", "derive", "2026-11-16T21:40")
    res = svc.derive(_ctx({"public_repos": lw.repos.public(), "fin_config": CFG, "kr": KR},
                          keys=(key,)))  # fmt: skip
    assert res.status == "ok" and res.collected == (key,)
    assert res.detail["sweep"] is True and res.detail["as_of"] == "2026-11-16"
    assert res.rows and res.rows > 0
    assert lw.repos.filings.overhang_summary(lw.corp(14), date(2026, 11, 16)) is not None


def test_handler_falls_back_to_repos_public_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lw = load()

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("저장소 오류(시험)")

    monkeypatch.setattr(lw.repos.filings, "put_insider_windows", broken)
    res = svc.derive(_ctx({"repos": lw.repos, "fin_config": CFG, "kr": KR}))
    assert res.status == "failed" and res.collected == ()
    assert res.detail["n_errors"] >= 1
    assert all(k.startswith("insider:") for k in res.detail["errors"])


def test_handler_without_any_repos_needs_connect() -> None:
    with pytest.raises(KeyError, match="connect"):
        svc.derive(_ctx({"fin_config": CFG, "kr": KR}))
