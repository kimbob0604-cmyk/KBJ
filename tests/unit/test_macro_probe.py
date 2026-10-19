"""KOSIS 항목 코드 실측 도구(`kbj.services.macro_probe`) — 코드표 줄 만들기."""

from __future__ import annotations

from decimal import Decimal

from kbj.data.public.kosis.client import KosisRow
from kbj.services.macro_probe import table_lines


def _row(c1: str, itm: str, v: str) -> KosisRow:
    return KosisRow(
        source="KOSIS:DT_X", org_id="101", tbl_id="DT_X", tbl_nm="표", itm_id=itm,
        itm_nm="항목", unit="%", prd_se="M", prd_de="202609",
        classes=((c1, "계"),), value=Decimal(v), raw_value=v, last_changed="",
    )  # fmt: skip


def test_table_lines_show_codes_once_per_class_and_item() -> None:
    rows = [_row("T10", "T", "1.5"), _row("T10", "T", "1.5"), _row("T20", "T", "2.0")]
    lines = table_lines(rows)
    assert len(lines) == 2
    assert "C1=T10(계)" in lines[0] and "itmId=T(항목)" in lines[0] and "202609=1.5" in lines[0]
