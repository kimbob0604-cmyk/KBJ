"""ETF 저장소 — 일별 원장·장중 시세·메타·분할·운용사 펀드·구성종목·변동.

근거: docs/p3_design.md §1.1·§4.3, 마이그레이션 0009.


- `etf_days(end, n)` 는 end 이하 최근 n 거래일(표에 행이 있는 날) 창을 ETF 마다 오름차순으로,
  (ETF, 날짜) 마다 원장 우선순위(krx > kis …) 로 한 행. 순유입·가격효과·검산 ③ 은 엔진
  (`kbj.engines.etf.flows`)이 이 원장으로 계산한다(metrics §4 — 일별 원장 하나).
- 분할 이벤트: 수동(origin=manual)이 우선 — 감지(detected)는 manual 을 덮지 않는다(`put_split_event`
  가 False).
- 구성종목: `put_holdings(fund_id, asof, rows)` 는 그 (펀드, 기준일) 스냅을 통째로 바꾼다
  (재수집 멱등).
- 변동: `put_changes(run_date, rows)` 는 그 실행일 행을 통째로 바꾼다(ET `INSERT OR REPLACE` 보다
  엄격 — 다시 돌린 분석에서 사라진 변동이 남지 않게).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from kbj.core.quality import Quality
from kbj.core.rows import (
    Change,
    EtfDay,
    EtfMeta,
    EtfQuote,
    EtfType,
    Fund,
    HoldingRow,
    SplitEvent,
    pick_best,
    row_rank,
)
from kbj.store.repos._common import (
    ConnFactory,
    code_filter,
    require_aware,
    require_loaded_by,
    to_float,
    to_int,
)


class EtfRepo(Protocol):
    def upsert_etf_days(
        self, rows: Sequence[EtfDay], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def etf_days(
        self, end: date, n: int, codes: Collection[str] | None = None
    ) -> dict[str, list[EtfDay]]: ...

    def put_quotes(
        self, rows: Sequence[EtfQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def quotes(self, start: datetime, end: datetime) -> list[EtfQuote]:
        """[start, end) 장중 시세(ts·code 순)."""
        ...

    def upsert_meta(self, rows: Sequence[EtfMeta], *, loaded_by: str, now: datetime) -> int: ...

    def meta(self) -> dict[str, EtfMeta]: ...

    def put_split_event(self, ev: SplitEvent, *, loaded_by: str, now: datetime) -> bool:
        """썼으면 True. 같은 (코드, 날짜) 에 manual 이 있는데 detected 가 오면 쓰지 않고 False."""
        ...

    def split_events(self) -> dict[str, list[SplitEvent]]:
        """{code: 날짜 오름차순}."""
        ...

    def upsert_funds(self, rows: Sequence[Fund], *, loaded_by: str, now: datetime) -> int: ...

    def funds(self) -> dict[str, Fund]: ...

    def put_holdings(
        self,
        fund_id: str,
        asof: date,
        rows: Sequence[HoldingRow],
        *,
        loaded_by: str,
        received_at: datetime | None = None,
    ) -> int: ...

    def holdings(self, fund_id: str, asof: date) -> dict[str, HoldingRow]: ...

    def holding_dates(self) -> dict[str, list[date]]:
        """{fund_id: 스냅 기준일 오름차순} — `kbj.engines.etf.holdings.fund_pairs` 입력."""
        ...

    def put_changes(
        self, run_date: date, rows: Sequence[Change], *, engine_version: str, now: datetime
    ) -> int: ...

    def changes(self, run_date: date) -> list[Change]: ...

    def last_change_run(self, upto: date) -> date | None: ...


def best_etf_days(rows: Iterable[EtfDay]) -> dict[str, list[EtfDay]]:
    best = pick_best(
        rows, lambda d: (d.code, d.date), lambda d: row_rank(d.source, d.quality, d.venue)
    )
    out: dict[str, list[EtfDay]] = {}
    for (code, _), d in sorted(best.items(), key=lambda kv: kv[0]):
        out.setdefault(code, []).append(d)
    return out


def check_changes(run_date: date, rows: Sequence[Change], engine_version: str) -> None:
    if not engine_version.strip():
        raise ValueError("engine_version 이 비었다")
    for c in rows:
        if c.run_date != run_date:
            raise ValueError(f"run_date {run_date} 와 다른 변동 행: {c.run_date}")


def check_holdings(rows: Sequence[HoldingRow]) -> None:
    codes = [h.code for h in rows]
    if len(set(codes)) != len(codes):
        raise ValueError("같은 종목이 한 스냅에 두 번 있다")


# ── Postgres ───────────────────────────────────────────────────────────────────────────

_ETF_COLS = (
    "code, trade_date, name, close, nav, list_shrs, net_asset, turnover, volume, mktcap, "
    "base_index, source, venue, quality"
)
_META_COLS = (
    "code, name, issuer, brand, theme, etf_type, leverage, base_index, listed_on, delisted_on, "
    "source, quality"
)
_FUND_COLS = (
    "fund_id, issuer, fund_key, ticker, name, theme, is_active, depth, track, empty_streak, source"
)
_CHANGE_COLS = (
    "run_date, fund_id, code, kind, name, asof, prev_asof, gap_days, prev_qty, cur_qty, prev_wt, "
    "cur_wt, qty_pct, qty_pct_adj"
)


def _etf_day(r: tuple[Any, ...]) -> EtfDay:
    return EtfDay(
        code=r[0], date=r[1], name=r[2], close=to_float(r[3]), nav=to_float(r[4]),
        list_shrs=to_int(r[5]), net_asset=to_int(r[6]), turnover=to_int(r[7]),
        volume=to_int(r[8]), mktcap=to_int(r[9]), base_index=r[10], source=r[11], venue=r[12],
        quality=Quality(r[13]),
    )  # fmt: skip


def _change(r: tuple[Any, ...]) -> Change:
    return Change(
        run_date=r[0], fund_id=r[1], code=r[2], kind=r[3], name=r[4], asof=r[5], prev_asof=r[6],
        gap_days=r[7], prev_qty=to_float(r[8]), cur_qty=to_float(r[9]), prev_wt=to_float(r[10]),
        cur_wt=to_float(r[11]), qty_pct=to_float(r[12]), qty_pct_adj=to_float(r[13]),
    )  # fmt: skip


class PgEtfRepo:
    """`EtfRepo` 의 Postgres 구현."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgEtfRepo()"

    def upsert_etf_days(
        self, rows: Sequence[EtfDay], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_etf.etf_daily (code, trade_date, source, venue, name, close, "
                b"nav, "
                b"list_shrs, net_asset, turnover, volume, mktcap, base_index, quality, "
                b"received_at, "
                b"loaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                b"%s) "
                b"ON CONFLICT (code, trade_date, source, venue) DO UPDATE SET name = "
                b"EXCLUDED.name, "
                b"close = EXCLUDED.close, nav = EXCLUDED.nav, list_shrs = EXCLUDED.list_shrs, "
                b"net_asset = EXCLUDED.net_asset, turnover = EXCLUDED.turnover, "
                b"volume = EXCLUDED.volume, mktcap = EXCLUDED.mktcap, "
                b"base_index = EXCLUDED.base_index, quality = EXCLUDED.quality, "
                b"received_at = EXCLUDED.received_at, loaded_by = EXCLUDED.loaded_by",
                [
                    (d.code, d.date, d.source, d.venue, d.name, d.close, d.nav, d.list_shrs,
                     d.net_asset, d.turnover, d.volume, d.mktcap, d.base_index, d.quality.value,
                     received_at, loaded_by)
                    for d in rows
                ],
            )  # fmt: skip
        return len(rows)

    def etf_days(
        self, end: date, n: int, codes: Collection[str] | None = None
    ) -> dict[str, list[EtfDay]]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        wanted = code_filter(codes)
        if wanted == []:
            return {}
        with self._connect() as conn:
            days = [
                r[0]
                for r in conn.execute(
                    b"SELECT DISTINCT trade_date FROM prv_etf.etf_daily WHERE trade_date <= %s "
                    b"ORDER BY trade_date DESC LIMIT %s",
                    (end, n),
                )
            ]
            if not days:
                return {}
            rows = conn.execute(
                f"SELECT {_ETF_COLS} FROM prv_etf.etf_daily "  # noqa: S608 — 상수 열 목록
                "WHERE trade_date BETWEEN %s AND %s "
                "AND (%s::text[] IS NULL OR code = ANY(%s::text[])) "
                "ORDER BY code, trade_date".encode(),
                (days[-1], end, wanted, wanted),
            ).fetchall()
        return best_etf_days(_etf_day(r) for r in rows)

    def put_quotes(
        self, rows: Sequence[EtfQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_etf.quote_intraday (code, ts, source, price, inav, premium_pct, "
                b"turnover, volume, quality, received_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (code, ts, source) DO UPDATE SET price = EXCLUDED.price, "
                b"inav = EXCLUDED.inav, premium_pct = EXCLUDED.premium_pct, "
                b"turnover = EXCLUDED.turnover, volume = EXCLUDED.volume, "
                b"quality = EXCLUDED.quality, received_at = EXCLUDED.received_at, "
                b"loaded_by = EXCLUDED.loaded_by",
                [
                    (q.code, q.ts, q.source, q.price, q.inav, q.premium_pct, q.turnover, q.volume,
                     q.quality.value, received_at, loaded_by)
                    for q in rows
                ],
            )  # fmt: skip
        return len(rows)

    def quotes(self, start: datetime, end: datetime) -> list[EtfQuote]:
        require_aware("start", start)
        require_aware("end", end)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, ts, price, inav, premium_pct, turnover, volume, source, quality "
                b"FROM prv_etf.quote_intraday WHERE ts >= %s AND ts < %s ORDER BY ts, code, source",
                (start, end),
            ).fetchall()
        return [
            EtfQuote(
                code=r[0], ts=r[1], price=to_float(r[2]), inav=to_float(r[3]),
                premium_pct=to_float(r[4]), turnover=to_int(r[5]), volume=to_int(r[6]),
                source=r[7], quality=Quality(r[8]),
            )
            for r in rows
        ]  # fmt: skip

    def upsert_meta(self, rows: Sequence[EtfMeta], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_etf.meta (code, name, issuer, brand, theme, etf_type, leverage, "
                b"base_index, listed_on, delisted_on, source, quality, updated_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, issuer = EXCLUDED.issuer, "
                b"brand = EXCLUDED.brand, theme = EXCLUDED.theme, etf_type = EXCLUDED.etf_type, "
                b"leverage = EXCLUDED.leverage, base_index = EXCLUDED.base_index, "
                b"listed_on = EXCLUDED.listed_on, delisted_on = EXCLUDED.delisted_on, "
                b"source = EXCLUDED.source, quality = EXCLUDED.quality, "
                b"updated_at = EXCLUDED.updated_at, loaded_by = EXCLUDED.loaded_by",
                [
                    (m.code, m.name, m.issuer, m.brand, m.theme,
                     None if m.etf_type is None else m.etf_type.value, m.leverage, m.base_index,
                     m.listed_on, m.delisted_on, m.source, m.quality.value, now, loaded_by)
                    for m in rows
                ],
            )  # fmt: skip
        return len(rows)

    def meta(self) -> dict[str, EtfMeta]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_META_COLS} FROM prv_etf.meta ORDER BY code".encode()  # noqa: S608
            ).fetchall()
        return {
            r[0]: EtfMeta(
                code=r[0], name=r[1], issuer=r[2], brand=r[3], theme=r[4],
                etf_type=None if r[5] is None else EtfType(r[5]), leverage=to_float(r[6]),
                base_index=r[7], listed_on=r[8], delisted_on=r[9], source=r[10],
                quality=Quality(r[11]),
            )
            for r in rows
        }  # fmt: skip

    def put_split_event(self, ev: SplitEvent, *, loaded_by: str, now: datetime) -> bool:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        with self._connect() as conn:
            cur = conn.execute(
                b"INSERT INTO prv_etf.split_event (code, effective_date, ratio, origin, note, "
                b"source, quality, updated_at, loaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
                b"%s) "
                b"ON CONFLICT (code, effective_date) DO UPDATE SET ratio = EXCLUDED.ratio, "
                b"origin = EXCLUDED.origin, note = EXCLUDED.note, source = EXCLUDED.source, "
                b"quality = EXCLUDED.quality, updated_at = EXCLUDED.updated_at, "
                b"loaded_by = EXCLUDED.loaded_by "
                b"WHERE prv_etf.split_event.origin <> 'manual' OR EXCLUDED.origin = 'manual'",
                (ev.code, ev.effective_date, ev.ratio, ev.origin, ev.note, ev.source,
                 ev.quality.value, now, loaded_by),
            )  # fmt: skip
            return cur.rowcount > 0

    def split_events(self) -> dict[str, list[SplitEvent]]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, effective_date, ratio, origin, note, source, quality "
                b"FROM prv_etf.split_event ORDER BY code, effective_date"
            ).fetchall()
        out: dict[str, list[SplitEvent]] = {}
        for r in rows:
            out.setdefault(r[0], []).append(
                SplitEvent(
                    code=r[0], effective_date=r[1], ratio=float(r[2]), origin=r[3], note=r[4],
                    source=r[5], quality=Quality(r[6]),
                )
            )  # fmt: skip
        return out

    def upsert_funds(self, rows: Sequence[Fund], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_etf.fund (fund_id, issuer, fund_key, ticker, name, theme, "
                b"is_active, depth, track, empty_streak, source, updated_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (fund_id) DO UPDATE SET issuer = EXCLUDED.issuer, "
                b"fund_key = EXCLUDED.fund_key, ticker = EXCLUDED.ticker, name = EXCLUDED.name, "
                b"theme = EXCLUDED.theme, is_active = EXCLUDED.is_active, depth = EXCLUDED.depth, "
                b"track = EXCLUDED.track, empty_streak = EXCLUDED.empty_streak, "
                b"source = EXCLUDED.source, updated_at = EXCLUDED.updated_at, "
                b"loaded_by = EXCLUDED.loaded_by",
                [
                    (f.fund_id, f.issuer, f.fund_key, f.ticker, f.name, f.theme, f.is_active,
                     f.depth, f.track, f.empty_streak, f.source, now, loaded_by)
                    for f in rows
                ],
            )  # fmt: skip
        return len(rows)

    def funds(self) -> dict[str, Fund]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FUND_COLS} FROM prv_etf.fund ORDER BY fund_id".encode()  # noqa: S608
            ).fetchall()
        return {
            r[0]: Fund(
                fund_id=r[0], issuer=r[1], fund_key=r[2], ticker=r[3], name=r[4], theme=r[5],
                is_active=bool(r[6]), depth=r[7], track=bool(r[8]), empty_streak=r[9], source=r[10],
            )
            for r in rows
        }  # fmt: skip

    def put_holdings(
        self,
        fund_id: str,
        asof: date,
        rows: Sequence[HoldingRow],
        *,
        loaded_by: str,
        received_at: datetime | None = None,
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        check_holdings(rows)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                b"DELETE FROM prv_etf.holding WHERE fund_id = %s AND asof = %s", (fund_id, asof)
            )
            if rows:
                cur.executemany(
                    b"INSERT INTO prv_etf.holding (fund_id, asof, code, name, qty, wt, val, "
                    b"source, quality, received_at, loaded_by) "
                    b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (fund_id, asof, h.code, h.name, h.qty, h.wt, h.val, h.source,
                         h.quality.value, received_at, loaded_by)
                        for h in rows
                    ],
                )  # fmt: skip
        return len(rows)

    def holdings(self, fund_id: str, asof: date) -> dict[str, HoldingRow]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, name, qty, wt, val, source, quality FROM prv_etf.holding "
                b"WHERE fund_id = %s AND asof = %s ORDER BY code",
                (fund_id, asof),
            ).fetchall()
        return {
            r[0]: HoldingRow(
                code=r[0], name=r[1], qty=to_float(r[2]), wt=to_float(r[3]), val=to_float(r[4]),
                source=r[5], quality=Quality(r[6]),
            )
            for r in rows
        }  # fmt: skip

    def holding_dates(self) -> dict[str, list[date]]:
        out: dict[str, list[date]] = {}
        with self._connect() as conn:
            for r in conn.execute(
                b"SELECT DISTINCT fund_id, asof FROM prv_etf.holding ORDER BY fund_id, asof"
            ):
                out.setdefault(r[0], []).append(r[1])
        return out

    def put_changes(
        self, run_date: date, rows: Sequence[Change], *, engine_version: str, now: datetime
    ) -> int:
        require_aware("now", now)
        check_changes(run_date, rows, engine_version)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(b"DELETE FROM prv_etf.change_log WHERE run_date = %s", (run_date,))
            if rows:
                cur.executemany(
                    f"INSERT INTO prv_etf.change_log ({_CHANGE_COLS}, engine_version, computed_at) "  # noqa: S608
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s)".encode(),
                    [
                        (c.run_date, c.fund_id, c.code, c.kind, c.name, c.asof, c.prev_asof,
                         c.gap_days, c.prev_qty, c.cur_qty, c.prev_wt, c.cur_wt, c.qty_pct,
                         c.qty_pct_adj, engine_version, now)
                        for c in rows
                    ],
                )  # fmt: skip
        return len(rows)

    def changes(self, run_date: date) -> list[Change]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_CHANGE_COLS} FROM prv_etf.change_log WHERE run_date = %s "  # noqa: S608
                "ORDER BY fund_id, kind, code".encode(),
                (run_date,),
            ).fetchall()
        return [_change(r) for r in rows]

    def last_change_run(self, upto: date) -> date | None:
        with self._connect() as conn:
            r = conn.execute(
                b"SELECT max(run_date) FROM prv_etf.change_log WHERE run_date <= %s", (upto,)
            ).fetchone()
        return None if r is None else r[0]
