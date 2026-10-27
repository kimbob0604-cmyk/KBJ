"""`kbj.engines.gex.futures_oi` — 코스피200 선물 OI·해석 라벨·파생 투자자 일별(metrics §32).

손계산 값과 비교한다(비율은 허용오차 — CLAUDE §4). 시계는 쓰지 않는다(입력 날짜·시각만).
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.gex.futures_oi import (
    OI_FLAT_PCT,
    PRICE_FLAT_PCT,
    BoardRow,
    FlowRow,
    SeriesDay,
    build_oi_board,
    confirmed_days,
    foreign_streak,
    futures_flows,
    last_trade_date,
    oi_label,
    option_flows,
    provisional_day,
)

CAL = TradingCalendar.default()
RX = datetime(2026, 11, 5, 23, 5, tzinfo=UTC)  # KRX 수신 시각(행 ts) — 값은 아무거나 고정


def _s(
    d: date, expiry: str, oi: int | None, close: str | None = None, cmp: str | None = None
) -> SeriesDay:
    return SeriesDay(
        trade_date=d, expiry=expiry, close=None if close is None else Decimal(close),
        cmp_prev=None if cmp is None else Decimal(cmp), oi=oi, received_at=RX,
    )  # fmt: skip


D1, D2, D3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)
BASE = [
    _s(D1, "202612", 200_000, "400.00", "2.00"),
    _s(D1, "202703", 30_000, "401.00", "2.00"),
    _s(D1, "202706", 1_000, "402.00", "2.00"),
    _s(D2, "202612", 203_000, "404.00", "4.00"),
    _s(D2, "202703", 30_500, "405.00", "4.00"),
    _s(D2, "202706", 1_000, "406.00", "4.00"),
]


def _close(a: float | None, b: float) -> bool:
    return a is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-4)


def test_expiry_dates_come_from_the_canonical_calendar() -> None:
    assert last_trade_date("202612", CAL) == date(2026, 12, 10)  # 둘째 목요일
    with pytest.raises(ValueError, match="YYYYMM"):
        last_trade_date("2612", CAL)


@pytest.mark.parametrize(
    ("price", "oi", "label"),
    [
        (1.0, 1.0, "new_long"),
        (-1.0, 1.0, "new_short"),
        (1.0, -1.0, "short_cover"),
        (-1.0, -1.0, "long_liquidation"),
        (PRICE_FLAT_PCT - 0.0001, 5.0, "flat"),
        (1.0, -(OI_FLAT_PCT - 0.0001), "flat"),
        (PRICE_FLAT_PCT, OI_FLAT_PCT, "new_long"),  # 경계값 자체는 보합이 아니다(< 만 보합)
        (None, 1.0, "unknown"),
        (1.0, None, "unknown"),
    ],
)
def test_label_quadrants_and_flat_band(price: float | None, oi: float | None, label: str) -> None:
    got, why = oi_label(price, oi)
    assert got == label and why


def test_confirmed_day_total_change_and_label() -> None:
    d1, d2 = confirmed_days(BASE, CAL)
    assert d1.label == "unknown" and "전일 없음" in d1.reasons and d1.oi_change is None
    assert d1.total_oi == 231_000 and d1.quality is Quality.OK
    assert (d2.total_oi, d2.oi_change, d2.oi_change_raw) == (234_500, 3_500, 3_500)
    assert _close(d2.oi_change_pct, 3_500 / 231_000 * 100)
    assert (d2.near_expiry, d2.near_oi, d2.near_close) == ("202612", 203_000, Decimal("404.00"))
    assert _close(d2.near_change_pct, 4 / 400 * 100)
    assert d2.label == "new_long" and not d2.provisional and d2.source.startswith("KRX:")


def test_expiry_day_rolls_the_near_month_and_drops_the_settled_series_next_day() -> None:
    e0, e1, e2 = date(2026, 12, 9), date(2026, 12, 10), date(2026, 12, 11)
    rows = [
        _s(e0, "202612", 150_000, "410", "1"), _s(e0, "202703", 120_000, "412", "1.2"),
        _s(e0, "202706", 2_000, "413", "1"),
        _s(e1, "202612", 140_000, "411", "1"), _s(e1, "202703", 125_000, "413", "1"),
        _s(e1, "202706", 2_000, "414", "1"),
        # 만기 다음 날: 12월물 소멸, 9월물 신규 상장(전일 0)
        _s(e2, "202703", 128_000, "410", "-3"), _s(e2, "202706", 2_500, "411", "-3"),
        _s(e2, "202709", 100, "412", "0"),
    ]  # fmt: skip
    _, x1, x2 = confirmed_days(rows, CAL)
    assert x1.expiry_day and x1.near_expiry == "202703"  # 만기일 근월물 = 다음 월물
    # 만기일 OI > 0 → 결제 전 잔고로 넣는다(롤 매도 −10,000 과 롤 매수 +5,000 이 상쇄)
    assert (x1.oi_change, x1.oi_change_raw) == (-5_000, -5_000)
    assert _close(x1.oi_change_pct, -5_000 / 272_000 * 100)
    assert _close(x1.near_change_pct, 1 / 412 * 100) and x1.label == "short_cover"
    assert not x2.expiry_day and x2.near_expiry == "202703"
    # 사라진 12월물(−140,000)은 라벨 입력에서 빠지고 단순 차에만 남는다
    assert (x2.oi_change, x2.oi_change_raw) == (3_600, 130_600 - 267_000)
    assert _close(x2.oi_change_pct, 3_600 / 127_000 * 100)
    assert x2.label == "new_short"


def test_expiry_day_zero_oi_row_is_treated_as_settled() -> None:
    e0, e1 = date(2026, 12, 9), date(2026, 12, 10)
    rows = [
        _s(e0, "202612", 150_000, "410", "1"), _s(e0, "202703", 120_000, "412", "1"),
        _s(e1, "202612", 0, "411", "1"), _s(e1, "202703", 125_000, "413", "1"),
    ]  # fmt: skip
    _, x1 = confirmed_days(rows, CAL)
    assert (x1.oi_change, x1.oi_change_raw) == (5_000, -145_000)
    assert _close(x1.oi_change_pct, 5_000 / 120_000 * 100)


def test_missing_previous_trading_day_and_blank_oi_are_not_filled() -> None:
    gap = [r for r in BASE if r.trade_date == D1] + [
        _s(D3, "202612", 205_000, "406", "2"), _s(D3, "202703", 31_000, "407", "2"),
    ]  # fmt: skip
    _, d3 = confirmed_days(gap, CAL)
    assert d3.oi_change is None and d3.label == "unknown" and "전일 없음" in d3.reasons
    blank = [*BASE[:3], _s(D2, "202612", None, "404", "4"), *BASE[4:]]
    _, b2 = confirmed_days(blank, CAL)
    assert b2.total_oi is None and b2.quality is Quality.INVALID and b2.label == "unknown"


def _board(
    oi: tuple[int | None, ...] = (204_000, 31_500, 1_000), price: str = "406.0"
) -> list[BoardRow]:
    ts = datetime(2026, 11, 4, 6, 44, tzinfo=UTC)  # 15:44 KST
    days = (37, 128, 219)
    return [
        BoardRow(ts=ts, code=f"A0{i}", price=Decimal(price) + i, oi=o, remaining_days=days[i],
                 quality=Quality.OK)
        for i, o in enumerate(oi)
    ]  # fmt: skip


def test_provisional_row_uses_the_board_against_the_last_confirmed_day() -> None:
    p = provisional_day(D3, _board(), BASE, CAL)
    assert p is not None and p.provisional and p.quality is Quality.ESTIMATED
    assert p.source.startswith("KIS:") and p.trade_date == D3
    assert (p.total_oi, p.oi_change, p.oi_change_raw) == (236_500, 2_000, 2_000)
    assert _close(p.oi_change_pct, 2_000 / 234_500 * 100)
    assert p.near_expiry == "202612" and p.near_change == Decimal("2.0")
    assert _close(p.near_change_pct, 2 / 404 * 100) and p.label == "new_long"
    # 확정 행이 이미 있으면 잠정은 없다
    assert provisional_day(D2, _board(), BASE, CAL) is None
    assert provisional_day(D3, [], BASE, CAL) is None


def test_provisional_guards() -> None:
    bad = provisional_day(D3, _board((100_000, 1_000, 1_000)), BASE, CAL)
    assert bad is not None and bad.quality is Quality.INVALID and bad.oi_change is None
    assert bad.label == "unknown" and any("구성 불일치" in r for r in bad.reasons)
    gap = provisional_day(D3, _board(), BASE[:3], CAL)  # 직전 확정일이 11-02 — 직전 거래일 아님
    assert gap is not None and gap.oi_change is None and "전일 확정 없음" in gap.reasons
    blank = provisional_day(D3, _board((204_000, None, 1_000)), BASE, CAL)
    assert blank is not None and blank.total_oi is None and blank.label == "unknown"
    e0, e1 = date(2026, 12, 9), date(2026, 12, 10)
    conf = [_s(e0, "202612", 150_000, "410", "1"), _s(e0, "202703", 120_000, "412", "1")]
    on_expiry = provisional_day(e1, _board((150_000, 121_000, 0)), conf, CAL)
    assert on_expiry is not None and on_expiry.expiry_day and on_expiry.label == "unknown"
    conf2 = [*conf, _s(e1, "202612", 140_000, "411", "1"), _s(e1, "202703", 125_000, "413", "1")]
    after = provisional_day(date(2026, 12, 11), _board((126_000, 2_000, 100)), conf2, CAL)
    assert after is not None and after.label == "unknown"


def _flow(d: date, hhmm: tuple[int, int], investor: str, qty: int | None, value: int | None,
          sector: str = "F001", market: str = "K2I") -> FlowRow:  # fmt: skip
    ts = datetime.combine(d, time(*hhmm), tzinfo=KST).astimezone(UTC)
    return FlowRow(d, ts, market, sector, investor, qty, value, Quality.OK)


def test_futures_flow_quality_depends_on_the_15_45_close_and_is_in_won() -> None:
    rows = [
        _flow(D2, (15, 40), "frgn", 3_451, 345), _flow(D2, (15, 40), "prsn", -1_000, -100),
        _flow(D2, (15, 40), "orgn", -2_451, -245), _flow(D2, (15, 40), "scrt", 5, 1),
        _flow(D3, (15, 46), "frgn", 10, 1),
    ]  # fmt: skip
    got = futures_flows(rows)
    assert [n.investor for n in got[D2]] == ["foreign", "institution", "individual"]
    f2 = got[D2][0]
    assert (f2.net_qty, f2.net_value_won) == (3_451, 345_000_000)
    assert f2.quality is Quality.ESTIMATED and "15:40" in f2.reason
    assert got[D3][0].quality is Quality.OK and got[D3][0].reason == ""


def test_option_flows_keep_series_call_put_and_investor_order() -> None:
    rows = [
        _flow(D2, (15, 46), "frgn", 100, 20, "OP01"), _flow(D2, (15, 46), "frgn", -50, -10, "OC01"),
        _flow(D2, (15, 46), "prsn", 7, 1, "OC05", "WKM"), _flow(D2, (15, 46), "frgn", 1, 1, "F001"),
    ]  # fmt: skip
    got = option_flows(rows)
    assert [(o.series, o.cp, o.investor) for o in got] == [
        ("monthly", "C", "foreign"), ("monthly", "P", "foreign"), ("weekly_mon", "C", "individual"),
    ]  # fmt: skip
    assert got[0].net_value_won == -10_000_000


def test_foreign_streak() -> None:
    days = [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)]
    rows = [_flow(days[0], (15, 46), "frgn", -5, -1)] + [
        _flow(d, (15, 46), "frgn", 100, 10) for d in days[1:]
    ]
    assert foreign_streak(futures_flows(rows), CAL) == 3  # 10-24·25 주말은 건너뛴다
    sell = [_flow(d, (15, 46), "frgn", -1, -1) for d in days[2:]]
    assert foreign_streak(futures_flows(sell), CAL) == -2
    zero = [*rows, _flow(date(2026, 10, 27), (15, 46), "frgn", 0, 0)]
    assert foreign_streak(futures_flows(zero), CAL) == 0
    gap = [_flow(days[1], (15, 46), "frgn", 1, 1), _flow(days[3], (15, 46), "frgn", 1, 1)]
    assert foreign_streak(futures_flows(gap), CAL) == 1
    assert foreign_streak({}, CAL) is None


def test_build_board_trims_days_attaches_flows_and_limits_options_to_the_window() -> None:
    flows = [
        _flow(D1, (15, 46), "frgn", 1, 1, "OC01"),  # 창 밖(첫 표시일 D2 전)
        _flow(D2, (15, 46), "frgn", 3_451, 345), _flow(D2, (15, 46), "frgn", 9, 1, "OC01"),
        _flow(D3, (15, 40), "frgn", 100, 10),
    ]  # fmt: skip
    b = build_oi_board(BASE, flows, _board(), target=D3, cal=CAL, days=1)
    assert [d.trade_date for d in b.days] == [D2]
    assert b.days[0].futures_flow[0].net_qty == 3_451
    assert b.provisional is not None and b.provisional.futures_flow[0].net_qty == 100
    assert [o.trade_date for o in b.options] == [D2]
    assert b.foreign_streak == 2
    none = build_oi_board(BASE, [], [], target=None, cal=CAL, days=60)
    assert len(none.days) == 2 and none.provisional is None and none.foreign_streak is None
    with pytest.raises(ValueError, match="days"):
        build_oi_board(BASE, [], [], target=None, cal=CAL, days=0)
