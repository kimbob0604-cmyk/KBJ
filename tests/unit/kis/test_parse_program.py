"""KIS 프로그램매매 TR 파서 — 합성 응답(P5 F2 §4: 칸 이름 표·원 단위 변환·날짜·장전 0·빈 문자열).

fixture 는 `tests/fixtures/synthetic/kis/p5_flows_program.json`(가짜 KIS 서버 `f2_output`).
TR·칸·단위는 전부 [확인 필요 #40·#41] — 이 시험은 파서가 상수 표대로 읽는지를 본다.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.private.kis.parse import KisEmpty, KisShapeError
from kbj.data.private.kis.program import (
    INVESTOR_CODES,
    MARKET_VALUE_UNIT_KRW,
    PROGRAM_TRS,
    STOCK_VALUE_UNIT_KRW,
    market_field,
    params_investor_today,
    params_market_daily,
    params_market_intraday,
    params_stock_daily,
    params_stock_intraday,
    parse_investor_today,
    parse_market_daily,
    parse_market_intraday,
    parse_stock_daily,
    parse_stock_intraday,
    stock_unit_ratio,
)
from kbj.engines.flows.program import check4

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "kis"
DAY = date(2026, 11, 17)
TS = datetime(2026, 11, 17, 13, 0, tzinfo=KST)


def _fx(name: str) -> dict[str, Any]:
    data = json.loads((FIX / "p5_flows_program.json").read_text(encoding="utf-8"))
    return data[name]["body"]


def test_tr_table_matches_the_official_examples() -> None:
    assert {k: v.tr_id for k, v in PROGRAM_TRS.items()} == {
        "program_market_daily": "FHPPG04600001",
        "program_market_intraday": "FHPPG04600101",
        "program_investor_today": "HHPPG046600C1",
        "program_stock_intraday": "FHPPG04650101",
        "program_stock_daily": "FHPPG04650201",
    }
    assert all(
        v.path.startswith("/uapi/domestic-stock/v1/quotations/") for v in PROGRAM_TRS.values()
    )


def test_params_use_venue_and_market_codes() -> None:
    p = params_market_daily("K", "KRX", date(2026, 9, 1), DAY)
    assert p == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_MRKT_CLS_CODE": "K",
        "FID_INPUT_DATE_1": "20260901",
        "FID_INPUT_DATE_2": "20261117",
    }
    assert params_market_intraday("Q", "NXT")["FID_COND_MRKT_DIV_CODE"] == "NX"
    assert params_investor_today("Q", "TOTAL") == {
        "EXCH_DIV_CLS_CODE": "UN",
        "MRKT_DIV_CLS_CODE": "4",
    }
    assert params_stock_intraday("990010", "KRX")["FID_INPUT_ISCD"] == "990010"
    assert params_stock_daily("990010", "KRX", DAY)["FID_INPUT_DATE_1"] == "20261117"
    with pytest.raises(ValueError, match="K"):
        params_market_daily("X", "KRX", DAY, DAY)
    with pytest.raises(ValueError, match="뒤"):
        params_market_daily("K", "KRX", DAY, date(2026, 11, 1))
    with pytest.raises(ValueError, match="거래소"):
        params_market_intraday("K", "LSE")


def test_market_daily_picks_the_asked_day_in_won_and_balances() -> None:
    body = _fx("program_daily_K")
    assert len(body["output"]) == 5  # 11-11~11-17 평일 5
    rows = parse_market_daily(body, day=DAY, market="K", venue="KRX")
    assert len(rows) == 9
    assert {(r.trade_date, r.market, r.venue, r.ts, r.quality) for r in rows} == {
        (DAY, "K", "KRX", None, Quality.OK)
    }
    raw = next(o for o in body["output"] if o["stck_bsop_date"] == "20261117")
    w = next(r for r in rows if (r.kind, r.side) == ("whol", "smtn"))
    assert (
        w.net_value
        == int(raw[market_field("whol", "smtn", "net_value")[0]]) * MARKET_VALUE_UNIT_KRW
    )
    assert w.index_close is not None
    assert check4(rows) == []  # 합성 응답은 검산 ④ 가 맞다
    # 그날 행이 없으면 공표 전(다른 날 값을 그날 값으로 쓰지 않는다)
    with pytest.raises(KisEmpty, match="행이 없다"):
        parse_market_daily(body, day=date(2026, 11, 18), market="K", venue="KRX")


def test_market_intraday_takes_the_latest_row_with_slot_ts() -> None:
    body = _fx("program_intraday_Q_1300")
    rows = parse_market_intraday(body, ts=TS, trade_date=DAY, market="Q", venue="KRX")
    latest = max(body["output"], key=lambda r: r["bsop_hour"])
    w = next(r for r in rows if (r.kind, r.side) == ("whol", "smtn"))
    assert w.net_value == int(latest["whol_smtn_ntby_tr_pbmn"]) * MARKET_VALUE_UNIT_KRW
    assert {r.ts for r in rows} == {TS}
    assert check4(rows) == []
    with pytest.raises(KisEmpty, match="0"):
        parse_market_intraday(
            _fx("program_intraday_preopen"), ts=TS, trade_date=DAY, market="K", venue="KRX"
        )


def test_missing_cells_are_none_not_zero_and_shape_error_names_keys() -> None:
    body = _fx("program_daily_K")
    row = dict(next(o for o in body["output"] if o["stck_bsop_date"] == "20261117"))
    for k in [k for k in row if k.startswith("arbt_onsl_")]:
        del row[k]
    row["nabt_entm_ntby_tr_pbmn"] = ""
    rows = parse_market_daily({"output": [row]}, day=DAY, market="K", venue="KRX")
    assert len(rows) == 8  # 차익·자기 칸이 통째로 없다 → 그 행 없음
    ne = next(r for r in rows if (r.kind, r.side) == ("nabt", "entm"))
    assert ne.net_value is None and ne.buy_value is not None
    with pytest.raises(KisShapeError, match="받은 키"):
        parse_market_daily(
            {"output": [{"stck_bsop_date": "20261117", "x": "1"}]}, day=DAY, market="K", venue="KRX"
        )


def test_investor_today_maps_codes_and_skips_unknown() -> None:
    body = _fx("program_investor_K")
    rows = parse_investor_today(body, trade_date=DAY, market="K", venue="KRX")
    assert {r.investor for r in rows} == set(INVESTOR_CODES.values())
    assert {r.kind for r in rows} == {"arbt", "nabt", "whol"}
    for inv in {r.investor for r in rows}:
        g = {r.kind: r for r in rows if r.investor == inv}
        assert g["whol"].net_value == (g["arbt"].net_value or 0) + (g["nabt"].net_value or 0)
    unknown = {"output": [{**body["output"][0], "invr_cls_code": "0042"}]}
    with pytest.raises(KisShapeError):
        parse_investor_today(unknown, trade_date=DAY, market="K", venue="KRX")


def test_stock_rows_scan_and_sweep_in_won() -> None:
    body = _fx("program_stock_1300")
    r = parse_stock_intraday(body, code="990010", ts=TS, trade_date=DAY, market="K", venue="KRX")
    latest = max(body["output"], key=lambda x: x["bsop_hour"])
    assert (r.scope, r.ts, r.quality) == ("scan", TS, Quality.ESTIMATED)
    assert r.net_value == int(latest["whol_smtn_ntby_tr_pbmn"]) * STOCK_VALUE_UNIT_KRW
    assert r.price == float(latest["stck_prpr"])
    d = parse_stock_daily(
        _fx("program_stock_daily"), code="990010", day=DAY, market="K", venue="KRX"
    )
    assert (d.scope, d.ts, d.quality, d.trade_date) == ("sweep", None, Quality.OK, DAY)
    with pytest.raises(KisEmpty):
        parse_stock_daily(
            _fx("program_stock_daily"),
            code="990010",
            day=date(2026, 11, 18),
            market="K",
            venue="KRX",
        )
    # 금액 자릿수 대조 — 단위를 맞게 알면 1 근처
    ratio = stock_unit_ratio([r, d])
    assert ratio is not None and 0.2 <= ratio <= 5
    assert stock_unit_ratio([]) is None
