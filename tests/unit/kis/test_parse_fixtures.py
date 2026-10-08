"""P3 합성 fixture(tests/fixtures/synthetic/kis/p3_responses.json) — 생성기와 같고, 모든 파서가
읽는다.

가짜 KIS 서버의 응답 모양과 kbj 파서가 맞물리는지 한 번에 본다(묶음 C). 실측 아님.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from kbj.core.rows import GROUP4, INST7, Investor
from kbj.data.private.kis.etf import parse_etf_quote
from kbj.data.private.kis.index import parse_index_quote, parse_sector_quotes
from kbj.data.private.kis.investors import (
    closes_of,
    parse_inst_foreign_total,
    parse_market_investor,
    parse_stock_investor,
    unit_check,
)
from kbj.data.private.kis.parse import PATHS
from kbj.data.private.kis.quotes import bar_from_quote, parse_quote
from kbj.data.private.kis.ranks import parse_rank_snaps, parse_volume_rank
from tests.fakes.kis_server import RANK_ROWS, SECTORS_PER_MARKET, TRS

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "kis"
TS = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)
DAY = date(2026, 10, 7)


def load() -> dict[str, Any]:
    return json.loads((FIX / "p3_responses.json").read_text(encoding="utf-8"))


def body(name: str) -> dict[str, Any]:
    return load()[name]["body"]


def test_fixture_is_synthetic_and_matches_the_generator() -> None:
    assert load()["_source"].startswith("SYNTHETIC")
    spec = importlib.util.spec_from_file_location("_make_p3", FIX / "make_p3.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (FIX / "p3_responses.json").read_text(encoding="utf-8") == mod.render()


def test_paths_agree_with_the_fake_server() -> None:
    for spec in PATHS.values():
        assert TRS[spec.tr_id] == spec.path


def test_every_parser_reads_the_fixture() -> None:
    s = parse_quote(body("stock_quote"), load()["stock_quote"]["params"]["FID_INPUT_ISCD"], day=DAY)
    assert s.close is not None
    b = bar_from_quote(body("stock_quote"), s)
    assert b.high is not None and b.low is not None
    assert b.low <= b.close <= b.high  # type: ignore[operator]

    code = load()["stock_investor"]["params"]["FID_INPUT_ISCD"]
    inv = parse_stock_investor(body("stock_investor"), code)
    assert len({r.date for r in inv}) == 3
    closes = {(code, d): c for d, c in closes_of(body("stock_investor")).items()}
    assert unit_check(inv, closes).ok

    m = parse_market_investor(body("market_investor"), "0001")
    for d in {r.date for r in m}:
        day = {r.investor: r.net_value or 0 for r in m if r.date == d}
        assert sum(day[i] for i in GROUP4) == 0
        assert sum(day[i] for i in INST7) == day[Investor.INSTITUTION]

    assert len({r.code for r in parse_inst_foreign_total(body("inst_foreign_total"), ts=TS)}) == (
        RANK_ROWS
    )
    assert len(parse_volume_rank(body("volume_rank"), market="KOSDAQ", ts=TS)) == RANK_ROWS
    assert len(parse_rank_snaps(body("volume_rank"), market="KOSDAQ", day=DAY)) == RANK_ROWS
    assert parse_etf_quote(body("etf_quote"), "995010", TS).inav is not None
    assert parse_index_quote(body("index_quote"), "0001", TS).turnover is not None
    assert len(parse_sector_quotes(body("sector_quotes"), "KOSDAQ", TS)) == SECTORS_PER_MARKET
