"""종목 수출 지수·겹쳐 보기(metrics §28 — 묶음 MAP): 품목·국가·지역 줄, 결측(0 채움 금지),
분기 합·YoY, 매출 YoY, 시차 상관, 상태 네 가지, 랭킹. 손으로 만든 작은 행 + 합성 세계
(p6_world — 고정 시드, 실데이터 아님)."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

import pytest

from kbj.config.trade import OverlayCfg
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import (
    ItemCountryMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    StockMapRow,
)
from kbj.engines.trade.explore import months_between
from kbj.engines.trade.mapping import resolve
from kbj.engines.trade.overlay import (
    build_overlay,
    export_index,
    lag_corr,
    pearson,
    quarter_of,
    quarter_sums,
    rank,
    rank_yoy,
    revenue_yoy,
    sources,
    to_rows,
)
from tests.fixtures.synthetic.p6_world import build_world

T = datetime(2026, 12, 11, 21, 0, tzinfo=KST)
D = Decimal
CFG = OverlayCfg(min_quarters=8, lags=(0, 1, 2), rank_months=3)
MONTHS = months_between("2024-01", "2026-09")


def _it(ym: str, hs: str, usd: int | None) -> ItemMonthRow:
    return ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs), exp_usd=None if usd is None else D(usd), exp_kg=None,
        imp_usd=D(1), imp_kg=None, bal_usd=None, source="DATAGO:15101609", as_of=T,
        quality=Quality.OK,
    )  # fmt: skip


def _ic(ym: str, cc: str, hs: str, usd: int) -> ItemCountryMonthRow:
    return ItemCountryMonthRow(
        ym=ym, country=cc, hs=hs, hs_len=len(hs), exp_usd=D(usd), exp_kg=None, imp_usd=D(0),
        imp_kg=None, bal_usd=None, source="DATAGO:15100475", as_of=T, quality=Quality.OK,
    )  # fmt: skip


def _sg(ym: str, rid: str, hs6: str, usd: int, sido: str = "41") -> SigunguMonthRow:
    return SigunguMonthRow(
        ym=ym, sido_cd=sido, sgg_nm="가상시", hs6=hs6, region_id=rid, exp_usd=D(usd),
        exp_cnt=1, imp_usd=D(0), imp_cnt=0, bal_usd=None, source="DATAGO:15134343", as_of=T,
        quality=Quality.OK,
    )  # fmt: skip


def _line(
    hs: str, w: float, countries: tuple[str, ...] = (), region: str = "", code: str = "990001"
) -> StockMapRow:
    return StockMapRow(
        code=code, hs=hs, flow="exp", region_id=region, name="가상", weight=w, origin="public",
        reviewed=False, map_version="v", source="KBJ:trade_map", as_of=T, quality=Quality.OK,
        countries=countries, evidence=({"kind": "synthetic", "note": "x"},),
    )  # fmt: skip


def test_품목·국가·지역_줄의_가중합() -> None:
    ym = "2026-09"
    src = sources(
        [_it(ym, "8542", 1000), _it(ym, "854231", 300), _it(ym, "854232", 700)],
        [_ic(ym, "US", "8542", 400), _ic(ym, "CN", "8542", 250), _ic(ym, "VN", "8542", 50)],
        [_sg(ym, "KR-41-a01", "330499", 80), _sg(ym, "KR-41-a01", "330499", 20, sido="12")],
        months=[ym],
    )
    lines = [
        _line("8542", 0.5),  # 1000
        _line("8542", 0.2, countries=("US", "CN")),  # 650
        _line("330499", 0.1, region="KR-41-a01"),  # 100(개편 전후 두 시도 행 합)
    ]
    got, has, notes = export_index(lines, src)
    assert has and notes == ()
    assert got[ym] == (D("500") + D("130") + D("10"), Quality.OK)


def test_국가_표에_없는_접두는_전_국가_값으로_추정() -> None:
    ym = "2026-09"
    src = sources([_it(ym, "854232", 700)], [_ic(ym, "US", "8542", 400)], [], months=[ym])
    got, has, notes = export_index([_line("854232", 1.0, countries=("US",))], src)
    assert has and got[ym] == (D(700), Quality.ESTIMATED)
    assert notes == ("국가 표에 854232 없음 — 전 국가 값(추정)",)


def test_결측_달은_None_0_으로_채우지_않는다() -> None:
    src = sources(
        [_it("2026-08", "85", 10), _it("2026-09", "85", 10), _it("2026-08", "33", 5)],
        [],
        [],
        months=["2026-08", "2026-09"],
    )
    got, has, _ = export_index([_line("85", 0.5), _line("33", 0.5)], src)
    assert has
    assert got["2026-08"] == (D("7.5"), Quality.OK)
    assert got["2026-09"][0] is None and got["2026-09"][1] is Quality.ESTIMATED


def test_자료_없는_줄은_빼고_추정_모두_없으면_no_trade() -> None:
    src = sources([_it("2026-09", "85", 10)], [], [], months=["2026-09"])
    got, has, notes = export_index([_line("85", 0.5), _line("9901", 0.5)], src)
    assert has and got["2026-09"] == (D(5), Quality.ESTIMATED)
    assert notes == ("품목 9901 자료 없음",)
    _, has2, _ = export_index([_line("9901", 0.5)], src)
    assert not has2


def test_분기_합과_분기_이름() -> None:
    assert quarter_of("2026-01") == "2026Q1" and quarter_of("2026-12") == "2026Q4"
    m = {f"2026-{k:02d}": (D(k), Quality.OK) for k in range(1, 5)}
    qs = quarter_sums(m)
    assert qs["2026Q1"] == (D(6), Quality.OK)
    assert qs["2026Q2"][0] is None  # 5·6월 없음


def test_매출_YoY() -> None:
    r = revenue_yoy({"2025Q1": 100, "2026Q1": 120, "2025Q2": 0, "2026Q2": 50, "2026Q3": None})
    assert r["2026Q1"] == pytest.approx(0.2)
    assert r["2026Q2"] is None and r["2026Q3"] is None and r["2025Q1"] is None


def test_피어슨과_시차() -> None:
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None
    qs = [f"{y}Q{q}" for y in range(2022, 2027) for q in range(1, 5)]
    e = {p: float(i % 5) for i, p in enumerate(qs)}
    r = {qs[i + 1]: e[qs[i]] for i in range(len(qs) - 1)}  # 매출이 한 분기 뒤따른다
    out, counts = lag_corr(e, r, lags=(0, 1, 2), min_quarters=8)
    assert out["1"] == pytest.approx(1.0)
    assert counts[1] == len(r)
    short, _ = lag_corr(e, dict(list(r.items())[:5]), lags=(0,), min_quarters=8)
    assert short == {"0": None}


def _flat_world(months: list[str], growth: float) -> list[ItemMonthRow]:
    rows = []
    for i, ym in enumerate(months):
        rows.append(_it(ym, "85", int(1000 * (1 + growth) ** (i // 12))))
    return rows


def test_상태_네_가지() -> None:
    src = sources(_flat_world(MONTHS, 0.1), [], [], months=MONTHS)
    rev = {
        f"{y}Q{q}": int(100 * 1.1 ** (y - 2020)) + q for y in range(2020, 2027) for q in (1, 2, 3)
    }
    ok = build_overlay("990001", [_line("85", 0.5)], src, revenue=rev, cfg=CFG)
    assert ok.status in ("ok", "short_overlap")
    nt = build_overlay("990001", [_line("9901", 0.5)], src, revenue=rev, cfg=CFG)
    assert nt.status == "no_trade" and all(p.usd is None for p in nt.monthly)
    nr = build_overlay("990001", [_line("85", 0.5)], src, revenue={}, cfg=CFG)
    assert nr.status == "no_revenue"
    sh = build_overlay(
        "990001", [_line("85", 0.5)], src, revenue=dict(list(rev.items())[-4:]), cfg=CFG
    )
    assert sh.status == "short_overlap" and set(sh.lag_corr.values()) == {None}


def test_랭킹과_3개월_YoY() -> None:
    m = {f"2025-{k:02d}": D(100) for k in range(1, 13)} | {
        f"2026-{k:02d}": D(150) for k in range(1, 10)
    }
    assert rank_yoy(m, ym="2026-09", months=3) == pytest.approx(0.5)
    assert rank_yoy(m, ym="2025-09", months=3) is None
    assert rank([("B", 0.1), ("A", 0.1), ("C", None), ("D", 0.5)]) == [
        ("D", 0.5), ("A", 0.1), ("B", 0.1), ("C", None),
    ]  # fmt: skip


def test_합성_세계_행_모양() -> None:
    w = build_world()
    src = sources(w.items, w.item_countries, w.sigungu, months=w.months)
    res = resolve(w.stock_map)
    assert len(res.codes()) == 33 and res.warnings == ()
    seen: set[str] = set()
    for code in res.codes():
        rev = dict(w.revenue(code))
        ov = build_overlay(code, res.lines[code], src, revenue=rev, cfg=CFG)
        seen.add(ov.status)
        rows = to_rows(ov, map_version="p6world-1", as_of=T)
        assert len([r for r in rows if not r.is_quarter]) == len(w.months)
        for r in rows:
            for v in (r.yoy, r.revenue_yoy, *r.lag_corr.values()):
                assert v is None or math.isfinite(v)
            if r.export_usd is None:
                assert r.quality is not Quality.OK
    assert "no_trade" in seen and "ok" in seen
    nt = build_overlay(
        "992060", res.lines["992060"], src, revenue=dict(w.revenue("992060")), cfg=CFG
    )
    assert nt.status == "no_trade"
