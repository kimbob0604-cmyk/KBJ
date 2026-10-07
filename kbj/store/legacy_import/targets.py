"""이관 대상 — Postgres(`PgTarget`)와 메모리(`MemoryTarget`, 단위 시험용)가 같은 순서로 동작한다.

매핑 하나 = 트랜잭션 하나(docs/p2_design.md §8.6):
1. `stage` — 넣을 행을 임시 표(`_li_stage`, 커밋·되돌림 때 사라진다)에 `COPY`. `nothing` 매핑이면
   다른 원본(`loaded_by` 가 다른 행)이 이미 가진 키를 빼고 그 키들을 돌려준다(`other_origin`).
2. `merge` — `INSERT … SELECT … ON CONFLICT (키) DO UPDATE … WHERE (열…) IS DISTINCT FROM (…)`
   (`update`) 또는 `DO NOTHING`(`nothing`). 바뀐·들어간 행 수를 돌려준다 — 같은 원본을 두 번
   넣으면 0.
3. `observe` — 임시 표의 키로 대상 행을 세고(원본 표시 열이 있으면 이 매핑 것만) 키 다이제스트·합.
4. `record` — `ops.legacy_import` 한 줄(값·키 없음).
검증이 어긋나면 실행기가 예외로 트랜잭션을 되돌린다(원본은 처음부터 읽기 전용).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from kbj.store.legacy_import.mappings import TableMapping, Target
from kbj.store.legacy_import.transforms import TargetRow
from kbj.store.legacy_import.verify import Observed, column_sums, digest_of, key_text

STAGE = "_li_stage"


@dataclass(frozen=True)
class ImportRecord:
    """`ops.legacy_import` 한 줄(0002)."""

    batch_id: str
    mapping: str
    source_name: str
    source_sha256: str
    source_table: str
    target_table: str
    phase: str
    rows_read: int
    rows_dropped: int
    drops: dict[str, int]
    rows_written: int
    key_digest_src: str | None
    key_digest_dst: str | None
    sums: dict[str, dict[str, str]]
    status: str  # ok · mismatch · failed
    started_at: datetime
    finished_at: datetime


class TargetStore(Protocol):
    def transaction(self) -> Any: ...  # 컨텍스트 관리자 — 예외면 되돌린다
    def stage(self, m: TableMapping, rows: Sequence[TargetRow]) -> set[str]: ...
    def merge(self, m: TableMapping) -> int: ...
    def observe(self, m: TableMapping) -> Observed: ...
    def record(self, rec: ImportRecord) -> None: ...


# ── 메모리 대상 ──────────────────────────────────────────────────────────────────────────


@dataclass
class MemoryTarget:
    """표 = {키 문자열: 행}. 트랜잭션은 들어갈 때 복사해 두고 예외면 되돌린다."""

    tables: dict[str, dict[str, TargetRow]] = field(default_factory=dict[str, dict[str, TargetRow]])
    records: list[ImportRecord] = field(default_factory=list[ImportRecord])
    _stage: list[TargetRow] = field(default_factory=list[TargetRow])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        saved = (copy.deepcopy(self.tables), list(self.records))
        try:
            yield
        except BaseException:
            self.tables, self.records = saved
            raise
        finally:
            self._stage = []

    def table(self, name: str) -> dict[str, TargetRow]:
        return self.tables.setdefault(name, {})

    def stage(self, m: TableMapping, rows: Sequence[TargetRow]) -> set[str]:
        t, origin = self.table(m.target.table), m.target.origin
        other: set[str] = set()
        kept: list[TargetRow] = []
        for r in rows:
            k = key_text(r, m.key_cols)
            have = t.get(k)
            if (
                m.on_conflict == "nothing"
                and origin is not None
                and have is not None
                and have.get(origin) != r.get(origin)
            ):
                other.add(k)
                continue
            kept.append(dict(r))
        self._stage = kept
        return other

    def merge(self, m: TableMapping) -> int:
        t = self.table(m.target.table)
        n = 0
        for r in self._stage:
            k = key_text(r, m.key_cols)
            have = t.get(k)
            if have is None:
                t[k] = dict(r)
                n += 1
            elif m.on_conflict == "update" and any(
                have.get(c) != r.get(c) for c in m.target.columns
            ):
                t[k] = dict(r)
                n += 1
        return n

    def observe(self, m: TableMapping) -> Observed:
        t, origin = self.table(m.target.table), m.target.origin
        got: list[TargetRow] = []
        for r in self._stage:
            have = t.get(key_text(r, m.key_cols))
            if have is None or (origin is not None and have.get(origin) != r.get(origin)):
                continue
            got.append(have)
        return Observed(
            count=len(got),
            digest=digest_of(key_text(r, m.key_cols) for r in got),
            sums=column_sums(got, m.sum_cols),
        )

    def record(self, rec: ImportRecord) -> None:
        self.records.append(rec)


# ── Postgres 대상 ────────────────────────────────────────────────────────────────────────


def _ident(name: str) -> sql.Composable:
    schema, _, table = name.partition(".")
    return sql.Identifier(schema, table) if table else sql.Identifier(schema)


def _cols(cols: Sequence[str], alias: str | None = None) -> sql.Composable:
    if alias is None:
        return sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    return sql.SQL(", ").join(sql.Identifier(alias, c) for c in cols)


def key_expr(target: Target, alias: str) -> sql.Composable:
    """파이썬 `verify.key_text` 와 같은 정규화: 날짜는 YYYY-MM-DD, 나머지는 ::text, '|' 로
    잇는다."""
    parts: list[sql.Composable] = []
    for c in target.key:
        if c in target.date_keys:
            parts.append(sql.SQL("to_char({}, 'YYYY-MM-DD')").format(sql.Identifier(alias, c)))
        else:
            parts.append(sql.SQL("{}::text").format(sql.Identifier(alias, c)))
    return sql.SQL("concat_ws('|', {})").format(sql.SQL(", ").join(parts))


def _key_match(target: Target, a: str, b: str) -> sql.Composable:
    return sql.SQL("({}) = ({})").format(_cols(target.key, a), _cols(target.key, b))


class PgTarget:
    """autocommit 연결 하나로 매핑마다 트랜잭션을 연다
    (`kbj.store.db.connect(..., autocommit=True)`)."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("autocommit 연결을 넘긴다(매핑마다 트랜잭션을 여기서 연다)")
        self.conn = conn

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgTarget()"

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn.transaction():
            yield

    def stage(self, m: TableMapping, rows: Sequence[TargetRow]) -> set[str]:
        t = m.target
        self.conn.execute(
            sql.SQL("CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT {} FROM {} WITH NO DATA").format(
                sql.Identifier(STAGE), _cols(t.columns), _ident(t.table)
            )
        )
        oids = [
            int(r[0])
            for r in self.conn.execute(
                sql.SQL(
                    "SELECT atttypid FROM pg_attribute WHERE attrelid = {}::regclass "
                    "AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
                ).format(sql.Literal(STAGE))
            ).fetchall()
        ]
        with (
            self.conn.cursor() as cur,
            cur.copy(
                sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(STAGE), _cols(t.columns))
            ) as cp,
        ):
            cp.set_types(oids)
            for r in rows:
                cp.write_row([Jsonb(r[c]) if c in t.jsonb else r[c] for c in t.columns])
        if m.on_conflict != "nothing" or t.origin is None:
            return set()
        removed = self.conn.execute(
            sql.SQL(
                "DELETE FROM {stage} s USING {table} t WHERE {match} "
                "AND t.{origin} IS DISTINCT FROM s.{origin} RETURNING {key}"
            ).format(
                stage=sql.Identifier(STAGE),
                table=_ident(t.table),
                match=_key_match(t, "t", "s"),
                origin=sql.Identifier(t.origin),
                key=key_expr(t, "s"),
            )
        ).fetchall()
        return {str(r[0]) for r in removed}

    def merge(self, m: TableMapping) -> int:
        t = m.target
        rest = [c for c in t.columns if c not in t.key]
        if m.on_conflict == "nothing" or not rest:
            action: sql.Composable = sql.SQL("DO NOTHING")
        else:
            action = sql.SQL("DO UPDATE SET {sets} WHERE ({old}) IS DISTINCT FROM ({new})").format(
                sets=sql.SQL(", ").join(
                    sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                    for c in rest
                ),
                old=_cols(rest, "t"),
                new=_cols(rest, "excluded"),
            )
        cur = self.conn.execute(
            sql.SQL(
                "INSERT INTO {table} AS t ({cols}) SELECT {cols} FROM {stage} "
                "ON CONFLICT ({key}) {action}"
            ).format(
                table=_ident(t.table),
                cols=_cols(t.columns),
                stage=sql.Identifier(STAGE),
                key=_cols(t.key),
                action=action,
            )
        )
        return max(cur.rowcount, 0)

    def observe(self, m: TableMapping) -> Observed:
        t = m.target
        cond: sql.Composable = _key_match(t, "t", "s")
        if t.origin is not None:
            cond = sql.SQL("{} AND t.{} = s.{}").format(
                cond, sql.Identifier(t.origin), sql.Identifier(t.origin)
            )
        sums = [sql.SQL("sum(x.{})::text").format(sql.Identifier(c)) for c in t.sums]
        picked = [sql.SQL("t.{}").format(sql.Identifier(c)) for c in t.sums]
        query = sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(x.k, ',' ORDER BY x.k COLLATE \"C\"), ''))"
            "{sums} FROM (SELECT {key} AS k{picked} FROM {table} t JOIN {stage} s ON {cond}) x"
        ).format(
            sums=sql.SQL("").join(sql.SQL(", ") + s for s in sums),
            key=key_expr(t, "t"),
            picked=sql.SQL("").join(sql.SQL(", ") + p for p in picked),
            table=_ident(t.table),
            stage=sql.Identifier(STAGE),
            cond=cond,
        )
        row = self.conn.execute(query).fetchone()
        if row is None:  # 집계는 늘 한 줄
            raise RuntimeError("집계 결과가 없다")
        return Observed(
            count=int(row[0]),
            digest=str(row[1]),
            sums={
                c: Decimal(v) if v is not None else Decimal(0)
                for c, v in zip(t.sums, row[2:], strict=True)
            },
        )

    def record(self, rec: ImportRecord) -> None:
        self.conn.execute(
            sql.SQL(
                "INSERT INTO ops.legacy_import (batch_id, mapping, source_name, source_sha256, "
                "source_table, target_table, phase, rows_read, rows_dropped, drops, rows_written, "
                "key_digest_src, key_digest_dst, sums, status, started_at, finished_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (batch_id, mapping) DO NOTHING"
            ),
            (
                rec.batch_id,
                rec.mapping,
                rec.source_name,
                rec.source_sha256,
                rec.source_table,
                rec.target_table,
                rec.phase,
                rec.rows_read,
                rec.rows_dropped,
                Jsonb(rec.drops),
                rec.rows_written,
                rec.key_digest_src,
                rec.key_digest_dst,
                Jsonb(rec.sums),
                rec.status,
                rec.started_at,
                rec.finished_at,
            ),
        )
