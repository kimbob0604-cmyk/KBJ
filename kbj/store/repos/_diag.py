"""진단 저장소 공용 — 공개(`pub_diag`)·로그인(`prv_diag`) 지표 표는 모양이 같다(0020, metrics §D0).

근거: docs/p7_design.md §1.1·§3.3. 지표 표 하나의 쓰기·읽기를 스키마 이름만 바꿔 두 번 쓴다
(`kbj.store.repos.diag` — 공개, `kbj.store.repos.diag_prv` — 로그인). 이 모듈은 SQL 조각과
`kbj.core.p7_rows` 만 안다(로그인 표 이름을 적지 않는다 — 스키마는 부르는 쪽이 준다).

- jsonb 칸(inputs·parts·components 등)의 `Decimal` 은 문자열로 저장하고(정밀도 유지 — float 로
  바꾸지 않는다), 공포탐욕 성분은 읽을 때 다시 `Decimal` 로 만든다.
- 같은 키는 덮는다(같은 날 재실행 멱등 — §3.3).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from kbj.core.p7_rows import DiagIndicatorRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, select, upsert

__all__ = [
    "DecJsonTable",
    "IndicatorStore",
    "MemoryIndicatorStore",
    "PgIndicatorStore",
    "check_write",
    "indicator_table",
]


def _plain_dec(v: object) -> object:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, Mapping):
        return {str(k): _plain_dec(x) for k, x in v.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, tuple | list):
        return [_plain_dec(x) for x in v]  # pyright: ignore[reportUnknownVariableType]
    return v


@dataclass(frozen=True)
class DecJsonTable[R](Table[R]):
    """jsonb 칸의 Decimal 을 문자열로 보내는 `Table`(`_table._plain` 은 Decimal 을 모른다).
    `dec_maps` 칸은 읽을 때 값(문자열·수)을 Decimal 로 되돌린다."""

    dec_maps: frozenset[str] = frozenset()

    def values(self, row: R, extra: Mapping[str, object]) -> tuple[object, ...]:
        plain = {
            f: _plain_dec(getattr(row, f)) for f in self.fields if self.col(f) in self.json_cols
        }
        base = super().values(row, extra)
        if not plain:
            return base
        out = list(base)
        for i, f in enumerate(self.fields):
            if f in plain:
                out[i] = Jsonb(plain[f])
        return tuple(out)

    def from_db(self, rec: Sequence[object]) -> R:
        if not self.dec_maps:
            return super().from_db(rec)
        fixed = list(rec)
        for i, f in enumerate(self.fields):
            if self.col(f) in self.dec_maps and isinstance(fixed[i], Mapping):
                m: Mapping[str, object] = fixed[i]  # type: ignore[assignment]
                fixed[i] = {k: None if v is None else Decimal(str(v)) for k, v in m.items()}
        return super().from_db(fixed)


def indicator_table(schema: str) -> DecJsonTable[DiagIndicatorRow]:
    return DecJsonTable(
        f"{schema}.indicator",
        DiagIndicatorRow,
        key=("trade_date", "key"),
        extra=("computed_at", "loaded_by"),
        json_cols=frozenset({"inputs", "flags", "reasons"}),
    )


def check_write(rows: Iterable[object], at: datetime, loaded_by: str) -> None:
    require_aware("computed_at", at)
    require_loaded_by(loaded_by)
    del rows


class IndicatorStore:
    """지표 표 하나(공개·로그인 공용 모양)의 쓰기·읽기 — 메모리·Pg 가 같은 뜻."""

    def upsert_indicators(
        self, rows: Sequence[DiagIndicatorRow], *, computed_at: datetime, loaded_by: str
    ) -> int:
        raise NotImplementedError

    def indicators_on(self, day: date) -> list[DiagIndicatorRow]:
        """그날 지표 전부(키 순)."""
        raise NotImplementedError

    def indicator_history(self, key: str, start: date, end: date) -> list[DiagIndicatorRow]:
        """한 지표의 [start, end] 날짜 오름차순."""
        raise NotImplementedError

    def last_indicator_date(self, upto: date | None = None) -> date | None:
        raise NotImplementedError


class PgIndicatorStore(IndicatorStore):
    def __init__(self, conn_factory: ConnFactory, schema: str) -> None:
        self._conn = conn_factory
        self._ind = indicator_table(schema)

    def _with[T](self, fn: Callable[[psycopg.Connection[Any]], T]) -> T:
        with self._conn() as conn:
            return fn(conn)

    def upsert_indicators(
        self, rows: Sequence[DiagIndicatorRow], *, computed_at: datetime, loaded_by: str
    ) -> int:
        check_write(rows, computed_at, loaded_by)
        extra = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._with(lambda c: upsert(c, self._ind, rows, extra))

    def indicators_on(self, day: date) -> list[DiagIndicatorRow]:
        return self._with(lambda c: select(c, self._ind, "trade_date = %s", (day,), "key"))

    def indicator_history(self, key: str, start: date, end: date) -> list[DiagIndicatorRow]:
        return self._with(
            lambda c: select(
                c,
                self._ind,
                "key = %s AND trade_date BETWEEN %s AND %s",
                (key, start, end),
                "trade_date",
            )
        )

    def last_indicator_date(self, upto: date | None = None) -> date | None:
        q = f"SELECT max(trade_date) FROM {self._ind.name}"  # noqa: S608 — 상수 표 이름
        params: tuple[object, ...] = ()
        if upto is not None:
            q += " WHERE trade_date <= %s"
            params = (upto,)
        row = self._with(lambda c: c.execute(q.encode(), params).fetchone())
        return None if row is None else row[0]


class MemoryIndicatorStore(IndicatorStore):
    def __init__(self, schema: str) -> None:
        self.indicator = MemoryTable(indicator_table(schema))

    def upsert_indicators(
        self, rows: Sequence[DiagIndicatorRow], *, computed_at: datetime, loaded_by: str
    ) -> int:
        check_write(rows, computed_at, loaded_by)
        return self.indicator.upsert(rows, {"computed_at": computed_at, "loaded_by": loaded_by})

    def indicators_on(self, day: date) -> list[DiagIndicatorRow]:
        return self.indicator.where(lambda r: r.trade_date == day)

    def indicator_history(self, key: str, start: date, end: date) -> list[DiagIndicatorRow]:
        return sorted(
            self.indicator.where(lambda r: r.key == key and start <= r.trade_date <= end),
            key=lambda r: r.trade_date,
        )

    def last_indicator_date(self, upto: date | None = None) -> date | None:
        days = [r.trade_date for r in self.indicator.all() if upto is None or r.trade_date <= upto]
        return max(days, default=None)
