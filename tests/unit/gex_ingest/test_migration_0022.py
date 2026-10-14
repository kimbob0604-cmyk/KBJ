"""마이그레이션 0022(P9 GXI — docs/p9_design.md §9.1): 0020 의 GX 수집 표 쓰기 주인 COMMENT 를 KBJ
주인으로 다시 적기만 한다(표·칸·권한은 그대로). 정적 검사 — DB 없이."""

from __future__ import annotations

import re
from pathlib import Path

from kbj.store.repos.gex import CHAIN_SNAPSHOTS, FUT_BOARD, SERIES_EXPIRIES
from kbj.store.repos.gex_ingest import INGEST_TABLES

MIGRATIONS = Path(__file__).resolve().parents[3] / "kbj" / "store" / "migrations"
FILE = "0022_gex_ingest.sql"


def _body() -> str:
    raw = (MIGRATIONS / FILE).read_text(encoding="utf-8")
    return "\n".join(line.split("--", 1)[0] for line in raw.splitlines())


def test_0022_follows_0021() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[names.index("0021_rules_journal.sql") + 1] == FILE


def test_0022_only_rewrites_comments() -> None:
    stmts = [s.strip() for s in _body().split(";") if s.strip()]
    assert stmts and all(s.startswith("COMMENT ON TABLE ") for s in stmts), stmts


def test_every_ingest_table_gets_a_kbj_owner_and_no_legacy_owner_is_left() -> None:
    body = _body()
    commented = dict(re.findall(r"COMMENT ON TABLE ([\w.]+) IS\s+((?:'[^']*'\s*)+)", body))
    want = {
        f"{t.schema}.{t.name}"
        for t in (*INGEST_TABLES, CHAIN_SNAPSHOTS, FUT_BOARD, SERIES_EXPIRIES)
    }
    assert want == set(commented)
    for table, text in commented.items():
        assert "legacy" not in text and "P9" in text, table
