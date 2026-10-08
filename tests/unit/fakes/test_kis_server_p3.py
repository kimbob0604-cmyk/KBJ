"""가짜 KIS 서버의 P3 TR(묶음 C) — 경로·슬롯 시드·합성 원장 성질·오류 주입."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from kbj.data.private.kis.parse import PATHS
from kbj.services.auth.issuer import TOKEN_PATH
from tests.fakes.kis_server import (
    APP_KEY,
    APP_SECRET,
    RANK_ROWS,
    SYMBOLS,
    TRS,
    FakeKisServer,
    market_symbols,
    synthetic_output,
)

KST = ZoneInfo("Asia/Seoul")


def test_p3_paths_match_the_kbj_parser_table() -> None:
    for spec in PATHS.values():
        assert TRS[spec.tr_id] == spec.path


def test_intraday_values_change_by_slot_and_are_stable_within_a_slot() -> None:
    p = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}
    a = synthetic_output("FHPUP02100000", p, "20261007", "1000")
    b = synthetic_output("FHPUP02100000", p, "20261007", "1000")
    c = synthetic_output("FHPUP02100000", p, "20261007", "1010")
    assert a == b
    assert a != c


def test_market_split_and_rank_rows() -> None:
    assert len(market_symbols("0001")) == len(market_symbols("1001")) == 10
    assert set(market_symbols("0001")) | set(market_symbols("1001")) == set(SYMBOLS)
    out = synthetic_output("FHPST01710000", {"FID_INPUT_ISCD": "1001"}, "20261007", "1000")
    rows = out["output"]
    assert len(rows) == RANK_ROWS
    turnovers = [int(r["acml_tr_pbmn"]) for r in rows]
    assert turnovers == sorted(turnovers, reverse=True)


def test_investor_rows_have_distinct_dates() -> None:
    out = synthetic_output("FHKST01010900", {"FID_INPUT_ISCD": SYMBOLS[0]}, "20261007")
    assert [r["stck_bsop_date"] for r in out["output"]] == ["20261007", "20261006", "20261005"]


def test_server_passes_the_slot_and_injects_http500() -> None:
    now = datetime(2026, 10, 7, 10, 3, tzinfo=KST)
    server = FakeKisServer(lambda: now)
    tok = server.handle(
        httpx.Request(
            "POST",
            "https://x" + TOKEN_PATH,
            json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        ),
        "auth",
    ).json()["access_token"]
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "995010"}
    req = httpx.Request(
        "GET",
        "https://x" + TRS["FHPST02400000"],
        params=params,
        headers={"authorization": f"Bearer {tok}", "appkey": APP_KEY, "appsecret": APP_SECRET,
                 "tr_id": "FHPST02400000"},
    )  # fmt: skip
    server.inject("HTTP500", 1, tr_id="FHPST02400000")
    assert server.handle(req, "scheduler").status_code == 500
    ok = server.handle(req, "scheduler")
    assert ok.status_code == 200
    assert ok.json() == synthetic_output("FHPST02400000", params, "20261007", "1000")
