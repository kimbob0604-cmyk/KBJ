"""가짜 KIS P5 F2 TR(장중 투자자 1분·프로그램매매) — 경로·tr_id 는 파서 표, 1분마다 누적 증가,
장전 0, 마감 뒤 고정, 검산 ①②④ 정확, 오류 주입 HTTP429·TIMEOUT."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from kbj.core.time import KST
from kbj.data.private.kis.investors_minute import TR_MARKET_MINUTE, parse_market_minute
from kbj.data.private.kis.parse import KisEmpty
from kbj.data.private.kis.program import PROGRAM_TRS, parse_market_daily
from kbj.engines.flows.minute import check_slot
from kbj.engines.flows.program import check4
from tests.fakes.kis_server import TRS, FakeKisServer, f2_minute_index, f2_output
from tests.unit.collectors.p3_fakes import Clock, DirectKis

DAY = "20261117"
P = {"FID_INPUT_ISCD": "SYNK", "FID_INPUT_ISCD_2": "SYN0"}


def rows_at(hhmm: str) -> dict[str, object]:
    t = datetime(2026, 11, 17, int(hhmm[:2]), int(hhmm[2:]), tzinfo=KST)
    got = parse_market_minute(
        f2_output(TR_MARKET_MINUTE.tr_id, P, DAY, hhmm), ts=t, trade_date=t.date(),
        market_code="SYNK", sector_code="SYN0",
    )  # fmt: skip
    return {r.investor: r for r in got}


def test_paths_come_from_the_parser_tables() -> None:
    assert TRS[TR_MARKET_MINUTE.tr_id] == TR_MARKET_MINUTE.path
    for spec in PROGRAM_TRS.values():
        assert TRS[spec.tr_id] == spec.path


def test_minute_index() -> None:
    assert [f2_minute_index(x) for x in ("0830", "0900", "0901", "1530", "1540", "")] == [
        0, 1, 2, 391, 391, 391,
    ]  # fmt: skip


def test_cumulative_grows_and_checks_hold_every_minute() -> None:
    prev = None
    for hhmm in ("0900", "0901", "1000", "1530"):
        by = rows_at(hhmm)
        c = check_slot(by, spot=True)  # type: ignore[arg-type]
        assert c.c1_residual == 0 and c.c2_ok is True
        if prev is not None:
            assert by["prsn"].buy_value >= prev["prsn"].buy_value  # type: ignore[attr-defined]
        prev = by
    tr = TR_MARKET_MINUTE.tr_id
    assert f2_output(tr, P, DAY, "1530") == f2_output(tr, P, DAY, "1540")  # 마감 뒤는 그대로
    with pytest.raises(KisEmpty):
        rows_at("0859")


def test_program_daily_rows_balance() -> None:
    from datetime import date

    params = {"FID_MRKT_CLS_CODE": "K", "FID_COND_MRKT_DIV_CODE": "J",
              "FID_INPUT_DATE_1": "20261101", "FID_INPUT_DATE_2": DAY}  # fmt: skip
    body = f2_output(PROGRAM_TRS["program_market_daily"].tr_id, params, DAY)
    assert {r["stck_bsop_date"] for r in body["output"]} >= {DAY, "20261116"}
    assert all(
        datetime.strptime(r["stck_bsop_date"], "%Y%m%d").replace(tzinfo=KST).weekday() < 5
        for r in body["output"]
    )
    rows = parse_market_daily(body, day=date(2026, 11, 17), market="K", venue="KRX")
    assert check4(rows) == []


def test_http429_and_timeout_injection() -> None:
    clock = Clock(datetime(2026, 11, 17, 10, 0, 5, tzinfo=KST))
    server = FakeKisServer(clock)
    kis = DirectKis(server)
    server.inject("HTTP429", 1, tr_id=TR_MARKET_MINUTE.tr_id)
    r = kis.get(TR_MARKET_MINUTE.path, TR_MARKET_MINUTE.tr_id, P)
    assert r.status == 429 and not r.ok
    server.inject("TIMEOUT", 1, tr_id=TR_MARKET_MINUTE.tr_id)
    with pytest.raises(httpx.ReadTimeout):
        kis.get(TR_MARKET_MINUTE.path, TR_MARKET_MINUTE.tr_id, P)
    ok = kis.get(TR_MARKET_MINUTE.path, TR_MARKET_MINUTE.tr_id, P)
    assert ok.ok and ok.body["output"][0]["prsn_ntby_tr_pbmn"]
    assert [c.msg_cd for c in server.calls][-3:] == ["HTTP429", "TIMEOUT", ""]
    with pytest.raises(ValueError, match="모르는"):
        server.inject("HTTP418")
