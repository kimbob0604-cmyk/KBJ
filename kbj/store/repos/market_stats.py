"""금투협 종합통계 저장소(공개) — 0016
`pub_market_stats.credit_balance`·`market_capital`·`fund_nav`·`cma`.

근거: docs/p5_design.md §1.1·§3.3, D-P5-20.

- 쓰기: `market_stats.kofia`(13:30 — `kbj.services.collectors.kofia:run`, MAC). 읽기: API 상단 띠
  칩·
  공개 내보내기 `credit.json`(공개 내보내기는 이 저장소가 아니라 pub_ SQL 로 — 계약 ⑧).
- 표 이름 = 행의 `stat`. 같은 (날짜, 항목)은 덮어쓴다. 값이 None 인 새 행은 있는 값을 지우지 않는다.
- 이 모듈은 공개 표만 다룬다(공개 작업이 import — 계약 ⑭).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Final, Protocol

from psycopg import sql

from kbj.core.p5_rows import MARKET_STATS, MarketStatRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, upsert

__all__ = ["MarketStatsRepo", "MemoryMarketStatsRepo", "PgMarketStatsRepo"]

TABLES: Final[dict[str, Table[MarketStatRow]]] = {
    stat: Table(
        f"pub_market_stats.{stat}",
        MarketStatRow,
        key=("stat_date", "item"),
        extra=("received_at", "loaded_by"),
        rename={"date": "stat_date"},
        skip=frozenset({"stat"}),
    )
    for stat in MARKET_STATS
}


class MarketStatsRepo(Protocol):
    def upsert(
        self, rows: Sequence[MarketStatRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """행의 stat 표에 넣는다(값 None 인 새 행은 있는 값을 지우지 않는다). 쓴 행 수."""
        ...

    def series(
        self,
        stat: str,
        items: Collection[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[MarketStatRow]:
        """(날짜, 항목) 오름차순."""
        ...

    def last_date(self, stat: str, upto: date | None = None) -> date | None: ...


def _check(rows: Sequence[MarketStatRow], loaded_by: str, received_at: datetime) -> None:
    require_loaded_by(loaded_by)
    require_aware("received_at", received_at)
    keys = [(r.stat, r.date, r.item) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("같은 (표, 날짜, 항목)이 두 번 있다")


def _table(stat: str) -> Table[MarketStatRow]:
    t = TABLES.get(stat)
    if t is None:
        raise ValueError(f"모르는 금투협 통계 표: {stat!r} — {MARKET_STATS}")
    return t


class PgMarketStatsRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgMarketStatsRepo()"

    def upsert(
        self, rows: Sequence[MarketStatRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at)
        extra = {"received_at": received_at, "loaded_by": loaded_by}
        n = 0
        with self._connect() as conn, conn.transaction():
            for stat in MARKET_STATS:
                part = [r for r in rows if r.stat == stat]
                if part:
                    n += upsert(
                        conn, _table(stat), part, extra,
                        update_where="EXCLUDED.value IS NOT NULL OR cur.value IS NULL",
                    )  # fmt: skip
        return n

    def series(
        self,
        stat: str,
        items: Collection[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[MarketStatRow]:
        t = _table(stat)
        wanted = None if items is None else sorted(set(items))
        q = sql.SQL(
            "SELECT {} FROM {} WHERE (%s::text[] IS NULL OR item = ANY(%s::text[])) "
            "AND (%s::date IS NULL OR stat_date >= %s) AND (%s::date IS NULL OR stat_date <= %s) "
            "ORDER BY stat_date, item"
        ).format(t.select_sql(), t.ident())
        with self._connect() as conn:
            recs = conn.execute(q, (wanted, wanted, start, start, end, end)).fetchall()
        # 표에 stat 열이 없다 — 읽은 표 이름을 붙인다(행 검증은 MarketStatRow 가)
        return [
            MarketStatRow(stat=stat, **dict(zip(t.fields, rec, strict=True)))  # pyright: ignore[reportArgumentType] — stat 은 __post_init__ 이 검사
            for rec in recs
        ]

    def last_date(self, stat: str, upto: date | None = None) -> date | None:
        t = _table(stat)
        with self._connect() as conn:
            r = conn.execute(
                f"SELECT max(stat_date) FROM {t.name} "  # noqa: S608 — 상수 표 이름
                "WHERE (%s::date IS NULL OR stat_date <= %s)".encode(),
                (upto, upto),
            ).fetchone()
        return None if r is None else r[0]


class MemoryMarketStatsRepo:
    def __init__(self) -> None:
        self.tables = {stat: MemoryTable(t) for stat, t in TABLES.items()}

    def __repr__(self) -> str:
        return "MemoryMarketStatsRepo()"

    def upsert(
        self, rows: Sequence[MarketStatRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at)
        extra = {"received_at": received_at, "loaded_by": loaded_by}
        n = 0
        for r in rows:
            t = self.tables[_table(r.stat).name.split(".", 1)[1]]
            old = t.get((r.date, r.item))
            if old is not None and r.value is None and old.value is not None:
                n += 1  # Pg ON CONFLICT … WHERE 거짓도 센다(upsert 의 행 수 규칙)
                continue
            n += t.upsert([r], extra)
        return n

    def series(
        self,
        stat: str,
        items: Collection[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[MarketStatRow]:
        _table(stat)
        wanted = None if items is None else set(items)
        got = self.tables[stat].where(
            lambda r: (
                (wanted is None or r.item in wanted)
                and (start is None or r.date >= start)
                and (end is None or r.date <= end)
            )
        )
        return sorted(got, key=lambda r: (r.date, r.item))

    def last_date(self, stat: str, upto: date | None = None) -> date | None:
        _table(stat)
        days = [r.date for r in self.tables[stat].all() if upto is None or r.date <= upto]
        return max(days) if days else None
