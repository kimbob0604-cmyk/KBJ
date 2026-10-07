"""가짜 PostgreSQL 연결 공장 — `PostgresSink(connect=FakeDb())` (DB 없이 서비스·스풀 시험).

`down` 이면 접속·쓰기가 연결 오류(OperationalError), `reject` 의 표는 데이터 오류(CheckViolation).
`reject_if(표, 행)` 가 참인 행이 하나라도 있으면 그 묶음 전체가 데이터 오류(트랜잭션 하나).
들어간 행은 (표, 행 튜플) 로 `rows` 에 남는다. 트랜잭션은 흉내만 낸다(묶음 단위로 들어간다).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
import psycopg.errors
from psycopg import sql

DbRow = tuple[str, tuple[Any, ...]]


@dataclass
class FakeDb:
    """연결 공장 겸 서버. down 이면 접속이 실패하고, 들어간 묶음을 (표, 행) 으로 남긴다."""

    down: bool = False
    connects: int = 0
    rows: list[DbRow] = field(default_factory=list[DbRow])
    reject: set[str] = field(default_factory=set[str])  # 이 표는 데이터 오류로 거절
    reject_if: Callable[[str, tuple[Any, ...]], bool] | None = None  # 이 행이 든 묶음은 거절
    error: str = "connection refused"
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __call__(self) -> Any:
        self.connects += 1
        if self.down:
            raise psycopg.OperationalError(self.error)
        return _Conn(self)

    def tables(self) -> list[str]:
        return [t for t, _ in self.rows]

    def of(self, table: str) -> list[tuple[Any, ...]]:
        with self.lock:
            return [r for t, r in self.rows if t == table]


class _Cursor:
    def __init__(self, db: FakeDb, conn: _Conn) -> None:
        self.db, self.conn = db, conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, query: sql.Composed, rows: Sequence[tuple[Any, ...]]) -> None:
        if self.db.down:
            self.conn.broken = True
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        table = query.as_string().split('"')[1]
        if table in self.db.reject:
            raise psycopg.errors.CheckViolation("new row violates check constraint")
        bad = self.db.reject_if
        if bad is not None and any(bad(table, tuple(r)) for r in rows):
            raise psycopg.errors.CheckViolation("new row violates check constraint")
        with self.db.lock:
            self.db.rows.extend((table, r) for r in rows)


class _Conn:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.closed = False
        self.broken = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def cursor(self) -> _Cursor:
        return _Cursor(self.db, self)

    def close(self) -> None:
        self.closed = True
