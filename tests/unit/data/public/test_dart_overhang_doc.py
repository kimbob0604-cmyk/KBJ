"""오버행 원문 해석 — kbj.data.public.dart.overhang_doc(묶음 F, §1.3·§4.3·§8.2).

SD `overhang_parser` 정규식(결정 공시)에는 원본 시험이 없다 — 새로 쓴다. 결정 공시
폴백(CB·BW·유상)은 합성 세계의 참값(`P4World.overhang_events`)과, 행사·조정(신규)은
`P4World.overhang_adjusts` 와 맞춘다. 대상 연결 실패 → quality invalid + 사유(§4.3).
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from kbj.core.fin_rows import Instrument, OverhangEvent
from kbj.core.quality import Quality
from kbj.data.public.dart import overhang_doc as O
from kbj.data.public.dart.doctext import ParseError
from tests.fixtures.synthetic.p4_world import build_world

DOCS = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "dart" / "p4_docs.json"


@cache
def _docs() -> dict[str, Any]:
    return json.loads(DOCS.read_text(encoding="utf-8"))["docs"]


def doc(name: str) -> str:
    return str(_docs()[name]["html"])


def truth_event(name: str) -> OverhangEvent:
    rc = _docs()[name]["rcept_no"]
    return next(e for e in build_world().overhang_events if e.rcept_no == rc)


@pytest.mark.parametrize("name", ["cb_decision", "bw_decision"])
def test_bond_decision_fallback_reads_every_field(name: str) -> None:
    t = truth_event(name)
    got = O.parse_cb_bw(doc(name))
    want = {
        "face_value": t.face_value, "price": t.price, "shares": t.shares,
        "exercise_start": t.exercise_start, "exercise_end": t.exercise_end,
        "maturity": t.maturity, "refix_floor": t.refix_floor, "seq": t.seq,
        "alloc_method": t.alloc_method,
    }  # fmt: skip
    assert got == want


def test_paid_in_fallback_reads_price_listing_and_method() -> None:
    t = truth_event("paid_in_decision")
    got = O.parse_paid_in(doc("paid_in_decision"))
    assert got == {
        "shares": t.shares,
        "price": t.price,
        "listing_date": t.listing_date,
        "alloc_method": t.alloc_method,
    }


def test_decision_fallback_on_a_page_without_values_is_empty() -> None:
    assert O.parse_cb_bw(doc("generic_notice")) == {}
    assert O.parse_paid_in(doc("generic_notice")) == {}


def test_exercise_and_price_adjust_match_the_world() -> None:
    w = build_world()
    by_rc = {a.rcept_no: a for a in w.overhang_adjusts}
    for name in ("cb_exercise", "bw_exercise", "price_adjust"):
        a = by_rc[_docs()[name]["rcept_no"]]
        p = O.parse_exercise(doc(name)) if a.kind == "exercise" else O.parse_price_adjust(doc(name))
        assert (p.kind, p.new_price, p.amount, p.shares, p.effective_date) == (
            a.kind, a.new_price, a.amount, a.shares, a.effective_date,
        ), name  # fmt: skip
        assert p.notes == ()
    ex = O.parse_exercise(doc("cb_exercise"))
    assert (ex.seq, ex.old_price, ex.listing_date) == ("1", 12_000.0, date(2025, 6, 25))
    adj = O.parse_price_adjust(doc("price_adjust"))
    assert (adj.seq, adj.old_price) == ("2", 10_000.0)


def test_exercise_rows_are_summed_and_the_last_day_is_effective() -> None:
    p = O.parse_exercise(doc("exercise_multi"))
    assert (p.seq, p.amount, p.shares, p.rows) == ("5", 3_000_000_000, 300_000, 2)
    assert p.effective_date == date(2026, 9, 3)
    assert p.listing_date == date(2026, 9, 17)


def test_price_adjust_with_two_series_links_the_first_and_says_so() -> None:
    p = O.parse_price_adjust(doc("price_adjust_multi"))
    assert (p.seq, p.new_price, p.old_price, p.rows) == ("3", 8_100.0, 9_000.0, 2)
    assert p.effective_date == date(2026, 10, 2)
    assert p.notes and "첫 회차" in p.notes[0]


def test_tables_missing_are_parse_errors() -> None:
    with pytest.raises(ParseError):
        O.parse_exercise(doc("generic_notice"))
    with pytest.raises(ParseError):
        O.parse_price_adjust(doc("cb_exercise"))


@pytest.mark.parametrize(
    ("title", "inst"),
    [
        ("전환청구권행사", Instrument.CB),
        ("전환가액의조정", Instrument.CB),
        ("신주인수권행사", Instrument.BW),
        ("행사가액조정", Instrument.BW),
        ("교환청구권행사", Instrument.EB),
        ("교환가액조정", Instrument.EB),
        ("[기재정정]전환가액의 조정", Instrument.CB),
        ("주주총회소집결의", None),
    ],
)
def test_adjust_instrument(title: str, inst: Instrument | None) -> None:
    assert O.adjust_instrument(title) is inst


def _ev(rc: str, inst: Instrument, seq: str | None) -> OverhangEvent:
    return OverhangEvent(
        rc, "99400130", date(2025, 1, 20), inst, 1_000, 10.0, 100, None, None, None, None, None,
        None, "api", seq=seq,
    )  # fmt: skip


def test_link_target_picks_same_instrument_and_series_latest_receipt() -> None:
    events = [
        _ev("20250120000001", Instrument.CB, "2"),
        _ev("20250127000001", Instrument.CB, "2"),  # 정정 공시 — 늦은 접수번호가 원 공시를 대신
        _ev("20250701000001", Instrument.BW, "2"),
        _ev("20240315000001", Instrument.CB, "1"),
    ]
    got = O.link_target(events, Instrument.CB, "2")
    assert got is not None and got.rcept_no == "20250127000001"
    got = O.link_target(events, Instrument.CB, "02")
    assert got is not None and got.rcept_no == "20250127000001"  # 앞 0 무시
    assert O.link_target(events, Instrument.EB, "2") is None
    assert O.link_target(events, Instrument.CB, "9") is None
    assert O.link_target(events, Instrument.CB, None) is None  # 회차 모름 + CB 여럿 → 못 잇는다
    one = O.link_target(events, Instrument.BW, None)
    assert one is not None and one.rcept_no == "20250701000001"


def test_to_adjust_unlinked_is_invalid_with_a_reason() -> None:
    p = O.parse_price_adjust(doc("price_adjust"))
    row = O.to_adjust(
        p, rcept_no="20260420800001", corp_code="99400130", rcept_dt=date(2026, 4, 20), target=None
    )
    assert row.quality is Quality.INVALID and row.target_rcept_no is None
    assert row.note is not None and "회차 2" in row.note
    linked = O.to_adjust(
        p,
        rcept_no="20260420800001",
        corp_code="99400130",
        rcept_dt=date(2026, 4, 20),
        target=_ev("20250120000001", Instrument.CB, "2"),
    )
    assert linked.quality is Quality.OK and linked.target_rcept_no == "20250120000001"
    assert linked.new_price == 8_500.0 and linked.note is None


def test_exercise_without_share_count_is_estimated() -> None:
    p = O.AdjustParse(kind="exercise", seq="1", amount=1_000_000)
    row = O.to_adjust(
        p,
        rcept_no="20260601800001",
        corp_code="99400130",
        rcept_dt=date(2026, 6, 1),
        target=_ev("20240315000001", Instrument.CB, "1"),
    )
    assert row.quality is Quality.ESTIMATED and row.note == "발행 주식 수를 읽지 못했다"


def _table(rows: list[list[str]]) -> str:
    cells = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{cells}</table>"


_EX_HEAD = ["청구일자", "회차", "청구금액(원)", "가액(원)", "발행주식수(주)", "상장(예정)일"]


def test_exercise_total_row_is_not_added_twice() -> None:
    """검증(묶음 F): 내역 끝의 합계 행(회차 칸 '-'·'합계')을 한 번 더 더하지 않는다 — 더하면 청구
    금액이 두 배가 되어 잔여 권면이 실제보다 줄어든다."""
    for total in (["합계", "-", "3,000,000,000", "-", "300,000", "-"],
                  ["2026-09-03", "합계", "3,000,000,000", "-", "300,000", "-"]):  # fmt: skip
        p = O.parse_exercise(_table([
            _EX_HEAD,
            ["2026-09-01", "제5회", "1,000,000,000", "10,000", "100,000", "2026-09-15"],
            ["2026-09-03", "5", "2,000,000,000", "10,000", "200,000", "2026-09-17"],
            total,
        ]))  # fmt: skip
        assert (p.seq, p.amount, p.shares, p.rows, p.other_seqs) == (
            "5", 3_000_000_000, 300_000, 2, (),
        )  # fmt: skip


def test_exercise_of_two_series_keeps_only_the_first_series_and_is_estimated() -> None:
    """검증(묶음 F): 한 공시에 회차가 둘이면 첫 회차 행만 합한다(조정 표는 접수번호마다 한 행 — 다른
    회차의 청구를 첫 회차에 더하면 첫 회차 잔여가 줄어든다). 다른 회차는 notes + estimated."""
    p = O.parse_exercise(_table([
        _EX_HEAD,
        ["2026-11-10", "3", "1,000,000,000", "10,000", "100,000", "2026-11-25"],
        ["2026-11-11", "5", "2,000,000,000", "20,000", "100,000", "2026-11-26"],
        ["2026-11-12", "03", "500,000,000", "10,000", "50,000", "2026-11-27"],
    ]))  # fmt: skip
    assert (p.seq, p.amount, p.shares, p.rows, p.other_seqs) == (
        "3", 1_500_000_000, 150_000, 2, ("5",),
    )  # fmt: skip
    assert p.effective_date == date(2026, 11, 12) and p.listing_date == date(2026, 11, 27)
    assert "회차 5" in p.notes[0]
    row = O.to_adjust(
        p, rcept_no="20261112800001", corp_code="99400130", rcept_dt=date(2026, 11, 12),
        target=_ev("20250120000001", Instrument.CB, "3"),
    )  # fmt: skip
    assert row.quality is Quality.ESTIMATED and row.amount == 1_500_000_000
    assert row.note is not None and "반영 못 함" in row.note


def test_price_adjust_other_series_is_estimated_and_series_forms_link() -> None:
    p = O.parse_price_adjust(doc("price_adjust_multi"))
    assert p.other_seqs == ("4",)
    row = O.to_adjust(
        p, rcept_no="20261002800001", corp_code="99400130", rcept_dt=date(2026, 10, 2),
        target=_ev("20250120000001", Instrument.CB, "3"),
    )  # fmt: skip
    assert row.quality is Quality.ESTIMATED and row.new_price == 8_100.0
    # 구조화 응답의 회차 표기('제3회')와 원문의 숫자 회차를 잇는다
    events = [_ev("20250120000001", Instrument.CB, "제3회"), _ev("20250121000001", Instrument.CB,
                                                               "4")]  # fmt: skip
    got = O.link_target(events, Instrument.CB, "3")
    assert got is not None and got.rcept_no == "20250120000001"
