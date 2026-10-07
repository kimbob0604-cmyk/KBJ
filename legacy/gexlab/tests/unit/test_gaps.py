"""무결측 판정 계산 (services/gaps.py, docs/phase1_design.md §9).

저장소 없이 판정 입력(`SessionInputs`)을 합성해 넣는다. 날짜는 2026-09-28(월 — WKM 260904 만기일)
주간·그날 밤(09-29 귀속)·추석 연휴(09-24~25 휴장, 09-23 밤 없음)·금요일 09-18 밤(09-21 월 귀속).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from core.calendar import TradingCalendar
from services.gaps import (
    GapConfig,
    SessionInputs,
    connection_report,
    dates_from_rows,
    down_intervals,
    evaluate_session,
    interval_report,
    load_inputs,
    role_spans,
    session_span,
    trade_report,
)
from services.poller.context import Series
from services.poller.endpoints import INVESTOR_PAIRS
from tests.fakes.gap_inputs import (
    D28,
    D29,
    day_bars,
    every,
    kst,
    minutes,
    perfect_day,
    perfect_night,
    series_rows,
    ticks_for,
)

CAL = TradingCalendar.default()
CFG = GapConfig()


# ── 주기 스트림 공백 경계 ──


SPAN = (kst(D28, 10), kst(D28, 10, 10))


@pytest.mark.parametrize(
    ("second", "gap"),
    [(60.0, False), (60.001, True), (59.999, False)],
)
def test_interval_gap_is_strictly_more_than_the_threshold(second: float, gap: bool) -> None:
    a, _ = SPAN
    times = [a + timedelta(seconds=s) for s in (0, second, second + 30)]
    times += every(a + timedelta(seconds=second + 60), SPAN[1] + timedelta(seconds=1), 30)
    r = interval_report("fut_board", times, SPAN, gap_s=60, period_s=30, required=True)
    assert (r.status == "gaps") is gap
    if gap:
        (g,) = r.gaps
        assert (g.start, g.end) == (a, a + timedelta(seconds=second))
        assert g.expected == 2 and g.received == 0
        assert r.max_gap_s == pytest.approx(second)


def test_interval_edges_count_from_span_start_and_to_span_end() -> None:
    a, b = SPAN
    first_late = [a + timedelta(seconds=61), *every(a + timedelta(seconds=90), b, 30)]
    r = interval_report("x", first_late, SPAN, gap_s=60, period_s=30, required=True)
    assert [(g.start, g.end) for g in r.gaps] == [(a, a + timedelta(seconds=61))]
    on_time = [a + timedelta(seconds=60), *every(a + timedelta(seconds=90), b, 30)]
    assert interval_report("x", on_time, SPAN, gap_s=60, period_s=30, required=True).gaps == ()
    stops = every(a, b - timedelta(seconds=90), 30)
    tail = interval_report("x", stops, SPAN, gap_s=60, period_s=30, required=True)
    assert [(g.start, g.end) for g in tail.gaps] == [(stops[-1], b)]
    empty = interval_report("x", [], SPAN, gap_s=60, period_s=30, required=True)
    assert [(g.start, g.end) for g in empty.gaps] == [SPAN] and empty.received == 0
    assert empty.expected == 20 and empty.max_gap_s == 600


def test_interval_counts_period_slots_and_ignores_points_outside() -> None:
    a, b = SPAN
    doubled = every(a, b, 15)  # 한 칸에 두 번 와도 칸은 하나
    outside = [a - timedelta(minutes=5), b + timedelta(minutes=5)]
    r = interval_report("x", doubled + outside, SPAN, gap_s=60, period_s=30, required=False)
    assert (r.expected, r.received, r.status, r.required) == (20, 20, "ok", False)
    assert r.detail == {"gap_s": 60, "period_s": 30}


# ── 웹소켓 연결 ──


def test_down_intervals_from_connection_events() -> None:
    a, b = SPAN
    t1, t2, t3 = (a + timedelta(minutes=m) for m in (2, 3, 8))
    events = [(t1, "ws_disconnected"), (t1, "ws_connect_failed"), (t2, "ws_connected")]
    assert down_intervals("ws_connected", events, a, b) == [(t1, t2)]
    # 시작 전 사건이 없거나 끊김이면 시작부터 끊김
    assert down_intervals(None, [(t2, "ws_connected")], a, b) == [(a, t2)]
    assert down_intervals("ws_disconnected", [], a, b) == [(a, b)]
    # 끊긴 채 끝남, 사건 없이 죽은 뒤 다시 기동해 접속 실패(연결 사건으로 다시 끊김)
    assert down_intervals("ws_connected", [(t3, "ws_connect_failed")], a, b) == [(t3, b)]
    # 구간 밖 사건은 보지 않는다
    assert (
        down_intervals("ws_connected", [(b + timedelta(seconds=1), "ws_disconnected")], a, b) == []
    )


def test_every_disconnection_is_a_gap_even_a_short_one() -> None:
    a = SPAN[0]
    t1 = a + timedelta(minutes=2)
    r = connection_report(
        "ws_connected", [(t1, "ws_disconnected"), (t1 + timedelta(seconds=1), "ws_connected")], SPAN
    )
    (g,) = r.gaps
    assert (g.start, g.end, g.expected) == (t1, t1 + timedelta(seconds=1), 1)
    assert (r.expected, r.received, r.max_gap_s, r.status) == (600, 599, 1.0, "gaps")
    assert connection_report("ws_connected", [], SPAN).status == "ok"
    assert r.detail["unit"] == "s"


# ── 선물 체결 vs 분봉 ──


DAY_SPAN = (kst(D28, 8, 45), kst(D28, 15, 45))


def test_bar_minute_without_ws_trades_is_a_gap_and_runs_merge() -> None:
    bars = day_bars(D28)
    missing = {kst(D28, 10, m) for m in (0, 1, 2)} | {kst(D28, 11, 30)}
    ticks = [
        (c, t, n) for c, t, n in ticks_for(bars) if t not in {x.astimezone(UTC) for x in missing}
    ]
    # 봉 시각 창은 [m − 60초, m + 60초) — 10:00 봉은 09:59 체결로 덮인다(표기가 봉 끝일 수 있다)
    r = trade_report(bars, ticks, D28, "day", DAY_SPAN, CFG)
    assert [(g.start, g.end, g.expected) for g in r.gaps] == [
        (kst(D28, 10, 1), kst(D28, 10, 3), 2),
    ]
    assert r.gaps[0].detail == {"code": "A01612"}
    strict = GapConfig(bar_before_s=0, bar_after_s=60)  # 표기가 봉 시작으로 확인되면
    r2 = trade_report(bars, ticks, D28, "day", DAY_SPAN, strict)
    assert [(g.start, g.end) for g in r2.gaps] == [
        (kst(D28, 10, 0), kst(D28, 10, 3)),
        (kst(D28, 11, 30), kst(D28, 11, 31)),
    ]
    assert r2.expected == len(bars) and r2.received == len(bars) - 4


def test_minutes_without_bars_are_not_gaps() -> None:
    """체결 없는 분(분봉 없음 — 야간 새벽 한산 구간)은 웹소켓 체결이 없어도 공백이 아니다."""
    a, b = kst(D28, 18), kst(D29, 6)
    quiet = [("A01612", t, 2) for t in minutes(a, b) if not (kst(D29, 3) <= t < kst(D29, 4))]
    r = trade_report(quiet, ticks_for(quiet), D29, "night", (a, b), CFG)
    assert r.status == "ok" and r.gaps == () and r.expected == len(quiet)


def test_closing_auction_and_zero_volume_bars_are_not_checked() -> None:
    bars = day_bars(D28)
    auction = [("A01612", kst(D28, 15, m), 0 if m == 40 else 5) for m in range(35, 45)]
    zero = [("A01612", kst(D28, 9, 0, 30), 0)]
    ticks = [(c, t, n) for c, t, n in ticks_for(bars)]
    r = trade_report(bars + auction + zero, ticks, D28, "day", DAY_SPAN, CFG)
    assert r.status == "ok" and r.expected == len(bars)
    assert r.detail["zero_volume"] == 2 and r.detail["outside"] == 9
    # 종가 단일가 체결(15:45 봉)은 본다
    no_close = [(c, t, n) for c, t, n in ticks if t != kst(D28, 15, 45).astimezone(UTC)]
    closed = trade_report(bars, no_close, D28, "day", DAY_SPAN, CFG)
    assert [(g.start, g.end) for g in closed.gaps] == [(kst(D28, 15, 45), kst(D28, 15, 46))]


def test_quarterly_expiry_day_checks_each_code_while_it_is_subscribed() -> None:
    """분기 만기일(2026-09-10 목) 주간: 만기 종목은 15:10 전까지, 차월물은 15:22 부터."""
    d = date(2026, 9, 10)
    span = (kst(d, 8, 45), kst(d, 15, 45))
    old = [("A01609", t, 5) for t in minutes(kst(d, 8, 45), kst(d, 15, 20))]
    new = day_bars(d, "A01612")
    ticks = ticks_for(old) + [
        (c, t, n) for c, t, n in ticks_for(new) if t >= kst(d, 15, 21).astimezone(UTC)
    ]
    r = trade_report(old + new, ticks, d, "day", span, CFG)
    assert r.status == "ok", r.gaps
    # 만기 종목: 15:10~15:19 단일가 봉 10개를 빼고 15:20 종가 체결 봉은 본다
    # 차월물: 15:22~15:34 + 15:45
    assert r.detail["codes"] == {"A01609": len(old) - 10, "A01612": 13 + 1}
    # 차월물 15:22 뒤 체결이 끊기면 공백
    cut = [
        (c, t, n)
        for c, t, n in ticks
        if not (c == "A01612" and t >= kst(d, 15, 30).astimezone(UTC))
    ]
    r2 = trade_report(old + new, cut, d, "day", span, CFG)
    assert [(g.start, g.detail["code"]) for g in r2.gaps] == [(kst(d, 15, 31), "A01612")]


@pytest.mark.parametrize("status", ["partial", "missing"])
def test_incomplete_minute_load_is_unverified(status: Any) -> None:
    bars = day_bars(D28)
    r = trade_report(bars, ticks_for(bars), D28, "day", DAY_SPAN, CFG, status)
    assert r.status == "unverified" and status in r.detail["reason"]
    none = trade_report([], [], D28, "day", DAY_SPAN, CFG, "loaded")
    assert none.status == "unverified" and none.detail["reason"] == "분봉이 없다"


def test_config_is_validated() -> None:
    with pytest.raises(ValidationError, match="60초 배수"):
        GapConfig(bar_before_s=30)
    with pytest.raises(ValidationError):
        GapConfig(board_gap_s=0)


# ── 추적 만기·세션 구간 ──


def test_tracked_series_switch_at_1520_on_an_expiry_day() -> None:
    dates = dates_from_rows(series_rows(kst(D28, 8)))
    a, b = DAY_SPAN
    spans = role_spans(dates, a, b, lambda t: t.tracked)
    switch = kst(D28, 15, 20)
    assert spans == {
        Series("WKM", "260904"): (a, switch),
        Series("WKI", "261001"): (a, b),
        Series("", "202610"): (a, b),
        Series("WKM", "261001"): (switch, b),
    }


def test_series_dates_prefer_kis_then_the_latest_row() -> None:
    t0, t1 = kst(D28, 8), kst(D28, 9)
    rows = [
        ("WKI", "261001", "calendar", date(2026, 10, 1), t1),
        ("WKI", "261001", "kis", date(2026, 10, 2), t0),
        ("", "202610", "kis", date(2026, 10, 7), t0),
        ("", "202610", "kis", date(2026, 10, 8), t1),
    ]
    assert dates_from_rows(rows) == {
        Series("WKI", "261001"): date(2026, 10, 2),
        Series("", "202610"): date(2026, 10, 8),
    }


def test_session_span_follows_the_calendar() -> None:
    assert session_span(D28, "day", CAL) == DAY_SPAN
    assert session_span(D29, "night", CAL) == (kst(D28, 18), kst(D29, 6))
    # 금요일 밤은 월요일 귀속 — 토 06:00 까지
    assert session_span(date(2026, 9, 21), "night", CAL) == (
        kst(date(2026, 9, 18), 18),
        kst(date(2026, 9, 19), 6),
    )
    assert session_span(date(2026, 9, 24), "day", CAL) is None  # 추석 휴장
    assert session_span(D28, "night", CAL) is None  # 09-23 밤은 열리지 않았다
    assert session_span(date(2026, 9, 26), "night", CAL) is None  # 토요일


# ── 세션 판정 ──


def test_perfect_day_is_clean_with_every_design_stream() -> None:
    rep = evaluate_session(perfect_day(), CAL, D28, "day", minute_status="loaded")
    names = [s.stream for s in rep.streams]
    assert names[:4] == [
        "board:M:202610",
        "board:WKI:261001",
        "board:WKM:260904",
        "board:WKM:261001",
    ]
    assert "fut_board" in names and "ws_connection" in names and "fut_trades" in names
    assert names[names.index("fut_board") + 1] == "underlying"  # 설계 §9 '선물 전광판·기초자산'
    assert [n for n in names if n.startswith("investor:")] == [
        f"investor:{m}/{s}" for m, s in INVESTOR_PAIRS
    ]
    assert [n for n in names if n.startswith("fill1:")] == ["fill1:M:202610"]
    assert rep.clean and rep.gaps == [] and all(s.required for s in rep.streams)
    board = rep.stream("board:WKM:260904")
    assert board.span_end == kst(D28, 15, 20) and board.detail["targets"] == "series_expiries"
    assert rep.stream("board:WKM:261001").span_start == kst(D28, 15, 20)


def test_a_stalled_stream_makes_the_day_not_clean() -> None:
    base = perfect_day()
    hole = (kst(D28, 11), kst(D28, 11, 2))
    stalled = replace(base, fut_board=[t for t in base.fut_board if not hole[0] <= t < hole[1]])
    rep = evaluate_session(stalled, CAL, D28, "day")
    (g,) = rep.gaps
    assert g.stream == "fut_board" and g.seconds == 150.0
    assert not rep.clean and [s.stream for s in rep.problems] == ["fut_board"]
    recs = rep.gap_records(kst(D28, 16))
    assert [(r.stream, r.trade_date, r.session, r.expected) for r in recs] == [
        ("fut_board", D28, "day", 5)
    ]
    reports = {r.stream: r for r in rep.report_records(kst(D28, 16))}
    assert reports["fut_board"].status == "gaps" and reports["fut_board"].gaps == 1
    assert reports["fut_board"].max_gap_s == 150.0 and reports["ws_connection"].status == "ok"


def test_a_stalled_underlying_poll_makes_the_day_not_clean() -> None:
    """기초자산 조회(poller 'underlying', P1 30초)는 원문만 남지만 필수 스트림 — 한도 60초."""
    base = perfect_day()

    def without(lo: datetime, hi: datetime) -> SessionInputs:
        return replace(base, underlying=[t for t in base.underlying if not lo <= t < hi])

    # 한 번 빠지면 이웃 간격 60초 = 한도 — 공백 아님, 두 번이면 90초 — 공백
    once = evaluate_session(without(kst(D28, 14), kst(D28, 14, 0, 30)), CAL, D28, "day")
    assert once.stream("underlying").status == "ok" and once.clean
    rep = evaluate_session(without(kst(D28, 14), kst(D28, 14, 1)), CAL, D28, "day")
    under = rep.stream("underlying")
    assert under.required and under.status == "gaps"
    (g,) = under.gaps
    assert (g.start, g.end) == (kst(D28, 13, 59, 30.5), kst(D28, 14, 1, 0.5))
    assert under.detail["gap_s"] == 60 and under.detail["period_s"] == 30
    assert [s.stream for s in rep.problems] == ["underlying"]
    none = evaluate_session(replace(base, underlying=()), CAL, D28, "day")
    assert none.stream("underlying").status == "gaps" and not none.clean


def test_night_b_has_no_board_and_night_investor_is_recorded_only() -> None:
    rep = evaluate_session(perfect_night(), CAL, D29, "night", minute_status="loaded")
    names = [s.stream for s in rep.streams]
    assert not any(n.startswith("board:") for n in names)
    assert "underlying" not in names  # 야간 B 는 기초자산을 부르지 않는다(선물 단건 CM 만)
    assert [n for n in names if n.startswith("fill1:")] == ["fill1:M:202610", "fill1:WKI:261001"]
    inv = [s for s in rep.streams if s.stream.startswith("investor:")]
    assert len(inv) == 7 and all(not s.required and s.status == "gaps" for s in inv)
    assert rep.clean  # 야간 투자자별 공백은 무결측 판정에 들지 않는다
    assert rep.start == kst(D28, 18) and rep.end == kst(D29, 6)
    c = evaluate_session(
        perfect_night(), CAL, D29, "night", GapConfig(night_mode="C", night_investor=False)
    )
    assert [s.stream for s in c.streams] == ["ws_connection", "fut_trades"]
    a = evaluate_session(perfect_night(), CAL, D29, "night", GapConfig(night_mode="A"))
    assert a.stream("underlying").required  # 야간 A 는 주간과 같은 구성


def test_without_series_expiries_the_observed_series_are_used_or_unverified() -> None:
    base = perfect_day()
    rep = evaluate_session(replace(base, series_dates=()), CAL, D28, "day")
    board = [s for s in rep.streams if s.stream.startswith("board:")]
    assert {s.detail["targets"] for s in board} == {"observed"}
    assert all(s.span_start == DAY_SPAN[0] for s in board)
    # 세션 전체로 보면 15:20 에 끝난 시리즈는 공백 — 추적 구간을 모르면 엄하게 본다
    assert rep.stream("board:WKM:260904").status == "gaps"
    # 추적 시리즈를 다 안다고 할 수 없다 — 표지 스트림이 판정 불가
    assert rep.stream("board").status == "unverified" and rep.stream("board").required
    blank = evaluate_session(replace(base, series_dates=(), board=(), fill1=()), CAL, D28, "day")
    assert (
        blank.stream("board").status == "unverified"
        and blank.stream("fill1").status == "unverified"
    )
    assert not blank.clean


def test_without_series_expiries_a_series_that_never_arrived_is_not_clean() -> None:
    """추적 만기를 모르면 관측된 시리즈만 볼 수 있다 — 하루 내내 통째로 안 온 시리즈(여기선 전광판
    4 시리즈 중 3)를 못 보므로, 관측된 것이 빈틈없어도 무결측으로 세지 않는다."""
    base = perfect_day()
    only = [r for r in base.board if (r[0], r[1]) == ("", "202610")]
    inputs = replace(base, series_dates=(), board=only)
    rep = evaluate_session(inputs, CAL, D28, "day", minute_status="loaded")
    assert rep.stream("board:M:202610").status == "ok"
    assert rep.stream("fill1:M:202610").status == "ok"
    marker = rep.stream("board")
    assert marker.required and marker.status == "unverified" and marker.gaps == ()
    assert "관측 시리즈만" in marker.detail["reason"]
    assert marker.detail["observed"] == ["M:202610"]
    assert (marker.span_start, marker.span_end) == DAY_SPAN
    assert rep.stream("fill1").status == "unverified"
    assert not rep.clean
    assert {s.stream for s in rep.problems} == {"board", "fill1"}
    # 리포트 행에도 남는다(nogap_report 가 '판정 불가'로 센다)
    rows = {r.stream: r for r in rep.report_records(kst(D28, 16))}
    assert rows["board"].status == "unverified" and rows["board"].required


def test_friday_night_is_judged_as_mondays_night() -> None:
    fri, sat, mon = date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 21)
    a, b = kst(fri, 18), kst(sat, 6)
    bars = [("A01612", t, 1) for t in minutes(a, b)]
    inputs = SessionInputs(
        series_dates=[
            ("WKI", "260924", "kis", date(2026, 9, 23), kst(fri, 17, 51)),
            ("", "202610", "kis", date(2026, 10, 8), kst(fri, 17, 51)),
        ],
        fut_board=every(a, b, 30),
        fill1=[("WKI:260924", t) for t in every(a, b, 30)]
        + [("M:202610", t) for t in every(a, b, 30)],
        ws_before="ws_connected",
        bars=bars,
        tick_minutes=ticks_for(bars),
    )
    rep = evaluate_session(inputs, CAL, mon, "night")
    assert (rep.trade_date, rep.session, rep.start, rep.end) == (mon, "night", a, b)
    assert rep.clean
    assert {r.trade_date for r in rep.report_records(kst(mon, 6, 30))} == {mon}
    with pytest.raises(ValueError, match="열리지 않은"):
        evaluate_session(inputs, CAL, sat, "night")


class _Reader:
    """GapReader 흉내 — 받은 인자를 기록한다."""

    def __init__(self, inputs: SessionInputs) -> None:
        self.i = inputs
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _log(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def series_dates(self, trade_date: date, session: str) -> list[Any]:
        self._log("series_dates", trade_date, session)
        return list(self.i.series_dates)

    def board_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self.i.board)

    def fut_board_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self.i.fut_board)

    def underlying_times(self, trade_date: date, session: str) -> list[Any]:
        self._log("underlying", trade_date, session)
        return list(self.i.underlying)

    def investor_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self.i.investor)

    def fill1_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self.i.fill1)

    def ws_connection_events(self, start: datetime, end: datetime, kinds: Any) -> Any:
        self._log("ws", start, end, tuple(kinds))
        return self.i.ws_before, list(self.i.ws_events)

    def minute_bar_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self.i.bars)

    def fut_tick_minutes(self, trade_date: date, session: str) -> list[Any]:
        return list(self.i.tick_minutes)


def test_load_inputs_asks_the_reader_for_the_session() -> None:
    base = perfect_day()
    reader = _Reader(base)
    got = load_inputs(reader, D28, "day", DAY_SPAN)
    assert got.fut_board == list(base.fut_board) and got.ws_before == "ws_connected"
    assert got.underlying == list(base.underlying) and len(got.underlying) == 840
    assert ("underlying", (D28, "day")) in reader.calls
    assert reader.calls[0] == (
        "ws",
        (*DAY_SPAN, ("ws_connected", "ws_connect_failed", "ws_disconnected")),
    )
    assert ("series_dates", (D28, "day")) in reader.calls
