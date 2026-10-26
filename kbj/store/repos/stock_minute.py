"""주식 5분봉 저장소(`prv_market.stock_bar_5m` — 0029, cis 30분봉 백테스트 입력).

`Repos` 묶음에 넣지 않는다 — 쓰는 곳이 수집 CLI(`kbj.services.collectors.stock_minutes`)와 백테스트
CLI 둘뿐이라 각자 연결 공장으로 만든다. 쓰기는 (code, ts) DO UPDATE(다시 받아도 행이 늘지 않는다).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from kbj.core.intraday_rows import IntraBar
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by, to_float, to_int

__all__ = ["MemoryStockMinuteRepo", "PgStockMinuteRepo"]

_UPSERT = (
    b"INSERT INTO prv_market.stock_bar_5m (code, ts, trade_date, open, high, low, close, volume, "
    b"minutes, received_at, loaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    b"ON CONFLICT (code, ts) DO UPDATE SET trade_date = EXCLUDED.trade_date, open = EXCLUDED.open, "
    b"high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume, "
    b"minutes = EXCLUDED.minutes, received_at = EXCLUDED.received_at, "
    b"loaded_by = EXCLUDED.loaded_by"
)


class PgStockMinuteRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgStockMinuteRepo()"

    def put(self, rows: Sequence[IntraBar], *, loaded_by: str, received_at: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (b.code, b.ts, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.minutes,
             received_at, loaded_by)
            for b in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT, params)
        return len(params)

    def days(self, start: date, end: date) -> set[tuple[str, date]]:
        """[start, end] 에 봉이 있는 (종목, 거래일)."""
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT DISTINCT code, trade_date FROM prv_market.stock_bar_5m "
                b"WHERE trade_date BETWEEN %s AND %s",
                (start, end),
            ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def bars(self, codes: Collection[str], start: date, end: date) -> dict[str, list[IntraBar]]:
        """종목 → [start, end] 5분봉(시각 순)."""
        if not codes:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, ts, trade_date, open, high, low, close, volume, minutes "
                b"FROM prv_market.stock_bar_5m WHERE code = ANY(%s) "
                b"AND trade_date BETWEEN %s AND %s ORDER BY code, ts",
                (sorted(codes), start, end),
            ).fetchall()
        out: dict[str, list[IntraBar]] = defaultdict(list)
        for r in rows:
            out[r[0]].append(
                IntraBar(code=r[0], ts=r[1], trade_date=r[2], open=to_float(r[3]) or 0.0,
                         high=to_float(r[4]) or 0.0, low=to_float(r[5]) or 0.0,
                         close=to_float(r[6]) or 0.0, volume=to_int(r[7]) or 0, minutes=r[8])
            )  # fmt: skip
        return dict(out)


@dataclass
class MemoryStockMinuteRepo:
    """시험용 — 같은 계약을 메모리로."""

    rows: dict[tuple[str, datetime], IntraBar] = field(
        default_factory=dict[tuple[str, datetime], IntraBar]
    )

    def put(self, rows: Sequence[IntraBar], *, loaded_by: str, received_at: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for b in rows:
            self.rows[(b.code, b.ts)] = b
        return len(rows)

    def days(self, start: date, end: date) -> set[tuple[str, date]]:
        return {(b.code, b.trade_date) for b in self.rows.values() if start <= b.trade_date <= end}

    def bars(self, codes: Collection[str], start: date, end: date) -> dict[str, list[IntraBar]]:
        out: dict[str, list[IntraBar]] = defaultdict(list)
        for b in sorted(self.rows.values(), key=lambda b: (b.code, b.ts)):
            if b.code in codes and start <= b.trade_date <= end:
                out[b.code].append(b)
        return dict(out)
