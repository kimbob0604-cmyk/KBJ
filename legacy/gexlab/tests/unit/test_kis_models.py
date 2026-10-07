"""KIS REST 응답 모델 — 합성 fixture(tests/fixtures/kis — probe 원본 발췌와 같은 형태, 값은
`scripts/make_synthetic_fixtures.py` 합성)로 파싱을 고정한다."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from data.kis.models import (
    INVESTORS,
    CallPutBoard,
    CallPutRow,
    FuturesBoard,
    InvestorRow,
    MinuteBar,
    OptionListRow,
    PriceOutput,
    output_rows,
    parse_rows,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kis"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ── 전광판 ──


def test_monthly_board_parses_top_and_bottom_rows() -> None:
    raw = load("callput_202610.json")
    b = CallPutBoard.model_validate(raw)
    assert len(b.calls) == len(raw["output1"]) == 20
    assert len(b.puts) == len(raw["output2"]) == 20
    top = b.calls[0]
    assert top.code == "B01610ZHQ"
    assert top.strike == Decimal("1595.00")
    assert top.oi == 7470 and top.otst_stpl_qty_icdc == 0
    assert top.gama == Decimal("0.0001") and top.delta_val == Decimal("0.0031")
    assert top.hts_ints_vltl == Decimal("69.0668") == top.iv_kis
    assert top.acml_tr_pbmn == 12128
    # 행사가 내림차순 (#11) — 월물은 ATM 구간이 빠진 1595.0~1347.5
    assert [r.strike for r in b.calls] == sorted((r.strike for r in b.calls), reverse=True)
    assert b.puts[-1].strike == Decimal("1347.50")


def test_board_iv_zero_means_missing() -> None:
    # 거래 없는 깊은 ITM 풋(1347.5)은 KIS IV 가 0.0000 으로 온다(실측)
    b = CallPutBoard.model_validate(load("callput_202610.json"))
    deep = b.puts[-1]
    assert deep.hts_ints_vltl == Decimal("0.0000")
    assert deep.iv_kis is None
    assert deep.acml_vol == 0


def test_weekly_board_atm_region_and_atm_label() -> None:
    b = CallPutBoard.model_validate(load("callput_wkm_260904.json"))
    strikes = [r.strike for r in b.calls]
    assert strikes[0] == Decimal("1130.00") and strikes[-1] == Decimal("1082.50")
    assert Decimal("1095.00") in strikes  # 선물가(약 1095) 부근
    atm = [r for r in b.calls + b.puts if r.atm_cls_name == "ATM"]
    # 전광판 ATM 표시는 1125.0 — 선물가 기준 ATM 과 다르다(#11a)
    assert {r.strike for r in atm} == {Decimal("1125.00")}
    assert all(r.code.startswith(("BAFBZW", "CAFBZW")) for r in b.calls + b.puts)


def test_blank_strings_become_none_and_unknown_fields_are_ignored() -> None:
    row = load("callput_202610.json")["output1"][0] | {
        "optn_bidp": "",
        "hts_otst_stpl_qty": "  ",
        "brand_new_field": "x",
    }
    r = CallPutRow.model_validate(row)
    assert r.optn_bidp is None
    assert r.oi is None
    assert not hasattr(r, "brand_new_field")


def test_board_row_requires_code_and_strike() -> None:
    row = load("callput_202610.json")["output1"][0]
    with pytest.raises(ValidationError):
        CallPutRow.model_validate(row | {"acpr": ""})
    with pytest.raises(ValidationError):
        CallPutRow.model_validate(row | {"optn_shrn_iscd": " "})


def test_board_models_are_frozen() -> None:
    r = CallPutRow.model_validate(load("callput_202610.json")["output1"][0])
    with pytest.raises(ValidationError):
        r.acpr = Decimal(1)  # type: ignore[misc]


# ── 월물리스트 ──


def test_option_list_rows() -> None:
    by_class = load("option_list.json")["by_class"]
    monthly = [OptionListRow.model_validate(r) for r in by_class["(blank)"]["output"]]
    assert [r.mtrt_yymm for r in monthly][:2] == ["202610", "202611"]
    assert monthly[0].mtrt_yymm_code == "0610"
    wkm = [OptionListRow.model_validate(r) for r in by_class["WKM"]["output"]]
    assert [r.mtrt_yymm for r in wkm] == ["260904", "261001"]  # YYMMWW (#12)
    assert by_class["K21"]["output"] == []


@pytest.mark.parametrize(
    ("yymm", "code"), [("20261", "0610"), ("2026100", "0610"), ("202610", "610"), ("", "0610")]
)
def test_option_list_rejects_wrong_widths(yymm: str, code: str) -> None:
    with pytest.raises(ValidationError):
        OptionListRow.model_validate({"mtrt_yymm": yymm, "mtrt_yymm_code": code})


# ── 선물 전광판 ──


def test_futures_board() -> None:
    fb = FuturesBoard.model_validate(load("futures_board.json"))
    assert [r.futs_shrn_iscd for r in fb.rows] == ["A01612", "A01703"]
    f = fb.rows[0]
    assert f.futs_prpr == Decimal("1095.10")
    assert (f.futs_bidp, f.futs_askp) == (Decimal("1095.00"), Decimal("1095.10"))
    assert f.hts_otst_stpl_qty == 137340 and f.acml_vol == 77504
    assert f.hts_rmnn_dynu == 74


# ── 단건 현재가 ──


def test_option_single_price() -> None:
    items = load("price_options.json")["items"]
    assert len(items) == 6
    for it in items:
        p = PriceOutput.model_validate(it["body"]["output1"])
        assert p.strike is not None
        assert p.hts_kor_isnm is not None and p.hts_kor_isnm.startswith(it["series"])
        assert p.futs_last_tr_date == date(2026, 10, 8)
        assert p.hts_rmnn_dynu == 11
        assert p.oi is not None and p.iv_kis is not None and p.gama is not None
    first = PriceOutput.model_validate(items[0]["body"]["output1"])
    assert first.strike == Decimal("1092.50")
    assert first.price == Decimal("27.85")
    assert first.futs_prdy_clpr == Decimal("0.00")


def test_single_price_futures_like_row_has_no_strike() -> None:
    p = PriceOutput.model_validate(
        {"futs_prpr": "1098.50", "acpr": "0.00", "futs_last_tr_date": ""}
    )
    assert p.strike is None
    assert p.futs_last_tr_date is None


@pytest.mark.parametrize("bad", ["2026-10-08", "2026108", "abcdefgh"])
def test_last_trade_date_format(bad: str) -> None:
    with pytest.raises(ValidationError):
        PriceOutput.model_validate({"futs_last_tr_date": bad})


# ── 투자자별 ──


def test_investor_rows_to_long_match_raw() -> None:
    pairs = load("investor.json")["pairs"]
    assert len(pairs) == 7
    for key, v in pairs.items():
        raw = v["output"][0]
        assert len(raw) == 72, key
        flows = InvestorRow.model_validate(raw).to_long()
        assert [f.investor for f in flows] == list(INVESTORS)
        for f in flows:
            p = f.investor
            assert f.sell_qty == int(raw[f"{p}_seln_vol"])
            assert f.buy_qty == int(raw[f"{p}_shnu_vol"])
            net_key = f"{p}_ntby_qty" if f"{p}_ntby_qty" in raw else f"{p}_ntby_vol"
            assert f.net_qty == int(raw[net_key])
            assert f.sell_value == int(raw[f"{p}_seln_tr_pbmn"])
            assert f.buy_value == int(raw[f"{p}_shnu_tr_pbmn"])
            assert f.net_value == int(raw[f"{p}_ntby_tr_pbmn"])


def test_investor_net_qty_naming_irregularity() -> None:
    # 실측: pe_fund·etc_orgt·etc_corp 만 순매수 수량이 `_ntby_vol`
    raw = load("investor.json")["pairs"]["K2I/F001"]["output"][0]
    vol_named = sorted(k.removesuffix("_ntby_vol") for k in raw if k.endswith("_ntby_vol"))
    assert vol_named == ["etc_corp", "etc_orgt", "pe_fund"]
    row = InvestorRow.model_validate(raw)
    assert row.flow("etc_corp").net_qty == -700
    assert row.flow("frgn").net_qty == -2460


def test_investor_real_rows_are_internally_consistent() -> None:
    # 순매수 수량 = 매수 − 매도 (대금은 반올림으로 ±1)
    for v in load("investor.json")["pairs"].values():
        for f in InvestorRow.model_validate(v["output"][0]).to_long():
            assert f.buy_qty is not None and f.sell_qty is not None and f.net_qty is not None
            assert f.net_qty == f.buy_qty - f.sell_qty
            assert f.buy_value is not None and f.sell_value is not None
            assert f.net_value is not None
            assert abs(f.net_value - (f.buy_value - f.sell_value)) <= 1


def test_investor_missing_field_is_an_error() -> None:
    raw = dict(load("investor.json")["pairs"]["K2I/OC01"]["output"][0])
    del raw["scrt_ntby_qty"]
    with pytest.raises(ValidationError, match="scrt_ntby_qty"):
        InvestorRow.model_validate(raw)


# ── 분봉 ──


def test_day_minute_bars() -> None:
    raw = load("minute_day.json")
    bars = [MinuteBar.model_validate(r) for r in raw["output2"]]
    assert [(b.stck_bsop_date, b.stck_cntg_hour) for b in bars] == [
        ("20260928", "140100"),
        ("20260928", "140000"),
        ("20260928", "135900"),
    ]
    b = bars[0]
    assert (b.futs_oprc, b.futs_hgpr, b.futs_lwpr, b.futs_prpr) == (
        Decimal("1097.10"),
        Decimal("1098.25"),
        Decimal("1096.90"),
        Decimal("1098.05"),
    )
    assert b.cntg_vol == 347 and b.acml_tr_pbmn == 21592073175


def test_night_minute_bar_keeps_extended_hour() -> None:
    # 합성 행 — 날짜·시각 짝은 #17 실측 (`20260922 300000` = 09-23 06:00 KST). 변환은 core/calendar
    bar = MinuteBar.model_validate(
        {
            "stck_bsop_date": "20260922",
            "stck_cntg_hour": "300000",
            "futs_oprc": "1127.00",
            "futs_hgpr": "1127.50",
            "futs_lwpr": "1126.90",
            "futs_prpr": "1127.25",
            "cntg_vol": "12",
            "acml_tr_pbmn": "",
        }
    )
    assert bar.stck_cntg_hour == "300000"
    assert bar.acml_tr_pbmn is None


@pytest.mark.parametrize("hour", ["084500", "154500", "235959", "240000", "295959", "300000"])
def test_minute_bar_accepts_session_hours(hour: str) -> None:
    row = load("minute_day.json")["output2"][0] | {"stck_cntg_hour": hour}
    assert MinuteBar.model_validate(row).stck_cntg_hour == hour


@pytest.mark.parametrize(
    "hour",
    # 야간 마지막 봉은 300000(= 06:00, #17) — 30시 이후 분·초는 세션 밖
    ["310000", "300001", "300100", "305959", "246000", "240060", "2400", "24000a"],
)
def test_minute_bar_rejects_bad_hour(hour: str) -> None:
    row = load("minute_day.json")["output2"][0] | {"stck_cntg_hour": hour}
    with pytest.raises(ValidationError):
        MinuteBar.model_validate(row)


# ── 공용 ──


def test_output_rows_shapes() -> None:
    assert output_rows({"output": {"a": "1"}}, "output") == [{"a": "1"}]
    assert output_rows({"output": [{"a": "1"}, "x"]}, "output") == [{"a": "1"}]
    assert output_rows({"output": None}, "output") == []
    assert output_rows({}, "output1") == []


def test_parse_rows_isolates_bad_rows() -> None:
    good = load("callput_202610.json")["output1"][:3]
    bad = dict(good[1]) | {"acpr": "N/A"}
    ok, errors = parse_rows(CallPutRow, [good[0], bad, good[2]])
    assert [r.code for r in ok] == [good[0]["optn_shrn_iscd"], good[2]["optn_shrn_iscd"]]
    assert [e.index for e in errors] == [1]
    assert "acpr" in errors[0].error
