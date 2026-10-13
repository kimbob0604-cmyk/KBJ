"""프로그램매매 저장소(로그인) — 0017 `prv_flows.program_*` 다섯 표(F2 — metrics §22).

근거: docs/p5_design.md §1.1·§3.3·§4.7, docs/p5_flow_program_prompt.md §1.4·§3.3.

- 쓰기 주인: `program.intraday`(시장 장중)·`program.daily`·`program.backfill`(시장 일별·투자자별)·
  `program.stock_scan`(종목 장중)·`program.stock_sweep`(종목 일별) — 처리기는 F2 묶음.
- 금액은 원 정수(행 자료형이 검사). 같은 키 재실행은 덮어쓴다(같은 분 멱등).
- 종목 일별 훑기는 끊기면 이어서 한다 — `swept_codes` 로 이미 받은 종목을 본다(F2 §3.4).
- 장중 투자자 1분(`prv_gex.investor_flow`)은 이 모듈이 아니라 `kbj.store.repos.flows`.
- 공개 작업·공개 내보내기는 이 모듈을 import 하지 않는다(KIS — 로그인).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Final, Protocol

from kbj.core.p5_rows import ProgramInvestorRow, ProgramMarketRow, ProgramStockRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, select, upsert

__all__ = ["MemoryProgramRepo", "PgProgramRepo", "ProgramRepo"]

_EXTRA: Final = ("received_at", "loaded_by")
MARKET_DAILY: Final = Table(
    "prv_flows.program_market_daily", ProgramMarketRow,
    key=("trade_date", "market", "venue", "kind", "side"), extra=_EXTRA, skip=frozenset({"ts"}),
)  # fmt: skip
MARKET_INTRADAY: Final = Table(
    "prv_flows.program_market_intraday", ProgramMarketRow,
    key=("ts", "market", "venue", "kind", "side"), extra=_EXTRA,
)  # fmt: skip
INVESTOR_DAILY: Final = Table(
    "prv_flows.program_investor_daily", ProgramInvestorRow,
    key=("trade_date", "market", "venue", "investor", "kind"), extra=_EXTRA,
)  # fmt: skip
STOCK_INTRADAY: Final = Table(
    "prv_flows.program_stock_intraday", ProgramStockRow, key=("ts", "code", "venue"), extra=_EXTRA,
)  # fmt: skip
STOCK_DAILY: Final = Table(
    "prv_flows.program_stock_daily", ProgramStockRow, key=("trade_date", "code", "venue"),
    extra=_EXTRA, skip=frozenset({"ts"}),
)  # fmt: skip


class ProgramRepo(Protocol):
    def put_market_days(
        self, rows: Sequence[ProgramMarketRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """시장 일별(ts 없음). 쓴 행 수."""
        ...

    def market_days(self, market: str, venue: str, end: date, n: int) -> list[ProgramMarketRow]:
        """end 이하 행이 있는 최근 n 거래일 — (날짜, kind, side) 오름차순."""
        ...

    def put_market_minutes(
        self, rows: Sequence[ProgramMarketRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """시장 장중(ts = 슬롯 분)."""
        ...

    def market_minutes(self, trade_date: date, market: str, venue: str) -> list[ProgramMarketRow]:
        """(ts, kind, side) 오름차순."""
        ...

    def put_investor_days(
        self, rows: Sequence[ProgramInvestorRow], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def investor_days(
        self, trade_date: date, market: str, venue: str
    ) -> list[ProgramInvestorRow]: ...

    def put_stock_scan(
        self, rows: Sequence[ProgramStockRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """종목 장중(ts 있음·scope scan)."""
        ...

    def stock_scan(
        self, trade_date: date, market: str, venue: str, ts: datetime | None = None
    ) -> list[ProgramStockRow]:
        """그 슬롯(없으면 그날 마지막 슬롯)의 행 — code 순."""
        ...

    def put_stock_days(
        self, rows: Sequence[ProgramStockRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """종목 일별(ts 없음·scope sweep)."""
        ...

    def stock_days(self, trade_date: date, market: str, venue: str) -> list[ProgramStockRow]:
        """code 순."""
        ...

    def swept_codes(self, trade_date: date, venue: str) -> set[str]:
        """그날 일별 훑기로 이미 받은 종목(이어 받기 — F2 §3.4)."""
        ...


def _check(rows: Sequence[object], loaded_by: str, received_at: datetime, *, ts: bool) -> None:
    require_loaded_by(loaded_by)
    require_aware("received_at", received_at)
    for r in rows:
        has_ts = getattr(r, "ts", None) is not None
        if has_ts != ts:
            raise ValueError("장중 행은 ts 가 있고 일별 행은 ts 가 없다")


def _check_scope(rows: Sequence[ProgramStockRow], scope: str) -> None:
    bad = [r.code for r in rows if r.scope != scope]
    if bad:
        raise ValueError(f"scope 가 {scope} 가 아닌 행: {bad[:3]}")


class PgProgramRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgProgramRepo()"

    def _put[R](self, table: Table[R], rows: Sequence[R], loaded_by: str, at: datetime) -> int:
        with self._connect() as conn:
            return upsert(conn, table, rows, {"received_at": at, "loaded_by": loaded_by})

    def put_market_days(
        self, rows: Sequence[ProgramMarketRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=False)
        return self._put(MARKET_DAILY, rows, loaded_by, received_at)

    def market_days(self, market: str, venue: str, end: date, n: int) -> list[ProgramMarketRow]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        with self._connect() as conn:
            days = [
                r[0]
                for r in conn.execute(
                    b"SELECT DISTINCT trade_date FROM prv_flows.program_market_daily "
                    b"WHERE market = %s AND venue = %s AND trade_date <= %s "
                    b"ORDER BY trade_date DESC LIMIT %s",
                    (market, venue, end, n),
                ).fetchall()
            ]
            if not days:
                return []
            return select(
                conn, MARKET_DAILY,
                "market = %s AND venue = %s AND trade_date BETWEEN %s AND %s",
                (market, venue, min(days), end), "trade_date, kind, side",
            )  # fmt: skip

    def put_market_minutes(
        self, rows: Sequence[ProgramMarketRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=True)
        return self._put(MARKET_INTRADAY, rows, loaded_by, received_at)

    def market_minutes(self, trade_date: date, market: str, venue: str) -> list[ProgramMarketRow]:
        with self._connect() as conn:
            return select(
                conn, MARKET_INTRADAY, "trade_date = %s AND market = %s AND venue = %s",
                (trade_date, market, venue), "ts, kind, side",
            )  # fmt: skip

    def put_investor_days(
        self, rows: Sequence[ProgramInvestorRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=False)
        return self._put(INVESTOR_DAILY, rows, loaded_by, received_at)

    def investor_days(self, trade_date: date, market: str, venue: str) -> list[ProgramInvestorRow]:
        with self._connect() as conn:
            return select(
                conn, INVESTOR_DAILY, "trade_date = %s AND market = %s AND venue = %s",
                (trade_date, market, venue), "investor, kind",
            )  # fmt: skip

    def put_stock_scan(
        self, rows: Sequence[ProgramStockRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=True)
        _check_scope(rows, "scan")
        return self._put(STOCK_INTRADAY, rows, loaded_by, received_at)

    def stock_scan(
        self, trade_date: date, market: str, venue: str, ts: datetime | None = None
    ) -> list[ProgramStockRow]:
        require_aware("ts", ts)
        with self._connect() as conn:
            if ts is None:
                r = conn.execute(
                    b"SELECT max(ts) FROM prv_flows.program_stock_intraday "
                    b"WHERE trade_date = %s AND market = %s AND venue = %s",
                    (trade_date, market, venue),
                ).fetchone()
                ts = None if r is None else r[0]
                if ts is None:
                    return []
            return select(
                conn, STOCK_INTRADAY, "ts = %s AND market = %s AND venue = %s",
                (ts, market, venue), "code",
            )  # fmt: skip

    def put_stock_days(
        self, rows: Sequence[ProgramStockRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=False)
        _check_scope(rows, "sweep")
        return self._put(STOCK_DAILY, rows, loaded_by, received_at)

    def stock_days(self, trade_date: date, market: str, venue: str) -> list[ProgramStockRow]:
        with self._connect() as conn:
            return select(
                conn, STOCK_DAILY, "trade_date = %s AND market = %s AND venue = %s",
                (trade_date, market, venue), "code",
            )  # fmt: skip

    def swept_codes(self, trade_date: date, venue: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code FROM prv_flows.program_stock_daily "
                b"WHERE trade_date = %s AND venue = %s",
                (trade_date, venue),
            ).fetchall()
        return {r[0] for r in rows}


class MemoryProgramRepo:
    def __init__(self) -> None:
        self.market_daily_t = MemoryTable(MARKET_DAILY)
        self.market_intraday_t = MemoryTable(MARKET_INTRADAY)
        self.investor_t = MemoryTable(INVESTOR_DAILY)
        self.stock_intraday_t = MemoryTable(STOCK_INTRADAY)
        self.stock_daily_t = MemoryTable(STOCK_DAILY)

    def __repr__(self) -> str:
        return "MemoryProgramRepo()"

    def put_market_days(
        self, rows: Sequence[ProgramMarketRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=False)
        return self.market_daily_t.upsert(
            rows, {"received_at": received_at, "loaded_by": loaded_by}
        )

    def market_days(self, market: str, venue: str, end: date, n: int) -> list[ProgramMarketRow]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        mine = self.market_daily_t.where(
            lambda r: r.market == market and r.venue == venue and r.trade_date <= end
        )
        days = sorted({r.trade_date for r in mine}, reverse=True)[:n]
        if not days:
            return []
        start = min(days)
        return sorted(
            (r for r in mine if r.trade_date >= start),
            key=lambda r: (r.trade_date, r.kind, r.side),
        )

    def put_market_minutes(
        self, rows: Sequence[ProgramMarketRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=True)
        return self.market_intraday_t.upsert(
            rows, {"received_at": received_at, "loaded_by": loaded_by}
        )

    def market_minutes(self, trade_date: date, market: str, venue: str) -> list[ProgramMarketRow]:
        got = self.market_intraday_t.where(
            lambda r: r.trade_date == trade_date and r.market == market and r.venue == venue
        )
        return sorted(got, key=lambda r: (r.ts.timestamp() if r.ts else 0.0, r.kind, r.side))

    def put_investor_days(
        self, rows: Sequence[ProgramInvestorRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=False)
        return self.investor_t.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})

    def investor_days(self, trade_date: date, market: str, venue: str) -> list[ProgramInvestorRow]:
        got = self.investor_t.where(
            lambda r: r.trade_date == trade_date and r.market == market and r.venue == venue
        )
        return sorted(got, key=lambda r: (r.investor, r.kind))

    def put_stock_scan(
        self, rows: Sequence[ProgramStockRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=True)
        _check_scope(rows, "scan")
        return self.stock_intraday_t.upsert(
            rows, {"received_at": received_at, "loaded_by": loaded_by}
        )

    def stock_scan(
        self, trade_date: date, market: str, venue: str, ts: datetime | None = None
    ) -> list[ProgramStockRow]:
        require_aware("ts", ts)
        mine = self.stock_intraday_t.where(
            lambda r: r.trade_date == trade_date and r.market == market and r.venue == venue
        )
        stamps = [r.ts for r in mine if r.ts is not None]
        if ts is None:
            if not stamps:
                return []
            ts = max(stamps)
        return sorted((r for r in mine if r.ts == ts), key=lambda r: r.code)

    def put_stock_days(
        self, rows: Sequence[ProgramStockRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at, ts=False)
        _check_scope(rows, "sweep")
        return self.stock_daily_t.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})

    def stock_days(self, trade_date: date, market: str, venue: str) -> list[ProgramStockRow]:
        got = self.stock_daily_t.where(
            lambda r: r.trade_date == trade_date and r.market == market and r.venue == venue
        )
        return sorted(got, key=lambda r: r.code)

    def swept_codes(self, trade_date: date, venue: str) -> set[str]:
        return {
            r.code
            for r in self.stock_daily_t.where(
                lambda r: r.trade_date == trade_date and r.venue == venue
            )
        }
