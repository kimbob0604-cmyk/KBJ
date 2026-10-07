"""`ops.nightly` — 백업·보존 정리, 단계 격리(설계 §6.7, R23)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kbj.config.settings import Settings
from kbj.services.ops import nightly
from kbj.services.scheduler.handlers import JobContext

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)  # 03:00 KST 10-06


class Cur:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.rowcount = 0
        self.fail = fail

    def __enter__(self) -> Cur:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def execute(self, q: str, params: Any = None) -> None:
        if self.fail:
            import psycopg

            raise psycopg.OperationalError("connection refused password=hunter2")
        self.calls.append((q, params))
        self.rowcount = 7 if "tg_inbox" in q else 2


class Conn:
    def __init__(self, cur: Cur) -> None:
        self.cur = cur

    def __enter__(self) -> Conn:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def cursor(self) -> Cur:
        return self.cur


def ctx(**res: Any) -> JobContext:
    return JobContext(
        "ops.nightly", "2026-10-06", "ops.nightly:2026-10-06:1", 1, NOW, resources=res
    )


def test_backup_and_prune() -> None:
    cur = Cur()
    made: list[datetime] = []

    def backup(now: datetime) -> Path:
        made.append(now)
        return Path("/x/kbj-20261006.dump")

    result = nightly.run(ctx(connect=lambda: Conn(cur), backup=backup, inbox_days=14))
    assert result.status == "ok"
    assert result.detail == {
        "backup": "kbj-20261006.dump",
        "inbox_deleted": 7,
        "messages_deleted": 2,
    }
    (q1, p1), (q2, p2) = cur.calls
    assert "prv_alerts.tg_inbox" in q1 and p1 == (NOW - timedelta(days=14),)
    assert "prv_alerts.notify_message" in q2 and p2 == (NOW - timedelta(days=30),)
    assert made == [NOW]


def test_one_failing_step_does_not_stop_the_other_and_reason_is_masked() -> None:
    cur = Cur()

    def backup(now: datetime) -> Path:
        raise RuntimeError("pg_dump 실패: password=hunter2")

    result = nightly.run(ctx(connect=lambda: Conn(cur), backup=backup, inbox_days=14))
    assert result.status == "failed" and len(cur.calls) == 2  # 정리는 했다
    assert "hunter2" not in result.detail["reason"]
    bad = nightly.run(
        ctx(connect=lambda: Conn(Cur(fail=True)), backup=lambda n: Path("a"), inbox_days=14)
    )
    assert bad.status == "failed" and "hunter2" not in str(bad.detail)


def test_inbox_days_default_comes_from_notify_yaml() -> None:
    cur = Cur()
    nightly.run(ctx(connect=lambda: Conn(cur), backup=lambda n: Path("a")))
    assert cur.calls[0][1] == (NOW - timedelta(days=14),)


def test_prune_backups_keeps_seven_days_and_foreign_files(tmp_path: Path) -> None:
    for d in ("20260920", "20260929", "20260930", "20261005"):
        (tmp_path / f"kbj-{d}.dump").write_bytes(b"x")
    (tmp_path / "kbj-notadate.dump").write_bytes(b"x")
    (tmp_path / "other.dump").write_bytes(b"x")
    gone = nightly.prune_backups(tmp_path, NOW)
    assert sorted(p.name for p in gone) == ["kbj-20260920.dump", "kbj-20260929.dump"]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["kbj-20260930.dump", "kbj-20261005.dump", "kbj-notadate.dump", "other.dump"]


def test_pg_dump_uses_env_not_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class Proc:
        returncode = 0
        stderr = ""

    def fake_run(argv: list[str], **kw: Any) -> Proc:
        seen["argv"] = argv
        seen["env"] = kw["env"]
        Path(argv[-1].split("=", 1)[1]).write_bytes(b"dump")
        return Proc()

    monkeypatch.setattr(nightly.subprocess, "run", fake_run)
    s = Settings(database_url="postgresql://kbj:s3cretpw@db:5432/kbj", data_dir=tmp_path)  # type: ignore[arg-type]
    path = nightly.pg_dump_backup(s, NOW)
    assert path == tmp_path / "backup" / "kbj-20261006.dump" and path.read_bytes() == b"dump"
    assert "s3cretpw" not in " ".join(seen["argv"])
    assert seen["env"]["PGPASSWORD"] == "s3cretpw" and seen["env"]["PGHOST"] == "db"


def test_pg_dump_missing_or_failing_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = Settings(database_url="postgresql://kbj:pw@db/kbj", data_dir=tmp_path)  # type: ignore[arg-type]

    def missing(argv: list[str], **kw: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(nightly.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="pg_dump 가 없다"):
        nightly.pg_dump_backup(s, NOW)

    class Bad:
        returncode = 1
        stderr = 'pg_dump: error: connection to server failed: password=pw2 "kbj"\nmore'

    monkeypatch.setattr(nightly.subprocess, "run", lambda argv, **kw: Bad())
    with pytest.raises(RuntimeError) as e:
        nightly.pg_dump_backup(s, NOW)
    assert "pw2" not in str(e.value) and not list((tmp_path / "backup").glob("*.dump"))
