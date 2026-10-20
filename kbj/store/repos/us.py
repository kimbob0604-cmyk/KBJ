"""미국 보드 저장소(로그인) — 0015 `prv_market.us_universe` + 0003
`prv_market.daily_bar`(market='US').

근거: docs/p5_design.md §1.1·§3.3·§4.6, D-P5-18, metrics §23.

- 유니버스: 스크리너 한 번 = as_of 한 판(`put_universe` 가 그 날짜 행을 통째로 바꾼다 — 재실행
  멱등).
- 일봉: 새 표 없이 daily_bar 에 market='US', venue '' 로 쓴다. 한 종목·날짜에 출처가 여럿이면(Nasdaq
  실패 → Stooq 폴백, 지수는 Yahoo) 읽을 때 `US_SOURCE_RANK` 순으로 하나를 고른다(invalid 는 맨 뒤)
  [제안 — US 묶음이 확정].
- 미국 보드 산출(us_summary·us_brief)은 `kbj.store.repos.board`(prv_board.artifact market='US').
- 공개 작업·공개 내보내기는 이 모듈을 import 하지 않는다(로그인 — Nasdaq·Stooq·Yahoo).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Final, Protocol

from kbj.core.p5_rows import UsDailyRow, UsUniverseRow
from kbj.core.quality import Quality
from kbj.store.repos._common import (
    ConnFactory,
    code_filter,
    require_aware,
    require_loaded_by,
    to_float,
    to_int,
)
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = ["US_SOURCE_RANK", "MemoryUsRepo", "PgUsRepo", "UsRepo", "best_us_bars"]

US_MARKET: Final = "US"
# nasdaq-screener = 스크리너로 붙인 그날 종가(고저가 없음) — 같은 날 일봉이 있으면 일봉이 이긴다
US_SOURCE_RANK: Final[Mapping[str, int]] = MappingProxyType(
    {"nasdaq": 0, "yahoo": 1, "stooq": 2, "nasdaq-screener": 3}
)
UNIVERSE: Final = Table(
    "prv_market.us_universe", UsUniverseRow, key=("symbol", "as_of"),
    extra=("received_at", "loaded_by"),
)  # fmt: skip
_BAR_COLS: Final = (
    "code, trade_date, asset, open, high, low, close, volume, adjusted, source, quality"
)


class UsRepo(Protocol):
    def put_universe(
        self, as_of: date, rows: Sequence[UsUniverseRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """그 날짜 판을 통째로 바꾼다. 다른 날짜 행이 섞이면 ValueError."""
        ...

    def universe(self, as_of: date | None = None) -> list[UsUniverseRow]:
        """as_of 이하 가장 최근 판(없으면 가장 최근 판) — symbol 순."""
        ...

    def upsert_daily(
        self, rows: Sequence[UsDailyRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def daily(
        self, symbols: Collection[str] | None, end: date, n: int
    ) -> dict[str, list[UsDailyRow]]:
        """end 이하 미국 일봉이 있는 최근 n 날짜 안 — 종목마다 날짜 오름차순, 날짜마다 출처 하나."""
        ...

    def last_trade_date(self, upto: date) -> date | None: ...


def _rank(r: UsDailyRow) -> tuple[int, int]:
    return (
        1 if r.quality is Quality.INVALID else 0,
        US_SOURCE_RANK.get(r.source.lower(), len(US_SOURCE_RANK)),
    )


def best_us_bars(rows: Sequence[UsDailyRow]) -> dict[str, list[UsDailyRow]]:
    """(종목, 날짜)마다 출처 하나 — Pg·메모리가 같은 함수로 고른다."""
    best: dict[tuple[str, date], UsDailyRow] = {}
    for r in rows:
        k = (r.symbol, r.date)
        if k not in best or _rank(r) < _rank(best[k]):
            best[k] = r
    out: dict[str, list[UsDailyRow]] = {}
    for (sym, _), r in sorted(best.items()):
        out.setdefault(sym, []).append(r)
    return out


def _check_universe(
    as_of: date, rows: Sequence[UsUniverseRow], loaded_by: str, received_at: datetime
) -> None:
    require_loaded_by(loaded_by)
    require_aware("received_at", received_at)
    bad = [r.symbol for r in rows if r.as_of != as_of]
    if bad:
        raise ValueError(f"다른 날짜 판의 행이 섞였다: {bad[:3]}")
    syms = [r.symbol for r in rows]
    if len(set(syms)) != len(syms):
        raise ValueError("같은 종목이 두 번 있다")


def _bar(r: Sequence[Any]) -> UsDailyRow:
    return UsDailyRow(
        symbol=r[0], date=r[1], asset=r[2], open=to_float(r[3]), high=to_float(r[4]),
        low=to_float(r[5]), close=to_float(r[6]), volume=to_int(r[7]), adjusted=r[8],
        source=r[9], quality=Quality(r[10]),
    )  # fmt: skip


class PgUsRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgUsRepo()"

    def put_universe(
        self, as_of: date, rows: Sequence[UsUniverseRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check_universe(as_of, rows, loaded_by, received_at)
        with self._connect() as conn, conn.transaction():
            delete(conn, UNIVERSE, "as_of = %s", (as_of,))
            return upsert(
                conn, UNIVERSE, rows, {"received_at": received_at, "loaded_by": loaded_by}
            )

    def universe(self, as_of: date | None = None) -> list[UsUniverseRow]:
        with self._connect() as conn:
            r = conn.execute(
                b"SELECT max(as_of) FROM prv_market.us_universe "
                b"WHERE (%s::date IS NULL OR as_of <= %s)",
                (as_of, as_of),
            ).fetchone()
            day = None if r is None else r[0]
            if day is None:
                return []
            return select(conn, UNIVERSE, "as_of = %s", (day,), "symbol")

    def upsert_daily(
        self, rows: Sequence[UsDailyRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (US_MARKET, r.asset, r.symbol, r.date, r.source, "", r.open, r.high, r.low, r.close,
             r.volume, r.adjusted, r.quality.value, received_at, loaded_by)
            for r in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_market.daily_bar (market, asset, code, trade_date, source, "
                b"venue, open, high, low, close, volume, adjusted, quality, received_at, "
                b"loaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (market, asset, code, trade_date, source, venue) DO UPDATE SET "
                b"open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
                b"close = EXCLUDED.close, volume = EXCLUDED.volume, adjusted = EXCLUDED.adjusted, "
                b"quality = EXCLUDED.quality, received_at = EXCLUDED.received_at, "
                b"loaded_by = EXCLUDED.loaded_by",
                params,
            )
        return len(params)

    def daily(
        self, symbols: Collection[str] | None, end: date, n: int
    ) -> dict[str, list[UsDailyRow]]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        wanted = code_filter(symbols)
        if wanted == []:
            return {}
        with self._connect() as conn:
            days = [
                r[0]
                for r in conn.execute(
                    b"SELECT DISTINCT trade_date FROM prv_market.daily_bar WHERE market = 'US' "
                    b"AND trade_date <= %s ORDER BY trade_date DESC LIMIT %s",
                    (end, n),
                ).fetchall()
            ]
            if not days:
                return {}
            rows = conn.execute(
                f"SELECT {_BAR_COLS} FROM prv_market.daily_bar WHERE market = 'US' "  # noqa: S608
                "AND trade_date BETWEEN %s AND %s "
                "AND (%s::text[] IS NULL OR code = ANY(%s::text[]))".encode(),
                (min(days), end, wanted, wanted),
            ).fetchall()
        return best_us_bars([_bar(r) for r in rows])

    def last_trade_date(self, upto: date) -> date | None:
        with self._connect() as conn:
            r = conn.execute(
                b"SELECT max(trade_date) FROM prv_market.daily_bar WHERE market = 'US' "
                b"AND trade_date <= %s",
                (upto,),
            ).fetchone()
        return None if r is None else r[0]


class MemoryUsRepo:
    def __init__(self) -> None:
        self.universe_t = MemoryTable(UNIVERSE)
        # (asset, symbol, date, source) → 행 — daily_bar(market='US', venue '') 의 기본 키
        self.bars: dict[tuple[str, str, date, str], UsDailyRow] = {}

    def __repr__(self) -> str:
        return "MemoryUsRepo()"

    def put_universe(
        self, as_of: date, rows: Sequence[UsUniverseRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check_universe(as_of, rows, loaded_by, received_at)
        self.universe_t.remove(lambda r: r.as_of == as_of)
        return self.universe_t.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})

    def universe(self, as_of: date | None = None) -> list[UsUniverseRow]:
        days = [r.as_of for r in self.universe_t.all() if as_of is None or r.as_of <= as_of]
        if not days:
            return []
        day = max(days)
        return sorted(self.universe_t.where(lambda r: r.as_of == day), key=lambda r: r.symbol)

    def upsert_daily(
        self, rows: Sequence[UsDailyRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for r in rows:
            self.bars[(r.asset, r.symbol, r.date, r.source)] = r
        return len(rows)

    def daily(
        self, symbols: Collection[str] | None, end: date, n: int
    ) -> dict[str, list[UsDailyRow]]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        wanted = None if symbols is None else set(symbols)
        days = sorted({r.date for r in self.bars.values() if r.date <= end}, reverse=True)[:n]
        if not days or wanted == set():
            return {}
        start = min(days)
        got = [
            r
            for r in self.bars.values()
            if start <= r.date <= end and (wanted is None or r.symbol in wanted)
        ]
        return best_us_bars(got)

    def last_trade_date(self, upto: date) -> date | None:
        days = [r.date for r in self.bars.values() if r.date <= upto]
        return max(days) if days else None
