"""무결측 리포트 '3거래일 연속 무결측' 판정 (scripts/nogap_report.py, 설계 §0·§9).

리포트 행은 합성. 달력: 2026-09-18(금)·21(월)·22(화)·23(수 — 그날 밤 없음), 24~25 추석 휴장,
28(월 — 09-23 밤이 없어 주간만), 29(화).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from core.calendar import HolidayOverride, TradingCalendar
from data.store import CollectionReportRecord, StoreError
from scripts.nogap_report import (
    RecomputeSource,
    judge,
    judge_session,
    main,
    render,
    sessions_of,
    trading_days,
)
from tests.fakes.gap_inputs import D28, D29, MemoryGapStore, perfect_day, perfect_night

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
FRI, MON, TUE, WED = date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)


def rep(d: date, session: str, stream: str = "fut_board", **kw: Any) -> CollectionReportRecord:
    start = datetime(d.year, d.month, d.day, 8, 45, tzinfo=KST)
    base: dict[str, Any] = {
        "trade_date": d,
        "session": session,
        "stream": stream,
        "evaluated_at": start + timedelta(hours=8),
        "span_start": start,
        "span_end": start + timedelta(hours=7),
        "required": True,
        "status": "ok",
        "expected": 840,
        "received": 840,
        "max_gap_s": 30.0,
    }
    return CollectionReportRecord(**(base | kw))


def clean_day(d: date) -> list[CollectionReportRecord]:
    return [
        rep(d, s, stream) for s in sessions_of(d, CAL) for stream in ("fut_board", "fut_trades")
    ]


class Store(MemoryGapStore):
    def __init__(self, reports: list[CollectionReportRecord] | None = None) -> None:
        super().__init__()
        self.stored = reports or []
        self.asked: list[tuple[date, date]] = []
        self.fail: Exception | None = None

    def gap_reports(self, first: date, last: date) -> list[CollectionReportRecord]:
        self.asked.append((first, last))
        if self.fail is not None:
            raise self.fail
        return [r for r in self.stored if first <= r.trade_date <= last]


def test_a_trading_day_is_its_day_plus_the_night_attributed_to_it() -> None:
    assert sessions_of(MON, CAL) == ["day", "night"]  # 금요일 09-18 밤 → 월요일
    assert sessions_of(TUE, CAL) == ["day", "night"]
    assert sessions_of(D28, CAL) == ["day"]  # 09-23 밤은 열리지 않았다
    assert sessions_of(D29, CAL) == ["day", "night"]


def test_trading_days_skip_holidays_and_weekends() -> None:
    assert trading_days(D29, 5, CAL) == [MON, TUE, WED, D28, D29]
    assert trading_days(date(2026, 9, 26), 2, CAL) == [TUE, WED]  # 토요일 끝 → 수요일까지


def test_three_clean_days_across_the_chuseok_holidays_satisfy() -> None:
    """휴장일은 세지도 끊지도 않는다 — 09-22·09-23·09-28 이 연속 3거래일."""
    reports = clean_day(TUE) + clean_day(WED) + clean_day(D28)
    j = judge(trading_days(D28, 4, CAL), reports, CAL)
    assert [d.trade_date for d in j.days] == [MON, TUE, WED, D28]
    assert not j.days[0].clean  # 월요일은 리포트가 없다
    assert j.best == (TUE, WED, D28) and j.satisfied
    assert [s.session for s in j.days[-1].sessions] == ["day"]


def test_mondays_night_is_the_friday_night() -> None:
    reports = clean_day(TUE) + clean_day(WED) + [rep(MON, "day"), rep(MON, "day", "fut_trades")]
    j = judge([MON, TUE, WED], reports, CAL)
    mon = j.days[0]
    assert [(s.session, s.verdict) for s in mon.sessions] == [
        ("day", "clean"),
        ("night", "missing"),
    ]
    assert j.best == (TUE, WED) and not j.satisfied
    friday_night = rep(MON, "night", span_start=datetime(2026, 9, 18, 18, tzinfo=KST))
    j2 = judge([MON, TUE, WED], [*reports, friday_night], CAL)
    assert j2.best == (MON, TUE, WED) and j2.satisfied
    assert FRI not in [d.trade_date for d in j2.days]  # 금요일 밤은 금요일 것이 아니다


def test_gaps_and_unverified_required_streams_break_the_run() -> None:
    days = [MON, TUE, WED, D28, D29]
    base = clean_day(MON) + clean_day(TUE) + clean_day(WED) + clean_day(D28) + clean_day(D29)
    gapped = [
        rep(WED, "night", "ws_connection", status="gaps", gaps=2, max_gap_s=4.0)
        if (r.trade_date, r.session, r.stream) == (WED, "night", "fut_board")
        else r
        for r in base
    ]
    j = judge(days, gapped, CAL)
    wed = j.days[2]
    night = wed.sessions[1]
    assert (night.verdict, night.gaps, night.max_gap_s) == ("gaps", 2, 4.0)
    assert night.notes == ("ws_connection 공백 2",)
    assert j.best == (MON, TUE) and not j.satisfied
    unknown = [
        rep(TUE, "day", "fut_trades", status="unverified", detail={"reason": "분봉이 없다"})
        if (r.trade_date, r.session, r.stream) == (TUE, "day", "fut_trades")
        else r
        for r in base
    ]
    j2 = judge(days, unknown, CAL)
    assert j2.days[1].sessions[0].verdict == "unverified"
    assert j2.best == (WED, D28, D29) and j2.satisfied


def test_optional_stream_gaps_do_not_count() -> None:
    reports = [
        *clean_day(TUE),
        rep(TUE, "night", "investor:WKM/OC05", required=False, status="gaps", gaps=9),
    ]
    s = judge_session(TUE, "night", [r for r in reports if r.session == "night"])
    assert (s.verdict, s.required_ok, s.required) == ("clean", 2, 2)


def test_render_prints_a_table_and_the_verdict() -> None:
    reports = clean_day(TUE) + clean_day(WED) + clean_day(D28)
    text = render(judge(trading_days(D28, 4, CAL), reports, CAL))
    lines = text.splitlines()
    assert lines[0].startswith("| 거래일 | 세션 | 판정 |")
    assert "| 2026-09-21 | 야간 | 리포트 없음 | - |" in text
    assert "| 2026-09-28 | 주간 | 무결측 | 2/2 | 0 | - |" in text
    assert "가장 긴 연속 무결측: 3거래일 (2026-09-22 ~ 2026-09-28" in text
    assert lines[-1].endswith("충족") and "미충족" not in lines[-1]


def test_main_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    store = Store(clean_day(TUE) + clean_day(WED) + clean_day(D28))
    assert main(["--end", "2026-09-28", "--days", "4"], store=store, cal=CAL) == 0
    assert store.asked == [(MON, D28)]
    out = capsys.readouterr().out
    assert "충족" in out
    assert main(["--end", "2026-09-28", "--days", "4", "--need", "4"], store=store, cal=CAL) == 1
    store.fail = StoreError("collection_reports: OperationalError(08006): 접속 실패")
    assert main(["--end", "2026-09-28"], store=store, cal=CAL) == 2
    err = capsys.readouterr().err
    assert "StoreError" in err and "접속 실패" in err
    # --end 가 없으면 오늘(KST)까지
    store.fail = None
    now = datetime(2026, 9, 28, 20, 0, tzinfo=KST)
    assert main(["--days", "3"], store=store, cal=CAL, now=now) == 0
    assert store.asked[-1] == (TUE, D28)  # 최근 3거래일 = 09-22·23·28
    with pytest.raises(SystemExit):
        main(["--days", "0"], store=store, cal=CAL)


def test_a_calendar_config_error_is_exit_code_2_not_unmet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """휴장 override 가 깨져 캘린더를 못 만들어도 설정 오류(2) — 미충족(1)과 섞이지 않는다."""

    def broken() -> TradingCalendar:
        raise ValueError("holidays_override.yaml: closed.0.date 형식이 틀렸다\n  input_value=…")

    monkeypatch.setattr("core.calendar._default_calendar", broken)
    store = Store(clean_day(TUE) + clean_day(WED) + clean_day(D28))
    assert main(["--end", "2026-09-28"], store=store) == 2
    err = capsys.readouterr().err
    assert "ValueError" in err and "holidays_override.yaml" in err and "input_value" not in err
    assert store.asked == []  # 리포트를 읽기 전에 멈춘다

    class Closed(TradingCalendar):
        """거래일을 못 찾는 캘린더(연속 휴장 한도) — trading_days 가 던진다."""

        def is_trading_day(self, d: date) -> bool:
            return False

    closed = Closed.from_override(HolidayOverride())
    assert main(["--end", "2026-09-28"], store=store, cal=closed) == 2
    assert "연속 휴장" in capsys.readouterr().err


def test_recompute_judges_from_stored_data_without_writing() -> None:
    store = Store()
    store.inputs[(D28, "day")] = perfect_day()
    store.inputs[(D29, "night")] = perfect_night()
    src = RecomputeSource(store, CAL)
    got = src.gap_reports(D28, D29)
    assert {(r.trade_date, r.session) for r in got} == {(D28, "day"), (D29, "night"), (D29, "day")}
    assert store.replaced == []  # 쓰지 않는다
    by = {(r.trade_date, r.session): [] for r in got}
    for r in got:
        by[(r.trade_date, r.session)].append(r)
    assert all(r.status == "ok" for r in by[(D28, "day")] if r.required)
    assert (
        main(
            ["--end", "2026-09-29", "--days", "2", "--need", "1", "--recompute"],
            store=store,
            cal=CAL,
        )
        == 0
    )
