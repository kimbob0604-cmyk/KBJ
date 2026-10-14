"""metrics §D9 이벤트 표시 — 만기(캘린더 정본·휴장 앞당김)·금통위·수출 속보·실적 집중·주의 창."""

from __future__ import annotations

from datetime import date, datetime

from kbj.config.diag import load_diag
from kbj.core.calendar import TradingCalendar, monthly_expiry
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.diagnosis.events import (
    caution_days,
    earnings_events,
    expiry_events,
    export_flash_events,
    mpc_events,
)
from tests.fixtures.synthetic.p7_world import build_world

CAL = TradingCalendar.default()
AT = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
CFG = load_diag().events


def test_d9_expiry_events_match_world_and_calendar() -> None:
    ev = expiry_events(date(2026, 11, 1), date(2027, 1, 31), CAL, as_of=AT)
    got = {(e.date, e.title) for e in ev}
    w = build_world()
    for e in w.events:
        if e.kind == "expiry":
            assert (e.day, e.title) in got, e
    monthly = sorted(e.date for e in ev if e.title == "월물 만기")
    assert monthly == [monthly_expiry(2026, 11, CAL), monthly_expiry(2026, 12, CAL),
                       monthly_expiry(2027, 1, CAL)]  # fmt: skip
    # 월물 만기 주에는 목요일 위클리가 없다
    assert (date(2026, 12, 10), "위클리(목) 만기") not in got
    assert all(e.quality is Quality.OK and e.source == "KBJ:calendar" for e in ev)


def test_d9_expiry_holiday_moves_earlier() -> None:
    # 2026-09-24(목)은 추석 연휴 — 그 달 월물 만기는 둘째 목요일(09-10)이라 영향 없음.
    # 2025-10-09(목, 한글날)이 둘째 목요일 → 앞 거래일로 당긴다
    ev = expiry_events(date(2025, 10, 1), date(2025, 10, 31), CAL, as_of=AT)
    monthly = [e.date for e in ev if e.title == "월물 만기"]
    assert monthly == [monthly_expiry(2025, 10, CAL)]
    assert monthly[0] < date(2025, 10, 9)


def test_d9_mpc_filters_window() -> None:
    items = [(date(2026, 11, 26), "금통위", "한국은행 공표 일정"),
             (date(2027, 1, 14), "금통위", "한국은행 공표 일정")]  # fmt: skip
    ev = mpc_events(items, date(2026, 12, 1), date(2027, 1, 31), as_of=AT)
    assert [e.date for e in ev] == [date(2027, 1, 14)]
    assert ev[0].quality is Quality.ESTIMATED


def test_d9_export_flash_days_and_unavailable() -> None:
    seen = {date(2026, 12, 1): datetime(2026, 12, 1, 9, 20, tzinfo=KST)}
    ev = export_flash_events(date(2026, 12, 1), date(2026, 12, 31), seen=seen, available=True,
                             as_of=AT)  # fmt: skip
    w = build_world()
    want = sorted(e.day for e in w.events if e.kind == "export_flash")
    assert [e.date for e in ev] == want
    assert ev[0].detail["seen_at"] == seen[date(2026, 12, 1)].isoformat()
    assert "seen_at" not in ev[1].detail and ev[2].detail["seg"] == 2
    assert export_flash_events(date(2026, 12, 1), date(2026, 12, 31), seen={}, available=False,
                               as_of=AT) == []  # fmt: skip


def test_d9_earnings_cluster_threshold() -> None:
    w = build_world()
    ev = earnings_events(w.earnings_counts, CFG, date(2026, 12, 1), date(2026, 12, 31),
                         source="DART:schedule", as_of=AT)  # fmt: skip
    assert [(e.date, e.detail["count"]) for e in ev] == [(date(2026, 12, 11), 12)]
    assert {(e.day, e.title) for e in w.events if e.kind == "earnings_cluster"} == {
        (e.date, e.title) for e in ev
    }


def test_caution_window_spans_weekend() -> None:
    # 금요일 → 다음 거래일 월요일까지(주말 포함)
    fri = date(2026, 12, 11)
    days = caution_days(fri, CFG, CAL)
    assert days[0] == fri and days[-1] == date(2026, 12, 14) and len(days) == 4
