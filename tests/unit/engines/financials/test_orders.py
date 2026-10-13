"""수주(dart-report `orders` 승격본) — 원문 키값·기간·NIPA·정정(legacy 는 거르지 않는다)."""

from __future__ import annotations

from datetime import date

from kbj.data.public.dart.client import DartClient
from kbj.engines.financials import orders as O
from tests.fixtures.synthetic.p4_world import build_world
from tests.golden.dart_report.golden import WorldDart, world_now


def test_금액_날짜_기간() -> None:
    assert O._money("계약금액 12,345,000,000 원") == 12_345_000_000.0
    assert O._money("123") is None
    assert O._parse_date("2025년 3월 5일") == date(2025, 3, 5)
    assert O._parse_date("2025-02-30") is None
    o = O.Order("1", "20250101", "x", amount=365e8, start=date(2025, 1, 1), end=date(2026, 1, 1))
    assert o.days == 365 and o.annualized_sales_eok == 365.0
    assert (
        O.Order("1", "", "", amount=1e8, start=date(2025, 2, 1), end=date(2025, 1, 1)).days is None
    )


def test_원문에서_계약을_읽고_정정도_한_행으로_남긴다() -> None:
    """#1 공급계약 + 정정 — legacy 는 정정을 거르지 않는다(두 행·합계에 둘 다 — 골든 고정)."""
    client = WorldDart(build_world(), world_now()).client(DartClient)
    lines: list[str] = []
    olist = O.fetch_orders(
        client, "99400010", "20230101", "20261231", verbose=True, log=lines.append
    )
    assert lines == ["  [수주] 계약 관련 공시 2건 발견"]
    assert [(o.amount, o.is_correction, o.counterparty) for o in olist] == [
        (12_345_000_000.0, False, "모의고객사A"),
        (13_000_000_000.0, True, "모의고객사A"),
    ]
    t = O.orders_table(olist)
    assert t["total_eok"] == 253.45 and [r["정정"] for r in t["rows"]] == ["", "정정"]


def test_nipa_비중() -> None:
    a = O.Order("1", "", "", amount=3e8, content="NIPA 과제")
    b = O.Order("2", "", "", amount=1e8, counterparty="x")
    c = O.Order("3", "", "", amount=None)
    t = O.orders_table([a, b, c])
    assert t["total_eok"] == 4.0 and t["nipa_eok"] == 3.0 and t["nipa_ratio"] == 0.75
    assert len(t["rows"]) == 2 and t["rows"][0]["NIPA"] == "Y"
    assert O.orders_table([])["nipa_ratio"] is None
