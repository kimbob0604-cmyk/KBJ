"""마스터 ⊇ KRX 행사가 대조 (설계 §5·§10) — 양쪽 방향.

실측 모양: 2609W4 는 KRX 09-23 106개(970.0~1232.5), 09-28 마스터 111개(970.0~1245.0) — 마스터가
더 많아도(신규 상장) 정상이고, KRX 에 있는데 마스터에 없으면 빠진 것이다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from data.kis.master import MasterRow, parse_master
from services.scheduler.master_check import master_covers_krx
from tests.fakes.kis_server import FakeChain, FakeSeries

D23 = date(2026, 9, 23)
WK_LO, WK_HI = Decimal("970.0"), Decimal("1245.0")
M_LO, M_HI = Decimal("745.0"), Decimal("1595.0")

Row = tuple[str, str, str, Decimal]


def master(*series: FakeSeries, extra: tuple[str, ...] = ()) -> list[MasterRow]:
    lines = FakeChain(series=series).master_lines() + list(extra)
    return parse_master("\n".join(lines))


def wkm(lo: Decimal = WK_LO, hi: Decimal = WK_HI) -> FakeSeries:
    return FakeSeries("WKM", "260904", date(2026, 9, 28), lo, hi)


def monthly(lo: Decimal = M_LO, hi: Decimal = M_HI) -> FakeSeries:
    return FakeSeries("", "202610", date(2026, 10, 8), lo, hi)


def krx(family: str, expiry: str, lo: str, hi: str, cps: str = "CP") -> list[Row]:
    s = FakeSeries("", expiry, D23, Decimal(lo), Decimal(hi))
    return [(family, expiry, cp, k) for k in s.strikes for cp in cps]


def test_a_master_with_newly_listed_strikes_covers_krx() -> None:
    """2609W4: KRX 106개 ⊂ 마스터 111개 — 새로 상장된 5개(콜·풋 10)는 health 가 아니다."""
    listing = krx("kospi200_weekly_mon", "260904", "970.0", "1232.5")
    assert len(listing) == 106 * 2
    rep = master_covers_krx(master(wkm()), listing, D23)
    assert rep.ok and rep.missing == 0 and rep.gaps == ()
    assert (rep.compared, rep.krx_strikes, rep.new_strikes) == (1, 212, 10)
    assert rep.krx_only == () and rep.master_only == ()
    assert rep.describe().startswith("마스터 ⊇ KRX 2026-09-23 — 대조 만기 1개·KRX 행사가 212개")


def test_krx_strikes_missing_from_the_master_are_reported_with_counts() -> None:
    listing = krx("kospi200_weekly_mon", "260904", "970.0", "1232.5")
    listing += [
        ("kospi200_weekly_mon", "260904", "C", Decimal("1250.0")),
        ("kospi200", "202610", "P", Decimal("1600.0")),
        ("kospi200", "202610", "P", Decimal("740.0")),
        ("kospi200", "202610", "C", Decimal("1100.0")),
    ]
    rep = master_covers_krx(master(wkm(), monthly()), listing, D23)
    assert not rep.ok and rep.missing == 3 and rep.compared == 2
    assert [(g.family, g.expiry, g.cp, g.missing) for g in rep.gaps] == [
        ("kospi200", "202610", "P", (Decimal("740.00"), Decimal("1600.00"))),
        ("kospi200_weekly_mon", "260904", "C", (Decimal("1250.00"),)),
    ]
    text = rep.describe()
    assert text.startswith("마스터에 없는 KRX 2026-09-23 행사가 3개(시리즈 2개)")
    assert "코스피200 202610 P 740.0·1,600.0" in text and "위클리(월) 260904 C 1,250.0" in text
    assert len(text) < 300  # health detail 상한 안


def test_only_expiries_present_in_both_are_compared() -> None:
    """KRX 에만 있는 만기(전날 만기 지남 — 새 마스터에서 빠짐)·마스터에만 있는 만기(오늘 새 위클리)
    는 대조하지 않는다."""
    listing = krx("kospi200_weekly_mon", "260904", "970.0", "1232.5")
    listing += krx(
        "kospi200_weekly_thu", "260904", "1000.0", "1100.0"
    )  # 09-24 만기 → 마스터에 없다
    new_weekly = FakeSeries("WKI", "261001", date(2026, 10, 1), WK_LO, WK_HI)
    rep = master_covers_krx(master(wkm(), new_weekly), listing, D23)
    assert rep.ok and rep.compared == 1
    assert rep.krx_only == (("kospi200_weekly_thu", "260904"),)
    assert rep.master_only == (("kospi200_weekly_thu", "261001"),)
    assert "KRX 만 있는 만기 1개·마스터만 있는 만기 1개는 정상" in rep.describe()


def test_strikes_compare_by_value_and_other_families_are_ignored() -> None:
    kosdaq = "7|Q01610A01|KR4Q016AA011|코스닥150C 202610 1,000|3|01000.00| |3003|KSQ150"
    listing: list[Row] = [
        ("kospi200", "202610", "C", Decimal("1125")),  # 마스터 01125.00
        ("kospi200", "202610", "C", Decimal("1127.50")),
        ("kosdaq150", "202610", "C", Decimal("1000")),  # 적재 대상이 아니다
    ]
    rows = master(monthly(), extra=(kosdaq,))
    assert any(r.family == "kosdaq150" for r in rows)
    rep = master_covers_krx(rows, listing, D23)
    assert rep.ok and rep.compared == 1 and rep.krx_strikes == 2
    assert rep.master_only == ()  # 코스닥150 마스터 행은 보지 않는다


def test_a_call_series_missing_entirely_under_a_shared_expiry_is_missing() -> None:
    """같은 만기가 둘 다에 있으면 콜·풋 어느 쪽이든 KRX 행사가는 모두 있어야 한다."""
    puts_only = "\n".join(
        line for line in FakeChain(series=(wkm(),)).master_lines() if "P " in line
    )
    listing = krx("kospi200_weekly_mon", "260904", "1000.0", "1010.0")
    rep = master_covers_krx(parse_master(puts_only), listing, D23)
    assert rep.missing == 5 and [g.cp for g in rep.gaps] == ["C"]
    assert "외 2개" in rep.gaps[0].describe()


def test_many_gaps_are_summarised() -> None:
    listing: list[Row] = []
    for e in ("202610", "202611", "202612", "202701"):
        listing += krx("kospi200", e, "2000.0", "2010.0", cps="C")
    series = [FakeSeries("", e, D23, M_LO, M_HI) for e in ("202610", "202611", "202612", "202701")]
    rep = master_covers_krx(master(*series), listing, D23)
    assert rep.missing == 20 and len(rep.gaps) == 4
    assert "외 1개 시리즈" in rep.describe() and len(rep.describe()) < 300
