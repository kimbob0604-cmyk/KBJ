"""SD 개인 기록 이관 읽기·변환(`kbj.store.legacy_import.sd_p8` — 묶음 JNL). 합성 sqlite·json."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from kbj.store.legacy_import.mappings import LATER, NEVER, P8_SD
from kbj.store.legacy_import.sd_p8 import (
    SOURCE,
    read_alert_rules,
    read_sd_db,
    read_sd_portfolio,
)
from kbj.store.legacy_import.sources import SourceError
from tests.fixtures.synthetic.sqlite.sd_p8_gen import build_sd_p8

NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return build_sd_p8(tmp_path)


def test_read_db_rows_and_drops(paths: dict[str, Path]) -> None:
    got = read_sd_db(paths["db"], now=NOW)
    assert got.missing_tables == []
    assert [a.code for a in got.analyses] == ["990001", "990002", "990004"]
    a = got.analyses[0]
    assert a.mktcap == Decimal(1_234_000_000) and a.price == Decimal("70000.00")
    assert a.eps == {"bear": Decimal("5000.00"), "base": Decimal("6000.00"),
                     "bull": Decimal("7000.00")}  # fmt: skip
    assert a.tp["base"] == Decimal("80000.00") and a.per == {
        "bear": None,
        "base": None,
        "bull": None,
    }
    assert a.chain_ids == ("T1", None, None) and a.conviction == "중"
    assert a.source == SOURCE and a.updated_at == NOW  # SD 지역 시각은 쓰지 않는다
    assert got.analyses[1].analyzed_on == date(2026, 9, 2) and got.analyses[1].tags == ()
    assert [(t.side, t.qty) for t in got.trades] == [("buy", 10), ("buy", 5), ("sell", 3)]
    assert got.trades[2].tax == Decimal("405.00") and got.trades[2].memo == "일부 익절"
    assert len(got.picks) == 4
    by = {(p.date, p.code): p for p in got.picks}
    assert by[(date(2026, 9, 1), "990001")].tags == {"tags": ["모멘텀"]}
    assert by[(date(2026, 9, 1), "990002")].tags == {"why": "합성"}
    assert (
        by[(date(2026, 9, 2), "990003")].tags == {}
        and by[(date(2026, 9, 2), "990003")].rank is None
    )
    assert sorted((d.origin, d.reason) for d in got.drops) == [
        ("analysis_journal", "bad_code"),
        ("recommendation_history", "not_kr"),
        ("trade_journal", "bad_side"),
        ("trade_journal", "not_kr"),
    ]


def test_missing_tables_and_readonly(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    got = read_sd_db(db, now=NOW)
    assert got.missing_tables == ["analysis_journal", "trade_journal", "recommendation_history"]
    with pytest.raises(SourceError):
        read_sd_db(tmp_path / "none.db", now=NOW)


def test_rules_and_portfolio_files(paths: dict[str, Path], tmp_path: Path) -> None:
    items = read_alert_rules(paths["rules"])
    assert len(items) == 5 and items[0]["type"] == "rsi_oversold"
    rows, drops = read_sd_portfolio(paths["portfolio"], now=NOW)
    assert [(r.code, r.qty, r.side) for r in rows] == [("990001", 12, "buy"), ("990003", 7, "buy")]
    assert rows[1].price == Decimal("20000.50") and rows[1].trade_date == date(2026, 8, 15)
    assert [(d.where, d.reason) for d in drops] == [(2, "bad_number")]
    bad = tmp_path / "x.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(SourceError):
        read_alert_rules(bad)
    with pytest.raises(SourceError):
        read_sd_portfolio(bad, now=NOW)
    token = tmp_path / ".kis_token.json"
    token.write_text("{}", encoding="utf-8")
    with pytest.raises(SourceError, match="토큰"):
        read_alert_rules(token)


def test_mapping_lists() -> None:
    assert not [x for x in LATER if x[2] == "P8"]  # P8 은 COPY 매핑이 아니라 쓰기 모듈 경로
    assert {x[2] for x in P8_SD} == {"P8"} and len(P8_SD) == 4
    assert any("alert_history" in n for n in NEVER)
