"""KIS 투자자 파서(kbj.data.private.kis.investors) — 원본 규칙 승격 + 새 시험.

옮긴 원본(ET — 원본은 네이버·옛 KIS 함수 대상이라 import 경로만 바꿔 옮길 수 없어, **같은 단언을 KIS
파서 대상으로** 옮겼다. 원본 시험 이름을 각 시험 머리에 적는다):
- `monitor/flow/tests/test_kis.py`: 단위를_믿지_않고_확인한다(5 — 마지막 '리포트를 쓰지 않는다' 는
  수집 처리기 시험 tests/unit/collectors/test_market_close.py), 없는_구분은_표에서_뺀다(기관 세부·
  기타법인), 검산을_못_한_것을_숨기지_않는다(기타법인이 없으면 검산 ① 입력이 없다)
- `board/tests/test_flows_parse.py::StockTrendApi`: json_is_read·sign_is_kept·
  absent_person_is_not_invented·no_investor_keys_in_any_row_is_a_failure·
  one_row_with_values_is_enough· empty_or_wrong_shape_is_not_silent·rows_without_a_date_are_dropped
- `board/tests/test_market_flows.py`: 기타법인은_없다고_적고_역산하지_않는다·
  날짜는_응답이_싣고_온_것을_쓴다·구분이_빠지면_무엇이_빠졌는지_적는다·날짜가_이상하면_실패다·응답이_객체가_아니면_실패다·
  값이_하나도_없으면_실패다·눈금이_백배_어긋나면_막는다(→ unit_check)
- `board/tests/test_stockflows.py::Collect`: absent_investor_is_not_zero·
  latest_never_picks_a_day_after_asof·negative_zero_is_stored_as_zero·
  kis_rows_without_investor_keys_fall_back(폴백은 없앴다 — 실패로)
- ET `board/ingest/kis.py:market_flows`:201 '셋 다 0 이면 질의 불성립'
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import GROUP4, INST7, Investor, InvestorDay
from kbj.data.private.kis.investors import (
    closes_of,
    merge_inst_foreign,
    on_day,
    params_inst_foreign,
    params_market_investor,
    params_stock_investor,
    parse_inst_foreign_total,
    parse_market_investor,
    parse_stock_investor,
    prelim_days,
    unit_check,
)
from kbj.data.private.kis.parse import KisEmpty, KisParseError, KisShapeError

OK = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상"}
TS = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)


def stock_body(*rows: dict[str, Any]) -> dict[str, Any]:
    return {**OK, "output": list(rows)}


ROW_0916 = {
    "stck_bsop_date": "20260916",
    "stck_clpr": "29,250",
    "prsn_ntby_qty": "-74,126",
    "frgn_ntby_qty": "+7,600",
    "orgn_ntby_qty": "+63,482",
    "prsn_ntby_tr_pbmn": "-2168",
    "frgn_ntby_tr_pbmn": "+222",
    "orgn_ntby_tr_pbmn": "1857",
}
ROW_0911 = {
    "stck_bsop_date": "20260911",
    "stck_clpr": "28950",
    "prsn_ntby_qty": "-153284",
    "frgn_ntby_qty": "597024",
    "orgn_ntby_qty": "-415612",
    "prsn_ntby_tr_pbmn": "-4438",
    "frgn_ntby_tr_pbmn": "17284",
    "orgn_ntby_tr_pbmn": "-12032",
}


def by(rows: list[InvestorDay], d: date) -> dict[Investor, InvestorDay]:
    return {r.investor: r for r in rows if r.date == d}


# ── 종목 투자자 ─────────────────────────────────────────────────────────────────────────


def test_json_is_read_amounts_in_won() -> None:
    """test_flows_parse::test_json_is_read — 금액은 백만원 → 원, 수량은 주."""
    rows = parse_stock_investor(stock_body(ROW_0916, ROW_0911), "003490")
    day = by(rows, date(2026, 9, 16))
    assert day[Investor.INSTITUTION].net_value == 1_857_000_000
    assert day[Investor.INSTITUTION].net_qty == 63_482
    assert day[Investor.FOREIGN].net_value == 222_000_000
    assert {r.source for r in rows} == {"kis"}
    assert {r.quality for r in rows} == {Quality.OK}
    assert {r.venue for r in rows} == {"KRX"}


def test_sign_is_kept() -> None:
    """test_flows_parse::test_sign_is_kept — 부호가 날아가면 정반대가 된다."""
    day = by(parse_stock_investor(stock_body(ROW_0911), "003490"), date(2026, 9, 11))
    assert day[Investor.INSTITUTION].net_value == -12_032_000_000
    assert day[Investor.INDIVIDUAL].net_qty == -153_284


def test_absent_person_is_not_invented() -> None:
    """test_flows_parse::test_absent_person_is_not_invented·test_stockflows::absent_investor."""
    row = {k: v for k, v in ROW_0916.items() if not k.startswith("prsn_")}
    day = by(parse_stock_investor(stock_body(row), "003490"), date(2026, 9, 16))
    assert Investor.INDIVIDUAL not in day
    assert day[Investor.FOREIGN].net_value == 222_000_000


def test_other_corp_and_inst7_are_not_made_for_a_stock() -> None:
    """test_kis::기타법인도_빠진다·기관_세부는_아예_안_나온다, test_market_flows::기타법인은_없다고_
    적고_역산하지_않는다 — 3구분 TR 에서 기타법인을 역산해 채우지 않는다(검산 ① 불가 — R2)."""
    rows = parse_stock_investor(stock_body(ROW_0916), "003490")
    got = {r.investor for r in rows}
    assert got == {Investor.INDIVIDUAL, Investor.FOREIGN, Investor.INSTITUTION}
    assert Investor.OTHER_CORP not in got
    assert not got & set(INST7)
    assert set(GROUP4) - got == {Investor.OTHER_CORP}  # 4구분 중 빠진 것이 그대로 보인다


def test_no_investor_keys_in_any_row_is_a_failure_with_keys() -> None:
    """test_flows_parse::test_no_investor_keys_in_any_row_is_a_failure."""
    rows = [{k: v for k, v in r.items() if "ntby" not in k} for r in (ROW_0916, ROW_0911)]
    with pytest.raises(KisParseError) as e:
        parse_stock_investor(stock_body(*rows), "003490")
    assert "투자자 구분이 한 행에도 없다" in str(e.value)
    assert "stck_clpr" in str(e.value)
    assert "29" not in str(e.value).split("받은 키")[1]  # 값은 싣지 않는다


def test_one_row_with_values_is_enough() -> None:
    """test_flows_parse::test_one_row_with_values_is_enough — 당일 행만 아직 빈 날."""
    empty_today = {k: v for k, v in ROW_0916.items() if "ntby" not in k}
    rows = parse_stock_investor(stock_body(empty_today, ROW_0911), "003490")
    assert by(rows, date(2026, 9, 16)) == {}
    assert by(rows, date(2026, 9, 11))[Investor.INSTITUTION].net_value == -12_032_000_000


@pytest.mark.parametrize("bad", [[], None, {}, "nope", [1, 2]])
def test_empty_or_wrong_shape_is_not_silent(bad: Any) -> None:
    """test_flows_parse::test_empty_or_wrong_shape_is_not_silent·test_market_flows::응답이_객체가_
    아니면_실패다."""
    with pytest.raises(KisParseError):
        parse_stock_investor({**OK, "output": bad}, "003490")


def test_missing_result_block_names_the_keys() -> None:
    with pytest.raises(KisShapeError) as e:
        parse_stock_investor({**OK, "data": []}, "003490")
    assert "받은 키" in str(e.value)


def test_rows_without_a_date_are_dropped_and_none_is_a_failure() -> None:
    """test_flows_parse::test_rows_without_a_date_are_dropped·test_market_flows::날짜가_이상하면."""
    bad_date = {**ROW_0916, "stck_bsop_date": "2026-09-16"}
    rows = parse_stock_investor(stock_body(bad_date, ROW_0911), "003490")
    assert {r.date for r in rows} == {date(2026, 9, 11)}
    with pytest.raises(KisParseError, match="날짜가 있는 행이 없다"):
        parse_stock_investor(stock_body(bad_date), "003490")


def test_date_is_the_one_the_response_carries() -> None:
    """test_market_flows::날짜는_응답이_싣고_온_것을_쓴다 — 부르는 쪽 날짜로 덮지 않는다."""
    rows = parse_stock_investor(stock_body(ROW_0911), "003490")
    assert {r.date for r in rows} == {date(2026, 9, 11)}


def test_on_day_never_picks_another_day() -> None:
    """test_stockflows::test_latest_never_picks_a_day_after_asof — 기준일 행만(직전 값으로 대신하지
    않는다)."""
    rows = parse_stock_investor(stock_body(ROW_0916, ROW_0911), "003490")
    assert {r.date for r in on_day(rows, date(2026, 9, 11))} == {date(2026, 9, 11)}
    assert on_day(rows, date(2026, 9, 12)) == []


def test_negative_zero_is_stored_as_zero() -> None:
    """test_stockflows::test_negative_zero_is_stored_as_zero."""
    row = {**ROW_0916, "orgn_ntby_tr_pbmn": "-0", "orgn_ntby_qty": "-0"}
    inst = by(parse_stock_investor(stock_body(row), "003490"), date(2026, 9, 16))
    assert inst[Investor.INSTITUTION].net_value == 0
    assert inst[Investor.INSTITUTION].net_qty == 0


def test_blank_and_dash_are_missing_not_zero() -> None:
    row = {**ROW_0916, "frgn_ntby_tr_pbmn": "", "frgn_ntby_qty": "-"}
    assert Investor.FOREIGN not in by(
        parse_stock_investor(stock_body(row), "003490"), date(2026, 9, 16)
    )


def test_fractional_amount_after_scaling_is_ok_but_garbage_fails() -> None:
    row = {**ROW_0916, "orgn_ntby_tr_pbmn": "1.5"}  # 1.5 백만원 = 1,500,000 원(정수)
    inst = by(parse_stock_investor(stock_body(row), "1"), date(2026, 9, 16))[Investor.INSTITUTION]
    assert inst.net_value == 1_500_000
    with pytest.raises(KisParseError):
        parse_stock_investor(stock_body({**ROW_0916, "orgn_ntby_qty": "abc"}), "1")
    with pytest.raises(KisParseError, match="소수"):
        parse_stock_investor(stock_body({**ROW_0916, "orgn_ntby_qty": "1.5"}), "1")


def test_closes_of_reads_the_close_by_date() -> None:
    assert closes_of(stock_body(ROW_0916, ROW_0911)) == {
        date(2026, 9, 16): 29250.0,
        date(2026, 9, 11): 28950.0,
    }


def test_params_use_venue_code() -> None:
    assert params_stock_investor("003490")["FID_COND_MRKT_DIV_CODE"] == "J"
    assert params_stock_investor("003490", "NXT")["FID_COND_MRKT_DIV_CODE"] == "NX"
    with pytest.raises(ValueError):
        params_stock_investor("003490", "XX")


# ── 시장 투자자 ─────────────────────────────────────────────────────────────────────────


def market_row(d: str, prsn: str, frgn: str, orgn: str, **extra: str) -> dict[str, str]:
    return {
        "stck_bsop_date": d,
        "prsn_ntby_tr_pbmn": prsn,
        "frgn_ntby_tr_pbmn": frgn,
        "orgn_ntby_tr_pbmn": orgn,
        **extra,
    }


def test_market_rows_are_coded_by_market_and_in_won() -> None:
    body = {**OK, "output1": [market_row("20261006", "-36019", "4394", "15063")]}
    rows = parse_market_investor(body, "0001")
    assert {r.code for r in rows} == {"0001"}
    d = by(rows, date(2026, 10, 6))
    assert d[Investor.INDIVIDUAL].net_value == -36_019_000_000
    assert Investor.OTHER_CORP not in d  # 칸이 없으면 만들지 않는다


def test_market_all_three_zero_on_latest_day_is_not_a_fact() -> None:
    """ET kis.market_flows:201 — 셋 다 정확히 0 이면 질의 불성립(KisEmpty)."""
    body = {
        **OK,
        "output1": [
            market_row("20261006", "0", "0", "0"),
            market_row("20261005", "-1", "2", "-1"),
        ],
    }
    with pytest.raises(KisEmpty, match="전부 0"):
        parse_market_investor(body, "0001")


def test_market_older_all_zero_rows_are_dropped() -> None:
    body = {
        **OK,
        "output1": [
            market_row("20261006", "-3", "2", "1"),
            market_row("20261005", "0", "0", "0"),
        ],
    }
    rows = parse_market_investor(body, "1001")
    assert {r.date for r in rows} == {date(2026, 10, 6)}


def test_market_four_groups_and_inst7_when_columns_exist() -> None:
    """[추정 칸] 기타법인·기관 7구분이 있으면 읽는다 — 합성 원장 성질(①②)이 그대로 남는다."""
    inst7 = {"scrt": 10, "ivtr": -3, "pe_fund": 2, "insu": 5, "bank": -1, "fund": 7, "mrbn": 1}
    inst = sum(inst7.values())
    extra = {f"{k}_ntby_tr_pbmn": str(v) for k, v in inst7.items()}
    body = {
        **OK,
        "output1": [
            market_row(
                "20261006",
                str(-(inst + 40 + 9)),
                "40",
                str(inst),
                etc_corp_ntby_tr_pbmn="9",
                **extra,
            )
        ],
    }
    d = by(parse_market_investor(body, "0001"), date(2026, 10, 6))
    assert sum(d[i].net_value or 0 for i in GROUP4) == 0  # ① 4구분 합 0
    assert sum(d[i].net_value or 0 for i in INST7) == d[Investor.INSTITUTION].net_value  # ②


def test_market_params_are_krx_only_until_probed() -> None:
    p = params_market_investor("0001", date(2026, 10, 7))
    assert p["FID_COND_MRKT_DIV_CODE"] == "U"
    assert p["FID_INPUT_DATE_1"] == "20261007"
    with pytest.raises(ValueError, match="실측"):
        params_market_investor("0001", date(2026, 10, 7), "NXT")


# ── 가집계 ──────────────────────────────────────────────────────────────────────────────


def test_inst_foreign_total_is_estimated_prelim_with_rank() -> None:
    body = {
        **OK,
        "output": [
            {"mksc_shrn_iscd": "990010", "frgn_ntby_tr_pbmn": "100", "orgn_ntby_tr_pbmn": "-5"},
            {"mksc_shrn_iscd": "990020", "frgn_ntby_tr_pbmn": "90"},
            {"hts_kor_isnm": "코드없음", "frgn_ntby_tr_pbmn": "1"},
        ],
    }
    rows = parse_inst_foreign_total(body, ts=TS)
    assert [(r.code, r.investor, r.rank) for r in rows] == [
        ("990010", Investor.FOREIGN, 1),
        ("990010", Investor.INSTITUTION, 1),
        ("990020", Investor.FOREIGN, 2),
    ]
    assert {r.source for r in rows} == {"kis.prelim"}
    assert {r.quality for r in rows} == {Quality.ESTIMATED}
    assert rows[0].net_value == 100_000_000
    days = prelim_days(rows + rows, date(2026, 10, 7))
    assert len(days) == 3
    assert {(d.source, d.quality) for d in days} == {("kis.prelim", Quality.ESTIMATED)}


def test_inst_foreign_total_empty_list_is_empty_not_a_failure() -> None:
    assert parse_inst_foreign_total({**OK, "output": []}, ts=TS) == []


def test_inst_foreign_total_without_codes_fails() -> None:
    with pytest.raises(KisParseError, match="종목코드"):
        parse_inst_foreign_total({**OK, "output": [{"frgn_ntby_tr_pbmn": "1"}]}, ts=TS)


def test_inst_foreign_params() -> None:
    assert params_inst_foreign("0001", "foreign")["FID_ETC_CLS_CODE"] == "1"
    assert params_inst_foreign("1001", "institution")["FID_INPUT_ISCD"] == "1001"


# ── 금액 자릿수 대조 (monitor/flow test_kis::단위를_믿지_않고_확인한다) ───────────────────

D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)


def three_way(scale: float = 1.0) -> tuple[list[InvestorDay], dict[tuple[str, date], float]]:
    """KIS 모양: 3구분, 금액은 원(원본 three_way 와 같은 값)."""
    rows: list[InvestorDay] = []
    for i, d in enumerate((D1, D2, D3)):
        for inv, amt, qty in (
            (Investor.INDIVIDUAL, -1_000_000_000 + i, -50_000),
            (Investor.FOREIGN, 600_000_000, 30_000),
            (Investor.INSTITUTION, 400_000_000, 20_000),
        ):
            rows.append(InvestorDay("x", d, inv, round(amt * scale), qty, "kis", "KRX", Quality.OK))
    closes = {("x", d): 20_000.0 for d in (D1, D2, D3)}
    return rows, closes


def test_unit_check_passes_when_digits_match() -> None:
    """자릿수가_맞으면_통과."""
    rows, closes = three_way()
    assert unit_check(rows, closes).ok


def test_unit_check_catches_a_million_times() -> None:
    """백만배_틀리면_잡는다."""
    rows, closes = three_way(1_000_000)
    assert not unit_check(rows, closes).ok


def test_unit_check_catches_a_millionth() -> None:
    """백만분의_일이어도_잡는다."""
    rows, closes = three_way(1 / 1_000_000)
    assert not unit_check(rows, closes).ok


def test_unit_check_does_not_pass_without_anything_to_compare() -> None:
    """대조할_게_없으면_통과라고_하지_않는다."""
    rows, _ = three_way()
    res = unit_check(rows, {})
    assert not res.ok
    assert res.ratio is None


def test_merge_keeps_each_investor_rank_from_its_own_list() -> None:
    """외국인 목록의 외국인 순위·기관 목록의 기관 순위가 남고, 다른 목록에서만 본 값은 순위 None."""
    foreign_list = parse_inst_foreign_total(
        {
            **OK,
            "output": [
                {"mksc_shrn_iscd": "A1", "frgn_ntby_tr_pbmn": "9", "orgn_ntby_tr_pbmn": "1"},
                {"mksc_shrn_iscd": "B2", "frgn_ntby_tr_pbmn": "8", "orgn_ntby_tr_pbmn": "7"},
            ],
        },
        ts=TS,
    )
    inst_list = parse_inst_foreign_total(
        {
            **OK,
            "output": [
                {"mksc_shrn_iscd": "B2", "frgn_ntby_tr_pbmn": "8", "orgn_ntby_tr_pbmn": "7"}
            ],
        },
        ts=TS,
    )
    rows = merge_inst_foreign([("foreign", foreign_list), ("institution", inst_list)])
    got = {(r.code, r.investor): r.rank for r in rows}
    assert got == {
        ("A1", Investor.FOREIGN): 1,
        ("A1", Investor.INSTITUTION): None,
        ("B2", Investor.FOREIGN): 2,
        ("B2", Investor.INSTITUTION): 1,
    }
