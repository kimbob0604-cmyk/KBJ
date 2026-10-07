"""무결측 판정 시험용 합성 입력·가짜 저장소 (services/gaps.py, docs/phase1_design.md §9).

- `perfect_day(d)`·`perfect_night()`: 빈틈없는 세션의 `SessionInputs` — 2026-09-28(월 — WKM 260904
  만기일, 15:20 에 추적 시리즈가 바뀐다) 주간과 그날 밤(09-29 귀속, 분기 B). SYNTHETIC
- `MemoryGapStore`: `GapReader` + `replace_gap_report` + `fut_tick_count` 흉내 — 세션마다 입력을
  넣어 두고, 교체한 공백·리포트를 세션 키로 덮어쓴다. `fail_reads`·`fail_writes` 로 실패를 넣는다
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.calendar import TradingCalendar
from data.store import CollectionGapRecord, CollectionReportRecord, StoreError
from services.gaps import Session, SessionInputs, session_span
from services.poller.endpoints import INVESTOR_PAIRS

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
D28, D29 = date(2026, 9, 28), date(2026, 9, 29)


def kst(d: date, h: int, m: int = 0, s: float = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST) + timedelta(seconds=s)


def every(start: datetime, end: datetime, step_s: float, offset_s: float = 0.0) -> list[datetime]:
    out: list[datetime] = []
    t = start + timedelta(seconds=offset_s)
    while t < end:
        out.append(t)
        t += timedelta(seconds=step_s)
    return out


def minutes(start: datetime, end: datetime) -> Iterator[datetime]:
    t = start
    while t <= end:
        yield t
        t += timedelta(minutes=1)


# 2026-09-28 기준 최종거래일 (poller series_expiries 가 KIS 에서 받은 값처럼)
EXPIRIES = {
    ("WKM", "260904"): date(2026, 9, 28),  # 09-28 15:20 만기
    ("WKI", "261001"): date(2026, 10, 1),
    ("WKM", "261001"): date(2026, 10, 6),  # 10-05 휴장 → 10-06
    ("", "202610"): date(2026, 10, 8),
    ("", "202611"): date(2026, 11, 12),
}


def series_rows(at: datetime) -> list[tuple[str, str, str, date, datetime]]:
    return [(c, m, "kis", d, at) for (c, m), d in EXPIRIES.items()]


def day_bars(d: date, code: str = "A01612") -> list[tuple[str, datetime, int | None]]:
    """08:45~15:34 매분 + 종가 단일가 15:45 (가짜 KIS 분봉과 같은 모양)."""
    ts = [*minutes(kst(d, 8, 45), kst(d, 15, 34)), kst(d, 15, 45)]
    return [(code, t, 10) for t in ts]


def ticks_for(bars: Sequence[tuple[str, datetime, int | None]]) -> list[tuple[str, datetime, int]]:
    """봉마다 그 분에 체결 3건."""
    return [(c, t.astimezone(UTC), 3) for c, t, _ in bars]


# 2026-09-28 주간의 추적 시리즈와 그 구간 (15:20 에 WKM 260904 → WKM 261001)
def day_board_spans(d: date = D28) -> list[tuple[tuple[str, str], datetime, datetime]]:
    a, b, switch = kst(d, 8, 45), kst(d, 15, 45), kst(d, 15, 20)
    return [
        (("WKM", "260904"), a, switch),
        (("WKI", "261001"), a, b),
        (("", "202610"), a, b),
        (("WKM", "261001"), switch, b),
    ]


def perfect_day(d: date = D28) -> SessionInputs:
    """주간 D 를 빈틈없이 — 전광판·선물·기초자산 30초, 투자자별 60초, 보강 1 20초, 연결 유지,
    봉마다 체결."""
    a, b = kst(d, 8, 45), kst(d, 15, 45)
    board: list[tuple[str, str, datetime]] = []
    for (cls, mtrt), lo, hi in day_board_spans(d):
        board += [(cls, mtrt, t) for t in every(lo, hi, 30, 0.25)]
    investor = [
        (mkt, sector, t)
        for j, (mkt, sector) in enumerate(INVESTOR_PAIRS)
        for t in every(a, b, 60, 5 + j)
    ]
    bars = day_bars(d)
    return SessionInputs(
        series_dates=series_rows(kst(d, 8, 1)),
        board=board,
        fut_board=every(a, b, 30),
        underlying=every(a, b, 30, 0.5),
        investor=investor,
        fill1=[("M:202610", t) for t in every(a, b, 20, 1)],
        ws_before="ws_connected",
        bars=bars,
        tick_minutes=ticks_for(bars),
    )


def perfect_night() -> SessionInputs:
    """09-28 에 시작한 밤(09-29 귀속), 분기 B — 전광판 없이 선물 단건·보강 1(최근접·월물)."""
    a, b = kst(D28, 18), kst(D29, 6)
    bars = [("A01612", t, 4) for t in minutes(a, b)]
    return SessionInputs(
        series_dates=series_rows(kst(D28, 17, 51)),
        fut_board=every(a, b, 30),
        fill1=[("WKI:261001", t) for t in every(a, b, 20)]
        + [("M:202610", t) for t in every(a, b, 40, 3)],
        ws_before="ws_connected",
        bars=bars,
        tick_minutes=ticks_for(bars),
    )


@dataclass
class MemoryGapStore:
    """판정 입력 읽기·리포트 교체 흉내. 입력이 없는 세션은 빈 입력."""

    inputs: dict[tuple[date, Session], SessionInputs] = field(
        default_factory=dict[tuple[date, Session], SessionInputs]
    )
    gaps: dict[tuple[date, str], list[CollectionGapRecord]] = field(
        default_factory=dict[tuple[date, str], list[CollectionGapRecord]]
    )
    reports: dict[tuple[date, str], list[CollectionReportRecord]] = field(
        default_factory=dict[tuple[date, str], list[CollectionReportRecord]]
    )
    fail_reads: int = 0  # 이만큼 읽기(세션 단위 첫 읽기)를 실패시킨다
    fail_writes: int = 0
    reads: list[tuple[date, str]] = field(default_factory=list[tuple[date, str]])
    replaced: list[tuple[date, str]] = field(default_factory=list[tuple[date, str]])
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _get(self, trade_date: date, session: str) -> SessionInputs:
        for (td, ss), i in self.inputs.items():
            if (td, ss) == (trade_date, session):
                return i
        return SessionInputs()

    def series_dates(self, trade_date: date, session: str) -> list[Any]:
        with self.lock:
            self.reads.append((trade_date, session))
            if self.fail_reads:
                self.fail_reads -= 1
                raise StoreError("series_expiries: OperationalError: server closed the connection")
        return list(self._get(trade_date, session).series_dates)

    def board_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).board)

    def fut_board_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).fut_board)

    def underlying_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).underlying)

    def investor_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).investor)

    def fill1_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).fill1)

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        """세션 구간이 [start, end] 인 입력의 연결 사건 (없으면 사건 없음)."""
        for (td, session), i in self.inputs.items():
            if session_span(td, session, CAL) == (start, end):
                return i.ws_before, list(i.ws_events)
        return None, []

    def minute_bar_times(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).bars)

    def fut_tick_minutes(self, trade_date: date, session: str) -> list[Any]:
        return list(self._get(trade_date, session).tick_minutes)

    def replace_gap_report(
        self,
        trade_date: date,
        session: str,
        gaps: Sequence[CollectionGapRecord],
        reports: Sequence[CollectionReportRecord],
    ) -> None:
        with self.lock:
            if self.fail_writes:
                self.fail_writes -= 1
                raise StoreError("collection_reports: OperationalError: connection refused")
            self.gaps[(trade_date, session)] = list(gaps)
            self.reports[(trade_date, session)] = list(reports)
            self.replaced.append((trade_date, session))
