"""PIT 재무·NTM·하루 배수 행 — 접수일 ≤ t 만, 재무 지문, 신규상장 사유, 그날 주식 수(§4.8)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.engines.valuation.multiples import CompanyCap, Facts
from kbj.engines.valuation.pit import (
    ConsensusBook,
    DayInput,
    PitBook,
    daily_series,
    per_share,
    share_basis,
    value_day,
)
from tests.unit.engines.valuation.val_rows import (
    CAPS,
    CODE,
    estimate,
    quarter,
    share_count,
    year_of_quarters,
)


def _book() -> PitBook:
    rows = year_of_quarters(2024) + year_of_quarters(2025, revenue=2_000)
    return PitBook(rows)


def test_접수일_이전에는_그_분기를_모른다() -> None:
    book = _book()
    assert book.at(date(2024, 5, 14)).balance is None  # 첫 보고서 전 — 재무 없음
    v = book.at(date(2025, 3, 19))  # 2024 4Q(3/20 접수) 전
    assert v.ttm is None  # 2024 1~3Q 셋뿐
    assert v.balance is not None and v.balance.ref == "2024Q3:CFS"
    v = book.at(date(2025, 3, 20))
    assert v.ttm is not None and v.ttm.ref == "2024Q4:CFS" and v.ttm.revenue == 4_000
    v = book.at(date(2025, 11, 14))  # 2025 3Q 접수일 — 2024Q4~2025Q3
    assert v.ttm is not None and v.ttm.revenue == 1_000 + 3 * 2_000
    assert v.known_through == date(2025, 11, 14)


def test_TTM_칸_하나라도_없으면_그_칸_None() -> None:
    rows = year_of_quarters(2025)
    rows[1] = replace(rows[1], ni_owner=None, eps_basic=None)
    v = PitBook(rows).at(date(2026, 4, 1))
    assert v.ttm is not None and v.ttm.ni_owner is None and v.ttm.eps is None
    assert v.ttm.revenue == 4_000


def test_TTM_은_마지막_분기의_기준과_층을_따른다() -> None:
    rows = [replace(r, tier="main") for r in year_of_quarters(2025)]
    rows += [quarter(2025, 4, rcept=date(2026, 3, 20), fs="OFS")]
    v = PitBook(rows).at(date(2026, 4, 1))
    assert v.ttm is not None and v.ttm.tier == "main" and v.ttm.ref == "2025Q4:CFS"
    assert v.balance is not None and v.balance.ref == "2025Q4:CFS"  # 같은 분기면 CFS


def test_재무_지문은_알려진_것만_본다() -> None:
    rows = year_of_quarters(2025)
    t = date(2025, 12, 1)
    base = PitBook(rows).at(t).digest
    later = [*rows, quarter(2026, 1, rcept=date(2026, 5, 15))]
    assert PitBook(later).at(t).digest == base  # 미래 분기를 더해도 그대로
    fixed = [replace(rows[0], revenue=999), *rows[1:]]
    assert PitBook(fixed).at(t).digest != base  # 과거 분기가 바뀌면 다르다(백필·정정)
    assert PitBook([]).at(t).digest == PitBook([]).at(date(2020, 1, 1)).digest


def test_신규상장과_반기보고_사유() -> None:
    book = PitBook([quarter(2026, 3, rcept=date(2026, 11, 13))])
    v = book.at(date(2026, 11, 13))
    assert v.ttm is None and v.balance is not None
    assert v.ttm_gap(date(2026, 10, 15)) is NaReason.SHORT_HISTORY
    semi = PitBook(
        [
            quarter(y, q, rcept=date(y + (q == 4), 3 if q == 4 else 8, 14))
            for y in (2023, 2024, 2025)
            for q in (2, 4)
        ]
    )
    sv = semi.at(date(2026, 4, 1))
    assert sv.ttm is None and sv.ttm_gap(date(2019, 6, 20)) is NaReason.MISSING_INPUT
    assert sv.ttm_gap(None) is NaReason.MISSING_INPUT
    assert sv.reports_since(date(2025, 1, 1)) == 3  # 2024 4Q(25.03)·2025 2Q·2025 4Q


def test_그날_주식_수() -> None:
    sc = share_count(date(2026, 9, 30), rcept=date(2026, 11, 13), common=1_000, treasury=50)
    v = PitBook(year_of_quarters(2025), [sc]).at(date(2026, 11, 13))
    assert share_basis(v, 1_000) == (950, "DART")
    assert share_basis(v, None) == (950, "DART")
    assert share_basis(v, 5_000) == (5_000, "KRX")  # 분할(×5) 뒤 — DART 는 분기말 기준이라 늦다
    assert share_basis(PitBook([]).at(date(2026, 1, 1)), 700) == (700, "KRX")
    assert share_basis(PitBook([]).at(date(2026, 1, 1)), None) == (None, None)


def test_보고된_주당_값이_그날_주식_수와_어긋나면_다시_센다() -> None:
    assert per_share(1_000.0, 10.0, 100) == (10.0, False)
    assert per_share(1_000.0, 10.0, 500) == (2.0, True)  # 분할 5:1 — 분할 전 EPS
    assert per_share(None, 10.0, 500) == (10.0, False)
    assert per_share(1_000.0, None, 400) == (2.5, True)
    assert per_share(None, None, 400) == (None, False)


def test_NTM_은_그날_이하_마지막_스냅() -> None:
    d1, d2 = date(2026, 9, 1), date(2026, 10, 1)
    rows = [
        estimate(CODE, d1, "202612", eps=10.0, ni_owner=100),
        estimate(CODE, d1, "202712", eps=12.0, ni_owner=120),
        estimate(CODE, d2, "202612", eps=20.0, ni_owner=200),
        estimate(CODE, d2, "202712", eps=24.0, ni_owner=240),
    ]
    book = ConsensusBook(rows)
    assert book.ntm(date(2026, 8, 31)) is None
    n1 = book.ntm(date(2026, 9, 15))
    n2 = book.ntm(date(2026, 10, 2))
    assert n1 is not None and n1.fy1.eps == 10.0 and n1.quality is Quality.STALE
    assert n2 is not None and n2.fy1.eps == 20.0


def _cap(
    day: date, value: int = 10_000, *, quality: Quality = Quality.OK, missing: tuple[str, ...] = ()
) -> CompanyCap:
    return CompanyCap(day, value, "krx", quality, (CODE,), missing)


def test_하루_행_flags() -> None:
    d = date(2026, 4, 1)
    book = PitBook(year_of_quarters(2025, ni_owner=10))  # TTM 지배순익 40 → PER 250(거름값 밖)
    row = value_day(
        CODE, DayInput(d, _cap(d), 100.0, 100), book.at(d), Facts(kind="common"), caps=CAPS
    )
    assert row.per == pytest.approx(250.0) and row.flags["out_of_cap"] == ["per"]
    assert row.flags["fin_digest"] == book.at(d).digest
    assert row.ttm_ref == "2025Q4:CFS" and row.source == "krx+DART"
    assert "na" not in row.flags or "per" not in row.flags["na"]
    loss = value_day(
        CODE,
        DayInput(d, _cap(d), 100.0, 100),
        PitBook(year_of_quarters(2025, ni_owner=-1)).at(d),
        Facts(),
        caps=CAPS,
    )
    assert loss.per is None and loss.flags["na"]["per"] == "loss"


def test_회사_시총이_없으면_모두_missing_input() -> None:
    d = date(2026, 4, 1)
    row = value_day(
        CODE,
        DayInput(d, None, 100.0),
        PitBook([]).at(d),
        Facts(),
        caps=CAPS,
        notes=["보통주 짝 없음"],
    )
    assert row.mktcap_company is None and row.quality is Quality.ESTIMATED
    assert set(row.flags["na"].values()) == {"missing_input"}
    assert "보통주 짝 없음" in row.flags["notes"]


def test_빠진_종류주식은_notes_와_estimated() -> None:
    d = date(2026, 4, 1)
    row = value_day(
        CODE,
        DayInput(d, _cap(d, missing=("990015",), quality=Quality.ESTIMATED), 100.0),
        PitBook(year_of_quarters(2025)).at(d),
        Facts(),
        caps=CAPS,
    )
    assert row.quality is Quality.ESTIMATED
    assert any("990015" in n for n in row.flags["notes"])


def test_여러_날() -> None:
    days = [date(2026, 3, 19), date(2026, 3, 20)]
    book = PitBook(
        year_of_quarters(
            2025,
            filed={
                1: date(2025, 5, 15),
                2: date(2025, 8, 14),
                3: date(2025, 11, 14),
                4: date(2026, 3, 20),
            },
        )
    )
    rows = daily_series(CODE, [DayInput(d, _cap(d), 100.0) for d in days], book, Facts(), caps=CAPS)
    assert rows[0].per is None and rows[0].flags["na"]["per"] == "missing_input"
    assert rows[1].per == pytest.approx(10_000 / 300)
