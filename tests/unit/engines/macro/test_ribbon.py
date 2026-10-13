"""상단 띠 P5 칩(kbj/engines/macro/ribbon.py — D-P5-20): 신용스프레드·신용잔고·한국 대 글로벌."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from kbj.core.p5_rows import MacroSeriesRow, MarketStatRow, UsDailyRow
from kbj.core.quality import Quality
from kbj.core.rows import IndexBar
from kbj.core.time import KST
from kbj.engines.macro.ribbon import (
    CREDIT_AA,
    CREDIT_KTB,
    credit_balance_chip,
    credit_spread_chip,
    kr_vs_global_chip,
)
from tests.fixtures.synthetic.p5_world import LAST_KRX_DAY, build_world

AT = datetime(2026, 11, 17, 6, 0, tzinfo=KST)


def _m(sid: str, d: str, v: str) -> MacroSeriesRow:
    return MacroSeriesRow(sid, d, Decimal(v), "%", "ECOS", AT, Quality.OK, released_at=AT)


def test_credit_spread_hand_value_change_and_stale_when_one_side_lags() -> None:
    aa = [_m(CREDIT_AA, "2026-11-13", "3.20"), _m(CREDIT_AA, "2026-11-16", "3.25")]
    ktb = [
        _m(CREDIT_KTB, "2026-11-13", "2.60"),
        _m(CREDIT_KTB, "2026-11-16", "2.61"),
        _m(CREDIT_KTB, "2026-11-17", "2.62"),
    ]
    c = credit_spread_chip(aa, ktb)
    assert (
        c is not None
        and c.key == "credit_spread"
        and c.tier == "public"
        and c.phase_pending is None
    )
    assert c.value is not None and c.value.value == pytest.approx(0.64)
    assert c.value.quality is Quality.STALE and c.note and "공표 지연" in c.note
    assert c.detail["change_bp"] == pytest.approx(4.0) and c.detail["period"] == "2026-11-16"
    fresh = credit_spread_chip(aa, ktb[:2])
    assert fresh is not None and fresh.value is not None and fresh.value.quality is Quality.OK
    assert credit_spread_chip([], ktb) is None


def _s(item: str, d: date, v: str | None) -> MarketStatRow:
    q = Quality.OK if v is not None else Quality.INVALID
    return MarketStatRow(
        "credit_balance",
        d,
        item,
        None if v is None else Decimal(v),
        "원",
        "DATAGO",
        datetime.combine(d, time(13), tzinfo=KST),
        q,
    )


def test_credit_balance_prefers_total_then_parts_and_flags_lag() -> None:
    d1, d2 = date(2026, 11, 13), date(2026, 11, 16)
    rows = [_s("loan_total", d1, "100"), _s("loan_total", d2, "110"), _s("loan_kospi", d2, "60")]
    c = credit_balance_chip(rows, expected=d2)
    assert c is not None and c.value is not None and c.value.value == 110.0
    assert c.detail["change"] == 10.0 and c.value.quality is Quality.OK
    parts = [_s("loan_kospi", d1, "60"), _s("loan_kosdaq", d1, "40"), _s("loan_kospi", d2, "61")]
    c2 = credit_balance_chip(parts, expected=d2)
    assert c2 is not None and c2.value is not None and c2.value.value == 100.0  # d2 는 반쪽뿐
    assert c2.value.quality is Quality.STALE and "공표 지연" in (c2.note or "")
    assert credit_balance_chip([_s("loan_total", d1, None)], expected=None) is None


def _ib(d: date, c: float) -> IndexBar:
    return IndexBar("0001", d, "코스피", c, c, c, c, 1, 1, "krx", Quality.OK)


def _us(sym: str, d: date, c: float) -> UsDailyRow:
    return UsDailyRow(sym, d, "index", c, c, c, c, None, None, "yahoo", Quality.OK)


def test_kr_vs_global_hand_value() -> None:
    kr = [_ib(date(2026, 11, 13), 2500.0), _ib(date(2026, 11, 16), 2525.0)]  # +1.0%
    us = {
        "^GSPC": [
            _us("^GSPC", date(2026, 11, 12), 5000.0),
            _us("^GSPC", date(2026, 11, 13), 5050.0),
        ],
        "^IXIC": [
            _us("^IXIC", date(2026, 11, 12), 18000.0),
            _us("^IXIC", date(2026, 11, 13), 17820.0),
        ],
    }
    c = kr_vs_global_chip(kr, us)
    assert c is not None and c.tier == "login" and c.value is not None
    assert c.value.value == pytest.approx(1.0 - (1.0 + -1.0) / 2)
    assert c.value.source == "krx+yahoo"
    assert kr_vs_global_chip(kr, {"^GSPC": us["^GSPC"]}) is None  # 한쪽이라도 없으면 None


def test_p5_world_credit_spread_is_stale_because_aa_lags() -> None:
    w = build_world()
    c = credit_spread_chip(w.series(CREDIT_AA), w.series(CREDIT_KTB))
    assert c is not None and c.value is not None and c.value.quality is Quality.STALE
    assert c.detail["period"] < LAST_KRX_DAY.isoformat()
