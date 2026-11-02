"""시장 체력 — ADR(20거래일 등락비율)·지수 이격도(kbj.engines.market.adr)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.engines.market.adr import (
    DayCount,
    adr_series,
    adr_zone,
    daily_counts,
    disparity,
)


def _bar(code: str, d: date, close: float | None) -> Bar:
    return Bar(code=code, date=d, open=None, high=None, low=None, close=close, volume=None,
               turnover=None, source="krx", venue="KRX", quality=Quality.OK)  # fmt: skip


D0 = date(2026, 10, 1)
DAYS = [D0 + timedelta(days=i) for i in range(5)]


def test_daily_counts_by_market_and_gaps() -> None:
    bars = {
        "A": [_bar("A", DAYS[0], 100), _bar("A", DAYS[1], 101), _bar("A", DAYS[2], 101)],
        "B": [_bar("B", DAYS[0], 50), _bar("B", DAYS[1], 49)],
        # C: 하루 빠졌다 — 빠진 다음 날은 세지 않는다(여러 날 변화를 하루로 세지 않는다)
        "C": [_bar("C", DAYS[0], 10), _bar("C", DAYS[2], 12), _bar("C", DAYS[3], 11)],
        "E": [_bar("E", DAYS[0], 10), _bar("E", DAYS[1], 11)],  # ETF — 뺀다
        "X": [_bar("X", DAYS[0], 10), _bar("X", DAYS[1], 11)],  # 코넥스 — 뺀다
    }
    market = {"A": "KOSPI", "B": "KOSPI", "C": "KOSDAQ", "E": "KOSPI", "X": "KONEX"}
    got = daily_counts(bars, market, DAYS, kind_of={"E": "etf"})
    assert got["KOSPI"] == {DAYS[1]: DayCount(1, 1, 0), DAYS[2]: DayCount(0, 0, 1)}
    assert got["KOSDAQ"] == {DAYS[3]: DayCount(0, 1, 0)}


def test_adr_needs_full_window() -> None:
    counts = {D0 + timedelta(days=i): DayCount(3, 2, 1) for i in range(25)}
    pts = adr_series(counts, window=20)
    assert [p.adr for p in pts[:19]] == [None] * 19
    assert pts[19].adr == pytest.approx(150.0) and pts[19].up20 == 60 and pts[19].down20 == 40
    assert pts[-1].up == 3 and pts[-1].flat == 1
    zero = adr_series({D0 + timedelta(days=i): DayCount(1, 0, 0) for i in range(20)}, window=20)
    assert zero[-1].adr is None  # 하락 합 0 — 값 없음(무한대로 그리지 않는다)


def test_zone() -> None:
    assert adr_zone(120.0) == "과열" and adr_zone(80.0) == "바닥권" and adr_zone(100.0) == "중립"
    assert adr_zone(None) is None


def test_disparity() -> None:
    closes = [(D0 + timedelta(days=i), float(v)) for i, v in enumerate([10, 20, 30, 40])]
    got = disparity(closes, 2)
    assert got[0][1] is None
    assert got[1][1] == pytest.approx(20 / 15 * 100)
    assert got[3][1] == pytest.approx(40 / 35 * 100)
    holes = disparity([(D0, 10.0), (DAYS[1], None), (DAYS[2], 12.0)], 2)
    assert [v for _, v in holes] == [None, None, None]  # 창 안 빈 값 — 지어내지 않는다
