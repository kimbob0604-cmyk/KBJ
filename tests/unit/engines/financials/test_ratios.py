"""DART 비율(`ratios`) — 손계산·분모 ≤ 0 사유·부호 전환·기준 바뀜(docs/metrics.md §9)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from kbj.core.fin_rows import NaReason, QuarterRow, ShareCount
from kbj.core.quality import Quality
from kbj.engines.financials.period import fiscal_quarter
from kbj.engines.financials.ratios import ratios, series


def _q(year: int, q: int, **kw: int | float) -> QuarterRow:
    """12월 결산 연결 분기 한 줄(기본값 + 바꿀 칸)."""
    p = fiscal_quarter(year, q, 12)
    row = QuarterRow(
        "99000001", year, q, "CFS", p.start, p.end, None, "full",
        revenue=100, op=10, ni=8, ni_owner=7, total_assets=1000, total_liabilities=400,
        total_equity=600, equity_owner=500, net_debt=50, eps_basic=70.0,
    )  # fmt: skip
    return replace(row, **kw)  # type: ignore[arg-type]


def _year(year: int, **kw: int | float) -> list[QuarterRow]:
    return [_q(year, k, **kw) for k in (1, 2, 3, 4)]


def test_손계산() -> None:
    rows = [*_year(2024), *_year(2025, revenue=120, op=18, total_assets=1200, equity_owner=700)]
    sc = ShareCount("99000001", date(2025, 12, 31), "20260315000001", 10_000, None, 1_000, None)
    r = ratios(rows, [sc])[-1]
    assert r.period == "2025Q4"
    assert (r.ttm_revenue, r.ttm_op, r.ttm_ni_owner) == (480, 72, 28)
    assert r.opm == pytest.approx(15.0) and r.npm == pytest.approx(28 / 480 * 100)
    assert r.roe == pytest.approx(28 / ((500 + 700) / 2) * 100)
    assert r.roa == pytest.approx(32 / ((1000 + 1200) / 2) * 100)
    assert r.debt_ratio == pytest.approx(400 / 600 * 100)
    assert r.net_debt_ratio == pytest.approx(50 / 600 * 100)
    assert r.rev_yoy == pytest.approx(20.0) and r.op_yoy == pytest.approx(80.0)
    assert r.eps_ttm == pytest.approx(280.0)
    assert r.shares_basis == 9_000 and r.bps == pytest.approx(700 / 9_000)
    assert r.na_reasons == {} and r.quality == Quality.OK


def test_ttm_이_없으면_사유() -> None:
    r = ratios(_year(2025)[:3])[-1]
    assert r.ttm_revenue is None and r.opm is None
    assert (
        r.na_reasons["opm"] == NaReason.MISSING_INPUT
        and r.na_reasons["bps"] == NaReason.MISSING_INPUT
    )
    assert any("TTM" in n for n in r.notes)


def test_분모_0_이하() -> None:
    rows = [*_year(2024, revenue=0, total_equity=-5, equity_owner=-5),
            *_year(2025, revenue=0, total_equity=-5, equity_owner=-5)]  # fmt: skip
    r = ratios(rows)[-1]
    assert r.na_reasons["opm"] == NaReason.NO_REVENUE
    assert r.na_reasons["roe"] == NaReason.CAPITAL_IMPAIRMENT
    assert r.na_reasons["debt_ratio"] == NaReason.CAPITAL_IMPAIRMENT
    assert r.na_reasons["rev_yoy"] == NaReason.MISSING_INPUT  # 전년 0


def test_부호_전환은_값_대신_문구() -> None:
    rows = [*_year(2024, op=-5), *_year(2025, op=7)]
    r = ratios(rows)[-1]
    assert r.op_yoy is None and "영업이익 흑자전환" in r.notes


def test_cfs_우선_없으면_ofs_기준이_바뀌면_문구() -> None:
    ofs = [replace(x, fs_div="OFS") for x in _year(2024)]
    cfs = _year(2025)
    s = series([*ofs, *cfs, replace(cfs[0], fs_div="OFS")])
    assert [x.fs_div for x in s] == ["OFS"] * 4 + ["CFS"] * 4
    rr = ratios([*ofs, *cfs])
    assert any("기준이 OFS → CFS" in n for n in rr[4].notes)
    assert rr[4].ttm_revenue is None  # 같은 기준 4분기가 아직 없다
    # 연결/별도 혼재 → estimated(metrics §9), 바뀐 뒤의 분기는 입력 품질 그대로(검증 보강)
    assert rr[4].quality == Quality.ESTIMATED and rr[5].quality == Quality.OK


def test_추정_입력이면_추정() -> None:
    rows = _year(2025)
    rows[1] = replace(rows[1], quality=Quality.ESTIMATED)
    assert ratios(rows)[-1].quality == Quality.ESTIMATED
