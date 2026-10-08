"""events.json — 금통위 일정(설정 파일 해석기 load_calendar_events 를 쓴다)."""

from __future__ import annotations

from datetime import datetime

from kbj.config.markets import CalendarEvents, load_calendar_events, parse_calendar_events
from kbj.core.time import KST
from kbj.services.public_export.events_json import SOURCE, build_events

NOW = datetime(2026, 10, 7, 5, 30, tzinfo=KST)


def _events(*dates: str) -> CalendarEvents:
    return parse_calendar_events(
        {
            "version": 1,
            "events": [
                {
                    "kind": "bok_mpc",
                    "date": d,
                    "title": "금통위 통화정책방향 결정",
                    "source": "한국은행 공표 일정",
                }
                for d in dates
            ],
        }
    )


def test_only_today_and_later_with_next() -> None:
    f = build_events(NOW, _events("2026-08-27", "2026-10-07", "2026-10-22"))
    rows = f.data["events"]
    assert [r["date"] for r in rows] == ["2026-10-07", "2026-10-22"]  # 당일 포함
    assert f.data["next_bok_mpc"] == "2026-10-07"
    assert rows[0]["kind"] == "bok_mpc" and rows[0]["label"] == "금통위"
    assert rows[0]["source"] == "한국은행 공표 일정"
    assert f.source == SOURCE


def test_quality_estimated_until_primary_checked() -> None:
    ev = _events("2026-10-22")
    f = build_events(NOW, ev)
    assert f.quality == "estimated"
    assert any("[확인 필요]" in n for n in f.notes)
    assert build_events(NOW, ev, primary_checked=True).quality == "ok"


def test_no_upcoming_is_stale_with_reason() -> None:
    f = build_events(NOW, _events("2026-01-15"), primary_checked=True)
    assert f.quality == "stale"
    assert f.data["events"] == [] and f.data["next_bok_mpc"] is None
    assert any("갱신" in n for n in f.notes)


def test_real_config_file_parses_and_has_upcoming() -> None:
    f = build_events(NOW, load_calendar_events())
    assert f.data["next_bok_mpc"] is not None
    assert f.data["next_bok_mpc"] >= "2026-10-07"


def test_kst_date_boundary() -> None:
    # UTC 10-21 15:30 = KST 10-22 00:30 → 10-22 일정은 '오늘'
    late = datetime.fromisoformat("2026-10-21T15:30:00+00:00")
    f = build_events(late, _events("2026-10-21", "2026-10-22"))
    assert f.data["next_bok_mpc"] == "2026-10-22"
