"""로그인 진단 저장소 — 0020 `prv_diag.indicator`·`fear_greed`·`verdict`·`vol_index`(시장 진단·
국장 공포탐욕·종합 판정·변동성 지수 — 로그인 등급).

근거: docs/p7_design.md §1.1·§2·§3.3, metrics §D6~§D8, D-P7-14·15.

- 쓰기: `diag.daily`(16:00 — 지표·공포탐욕·판정, 그날 D 는 estimated·D−1 은 확정으로 덮음),
  `krx.vkospi`(vol_index — 꺼짐 #P7-1). 읽기: API `/api/diag/*`·`brief.closing`.
- 공개 작업은 이 모듈을 import 하지 않는다(공개 진단은 `kbj.store.repos.diag`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Final, Protocol

import psycopg

from kbj.core.p7_rows import DiagIndicatorRow, FearGreedRow, VerdictRow, VolIndexRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._diag import (
    DecJsonTable,
    MemoryIndicatorStore,
    PgIndicatorStore,
    check_write,
)
from kbj.store.repos._table import MemoryTable, select, upsert

__all__ = ["DiagRepo", "MemoryDiagRepo", "PgDiagRepo"]

SCHEMA: Final = "prv_diag"
FEAR_GREED: Final = DecJsonTable(
    f"{SCHEMA}.fear_greed",
    FearGreedRow,
    key=("trade_date",),
    extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"components", "reasons"}),
    dec_maps=frozenset({"components"}),
)
VERDICT: Final = DecJsonTable(
    f"{SCHEMA}.verdict",
    VerdictRow,
    key=("trade_date",),
    extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"parts", "event_flags", "reasons"}),
)
VOL_INDEX: Final = DecJsonTable(
    f"{SCHEMA}.vol_index",
    VolIndexRow,
    key=("trade_date", "index_id"),
    extra=("received_at", "loaded_by"),
)


class DiagRepo(Protocol):
    def upsert_indicators(
        self, rows: Sequence[DiagIndicatorRow], *, computed_at: datetime, loaded_by: str
    ) -> int: ...

    def indicators_on(self, day: date) -> list[DiagIndicatorRow]: ...

    def indicator_history(self, key: str, start: date, end: date) -> list[DiagIndicatorRow]: ...

    def last_indicator_date(self, upto: date | None = None) -> date | None: ...

    def upsert_fear_greed(
        self, row: FearGreedRow, *, computed_at: datetime, loaded_by: str
    ) -> int: ...

    def fear_greed_history(self, start: date, end: date) -> list[FearGreedRow]:
        """날짜 오름차순."""
        ...

    def upsert_verdict(self, row: VerdictRow, *, computed_at: datetime, loaded_by: str) -> int: ...

    def verdict_on(self, day: date) -> VerdictRow | None: ...

    def last_verdict_date(self, upto: date | None = None) -> date | None: ...

    def upsert_vol_index(
        self, rows: Sequence[VolIndexRow], *, received_at: datetime, loaded_by: str
    ) -> int: ...

    def vol_index_history(self, index_id: str, start: date, end: date) -> list[VolIndexRow]: ...


class PgDiagRepo(PgIndicatorStore):
    def __init__(self, conn_factory: ConnFactory) -> None:
        super().__init__(conn_factory, SCHEMA)

    def _run[T](self, fn: Callable[[psycopg.Connection[Any]], T]) -> T:
        with self._conn() as conn:
            return fn(conn)

    def upsert_fear_greed(self, row: FearGreedRow, *, computed_at: datetime, loaded_by: str) -> int:
        check_write((row,), computed_at, loaded_by)
        extra = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._run(lambda c: upsert(c, FEAR_GREED, (row,), extra))

    def fear_greed_history(self, start: date, end: date) -> list[FearGreedRow]:
        return self._run(
            lambda c: select(
                c, FEAR_GREED, "trade_date BETWEEN %s AND %s", (start, end), "trade_date"
            )
        )

    def upsert_verdict(self, row: VerdictRow, *, computed_at: datetime, loaded_by: str) -> int:
        check_write((row,), computed_at, loaded_by)
        extra = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._run(lambda c: upsert(c, VERDICT, (row,), extra))

    def verdict_on(self, day: date) -> VerdictRow | None:
        rows = self._run(lambda c: select(c, VERDICT, "trade_date = %s", (day,)))
        return rows[0] if rows else None

    def last_verdict_date(self, upto: date | None = None) -> date | None:
        where, params = ("trade_date <= %s", (upto,)) if upto is not None else ("TRUE", ())
        rows = self._run(lambda c: select(c, VERDICT, where, params, "trade_date DESC", limit=1))
        return rows[0].trade_date if rows else None

    def upsert_vol_index(
        self, rows: Sequence[VolIndexRow], *, received_at: datetime, loaded_by: str
    ) -> int:
        require_aware("received_at", received_at)
        require_loaded_by(loaded_by)
        extra = {"received_at": received_at, "loaded_by": loaded_by}
        return self._run(lambda c: upsert(c, VOL_INDEX, rows, extra))

    def vol_index_history(self, index_id: str, start: date, end: date) -> list[VolIndexRow]:
        return self._run(
            lambda c: select(
                c,
                VOL_INDEX,
                "index_id = %s AND trade_date BETWEEN %s AND %s",
                (index_id, start, end),
                "trade_date",
            )
        )


class MemoryDiagRepo(MemoryIndicatorStore):
    def __init__(self) -> None:
        super().__init__(SCHEMA)
        self.fear_greed = MemoryTable(FEAR_GREED)
        self.verdict = MemoryTable(VERDICT)
        self.vol_index = MemoryTable(VOL_INDEX)

    def upsert_fear_greed(self, row: FearGreedRow, *, computed_at: datetime, loaded_by: str) -> int:
        check_write((row,), computed_at, loaded_by)
        return self.fear_greed.upsert((row,), {"computed_at": computed_at, "loaded_by": loaded_by})

    def fear_greed_history(self, start: date, end: date) -> list[FearGreedRow]:
        return self.fear_greed.where(lambda r: start <= r.trade_date <= end)

    def upsert_verdict(self, row: VerdictRow, *, computed_at: datetime, loaded_by: str) -> int:
        check_write((row,), computed_at, loaded_by)
        return self.verdict.upsert((row,), {"computed_at": computed_at, "loaded_by": loaded_by})

    def verdict_on(self, day: date) -> VerdictRow | None:
        return self.verdict.get((day,))

    def last_verdict_date(self, upto: date | None = None) -> date | None:
        days = [r.trade_date for r in self.verdict.all() if upto is None or r.trade_date <= upto]
        return max(days, default=None)

    def upsert_vol_index(
        self, rows: Sequence[VolIndexRow], *, received_at: datetime, loaded_by: str
    ) -> int:
        require_aware("received_at", received_at)
        require_loaded_by(loaded_by)
        return self.vol_index.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})

    def vol_index_history(self, index_id: str, start: date, end: date) -> list[VolIndexRow]:
        return self.vol_index.where(
            lambda r: r.index_id == index_id and start <= r.trade_date <= end
        )
