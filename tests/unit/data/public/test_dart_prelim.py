"""잠정실적·결산실적공시예고 원문 해석 — kbj.data.public.dart.prelim_doc(묶음 F, §1.3·§4.2·§8.2).

SD `earnings_parser` 에는 원본 시험이 없다(설계 §1.15 — 옮김 0). 새 시험: 합성 원문(`tests/fixtures/
synthetic/dart/p4_docs.json` — 세계 원문 + 세계에 없는 폼)으로 단위 4종(억원·백만원·천원·원)·표기
없음, 6열·2열 폼, `-`·괄호·△ 음수, 범위(자회사 > 본문 > 제목), 제외(실적등에대한전망), 비12월 결산
기간, 연간, 정정, 본문 % 대조를 본다. 세계의 잠정실적 11건은 참값(`P4World.prelims`)과 칸마다 같아야
한다.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from kbj.core.fin_rows import PrelimScope
from kbj.core.quality import Quality
from kbj.data.public.dart import prelim_doc as P
from kbj.data.public.dart.doctext import ParseError, largest
from tests.fixtures.synthetic.p4_world import build_world

DOCS = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "dart" / "p4_docs.json"
FIELDS = (
    "scope", "fiscal_year", "fiscal_q", "is_annual", "period_end", "period_inferred", "unit_mult",
    "revenue_cur", "revenue_prev_q", "revenue_prev_y", "op_cur", "op_prev_q", "op_prev_y",
    "pretax_cur", "pretax_prev_q", "pretax_prev_y", "ni_cur", "ni_prev_q", "ni_prev_y",
    "ni_owner_cur", "ni_owner_prev_q", "ni_owner_prev_y", "eps_cur", "yoy_reported", "quality",
)  # fmt: skip


@cache
def _docs() -> dict[str, Any]:
    return json.loads(DOCS.read_text(encoding="utf-8"))["docs"]


def doc(name: str) -> str:
    return str(_docs()[name]["html"])


def test_every_world_prelim_matches_the_truth_field_by_field() -> None:
    w = build_world()
    acc = {p.corp_code: p.acc_mt for p in w.profiles}
    assert len(w.prelims) == 11
    for truth in w.prelims:
        d = w.disclosure_by_rcept(truth.rcept_no)
        parsed = P.parse(largest(list(w.documents(truth.rcept_no))) or "")
        row = P.to_row(
            parsed,
            rcept_no=d.rcept_no,
            corp_code=d.corp_code,
            rcept_dt=d.rcept_dt,
            title=d.title,
            acc_mt=acc[d.corp_code],
        )
        for f in FIELDS:
            assert getattr(row, f) == getattr(truth, f), (d.ident, f)
        assert row.notes == (), d.ident  # 세계의 본문 % 는 우리 계산과 0.1%p 안


def test_unit_eok_six_columns_keep_three_columns_in_won() -> None:
    p = P.parse(doc("prelim_consolidated_eok"))
    assert (p.unit_label, p.unit_mult, p.unit_assumed, p.two_col) == (
        "억원",
        100_000_000,
        False,
        False,
    )
    assert p.scope is PrelimScope.CONSOLIDATED
    rev = p.metrics["revenue"]
    assert rev.six_col and rev.prev_q is not None and rev.prev_y is not None  # 전기 열도 남긴다
    assert isinstance(rev.cur, int) and rev.cur % 100_000_000 == 0
    assert p.period == (date(2026, 7, 1), date(2026, 9, 30))
    assert set(p.metrics) == {"revenue", "op", "pretax", "ni", "ni_owner"}


def test_unit_million_and_thousand() -> None:
    m = P.parse(doc("prelim_mil_no_period"))
    assert (m.unit_label, m.unit_mult, m.period) == ("백만원", 1_000_000, None)
    t = P.parse(doc("prelim_standalone_thousand"))
    assert (t.unit_label, t.unit_mult) == ("천원", 1_000)
    assert t.scope is PrelimScope.STANDALONE
    assert "ni_owner" not in t.metrics  # 별도 폼에는 지배주주 줄이 없다


def test_unit_won_with_parenthesis_and_triangle_negatives() -> None:
    p = P.parse(doc("prelim_won_negative"))
    assert (p.unit_label, p.unit_mult) == ("원", 1)
    rev, op = p.metrics["revenue"], p.metrics["op"]
    assert (rev.cur, rev.prev_q, rev.prev_y) == (1_234_567_890, 1_000_000_000, -987_654_321)
    assert (rev.yoy_pct, rev.yoy_turn, rev.qoq_pct) == (None, "흑자전환", 23.5)
    assert (op.cur, op.prev_q, op.prev_y) == (-120_000_000, -50_000_000, 80_000_000)
    assert (op.qoq_pct, op.qoq_turn, op.yoy_turn) == (None, "적자지속", "적자전환")


def test_missing_unit_is_assumed_million_and_marked_estimated() -> None:
    p = P.parse(doc("prelim_no_unit"))
    assert (p.unit_mult, p.unit_assumed) == (1_000_000, True)
    assert any("단위 표기 없음" in n for n in p.notes)
    row = P.to_row(
        p, rcept_no="20261116800001", corp_code="99400010", rcept_dt=date(2026, 10, 20),
        title="연결재무제표기준영업(잠정)실적(공정공시)", acc_mt=12,
    )  # fmt: skip
    assert row.revenue_cur == 5_000 * 1_000_000
    assert row.quality is Quality.ESTIMATED


def test_two_column_form_has_no_prior_year_and_no_reported_yoy() -> None:
    p = P.parse(doc("prelim_2col"))
    assert p.two_col
    assert all(m.prev_y is None and not m.six_col for m in p.metrics.values())
    row = P.to_row(
        p, rcept_no="20261116800143", corp_code="99400170", rcept_dt=date(2026, 11, 16),
        title="영업(잠정)실적(공정공시)", acc_mt=12,
    )  # fmt: skip
    assert row.yoy_reported == {}
    assert row.revenue_prev_y is None and row.revenue_prev_q is not None


def test_scope_subsidiary_title_wins_then_body_then_title() -> None:
    sub = P.parse(doc("prelim_subsidiary"))
    assert sub.scope is PrelimScope.CONSOLIDATED  # 본문은 연결이지만
    row = P.to_row(
        sub, rcept_no="20261116800100", corp_code="99400100", rcept_dt=date(2026, 11, 16),
        title=str(_docs()["prelim_subsidiary"]["title"]), acc_mt=12,
    )  # fmt: skip
    assert row.scope is PrelimScope.SUBSIDIARY  # 제목의 자회사가 먼저(SD source_type _SUB)
    # 제목은 별도('영업(잠정)실적')인데 본문이 연결 → 연결(e18 — 본문 > 제목)
    two = P.parse(doc("prelim_2col"))
    assert P.classify("영업(잠정)실적(공정공시)") is PrelimScope.STANDALONE
    assert two.scope is PrelimScope.CONSOLIDATED


@pytest.mark.parametrize(
    ("title", "want"),
    [
        ("연결재무제표기준영업(잠정)실적(공정공시)", PrelimScope.CONSOLIDATED),
        ("영업(잠정)실적(공정공시)", PrelimScope.STANDALONE),
        ("[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", PrelimScope.CONSOLIDATED),
        ("연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)", PrelimScope.SUBSIDIARY),
        ("결산실적공시예고", "PRE_ANNOUNCE"),
        ("실적등에대한전망(공정공시)", None),
        ("매출액또는손익구조30%(대규모법인은15%)이상변경", None),
        ("", None),
        (None, None),
    ],
)
def test_classify_follows_sd_order(title: str | None, want: object) -> None:
    assert P.classify(title) == want


def test_no_table_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        P.parse(doc("generic_notice"))
    with pytest.raises(ParseError):
        P.parse("")


def test_period_inference_by_fiscal_month_not_calendar_quarter() -> None:
    # 3월 결산: 11-14 접수 → 직전 분기말 9-30 = FY2026 2Q(회계연도 = 시작한 해 [확인 필요 #34])
    fp, inferred, annual, notes = P.infer_period(date(2026, 11, 14), 3, None)
    assert (fp.year, fp.q, fp.start, fp.end, inferred, annual) == (
        2026, 2, date(2026, 7, 1), date(2026, 9, 30), True, False,
    )  # fmt: skip
    assert notes == ()
    assert fp.label == "FY26 2Q (26.07~26.09)"
    # 12월 결산 1월 말 접수 → 전년 4분기
    fp, inferred, _, _ = P.infer_period(date(2026, 1, 28), 12, None)
    assert (fp.year, fp.q, fp.end, inferred) == (2025, 4, date(2025, 12, 31), True)
    # 분기말 당일 접수는 그 분기가 아니다(끝나기 전 잠정실적은 없다)
    fp, _, _, _ = P.infer_period(date(2026, 9, 30), 12, None)
    assert fp.end == date(2026, 6, 30)
    # 결산월 모름 → 달력 분기 + notes
    fp, inferred, _, notes = P.infer_period(date(2026, 10, 28), None, None)
    assert (fp.year, fp.q, inferred) == (2026, 3, True)
    assert any("결산월 모름" in n for n in notes)


def test_fiscal_period_of_end_rejects_a_non_quarter_end() -> None:
    assert P.fiscal_period_of_end(date(2027, 3, 31), 3).q == 4
    assert P.fiscal_period_of_end(date(2027, 3, 31), 3).year == 2026
    with pytest.raises(ValueError, match="분기말"):
        P.fiscal_period_of_end(date(2026, 8, 31), 12)
    with pytest.raises(ValueError, match="결산월"):
        P.fiscal_period_of_end(date(2026, 9, 30), 13)


def test_body_period_wins_and_full_year_is_annual() -> None:
    p = P.parse(doc("prelim_annual"))
    row = P.to_row(
        p, rcept_no="20260205800001", corp_code="99400010", rcept_dt=date(2026, 2, 5),
        title="연결재무제표기준영업(잠정)실적(공정공시)", acc_mt=12,
    )  # fmt: skip
    assert (row.is_annual, row.fiscal_year, row.fiscal_q, row.period_end) == (
        True, 2025, 4, date(2025, 12, 31),
    )  # fmt: skip
    assert (row.period_inferred, row.quality, row.period) == (False, Quality.OK, "2025FY")


def test_body_period_after_receipt_is_dropped_with_a_note() -> None:
    p = P.parse(doc("prelim_consolidated_eok"))  # 본문 기간 2026-07-01~09-30
    fp, inferred, _, notes = P.infer_period(date(2026, 9, 15), 12, p)
    assert inferred and fp.end == date(2026, 6, 30)
    assert any("접수일 뒤" in n for n in notes)


def test_reported_yoy_mismatch_is_a_note_not_a_change() -> None:
    p = P.parse(doc("prelim_no_unit").replace("11.1", "15.0", 1))
    row = P.to_row(
        p, rcept_no="20261020800001", corp_code="99400010", rcept_dt=date(2026, 10, 20),
        title="연결재무제표기준영업(잠정)실적(공정공시)", acc_mt=12,
    )  # fmt: skip
    assert row.yoy_reported["revenue"] == 15.0  # 본문 값 그대로
    assert any(n.startswith("revenue 전년동기대비 본문 15.0%") for n in row.notes)


def test_pre_announce_reads_date_time_and_target() -> None:
    got = P.parse_pre_announce_detail(doc("pre_announce"))
    assert got == P.PreAnnounce(date(2027, 1, 28), "16:00", "2026년 연간 및 4분기 잠정실적")
    assert got is not None
    assert P.parse_pre_announce(doc("pre_announce")) == date(2027, 1, 28)
    note = P.encode_pre_announce(got)
    assert note == "planned=2027-01-28;time=16:00;target=2026년 연간 및 4분기 잠정실적"
    assert P.decode_pre_announce(note) == got


def test_pre_announce_body_text_fallback_and_missing() -> None:
    html = "<html><body><p>결산실적 공시예정일 2027.02.05 (예정)</p></body></html>"
    assert P.parse_pre_announce(html) == date(2027, 2, 5)
    assert P.parse_pre_announce(doc("generic_notice")) is None
    assert P.decode_pre_announce("ValueError: 원문이 없다") is None
    assert P.decode_pre_announce(None) is None
    odd = P.PreAnnounce(date(2027, 1, 2), None, "a;b=c")
    assert P.decode_pre_announce(P.encode_pre_announce(odd)) == P.PreAnnounce(
        date(2027, 1, 2), None, "a,b c"
    )


def _table(rows: list[list[str]]) -> str:
    cells = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{cells}</table>"


_HEAD = ["구분", "", "당기", "전기", "전기대비증감율(%)", "흑자적자전환여부", "전년동기",
         "전년동기대비증감율(%)", "흑자적자전환여부"]  # fmt: skip


def test_blank_turn_cell_does_not_shift_the_prior_year_columns() -> None:
    """검증(묶음 F): 전기 쪽 흑자적자전환 칸이 빈 행 — 태그를 지우면 칸이 사라진다. SD 의 `(\\S+)`
    전환 칸은 다음 열(전년동기 숫자)을 먹어 전년동기·증감율이 한 칸 밀렸다(전년동기 = 25.0)."""
    html = "<p>(단위 : 억원, %)</p><p>연결재무제표 기준</p>" + _table([
        _HEAD,
        ["매출액", "당해실적", "1,000", "900", "11.1", "", "800", "25.0", "-"],
        ["", "누계실적", "3,000", "-", "-", "-", "2,500", "20.0", "-"],
        ["영업이익", "당해실적", "100", "50", "100.0", "", "80", "25.0", "-"],
        ["당기순이익", "당해실적", "(50)", "30", "-", "적자전환", "20", "-", "적자전환"],
    ])  # fmt: skip
    p = P.parse(html)
    rev, op, ni = p.metrics["revenue"], p.metrics["op"], p.metrics["ni"]
    eok = 100_000_000
    assert (rev.cur, rev.prev_q, rev.prev_y, rev.qoq_pct, rev.yoy_pct, rev.qoq_turn) == (
        1_000 * eok, 900 * eok, 800 * eok, 11.1, 25.0, None,
    )  # fmt: skip
    assert (op.prev_y, op.yoy_pct, op.six_col) == (80 * eok, 25.0, True)
    assert (ni.cur, ni.prev_y, ni.qoq_turn, ni.yoy_turn) == (-50 * eok, 20 * eok, "적자전환",
                                                             "적자전환")  # fmt: skip
    assert not p.two_col and p.notes == ()


def test_amount_beyond_bigint_is_a_parse_error_not_an_arithmetic_crash() -> None:
    """검증(묶음 F): 정밀도·bigint 를 넘는 금액은 `decimal.InvalidOperation`(ValueError 아님)이
    처리기 밖으로 새지 않고 그 공시 한 건의 `ParseError` 가 된다."""
    huge = "9" * 30
    html = "<p>(단위 : 억원, %)</p>" + _table([
        ["매출액", "당해실적", huge, "900"],
    ])  # fmt: skip
    with pytest.raises(ParseError, match="금액"):
        P.parse(html)
    with pytest.raises(ParseError, match="bigint"):
        P.parse("<p>(단위 : 억원, %)</p>" + _table([["매출액", "당해실적", "99" * 7, "1"]]))
