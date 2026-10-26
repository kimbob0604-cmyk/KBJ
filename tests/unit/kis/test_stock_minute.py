"""KIS 주식일별분봉조회 파서·5분 묶기(kbj.data.private.kis.stock_minute)."""

from __future__ import annotations

from datetime import date, time

import pytest

from kbj.core.time import KST
from kbj.data.private.kis.stock_minute import (
    earlier_hour,
    parse_minutes,
    stock_minute_params,
    to_5m,
)
from tests.unit.services.cis30_fake import FakeKis

D = date(2026, 10, 23)


def _body(hour: str) -> dict:
    return FakeKis().get("", "", stock_minute_params("000100", D, hour)).body


def test_파라미터() -> None:
    p = stock_minute_params("005930", D, "153000")
    assert p["FID_COND_MRKT_DIV_CODE"] == "J" and p["FID_INPUT_DATE_1"] == "20261023"
    with pytest.raises(ValueError):
        stock_minute_params("005930", D, "1530")


def test_파서는_빈칸·0원_행을_버린다() -> None:
    body = _body("093000")
    body["output2"].append(
        {
            "stck_bsop_date": "20261023",
            "stck_cntg_hour": "090000",
            "stck_oprc": "",
            "stck_hgpr": "0",
            "stck_lwpr": "0",
            "stck_prpr": "0",
        }
    )
    rows = parse_minutes(body)
    assert rows[0].at == time(9, 30) and all(r.close > 0 for r in rows)
    assert {r.day for r in rows} == {D, date(2026, 10, 22)}  # 앞 거래일 봉이 섞여 온다


def test_이어_받을_시각() -> None:
    assert earlier_hour(time(11, 31)) == "113000" and earlier_hour(time(9, 0)) == "085900"


def test_5분_묶기() -> None:
    rows = parse_minutes(_body("153000")) + parse_minutes(_body("133000"))  # 겹치는 분 있음
    bars = to_5m("000100", rows, D)
    first = bars[0]
    assert first.ts.astimezone(KST).time() == time(11, 30) and first.trade_date == D
    assert bars[-1].ts.astimezone(KST).time() == time(15, 30) and bars[-1].minutes == 1
    mid = next(b for b in bars if b.ts.astimezone(KST).time() == time(12, 0))
    assert mid.minutes == 5 and mid.high >= max(mid.open, mid.close) and mid.volume == 5_000
