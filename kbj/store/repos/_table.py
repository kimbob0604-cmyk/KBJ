"""P4 저장소 공용 — 행 자료형(`kbj.core.fin_rows`) ↔ 표 열 변환과 upsert·select 를 한 곳에서.

근거: docs/p4_design.md §1.1(M — 저장소)·§3.4. P4 표는 행 자료형의 필드 이름과 열 이름이 거의
같다(다른
것은 `rename` — 예 BuybackEvent.start → start_date, BandRow.min → min_value: SQL 예약어·흔한 함수
이름을
피했다). 그래서 표마다 SQL 을 손으로 다시 쓰지 않고 이 모듈의 `Table` 명세 하나로 쓰고 읽는다.

- 쓰기: `upsert(conn, table, rows, extra)` — `extra` 는 행 밖의
  열(received_at·computed_at·updated_at·
  loaded_by). 충돌하면 키가 아닌 열을 덮는다(`keep` 열은 처음 값을 지킨다 — 예
  disclosure.first_seen_at).
- 읽기: `select(conn, table, where, params, order)` — numeric 은 필드 형에 따라 int(원 — 소수면
  오류)·
  float 로, jsonb 목록은 튜플로, jsonb 객체는 dict 로 돌려 행 자료형을 만든다(검증은 행의
  __post_init__).
- 이 모듈은 `kbj.core`·psycopg 만 쓴다(계약 ⑦). SQL 의 표·열 이름은 모두 이 파일과 각 저장소의
  상수다
  (사용자 입력이 아니다 — S608 무시 사유).
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from functools import cached_property
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from kbj.store.repos._common import to_float, to_int

Kind = Literal["int", "float", "tuple", "map", "plain"]


def _kind(tp: Any) -> Kind:
    """필드 형 → 읽을 때 바꿀 모양. `int | None` 은 int, `float | None` 은 float."""
    origin = typing.get_origin(tp)
    args: tuple[Any, ...] = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        return _kind(inner[0]) if len(inner) == 1 else "plain"
    if tp is int:
        return "int"
    if tp is float:
        return "float"
    if origin is tuple:
        return "tuple"
    if origin in (Mapping, dict) or tp in (Mapping, dict):
        return "map"
    return "plain"


@dataclass(frozen=True)
class Table[R]:
    """표 하나의 명세. `columns` = 행 자료형의 필드(순서대로, rename 반영) + `extra`."""

    name: str  # '스키마.표'
    row_type: type[R]
    key: tuple[str, ...]  # 열 이름(= PRIMARY KEY)
    extra: tuple[str, ...]  # 행 밖에서 넣는 열(받은·계산한 시각, 쓴 작업)
    rename: Mapping[str, str] = field(default_factory=dict[str, str])  # 필드 → 열
    json_cols: frozenset[str] = frozenset()
    keep: frozenset[str] = frozenset()  # 충돌해도 덮지 않는 열(처음 값 유지)
    skip: frozenset[str] = frozenset()  # 표에 없는 필드(파생 속성 등)

    @cached_property
    def fields(self) -> tuple[str, ...]:
        return tuple(
            f.name
            for f in dataclasses.fields(self.row_type)  # pyright: ignore[reportArgumentType]
            if f.name not in self.skip
        )

    @cached_property
    def kinds(self) -> dict[str, Kind]:
        hints = typing.get_type_hints(self.row_type)
        return {f: _kind(hints[f]) for f in self.fields}

    def col(self, field_name: str) -> str:
        return self.rename.get(field_name, field_name)

    @cached_property
    def row_columns(self) -> tuple[str, ...]:
        return tuple(self.col(f) for f in self.fields)

    @cached_property
    def columns(self) -> tuple[str, ...]:
        return (*self.row_columns, *self.extra)

    def values(self, row: R, extra: Mapping[str, object]) -> tuple[object, ...]:
        out: list[object] = []
        for f in self.fields:
            out.append(_to_db(getattr(row, f), self.col(f) in self.json_cols))
        for c in self.extra:
            if c not in extra:
                raise ValueError(f"{self.name}: 열 {c} 의 값을 주지 않았다")
            out.append(extra[c])
        return tuple(out)

    def from_db(self, rec: Sequence[object]) -> R:
        """`select_sql()` 순서(행 필드 열만)의 한 줄 → 행 자료형."""
        kw: dict[str, object] = {}
        for f, v in zip(self.fields, rec, strict=False):
            kw[f] = _from_db(v, self.kinds[f])
        return self.row_type(**kw)

    def select_sql(self) -> sql.Composable:
        return sql.SQL(", ").join(sql.Identifier(c) for c in self.row_columns)

    def ident(self) -> sql.Composable:
        schema, table = self.name.split(".", 1)
        return sql.Identifier(schema, table)


def _to_db(v: object, as_json: bool) -> object:
    if isinstance(v, Enum):  # Quality·NaReason·Instrument … (StrEnum)
        return v.value
    if as_json:
        if isinstance(v, Mapping):
            return Jsonb(_plain(v))
        if isinstance(v, tuple | list):
            return Jsonb(_plain(v))
        if v is None:
            return None
        return Jsonb(v)
    if isinstance(v, tuple):  # text[] 등
        return list(v)  # pyright: ignore[reportUnknownArgumentType]
    return v


def _plain(v: object) -> object:
    """jsonb 로 보낼 값 — 열거는 값, 날짜는 ISO 문자열, 튜플은 목록(재귀)."""
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Mapping):
        return {str(k): _plain(x) for k, x in v.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, tuple | list):
        return [_plain(x) for x in v]  # pyright: ignore[reportUnknownVariableType]
    return v


def _from_db(v: object, kind: Kind) -> object:
    if v is None:
        return None
    if kind == "int":
        return to_int(v)
    if kind == "float":
        return to_float(v)
    if kind == "tuple":
        return tuple(v) if isinstance(v, list | tuple) else v  # pyright: ignore[reportUnknownArgumentType]
    if kind == "map":
        return dict(v) if isinstance(v, Mapping) else v  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(v, memoryview):
        return bytes(v)
    return v


def upsert[R](
    conn: psycopg.Connection[Any],
    table: Table[R],
    rows: Iterable[R],
    extra: Mapping[str, object],
    *,
    on_conflict: Literal["update", "nothing"] = "update",
    update_where: str | None = None,
) -> int:
    """행들을 넣는다. 충돌하면 키·`keep` 이 아닌 열을 덮는다(또는 아무것도 안 한다). 넣은(덮은) 행
    수(덮지 않은 충돌 행도 센다).

    `update_where` = 덮어쓰기 조건(이 패키지의 상수 SQL 조각 — 있는 행은 `cur`, 새 행은 `EXCLUDED`).
    거짓이면 그 행은 그대로 둔다(예: 주요계정 층 행이 전체 재무제표 층 행을 덮지 않게 — fin)."""
    params = [table.values(r, extra) for r in rows]
    if not params:
        return 0
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in table.columns)
    marks = sql.SQL(", ").join(sql.Placeholder() for _ in table.columns)
    key = sql.SQL(", ").join(sql.Identifier(c) for c in table.key)
    update = [c for c in table.columns if c not in table.key and c not in table.keep]
    if on_conflict == "nothing" or not update:
        tail = sql.SQL("DO NOTHING")
    else:
        tail = sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in update
        )
        if update_where is not None:
            tail = tail + sql.SQL(" WHERE ") + sql.SQL(update_where)  # pyright: ignore[reportArgumentType] — 상수 조각
    q = (
        sql.SQL("INSERT INTO {} AS cur ({}) VALUES ({}) ON CONFLICT ({}) ").format(
            table.ident(), cols, marks, key
        )
        + tail
    )
    with conn.cursor() as cur:
        cur.executemany(q, params)
    return len(params)


def select[R](
    conn: psycopg.Connection[Any],
    table: Table[R],
    where: str = "TRUE",
    params: Sequence[object] = (),
    order: str = "",
    limit: int | None = None,
) -> list[R]:
    """`where`·`order` 는 이 패키지의 상수 SQL 조각(값은 %s 자리표시자로만)."""
    q = sql.SQL("SELECT {} FROM {} WHERE ").format(table.select_sql(), table.ident()) + sql.SQL(
        where  # pyright: ignore[reportArgumentType] — 상수 조각
    )
    if order:
        q = q + sql.SQL(" ORDER BY ") + sql.SQL(order)  # pyright: ignore[reportArgumentType]
    if limit is not None:
        q = q + sql.SQL(" LIMIT {}").format(sql.Literal(int(limit)))
    rows = conn.execute(q, tuple(params)).fetchall()
    return [table.from_db(r) for r in rows]


def delete(
    conn: psycopg.Connection[Any], table: Table[Any], where: str, params: Sequence[object]
) -> int:
    q = sql.SQL("DELETE FROM {} WHERE ").format(table.ident()) + sql.SQL(where)  # pyright: ignore[reportArgumentType]
    cur = conn.execute(q, tuple(params))
    return cur.rowcount


def key_of[R](table: Table[R], row: R) -> tuple[object, ...]:
    """메모리 구현의 키 — 표의 PRIMARY KEY 열에 해당하는 필드 값."""
    back = {table.col(f): f for f in table.fields}
    return tuple(_to_db(getattr(row, back[c]), False) for c in table.key)


@dataclass
class Stored[R]:
    """메모리 구현의 한 줄 — 행 + 행 밖 열(받은 시각·쓴 작업)."""

    row: R
    extra: dict[str, object]


class MemoryTable[R]:
    """`Table` 하나의 메모리판 — Pg 와 같은 키·덮어쓰기(`keep` 열 유지) 규칙."""

    def __init__(self, table: Table[R]) -> None:
        self.table = table
        self.rows: dict[tuple[object, ...], Stored[R]] = {}

    def upsert(
        self,
        rows: Iterable[R],
        extra: Mapping[str, object],
        *,
        on_conflict: Literal["update", "nothing"] = "update",
        merge: Callable[[R, R], R] | None = None,
    ) -> int:
        n = 0
        missing = [c for c in self.table.extra if c not in extra]
        if missing:
            raise ValueError(f"{self.table.name}: 열 {missing} 의 값을 주지 않았다")
        for r in rows:
            k = key_of(self.table, r)
            old = self.rows.get(k)
            if old is not None and on_conflict == "nothing":
                n += 1
                continue
            new = r
            if old is not None:
                kept = {f: getattr(old.row, f) for f in self.table.fields
                        if self.table.col(f) in self.table.keep}  # fmt: skip
                if kept:
                    new = dataclasses.replace(new, **kept)  # pyright: ignore[reportArgumentType]
                if merge is not None:
                    new = merge(old.row, new)
            ext = dict(extra)
            if old is not None:
                for c in self.table.keep:
                    if c in ext:
                        ext[c] = old.extra.get(c, ext[c])
            self.rows[k] = Stored(new, ext)
            n += 1
        return n

    def all(self) -> list[R]:
        return [s.row for _, s in sorted(self.rows.items(), key=lambda kv: _sort_key(kv[0]))]

    def where(self, pred: Callable[[R], bool]) -> list[R]:
        return [r for r in self.all() if pred(r)]

    def remove(self, pred: Callable[[R], bool]) -> int:
        doomed = [k for k, s in self.rows.items() if pred(s.row)]
        for k in doomed:
            del self.rows[k]
        return len(doomed)

    def get(self, key: tuple[object, ...]) -> R | None:
        s = self.rows.get(key)
        return None if s is None else s.row


def _sort_key(k: tuple[object, ...]) -> tuple[tuple[int, Any], ...]:
    """키 열 값 그대로 비교(같은 열은 같은 형). None 은 맨 앞(Pg NULLS FIRST 와 같은 뜻은 아니다 —
    키 열에는 None 이 없다)."""
    return tuple((0, "") if v is None else (1, v) for v in k)
