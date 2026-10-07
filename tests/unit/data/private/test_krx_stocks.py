"""KRX 주식 일별 → 저장 모양(ET `board/ingest/krx.py` 승격 부분). 합성 fixture.

ET 에는 이 모듈의 시험이 없었다(`test_close_source` 는 파이프라인 시험이라 P3) — 새로 쓴 시험.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.data.private.krx.models import KrxRowsError
from kbj.data.private.krx.stocks import (
    index_by_name,
    isu_to_code,
    normalize_stock_row,
    parse_index_rows,
    parse_stock_rows,
    sector_map,
)
from kbj.data.spec import Venue

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def rows(market: str = "kospi") -> list[dict[str, Any]]:
    return fixture("stock_daily.json")[market]["20260930"]


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("005930", "005930"),
        (" 005930 ", "005930"),
        ("KR7005930003", "005930"),  # ISIN 가운데 6자리(ET)
        ("A005930", "005930"),
        ("9900K0", "9900K0"),  # 영숫자 단축코드 [추정]
        ("kr79900k0004", "9900K0"),
        ("", ""),
        (None, ""),
        ("KOSPI", ""),  # ET 의 옛 버그(전체 매치 → 빈 문자열)와 달리 '못 뽑음'만 빈 문자열
    ],
)
def test_isu_to_code(raw: object, want: str) -> None:
    assert isu_to_code(raw) == want


def test_normalized_rows_keep_won_amounts_source_and_venue() -> None:
    raw = rows()[0]
    r = normalize_stock_row(raw, market="kospi")
    assert (r.code, r.name, r.market) == (raw["ISU_CD"], raw["ISU_NM"], "KOSPI")
    assert r.turnover == int(raw["ACC_TRDVAL"]) and not r.turnover_is_estimate  # 원(억원 아님)
    assert r.mktcap == int(raw["MKTCAP"]) and r.shares == int(raw["LIST_SHRS"])
    assert r.close == Decimal(raw["TDD_CLSPRC"]) and r.chg_pct == Decimal(raw["FLUC_RT"])
    assert r.as_of == date(2026, 9, 30)
    assert r.source == "KRX:sto/stk_bydd_trd" and r.quality is Quality.OK
    assert r.venue is Venue.KRX


def test_market_name_falls_back_to_the_called_market() -> None:
    raw = {k: v for k, v in rows("kosdaq")[0].items() if k != "MKT_NM"}
    assert normalize_stock_row(raw, market="kosdaq").market == "KOSDAQ"


def test_missing_turnover_is_marked_estimate_not_zero() -> None:
    r = normalize_stock_row(dict(rows()[0]) | {"ACC_TRDVAL": ""}, market="kospi")
    assert r.turnover is None and r.turnover_is_estimate  # 0 으로 채우지 않는다(ET D-019)


def test_parse_keeps_closeless_rows_as_invalid_and_reports_codeless_rows() -> None:
    items = rows()[:4]
    items[1] = dict(items[1]) | {"TDD_CLSPRC": ""}
    items[2] = dict(items[2]) | {"ISU_CD": "KOSPI"}
    ok, bad = parse_stock_rows(items, market="kospi")
    assert [r.quality for r in ok] == [Quality.OK, Quality.INVALID, Quality.OK]
    assert [e.index for e in bad] == [2] and "종목코드" in bad[0].error


def test_parse_raises_when_no_row_fits() -> None:
    with pytest.raises(KrxRowsError):
        parse_stock_rows([{"foo": "bar"}, {"BAS_DD": "x"}], market="kospi")
    assert parse_stock_rows([], market="kosdaq") == ([], [])  # 휴장·갱신 전


def test_sector_map_uses_segment_when_there_is_no_industry_field() -> None:
    ok, _ = parse_stock_rows(rows("kosdaq"), market="kosdaq")
    m = sector_map(ok)
    assert set(m) == {r.code for r in ok}
    assert set(m.values()) <= {"우량기업부", "벤처기업부", "중견기업부", "기술성장기업부"}
    with_industry = normalize_stock_row(dict(rows("kosdaq")[0]) | {"IDX_IND_NM": "반도체"})
    assert with_industry.sector == "반도체"  # ET 후보 순서: 업종명이 먼저


def test_sector_map_fails_loudly_when_empty() -> None:
    ok, _ = parse_stock_rows(rows("kospi"), market="kospi")  # 코스피는 소속부가 비었다
    with pytest.raises(ValueError, match="업종 필드가 비었다"):
        sector_map(ok)


def test_index_rows_by_name() -> None:
    ok, bad = parse_index_rows(
        fixture("index_daily.json")["kospi"]["20260930"], endpoint="/idx/kospi_dd_trd"
    )
    by = index_by_name(ok)
    assert bad == [] and "코스피" in by and by["코스피"].clsprc_idx is not None
    with pytest.raises(ValueError, match="겹친다"):
        index_by_name([*ok, ok[0]])
