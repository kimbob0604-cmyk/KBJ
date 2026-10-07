"""가짜 KIS 서버(tests/fakes/kis_server.py) 자체 검사 — poller 테스트가 믿는 동작을 고정한다."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from data.kis.master import series_for
from data.kis.models import CallPutBoard, PriceOutput, output_rows
from data.kis.ratelimit import LocalRateLimiter
from scripts.probe_common import (
    P_CALLPUT,
    P_FUT_BOARD,
    P_INVESTOR,
    P_OPTION_LIST,
    P_PRICE,
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_INVESTOR,
    TR_OPTION_LIST,
    TR_PRICE,
)
from tests.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    StaticTokenProvider,
    default_chain,
    make_client,
)

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 28, 10, 0, tzinfo=KST)


def server() -> FakeKisServer:
    return FakeKisServer(FakeClock(T0), default_chain())


def get(s: FakeKisServer, path: str, tr_id: str, params: dict[str, str]) -> httpx.Response:
    with httpx.Client(transport=s.transport, base_url="https://fake.invalid") as c:
        return c.get(path, params=params, headers={"tr_id": tr_id})


def board_params(cls: str, mtrt: str, market: str = "O") -> dict[str, str]:
    return {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": "20503",
        "FID_MRKT_CLS_CODE": "CO",
        "FID_MTRT_CNT": mtrt,
        "FID_MRKT_CLS_CODE1": "PO",
        "FID_COND_MRKT_CLS_CODE": cls,
    }


def test_synthetic_master_parses_into_expected_families() -> None:
    rows = default_chain().master_rows()
    wkm = series_for(rows, "kospi200_weekly_mon", "260904", "C")
    wki = series_for(rows, "kospi200_weekly_thu", "261001", "P")
    mon = series_for(rows, "kospi200", "202610", "C")
    assert len(wkm) == len(wki) == 111 and len(mon) == 341
    assert (wkm[0].strike, wkm[-1].strike) == (Decimal("970.00"), Decimal("1245.00"))
    assert mon[0].name == "C 202610 745.0" and wkm[0].name.startswith("위클리M C 2609W4")
    futs = [r for r in rows if r.strike is None]
    assert [r.code for r in futs] == ["A01612", "A01703"]


def test_board_is_top_100_descending_with_master_codes() -> None:
    s = server()
    body: dict[str, Any] = get(s, P_CALLPUT, TR_CALLPUT, board_params("", "202610")).json()
    b = CallPutBoard.model_validate(body)
    assert len(b.calls) == len(b.puts) == 100
    assert b.calls[0].strike == Decimal("1595.00") and b.calls[-1].strike == Decimal("1347.50")
    master = {r.code: r for r in default_chain().master_rows()}
    assert all(master[r.code].strike == r.strike for r in b.calls + b.puts)
    assert [r.strike for r in b.calls if r.atm_cls_name == "ATM"] == []  # 1125.0 은 100행 밖


def test_board_rejects_night_market_codes() -> None:
    s = server()
    body = get(s, P_CALLPUT, TR_CALLPUT, board_params("WKM", "260904", "EU")).json()
    assert body["msg_cd"] == "OPSQ2001"
    body = get(s, P_FUT_BOARD, TR_FUT_BOARD, {"FID_COND_MRKT_DIV_CODE": "CM"}).json()
    assert body["msg_cd"] == "OPSQ2001"


def test_unknown_board_series_falls_back_to_fixture_or_empty() -> None:
    s = FakeKisServer(FakeClock(T0))  # 체인 없음 → fixture 원문
    body = get(s, P_CALLPUT, TR_CALLPUT, board_params("WKM", "260904")).json()
    assert len(body["output1"]) == 20 and "_source" not in body
    body = get(s, P_CALLPUT, TR_CALLPUT, board_params("WKI", "261001")).json()
    assert body["rt_cd"] == "0" and body["output1"] == []


def test_single_price_by_code_and_market() -> None:
    s = server()
    chain = default_chain()
    wk = chain.find("WKI", "261001")
    assert wk is not None
    code = wk.code("P", Decimal("1100.0"))
    for market in ("O", "EU"):
        body = get(s, P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code})
        out = PriceOutput.model_validate(output_rows(body.json(), "output1")[0])
        assert out.strike == Decimal("1100.00")
        assert out.futs_last_tr_date == date(2026, 10, 1)
    s.futures_price = Decimal("990.25")
    fut = get(s, P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "CM", "FID_INPUT_ISCD": "A01612"})
    out = PriceOutput.model_validate(fut.json()["output1"])
    assert out.price == Decimal("990.25") and out.strike is None
    fix = get(s, P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "O", "FID_INPUT_ISCD": "B01610ZC6"})
    assert fix.json()["output1"]["acpr"] == "1095.00"  # 체인 밖 코드는 fixture 원문


def test_option_list_investor_and_futures_fixtures() -> None:
    s = server()
    ol = get(s, P_OPTION_LIST, TR_OPTION_LIST, {"FID_COND_MRKT_CLS_CODE": "WKM"}).json()
    assert [r["mtrt_yymm"] for r in ol["output"]] == ["260904", "261001"]
    inv = get(s, P_INVESTOR, TR_INVESTOR, {"FID_INPUT_ISCD": "WKI", "FID_INPUT_ISCD_2": "OP04"})
    assert inv.json()["rt_cd"] == "0" and len(inv.json()["output"]) == 1
    s.futures_price = Decimal("1100.00")
    fb = get(s, P_FUT_BOARD, TR_FUT_BOARD, {"FID_COND_MRKT_DIV_CODE": "F"}).json()
    assert fb["output"][0]["futs_prpr"] == "1100.00"


def test_faults_apply_to_matching_calls_only() -> None:
    s = server()
    s.inject("rate_limit", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "202610"})
    s.inject("malformed", tr_id=TR_INVESTOR, mutate=lambda b: b["output"][0].pop("frgn_seln_vol"))
    s.inject("http500", tr_id=TR_PRICE)
    r = get(s, P_CALLPUT, TR_CALLPUT, board_params("WKM", "260904"))
    assert r.json()["rt_cd"] == "0"
    r = get(s, P_CALLPUT, TR_CALLPUT, board_params("", "202610"))
    assert (r.status_code, r.json()["msg_cd"]) == (500, "EGW00201")
    r = get(s, P_CALLPUT, TR_CALLPUT, board_params("", "202610"))
    assert r.json()["rt_cd"] == "0"  # 한 번만
    inv = get(s, P_INVESTOR, TR_INVESTOR, {"FID_INPUT_ISCD": "K2I", "FID_INPUT_ISCD_2": "F001"})
    assert "frgn_seln_vol" not in inv.json()["output"][0]
    r = get(s, P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "O", "FID_INPUT_ISCD": "x"})
    assert r.status_code == 500 and r.text == "Internal Server Error"
    assert [c.fault for c in s.calls] == [None, "rate_limit", None, "malformed", "http500"]


def test_calls_are_timestamped_by_fake_clock_and_windowed() -> None:
    clock = FakeClock(T0)
    s = FakeKisServer(clock, default_chain())
    for dt in (0.0, 0.25, 0.25, 0.25, 0.25, 0.5):
        clock.sleep(dt)
        get(s, P_OPTION_LIST, TR_OPTION_LIST, {"FID_COND_MRKT_CLS_CODE": ""})
    assert [c.t_us - s.calls[0].t_us for c in s.calls] == [
        0,
        250_000,
        500_000,
        750_000,
        1_000_000,
        1_500_000,
    ]
    assert s.max_in_window(1.0) == 4  # [0, 1) 에 4건, 1.0 은 다음 창
    assert clock.now() == T0.replace(second=1, microsecond=500_000)


def test_client_through_limiter_and_token_retry() -> None:
    clock = FakeClock(T0)
    s = FakeKisServer(clock, default_chain())
    tokens = StaticTokenProvider()
    kis = make_client(s, LocalRateLimiter(clock=clock), tokens)
    s.inject("token", tr_id=TR_OPTION_LIST)
    r = kis.get(P_OPTION_LIST, TR_OPTION_LIST, {"FID_COND_MRKT_CLS_CODE": "WKI"})
    assert r.ok and tokens.invalidated == 1 and len(s.calls) == 2
    assert s.calls[1].t_us - s.calls[0].t_us == 250_000  # 재시도도 리미터를 거친다
    assert s.token_posts == 0
