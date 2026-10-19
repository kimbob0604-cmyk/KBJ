"""KIS 장중 시장별 투자자(FHPTJ04030000) 파서 — 합성 응답(P5 F2 §4: 12구분·`_ntby_vol`/`_ntby_qty`
차이·빈 문자열·음수·단위·장전 0).

fixture 는 `tests/fixtures/synthetic/kis/p5_flows_program.json`(생성기 `p5_make.py` — 가짜 KIS 서버
`f2_output`, 실측 아님). 대금 단위는 백만원 그대로(investor_flow 계약 — 원으로는 API 가 바꾼다).
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from kbj.core.p5_rows import MINUTE_INVESTORS
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.private.kis.investors_minute import (
    TR_MARKET_MINUTE,
    VALUE_UNIT,
    field_names,
    params_market_minute,
    parse_market_minute,
)
from kbj.data.private.kis.parse import KisEmpty, KisParseError, KisShapeError
from kbj.engines.flows.minute import ORGN_PARTS

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "kis"
DAY = date(2026, 11, 17)
TS = datetime(2026, 11, 17, 13, 0, tzinfo=KST)


def _fx(name: str) -> dict[str, Any]:
    data = json.loads((FIX / "p5_flows_program.json").read_text(encoding="utf-8"))
    return data[name]


def parse(body: dict[str, Any], **kw: Any) -> list[Any]:
    args: dict[str, Any] = {
        "ts": TS, "trade_date": DAY, "market_code": "K2I", "sector_code": "F001",
    }  # fmt: skip
    args.update(kw)
    return parse_market_minute(body, **args)


def test_committed_fixture_equals_generator_output() -> None:
    spec = importlib.util.spec_from_file_location("_p5_make", FIX / "p5_make.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (FIX / "p5_flows_program.json").read_text(encoding="utf-8") == mod.render()
    data = json.loads((FIX / "p5_flows_program.json").read_text(encoding="utf-8"))
    assert str(data["_source"]).startswith("SYNTHETIC")


def test_tr_and_params() -> None:
    assert TR_MARKET_MINUTE.tr_id == "FHPTJ04030000"
    assert TR_MARKET_MINUTE.path.endswith("/inquire-investor-time-by-market")
    assert params_market_minute("K2I", "F001") == {
        "FID_INPUT_ISCD": "K2I",
        "FID_INPUT_ISCD_2": "F001",
    }
    with pytest.raises(ValueError, match="코드를 모르는"):
        params_market_minute("", "F001")
    # 현물·주식선물 — KIS 공식 Postman 샘플의 코드 표
    assert params_market_minute("KSP", "0001") == {
        "FID_INPUT_ISCD": "KSP",
        "FID_INPUT_ISCD_2": "0001",
    }
    assert params_market_minute("999", "S001")["FID_INPUT_ISCD"] == "999"
    assert VALUE_UNIT == "백만원"


def test_twelve_investors_with_vol_and_qty_suffixes() -> None:
    fx = _fx("minute_fut_1300")
    row = fx["body"]["output"][0]
    # GX 실측: pe_fund·etc_orgt·etc_corp 순매수 수량은 _ntby_vol, 나머지는 _ntby_qty
    for inv in ("pe_fund", "etc_orgt", "etc_corp"):
        assert f"{inv}_ntby_vol" in row and f"{inv}_ntby_qty" not in row
    assert "frgn_ntby_qty" in row and "frgn_ntby_vol" not in row
    rows = parse(fx["body"])
    assert [r.investor for r in rows] == list(MINUTE_INVESTORS)
    by = {r.investor: r for r in rows}
    for inv in MINUTE_INVESTORS:
        names = field_names(inv)
        sfx = 1 if inv in ("pe_fund", "etc_orgt", "etc_corp") else 0
        assert by[inv].net_qty == int(row[names["net_qty"][sfx]])
        # 대금은 백만원 그대로(× 1 — 원 변환은 API)
        assert by[inv].net_value == int(row[names["net_value"][0]])
        assert by[inv].net_value == (by[inv].buy_value or 0) - (by[inv].sell_value or 0)
    assert {(r.ts, r.trade_date, r.quality, r.source, r.session) for r in rows} == {
        (TS, DAY, Quality.OK, "KIS", "day")
    }
    # 합성 응답은 검산 ②(기관 = 7세부)가 맞다
    orgn = by["orgn"].net_value
    assert orgn == sum(by[p].net_value or 0 for p in ORGN_PARTS)


def test_cumulative_grows_with_minutes() -> None:
    a = {r.investor: r for r in parse(_fx("minute_fut_0901")["body"])}
    b = {r.investor: r for r in parse(_fx("minute_fut_1300")["body"])}
    assert all((b[i].sell_value or 0) >= (a[i].sell_value or 0) for i in MINUTE_INVESTORS)
    assert (b["prsn"].buy_value or 0) > (a["prsn"].buy_value or 0)


def test_preopen_all_zero_is_not_a_fact() -> None:
    with pytest.raises(KisEmpty, match="장 시작 전"):
        parse(_fx("minute_preopen")["body"], sector_code="OC01")


def test_blank_values_are_none_and_missing_investor_has_no_row() -> None:
    row = dict(_fx("minute_fut_1300")["body"]["output"][0])
    for k in [k for k in row if k.startswith("mrbn_")]:
        del row[k]  # 종금 칸이 통째로 없다 → 행 없음(0 으로 채우지 않는다)
    row["bank_ntby_tr_pbmn"] = ""
    row["bank_seln_tr_pbmn"] = "-"
    rows = {r.investor: r for r in parse({"output": [row]})}
    assert "mrbn" not in rows
    assert rows["bank"].net_value is None and rows["bank"].sell_value is None
    assert rows["bank"].buy_value is not None


def test_negative_net_and_signed_strings() -> None:
    row = dict(_fx("minute_fut_1300")["body"]["output"][0])
    row["frgn_ntby_tr_pbmn"] = "-1,234"
    row["frgn_ntby_qty"] = "+5"
    rows = {r.investor: r for r in parse({"output": [row]})}
    assert rows["frgn"].net_value == -1234 and rows["frgn"].net_qty == 5


def test_shape_errors_name_keys_not_values() -> None:
    with pytest.raises(KisShapeError, match="투자자 칸"):
        parse({"output": [{"foo": "1", "bar": "2"}]})
    with pytest.raises(KisShapeError):
        parse({"rt_cd": "0"})
    with pytest.raises(KisEmpty):
        parse({"output": []})
    with pytest.raises(KisParseError):
        row = dict(_fx("minute_fut_1300")["body"]["output"][0])
        row["frgn_ntby_tr_pbmn"] = "1.5"  # 정수 칸에 소수 — 조용히 자르지 않는다
        parse({"output": [row]})


def test_negative_sell_is_rejected() -> None:
    row = dict(_fx("minute_fut_1300")["body"]["output"][0])
    row["frgn_seln_tr_pbmn"] = "-3"
    with pytest.raises(ValueError, match="0 이상"):
        parse({"output": [row]})
