"""운영 점검 `check`·`week` — 항목별 경계값(가짜 원천·가짜 하트비트·시계 주입, ADR 0037)."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from kbj.config.files import REPO_ROOT
from kbj.core.calendar import TradingCalendar
from kbj.services.ops.__main__ import Deps, main
from kbj.services.ops.check import (
    GAP_LIMIT_S,
    job_outcomes,
    outages,
    run_check,
    run_week,
    service_states,
)
from kbj.services.ops.source import (
    ExpectError,
    GapEvent,
    MemoryOpsSource,
    load_expect,
    record_heartbeat_gaps,
)
from kbj.services.scheduler.registry import Registry
from tests.unit.ops.ops_world import (
    EXPECT,
    WEEK_END,
    WEEK_START,
    add_gap,
    brief_row,
    fresh_ages,
    kst,
    run_row,
    week_source,
)

CAL = TradingCalendar.default()
NOW = kst(date(2026, 10, 16), time(21, 0))  # 금요일 밤 — 아침·마감 브리핑 마감이 모두 지났다


# ── 기대값 — 등록부와 같은 값 ────────────────────────────────────────────────────────


def test_load_expect_matches_registry() -> None:
    path = REPO_ROOT / "config" / "jobs.yaml"
    exp = load_expect(path)
    reg = Registry.load(path)
    want_svc = {
        (s.name, s.beat_name, s.heartbeat_s)
        for s in reg.services
        if s.enabled and s.heartbeat_s is not None
    }
    assert {(s.name, s.beat, s.heartbeat_s) for s in exp.services} == want_svc
    assert exp.jobs == {j.name for j in reg.enabled_jobs()}
    assert all(exp.deadline_min[j.name] == j.deadline_min for j in reg.enabled_jobs())
    assert exp.brief_until["brief.morning"] == time(9, 0)
    assert exp.brief_until["brief.closing"] == time(20, 30)


def test_load_expect_errors(tmp_path: Path) -> None:
    with pytest.raises(ExpectError):
        load_expect(tmp_path / "없음.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ExpectError):
        load_expect(bad)


# ── 하트비트 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("age", "level"),
    [(60.0, "OK"), (61.0, "WARN"), (600.0, "WARN"), (601.0, "FAIL"), (None, "FAIL")],
)
def test_service_level_boundaries(age: float | None, level: str) -> None:
    ages = fresh_ages(NOW)
    if age is None:
        del ages.last["notifier"]
    else:
        ages.last["notifier"] = NOW - timedelta(seconds=age)
    states = {s.name: s.level for s in service_states(EXPECT, ages, NOW)}
    assert states["notifier"] == level
    assert states["auth"] == "OK"


def test_outages_group_and_limit() -> None:
    t0 = kst(date(2026, 10, 14), time(10, 0))
    src = MemoryOpsSource()
    add_gap(src, "notifier", t0, 10)  # 관측 나이 300·600초 — 정확히 10분은 넘지 않음
    add_gap(src, "auth", t0, 15)  # 300·600·900초 → 한 번의 끊김, 넘음
    outs = {o.service: o for o in outages(src.gaps)}
    assert len(outages(src.gaps)) == 2
    assert outs["notifier"].longest_s == GAP_LIMIT_S and not outs["notifier"].over_limit
    assert outs["auth"].longest_s == 900 and outs["auth"].over_limit
    assert outs["auth"].start == t0


def test_outages_separate_and_unknown_length() -> None:
    t0 = kst(date(2026, 10, 14), time(10, 0))
    evs = [
        GapEvent("auth", t0 + timedelta(minutes=5), 300.0),
        GapEvent("auth", t0 + timedelta(hours=2), 300.0),  # 다른 끊김
        GapEvent("notifier", t0, None),  # 하트비트가 아예 없음 — 길이 모름 → 넘은 것으로
    ]
    outs = outages(evs)
    assert [o.service for o in outs] == ["auth", "auth", "notifier"]
    assert [o.over_limit for o in outs] == [False, False, True]


def test_record_heartbeat_gaps_writes_only_stale() -> None:
    src = MemoryOpsSource()
    ages = fresh_ages(NOW, stale={"engine": 61.0})
    del ages.last["auth"]
    got = record_heartbeat_gaps(EXPECT.services, ages, NOW, src)
    assert {(g.service, g.age_s) for g in got} == {("gex.engine", 61.0), ("auth", None)}
    assert src.gaps == got


# ── 작업 ─────────────────────────────────────────────────────────────────────────────


def test_job_outcomes_final_attempt_and_retry() -> None:
    d = date(2026, 10, 14)
    runs = [
        run_row("board.daily", d, time(15, 45), "failed", 1),
        run_row("board.daily", d, time(15, 45), "ok", 2),
        run_row("brief.closing", d, time(16, 0), "failed", 1),
        run_row("brief.closing", d, time(16, 0), "timeout", 2),
        run_row("not.enabled", d, time(9, 0), "failed", 1),
    ]
    outs = {o.job: o for o in job_outcomes(runs, EXPECT.jobs)}
    assert set(outs) == {"board.daily", "brief.closing"}
    assert outs["board.daily"].retried_ok and not outs["board.daily"].final_failure
    assert outs["brief.closing"].final_failure and outs["brief.closing"].attempts == 2


# ── check ────────────────────────────────────────────────────────────────────────────


def _items(src: MemoryOpsSource, ages: object = None, now: datetime = NOW) -> dict[str, str]:
    r = run_check(src, EXPECT, CAL, ages or fresh_ages(now), now)  # pyright: ignore[reportArgumentType]
    return {i.key: i.level for i in r.items} | {"_": r.level}


def test_check_clean_week_is_ok() -> None:
    r = run_check(week_source(), EXPECT, CAL, fresh_ages(NOW), NOW, disk=lambda: (50, 100))
    assert r.level == "OK" and r.exit_code == 0
    assert [t.table for t in r.tables][:2] == ["tick_raw", "minute_bar"]
    assert r.database_bytes == 12 * 2**30


def test_check_final_failure_fails() -> None:
    src = week_source()
    src.runs.append(run_row("board.daily", date(2026, 10, 16), time(15, 45), "failed", 2))
    got = _items(src)
    assert got["jobs"] == "FAIL" and got["_"] == "FAIL"


def test_check_overdue_running_fails() -> None:
    src = week_source()
    src.runs.append(run_row("brief.closing", date(2026, 10, 16), time(19, 0), "running", 1))
    assert _items(src)["jobs"] == "FAIL"  # 마감 30분을 두 시간 넘김


def test_check_long_gap_fails_short_gap_ok() -> None:
    src = week_source()
    add_gap(src, "auth", kst(date(2026, 10, 16), time(11, 0)), 10)
    assert _items(src)["heartbeat_gaps"] == "OK"
    add_gap(src, "notifier", kst(date(2026, 10, 16), time(12, 0)), 15)
    assert _items(src)["heartbeat_gaps"] == "FAIL"


def test_check_briefs_missing_double_and_failed() -> None:
    d = date(2026, 10, 16)
    src = week_source()
    src.notify = [n for n in src.notify if not (n.kind == "brief.closing" and n.as_of == d)]
    assert _items(src)["briefs"] == "FAIL"  # 마감 브리핑 0통
    # 마감(20:30) 전에는 아직 판정하지 않는다
    assert _items(src, now=kst(d, time(20, 0)))["briefs"] == "OK"
    src = week_source()
    src.notify.append(brief_row("brief.morning", d, time(8, 30)))
    assert _items(src)["briefs"] == "FAIL"  # 두 통
    src = week_source()
    src.notify.append(brief_row("brief.closing", d, time(16, 1), "failed"))
    assert _items(src)["briefs"] == "FAIL"  # 발송 실패


def test_check_reference_items_only_warn() -> None:
    src = week_source()
    src.collection_gap_ts.append(NOW - timedelta(hours=2))
    r = run_check(src, EXPECT, CAL, fresh_ages(NOW), NOW, disk=lambda: (5, 100))
    lv = {i.key: i.level for i in r.items}
    assert lv["collection"] == "WARN" and lv["disk"] == "WARN"
    assert r.level == "WARN" and r.exit_code == 0


def test_check_backup_stale_warns() -> None:
    src = week_source()
    src.runs = [r for r in src.runs if r.job != "ops.nightly"]
    lv = _items(src)
    assert lv["backup"] == "WARN" and lv["_"] == "WARN"


def test_check_heartbeat_now_fail() -> None:
    lv = _items(week_source(), fresh_ages(NOW, stale={"engine": 700.0}))
    assert lv["heartbeat_now"] == "FAIL" and lv["_"] == "FAIL"


def test_check_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="시간대"):
        run_check(week_source(), EXPECT, CAL, fresh_ages(NOW), datetime(2026, 10, 16, 21))  # noqa: DTZ001 — naive 거절 시험


# ── week ─────────────────────────────────────────────────────────────────────────────


def test_week_clean_passes() -> None:
    w = run_week(week_source(), EXPECT, CAL, WEEK_END)
    assert (w.start, w.end) == (WEEK_START, WEEK_END)
    assert w.trading_days == 5 and w.passed and w.exit_code == 0 and w.reason == ""
    assert all(d.passed for d in w.days)


def test_week_one_gap_fails_that_day() -> None:
    src = week_source()
    add_gap(src, "gex.engine", kst(date(2026, 10, 13), time(23, 55)), 20)  # 자정을 넘김
    w = run_week(src, EXPECT, CAL, WEEK_END)
    bad = [d.day for d in w.days if not d.passed]
    assert bad == [date(2026, 10, 14)]  # 마지막 관측일로 센다
    assert not w.passed and "2026-10-14" in w.reason


def test_week_retry_success_is_not_failure() -> None:
    src = week_source()
    d = date(2026, 10, 15)
    src.runs = [r for r in src.runs if not (r.job == "board.daily" and r.as_of == d.isoformat())]
    src.runs.append(run_row("board.daily", d, time(15, 45), "failed", 1))
    src.runs.append(run_row("board.daily", d, time(15, 45), "ok", 2))
    w = run_week(src, EXPECT, CAL, WEEK_END)
    day = next(x for x in w.days if x.day == d)
    assert day.retried_ok == 1 and day.final_failures == 0 and w.passed


def test_week_too_few_trading_days() -> None:
    # 2026-10-03(토)~09(금) — 개천절 대체·한글날로 거래일 3일
    w = run_week(week_source(start=date(2026, 10, 3), end=date(2026, 10, 9)), EXPECT, CAL,
                 date(2026, 10, 9))  # fmt: skip
    assert w.trading_days < 5 and not w.passed and "판정 불가" in w.reason


def test_week_holiday_needs_no_brief() -> None:
    w = run_week(week_source(), EXPECT, CAL, WEEK_END)
    sat = next(d for d in w.days if d.day == date(2026, 10, 17))
    assert not sat.trading and sat.morning_sent == 0 and sat.passed


# ── CLI ──────────────────────────────────────────────────────────────────────────────


def _deps(src: MemoryOpsSource, stale: dict[str, float] | None = None) -> Deps:
    return Deps(src, EXPECT, CAL, fresh_ages(NOW, stale=stale), lambda: (50, 100))


def test_cli_check_text_and_codes() -> None:
    out = io.StringIO()
    code = main(["check"], deps=lambda: _deps(week_source()), now=lambda: NOW, out=out)
    assert code == 0 and "— OK" in out.getvalue() and "tick_raw" in out.getvalue()
    out = io.StringIO()
    code = main(
        ["check", "--json"],
        deps=lambda: _deps(week_source(), {"auth": 9999.0}),
        now=lambda: NOW,
        out=out,
    )
    body = json.loads(out.getvalue())
    assert code == 1 and body["level"] == "FAIL"
    assert {s["name"]: s["level"] for s in body["services"]}["auth"] == "FAIL"


def test_cli_week_default_end_is_yesterday() -> None:
    out = io.StringIO()
    now = kst(date(2026, 10, 19), time(7, 0))
    code = main(["week", "--json"], deps=lambda: _deps(week_source()), now=lambda: now, out=out)
    body = json.loads(out.getvalue())
    assert code == 0 and body["end"] == "2026-10-18" and body["passed"] is True
    out = io.StringIO()
    assert main(["week", "--end", "2026-10-18"], deps=lambda: _deps(week_source()), out=out) == 0
    assert "통과" in out.getvalue()


def test_cli_store_error_masked() -> None:
    from kbj.store.db import StoreError

    def boom() -> Deps:
        raise StoreError("DB 접속 실패: postgresql://kbj:hunter2secret@db/kbj")

    err = io.StringIO()
    assert main(["check"], deps=boom, now=lambda: NOW, err=err) == 2
    assert "hunter2secret" not in err.getvalue()


def test_cli_since_validation() -> None:
    with pytest.raises(SystemExit):
        main(["check", "--since", "0h"], deps=lambda: _deps(week_source()), now=lambda: NOW)
    with pytest.raises(SystemExit):
        main(["check", "--since", "x"], deps=lambda: _deps(week_source()), now=lambda: NOW)
