"""공개 진단 저장소 — 0020 `pub_diag.indicator`·`gauge`·`event_marker`(공공지표 진단·매크로 위험
게이지·이벤트 표시 — 공개 등급).

근거: docs/p7_design.md §1.1·§2·§3.3, metrics §D1~§D5·§D9.

- 쓰기: `diag.public`(`kbj.services.engine.diag_pub:run` — 08:40). 읽기: API(`/api/diag/*` 공개 칸)·
  공개 내보내기 입력(공개 내보내기 자체는 pub_ SQL 로 — 계약 ⑧).
- **이 모듈은 공개 표만 다룬다**(공개 작업이 import — 로그인 `kbj.store.repos.diag_prv` 를 모른다).
- 이벤트는 기간 단위로 갈아 끼운다(`replace_events` — 같은 날 다시 돌려도 같은 결과, 사라진
  이벤트는 지운다). 게이지·지표는 키를 덮는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Final, Protocol

import psycopg

from kbj.core.p7_rows import DiagIndicatorRow, EventMarker, GaugeRow
from kbj.store.repos._common import ConnFactory
from kbj.store.repos._diag import (
    DecJsonTable,
    MemoryIndicatorStore,
    PgIndicatorStore,
    check_write,
)
from kbj.store.repos._table import MemoryTable, delete, select, upsert

__all__ = ["DiagPublicRepo", "MemoryDiagPublicRepo", "PgDiagPublicRepo"]

SCHEMA: Final = "pub_diag"
GAUGE: Final = DecJsonTable(
    f"{SCHEMA}.gauge",
    GaugeRow,
    key=("trade_date",),
    extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"parts", "reasons"}),
)
EVENTS: Final = DecJsonTable(
    f"{SCHEMA}.event_marker",
    EventMarker,
    key=("date", "kind", "title"),
    extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"detail"}),
)


class DiagPublicRepo(Protocol):
    def upsert_indicators(
        self, rows: Sequence[DiagIndicatorRow], *, computed_at: datetime, loaded_by: str
    ) -> int: ...

    def indicators_on(self, day: date) -> list[DiagIndicatorRow]: ...

    def indicator_history(self, key: str, start: date, end: date) -> list[DiagIndicatorRow]: ...

    def last_indicator_date(self, upto: date | None = None) -> date | None: ...

    def upsert_gauge(self, row: GaugeRow, *, computed_at: datetime, loaded_by: str) -> int: ...

    def gauge_on(self, day: date) -> GaugeRow | None: ...

    def gauge_history(self, start: date, end: date) -> list[GaugeRow]:
        """날짜 오름차순."""
        ...

    def replace_events(
        self,
        start: date,
        end: date,
        rows: Sequence[EventMarker],
        *,
        computed_at: datetime,
        loaded_by: str,
    ) -> int:
        """[start, end] 의 이벤트를 rows 로 갈아 끼운다(밖의 날짜 행은 거부). 넣은 행 수."""
        ...

    def events_between(self, start: date, end: date) -> list[EventMarker]:
        """(날짜, 종류, 제목) 오름차순."""
        ...


def _check_events(start: date, end: date, rows: Sequence[EventMarker]) -> None:
    if start > end:
        raise ValueError(f"기간이 거꾸로다: {start} > {end}")
    bad = [e for e in rows if not start <= e.date <= end]
    if bad:
        raise ValueError(f"기간 밖 이벤트: {bad[0].date} {bad[0].title}")


class PgDiagPublicRepo(PgIndicatorStore):
    def __init__(self, conn_factory: ConnFactory) -> None:
        super().__init__(conn_factory, SCHEMA)

    def _tx[T](self, fn: Callable[[psycopg.Connection[Any]], T]) -> T:
        with self._conn() as conn, conn.transaction():
            return fn(conn)

    def upsert_gauge(self, row: GaugeRow, *, computed_at: datetime, loaded_by: str) -> int:
        check_write((row,), computed_at, loaded_by)
        extra = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._tx(lambda c: upsert(c, GAUGE, (row,), extra))

    def gauge_on(self, day: date) -> GaugeRow | None:
        rows = self._tx(lambda c: select(c, GAUGE, "trade_date = %s", (day,)))
        return rows[0] if rows else None

    def gauge_history(self, start: date, end: date) -> list[GaugeRow]:
        return self._tx(
            lambda c: select(c, GAUGE, "trade_date BETWEEN %s AND %s", (start, end), "trade_date")
        )

    def replace_events(
        self,
        start: date,
        end: date,
        rows: Sequence[EventMarker],
        *,
        computed_at: datetime,
        loaded_by: str,
    ) -> int:
        _check_events(start, end, rows)
        check_write(rows, computed_at, loaded_by)
        extra = {"computed_at": computed_at, "loaded_by": loaded_by}

        def go(c: psycopg.Connection[Any]) -> int:
            delete(c, EVENTS, "date BETWEEN %s AND %s", (start, end))
            return upsert(c, EVENTS, rows, extra)

        return self._tx(go)

    def events_between(self, start: date, end: date) -> list[EventMarker]:
        return self._tx(
            lambda c: select(c, EVENTS, "date BETWEEN %s AND %s", (start, end), "date, kind, title")
        )


class MemoryDiagPublicRepo(MemoryIndicatorStore):
    def __init__(self) -> None:
        super().__init__(SCHEMA)
        self.gauge = MemoryTable(GAUGE)
        self.events = MemoryTable(EVENTS)

    def upsert_gauge(self, row: GaugeRow, *, computed_at: datetime, loaded_by: str) -> int:
        check_write((row,), computed_at, loaded_by)
        return self.gauge.upsert((row,), {"computed_at": computed_at, "loaded_by": loaded_by})

    def gauge_on(self, day: date) -> GaugeRow | None:
        return self.gauge.get((day,))

    def gauge_history(self, start: date, end: date) -> list[GaugeRow]:
        return self.gauge.where(lambda r: start <= r.trade_date <= end)

    def replace_events(
        self,
        start: date,
        end: date,
        rows: Sequence[EventMarker],
        *,
        computed_at: datetime,
        loaded_by: str,
    ) -> int:
        _check_events(start, end, rows)
        check_write(rows, computed_at, loaded_by)
        self.events.remove(lambda e: start <= e.date <= end)
        return self.events.upsert(rows, {"computed_at": computed_at, "loaded_by": loaded_by})

    def events_between(self, start: date, end: date) -> list[EventMarker]:
        return self.events.where(lambda e: start <= e.date <= end)
