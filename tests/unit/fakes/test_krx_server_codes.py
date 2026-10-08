"""가짜 KRX 의 종목 코드가 시장·자산 사이에서 겹치지 않는다(묶음 S — 메인이 본 열린 항목).

코넥스 템플릿이 코스닥 첫 행(998010)을 코드째 베껴 같은 (code, date, source='krx') 원장 행을 두
시장이 덮어쓰던 문제를 막는다. 원장·유니버스는 코드가 키라 겹치면 시장이 조용히 바뀐다.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from tests.fakes.krx_server import rows_for

DAY = date(2026, 10, 6)
LISTED = (
    "/sto/stk_bydd_trd",
    "/sto/ksq_bydd_trd",
    "/sto/knx_bydd_trd",
    "/etp/etf_bydd_trd",
    "/etp/etn_bydd_trd",
)


def test_codes_are_unique_across_markets_and_assets() -> None:
    seen: Counter[str] = Counter()
    for ep in LISTED:
        seen.update(str(r["ISU_CD"])[-6:] for r in rows_for(ep, DAY))
    assert [c for c, n in seen.items() if n > 1] == []


def test_konex_row_is_its_own_stock() -> None:
    konex = rows_for("/sto/knx_bydd_trd", DAY)
    kosdaq = {r["ISU_CD"]: r for r in rows_for("/sto/ksq_bydd_trd", DAY)}
    assert len(konex) == 1
    row = konex[0]
    assert row["MKT_NM"] == "KONEX"
    assert row["ISU_CD"] not in kosdaq
    assert row["ISU_NM"] not in {r["ISU_NM"] for r in kosdaq.values()}
    assert row["BAS_DD"] == "20261006"
