"""P8 행 자료형(`kbj.core.rules_rows`) — 검증·불변·쓰기 주체 표."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rules_rows import (
    P8_TABLE_WRITERS,
    AnalysisRow,
    MatchRow,
    OutcomeRow,
    PickRow,
    QuoteSlot,
    RuleRow,
    RuleVersion,
    SignalRow,
    TradeRow,
    new_rule_id,
)
from kbj.core.time import KST

T = datetime(2026, 11, 2, 10, 0, tzinfo=KST)
SID = "si_01JBX0000000000000000000AB"
H64 = "a" * 64


def _signal(**kw: Any) -> SignalRow:
    base: dict[str, Any] = dict(
        signal_id=SID,
        mode="intraday",
        rule_id="r_abcdefghijkl",
        version=1,
        dsl_hash=H64,
        code="005930",
        eval_ts=T,
        trade_date=T.date(),
        entry_price=71200.0,
        evidence={"x": 1},
        input_fp="0123456789abcdef",
        status="sent",
        skip_reason=None,
        ticket="k",
        quality=Quality.ESTIMATED,
        source="KBJ:rules.intraday",
    )
    base.update(kw)
    return SignalRow(**base)


def test_rule_id_and_rule_rows() -> None:
    rid = new_rule_id(b"\x01" * 8)
    assert rid.startswith("r_") and len(rid) == 14
    assert new_rule_id(b"\x01" * 8) == rid
    with pytest.raises(ValueError):
        new_rule_id(b"x")
    RuleRow(rid, "이름", 1, True, None, "api", T, T, "test")
    with pytest.raises(ValueError):
        RuleRow(rid, "이름", 1, True, None, "web", T, T, "test")
    v = RuleVersion(rid, 1, {"v": 1}, H64, T)
    with pytest.raises(TypeError):
        v.dsl["v"] = 2  # type: ignore[index]


def test_signal_row_rules() -> None:
    s = _signal()
    assert s.as_of == T
    with pytest.raises(ValueError):
        _signal(signal_id="sc_01JBX0000000000000000000AB")  # 접두 ≠ mode
    with pytest.raises(ValueError):
        _signal(status="skipped")  # 사유 없음
    _signal(status="skipped", skip_reason="no_live_flow", entry_price=None)
    with pytest.raises(ValueError):
        _signal(eval_ts=T.replace(tzinfo=None))


def test_quote_slot_and_outcome_quality() -> None:
    QuoteSlot(T, "005930", 1.0, 2.0, 0.5, 1.5, 10, None, None, (), Quality.ESTIMATED, "KIS")
    with pytest.raises(ValueError):
        QuoteSlot(T, "005930", None, None, None, None, None, None, None, (), Quality.OK, "KIS")
    with pytest.raises(ValueError):
        OutcomeRow(
            SID,
            1,
            date(2026, 11, 3),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            Quality.ESTIMATED,
            T,
            "KBJ:rules.outcome",
        )
    OutcomeRow(
        SID,
        1,
        date(2026, 11, 3),
        None,
        None,
        None,
        None,
        None,
        None,
        "halted",
        Quality.INVALID,
        T,
        "KBJ:rules.outcome",
    )


def test_match_and_journal_rows() -> None:
    m = MatchRow(
        T.date(),
        "r_abcdefghijkl",
        1,
        2,
        3,
        ({"code": "005930", "reason": "logic"},),
        T,
        Quality.OK,
        "KBJ",
    )
    assert m.logic_n == 1
    with pytest.raises(ValueError):
        MatchRow(T.date(), "r_abcdefghijkl", 1, 2, 3, ({"reason": "?"},), T, Quality.OK, "KBJ")
    TradeRow(
        None,
        "005930",
        None,
        "buy",
        T.date(),
        Decimal("70000"),
        10,
        Decimal(0),
        Decimal(0),
        None,
        None,
        None,
        "api",
        T,
    )
    with pytest.raises(ValueError):
        TradeRow(
            None,
            "005930",
            None,
            "buy",
            T.date(),
            Decimal("70000"),
            0,
            Decimal(0),
            Decimal(0),
            None,
            None,
            None,
            "api",
            T,
        )
    AnalysisRow(
        None,
        "005930",
        T.date(),
        (None, None, None),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        {"base": None},
        {},
        {},
        None,
        None,
        None,
        ("반도체",),
        None,
        {},
        "api",
        T,
    )
    PickRow(T.date(), "sd", 1, "005930", None, None, None, None)


def test_table_writers_single() -> None:
    assert len(P8_TABLE_WRITERS) == 12
    assert P8_TABLE_WRITERS["prv_alerts.signal_intraday"] == "rules.intraday"
    assert P8_TABLE_WRITERS["prv_alerts.signal_close"] == "rules.close"
