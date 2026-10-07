"""마이그레이션 적용기 단위 시험(kbj/store/migrate.py — DB 없이).

- 파일 찾기: 이름 규칙(NNNN_소문자.sql)·번호 순서·번호 겹침
- 적용할 것 고르기: 적용된 번호는 건너뛰고, 적용 뒤 바뀐 파일(체크섬)·파일 없는 기록은 아무것도
  적용하지 않고 멈춘다
- 기록 표는 `ops.schema_migrations`(0001 과 같은 열), 잠금 키는 GX 와 다른 KBJ 상수
- autocommit 이 아닌 연결은 거부, `main` 은 접속 정보가 없으면 2(문구에 비밀 없음)
실제 DB 적용(두 번·동시·hypertable)은 tests/integration/test_migrations_pg.py.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from kbj.store import migrate as mig
from kbj.store.migrate import (
    BOOTSTRAP,
    LOCK_KEY,
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    apply,
    discover,
    pending,
)

GX_LOCK_KEY = 0x4745_584C_4142_0001  # legacy/gexlab/db/migrate.py — 같은 서버에서 겹치면 안 된다
P2_FILES = [
    "0001_schemas.sql",
    "0002_ops_core.sql",
    "0003_market_flows.sql",
    "0004_filings_corp.sql",
    "0005_alerts_inbox.sql",
    "0006_gex.sql",
]


def _write(d: Path, name: str, body: str = "SELECT 1;") -> None:
    (d / name).write_text(body, encoding="utf-8")


def test_repo_migrations_are_found_in_order() -> None:
    found = discover()
    assert [m.name for m in found][: len(P2_FILES)] == P2_FILES
    assert [m.version for m in found][: len(P2_FILES)] == [f[:4] for f in P2_FILES]
    assert all(len(m.checksum) == 64 for m in found)


def test_discover_orders_by_number_not_by_text(tmp_path: Path) -> None:
    _write(tmp_path, "0010_b.sql")
    _write(tmp_path, "0002_a.sql")
    _write(tmp_path, "0009_c.sql")
    assert [m.version for m in discover(tmp_path)] == ["0002", "0009", "0010"]


@pytest.mark.parametrize("bad", ["2_x.sql", "0002-ops.sql", "0002_Ops.sql", "abcd_x.sql"])
def test_bad_file_names_are_refused(tmp_path: Path, bad: str) -> None:
    _write(tmp_path, bad)
    with pytest.raises(MigrationError, match="이름"):
        discover(tmp_path)


def test_duplicate_numbers_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "0002_a.sql")
    _write(tmp_path, "002_b.sql")  # 같은 번호(2)
    with pytest.raises(MigrationError, match="겹친다"):
        discover(tmp_path)


def _m(version: str, body: str = "SELECT 1;") -> Migration:
    return Migration(version, f"{version}_x.sql", body)


def test_pending_skips_applied_and_keeps_order() -> None:
    ms = [_m("0001"), _m("0002"), _m("0003")]
    applied = {"0001": ("0001_x.sql", ms[0].checksum)}
    assert [m.version for m in pending(ms, applied)] == ["0002", "0003"]
    assert pending(ms, {m.version: (m.name, m.checksum) for m in ms}) == []


def test_a_changed_file_after_apply_stops_everything() -> None:
    ms = [_m("0001", "SELECT 2;"), _m("0002")]
    applied = {"0001": ("0001_x.sql", _m("0001", "SELECT 1;").checksum)}
    with pytest.raises(MigrationError, match="바뀐 파일"):
        pending(ms, applied)


def test_a_record_without_its_file_stops_everything() -> None:
    with pytest.raises(MigrationError, match="파일이 없다"):
        pending([_m("0001")], {"0099": ("0099_gone.sql", "0" * 64)})


def test_checksum_is_sha256_of_the_file_text() -> None:
    import hashlib

    m = _m("0001", "CREATE SCHEMA IF NOT EXISTS ops;\n")
    assert m.checksum == hashlib.sha256(m.sql.encode("utf-8")).hexdigest()


def test_bootstrap_creates_the_ops_record_table_with_0001_columns() -> None:
    text = BOOTSTRAP.as_string(None)
    assert "CREATE SCHEMA IF NOT EXISTS ops" in text
    assert "CREATE TABLE IF NOT EXISTS ops.schema_migrations" in text
    first = (MIGRATIONS_DIR / "0001_schemas.sql").read_text(encoding="utf-8")
    cols = re.findall(r"^\s+(\w+)\s+(text|timestamptz)", text, re.M)
    body = first.split("CREATE TABLE IF NOT EXISTS ops.schema_migrations", 1)[1].split(");", 1)[0]
    assert cols == re.findall(r"^\s+(\w+)\s+(text|timestamptz)", body, re.M)
    assert [c for c, _ in cols] == ["version", "name", "checksum", "applied_at"]


def test_lock_key_is_kbj_own_and_fits_bigint() -> None:
    assert LOCK_KEY != GX_LOCK_KEY
    assert 0 < LOCK_KEY < 2**63


class _FakeConn:
    def __init__(self, autocommit: bool) -> None:
        self.autocommit = autocommit
        self.executed: list[Any] = []

    def execute(self, *a: Any) -> Any:  # 불리면 안 된다
        self.executed.append(a)
        raise AssertionError("autocommit 이 아니면 아무것도 보내지 않는다")


def test_apply_refuses_a_non_autocommit_connection() -> None:
    conn = _FakeConn(autocommit=False)
    with pytest.raises(MigrationError, match="autocommit"):
        apply(conn, [])  # type: ignore[arg-type]
    assert conn.executed == []


def test_main_without_database_url_exits_2_without_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # 레포의 .env 를 읽지 않게
    monkeypatch.delenv("KBJ_DATABASE_URL", raising=False)
    with caplog.at_level(logging.ERROR, logger="kbj.store.migrate"):
        assert mig.main([]) == 2
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["event"] == "migration_failed" and rec["service"] == "db-migrate"


def test_main_reports_only_the_error_kind_on_connection_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    pw = "s3cr@t-migrate-pw"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KBJ_DATABASE_URL", f"postgresql://kbj:{pw}@127.0.0.1:1/kbj")
    with caplog.at_level(logging.INFO, logger="kbj.store.migrate"):
        assert mig.main([]) == 1
    text = "".join(r.getMessage() for r in caplog.records)
    assert pw not in text and "127.0.0.1" not in text
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["error"] == "OperationalError"
