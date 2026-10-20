"""최대주주 현황 — kbj.data.public.dart.largest_holder(metrics §24.2, 실측 모양 10-21, 합성 값).

종류별 '계' 행을 쓰고(없으면 사람 행 합), '의결권 있는/없는 주식'(줄바꿈 포함)도 보통주·우선주로
읽는다. 접수일은 넘긴 값, 없으면 접수번호 앞 8자리.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kbj.data.public.dart.doctext import ParseError
from kbj.data.public.dart.largest_holder import parse_largest_holder

CORP = "00000001"
RC = "20260814000123"


def _row(nm: str, knd: str, n: str, relate: str | None = "특수관계인") -> dict[str, Any]:
    return {"rcept_no": RC, "corp_code": CORP, "nm": nm, "relate": relate, "stock_knd": knd,
            "trmend_posesn_stock_co": n, "stlm_dt": "2026-06-30"}  # fmt: skip


def test_total_rows_by_class() -> None:
    js = {"status": "000", "list": [
        _row("합성지주㈜", "의결권\n있는 주식", "1,000", "최대주주"),
        _row("임원갑", "의결권\n있는 주식", "20"),
        _row("임원갑", "의결권 없는 주식", "5"),
        _row("계", "의결권\n있는 주식", "1,020", None),
        _row("계", "의결권 없는 주식", "5", None),
    ]}  # fmt: skip
    got = parse_largest_holder(js, CORP)
    assert got is not None
    assert (got.common_held, got.pref_held) == (1020, 5)
    assert got.holders == ("합성지주㈜", "임원갑")
    assert got.period_end == date(2026, 6, 30)
    assert got.rcept_dt == date(2026, 8, 14)  # 접수번호 앞 8자리


def test_sum_when_no_total_row_and_dash_class() -> None:
    js = {"status": "000", "list": [
        _row("합성재단", "보통주", "300", "본인"),
        _row("임원을", "보통주", "7"),
        _row("계", "-", "-", None),  # 종류 없는 빈 합계 행
    ]}  # fmt: skip
    got = parse_largest_holder(js, CORP, rcept_dt=date(2026, 8, 20))
    assert got is not None
    assert (got.common_held, got.pref_held) == (307, None)
    assert got.rcept_dt == date(2026, 8, 20)


def test_empty_and_bad() -> None:
    assert parse_largest_holder({"status": "013"}, CORP) is None
    assert parse_largest_holder({"status": "000", "list": []}, CORP) is None
    bad = _row("갑", "보통주", "1")
    bad["rcept_no"] = "x"
    with pytest.raises(ParseError):
        parse_largest_holder({"status": "000", "list": [bad]}, CORP)
