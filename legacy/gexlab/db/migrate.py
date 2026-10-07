"""DB 마이그레이션 적용기 (PLAN §4.5, docs/phase1_design.md §8).

- `db/migrations/NNN_이름.sql` 을 번호 순서로, 파일 하나를 트랜잭션 하나로 적용하고
  `schema_migrations` 에 (번호, 파일명, 체크섬, 적용 시각)을 같은 트랜잭션에서 남긴다
- 멱등: 이미 적용된 번호는 건너뛴다. 적용된 파일 내용이 바뀌었으면(체크섬 불일치) 아무것도 적용하지
  않고 멈춘다 — 바꿀 일은 새 번호 파일로 쓴다
- 두 프로세스가 동시에 돌려도 advisory lock 으로 하나씩 적용한다
- `python -m db.migrate` 는 `Settings.database_url` 을 쓴다. 접속 문자열·비밀번호는 출력하지 않는다
  (로그는 구조화 JSON 한 줄)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

log = logging.getLogger("db.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# pg_advisory_lock 키 — 'GEXLAB' 을 64비트 정수로 (다른 잠금과 겹치지 않을 임의 상수)
LOCK_KEY = 0x4745_584C_4142_0001
_FILE = re.compile(r"^(?P<version>\d{3,})_[a-z0-9_]+\.sql$")

BOOTSTRAP = sql.SQL(
    """CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)"""
)
_SELECT_APPLIED = sql.SQL("SELECT version, name, checksum FROM schema_migrations")
_RECORD = sql.SQL("INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)")


class MigrationError(RuntimeError):
    """마이그레이션 파일·기록이 맞지 않는다 (DB 오류는 psycopg.Error 로 그대로 올라간다)."""


@dataclass(frozen=True)
class Migration:
    version: str  # '001'
    name: str  # '001_init.sql'
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """마이그레이션 파일을 번호 순서로. 이름 규칙이 틀리거나 번호가 겹치면 MigrationError."""
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        m = _FILE.fullmatch(path.name)
        if m is None:
            raise MigrationError(f"마이그레이션 파일 이름은 NNN_소문자_이름.sql: {path.name}")
        n = int(m["version"])
        if n in found:
            raise MigrationError(f"번호가 겹친다: {found[n].name}, {path.name}")
        found[n] = Migration(m["version"], path.name, path.read_text(encoding="utf-8"))
    return [found[n] for n in sorted(found)]


def pending(
    migrations: Sequence[Migration], applied: dict[str, tuple[str, str]]
) -> list[Migration]:
    """적용할 것만 순서대로. applied = {번호: (파일명, 체크섬)}.

    적용된 파일의 체크섬이 다르거나, 기록엔 있는데 파일이 없으면 MigrationError — 아무것도
    적용하지 않는다.
    """
    by_version = {m.version: m for m in migrations}
    for version, (name, checksum) in sorted(applied.items()):
        m = by_version.get(version)
        if m is None:
            raise MigrationError(f"적용 기록({name})에 맞는 파일이 없다")
        if m.checksum != checksum:
            raise MigrationError(f"적용된 뒤 바뀐 파일: {m.name} — 새 번호 파일로 고친다")
    return [m for m in migrations if m.version not in applied]


def apply(conn: psycopg.Connection, migrations: Sequence[Migration] | None = None) -> list[str]:
    """아직 적용하지 않은 마이그레이션을 적용하고 적용한 파일명을 돌려준다.

    conn 은 autocommit 이어야 한다 — 파일마다 트랜잭션을 여기서 연다.
    """
    if not conn.autocommit:
        raise MigrationError("autocommit 연결을 넘긴다 (파일마다 트랜잭션을 연다)")
    todo_all = discover() if migrations is None else list(migrations)
    conn.execute(sql.SQL("SELECT pg_advisory_lock(%s)"), (LOCK_KEY,))
    try:
        conn.execute(BOOTSTRAP)
        rows = conn.execute(_SELECT_APPLIED).fetchall()
        applied = {str(v): (str(n), str(c)) for v, n, c in rows}
        done: list[str] = []
        for m in pending(todo_all, applied):
            with conn.transaction():
                # 파라미터 없이 보내면 여러 문장을 한 번에 실행한다(파일 그대로, % 치환 없음)
                conn.execute(m.sql.encode("utf-8"))
                conn.execute(_RECORD, (m.version, m.name, m.checksum))
            done.append(m.name)
            _log(logging.INFO, "migration_applied", name=m.name)
        return done
    finally:
        conn.execute(sql.SQL("SELECT pg_advisory_unlock(%s)"), (LOCK_KEY,))


def migrate(dsn: str, *, connect_timeout: int = 10) -> list[str]:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=connect_timeout) as conn:
        return apply(conn)


def _log(level: int, event: str, **fields: object) -> None:
    """구조화 JSON 로그 (CLAUDE.md). 마이그레이션은 거래일·세션에 속하지 않아 null."""
    rec = {"service": "db-migrate", "event": event, "trade_date": None, "session": None, **fields}
    log.log(level, json.dumps(rec, ensure_ascii=False, default=str))


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # 인자 없음 — 접속 정보는 Settings(.env·환경변수)에서만
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    from config.settings import Settings

    url = Settings().database_url
    if url is None:
        _log(logging.ERROR, "migration_failed", error="DATABASE_URL 이 없다")
        return 2
    try:
        done = migrate(url.get_secret_value())
    except MigrationError as e:
        _log(logging.ERROR, "migration_failed", error=str(e))
        return 1
    except psycopg.Error as e:
        # 오류 문구에 접속 문자열이 섞일 수 있어(연결 문자열 구문 오류 등) 종류와 SQLSTATE 만 남긴다
        _log(logging.ERROR, "migration_failed", error=type(e).__name__, sqlstate=e.sqlstate)
        return 1
    _log(logging.INFO, "migration_done", applied=done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
