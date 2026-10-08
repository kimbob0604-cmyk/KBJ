"""신고가 보드 저장소 — 역사적 최고가 스칼라·라벨·분할 대조·종목 하루·산출 JSON
(docs/p3_design.md §1.1·§4.1, 0007).

ET `board/engine/db.py`(`split_cleared`:113·`split_unknown`:123·`put_split_check`:133 과 alltime·
label 읽기)를 Postgres 로 옮겼다. 엔진 산출(`kbj.engines.board.build.BoardDay`)은 저장소가 모른다
(계약 ⑦) — 서비스가 `kbj.core.rows.BoardDayRecord` 로 옮겨 `put_day` 에 넘긴다.

- `put_day` 는 그날(market, trade_date)의 label·stock_day·artifact 를 **지우고 다시 쓴다**(한
  트랜잭션 — 같은 as_of 재실행 멱등, board.confirm 이 KIS 잠정 보드를 KRX 확정으로 바꿀 때 사라진
  라벨도 지워진다).
- `split_cleared` 는 verdict 가 'none' 인 것만(모르는 것과 아닌 것은 다르다 — ET 그대로).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from kbj.core.quality import Quality
from kbj.core.rows import AllTime, BoardArtifact, BoardDayRecord, Label, SplitCheck, StockDay
from kbj.store.repos._common import (
    MARKET,
    ConnFactory,
    code_filter,
    require_aware,
    require_loaded_by,
    to_float,
    to_int,
)


class BoardRepo(Protocol):
    def alltime(self, codes: Collection[str] | None = None) -> dict[str, AllTime]:
        """역사적 최고가 스칼라 {code: 행}."""
        ...

    def put_alltime(self, rows: Sequence[AllTime], *, loaded_by: str, now: datetime) -> int:
        """종목마다 통째로 덮어쓴다(ET `INSERT OR REPLACE`)."""
        ...

    def labels(self, asof: date, basis: str) -> dict[str, Label]:
        """그날 그 기준의 라벨 {code: 라벨}."""
        ...

    def put_day(self, rec: BoardDayRecord, *, loaded_by: str, now: datetime) -> int:
        """그날 보드 행을 통째로 바꾼다. 쓴 행 수(라벨 + 종목 + 산출)."""
        ...

    def stock_days(self, asof: date) -> dict[str, StockDay]: ...

    def artifact(self, asof: date, name: str) -> BoardArtifact | None: ...

    def last_day(self, upto: date) -> date | None:
        """upto 이하 보드가 있는 가장 최근 날짜(산출 JSON 기준)."""
        ...

    def split_cleared(self) -> set[str]: ...

    def split_unknown(self) -> list[tuple[str, str]]:
        """공시 대조를 못 한 종목 [(code, note)] — 코드 순."""
        ...

    def put_split_check(self, rows: Sequence[SplitCheck], *, now: datetime) -> int: ...


# ── Postgres ───────────────────────────────────────────────────────────────────────────

_ALLTIME_COLS = (
    "code, hi, hi_date, cl, cl_date, prev_hi, prev_cl, first_date, last_date, n_days, suspect, "
    "suspect_date, suspect_note, history_from, source, quality"
)
_STOCK_DAY_COLS = (
    "code, trade_date, name, close, chg_pct, turnover, turnover_is_estimate, mktcap, label, "
    "near_kind, near_gap, status, suspect, sector, theme, ret_5d, ret_21d, vol_mult, source, "
    "quality, extra"
)


def _alltime(r: tuple[Any, ...]) -> AllTime:
    return AllTime(
        code=r[0], hi=to_float(r[1]), hi_date=r[2], cl=to_float(r[3]), cl_date=r[4],
        prev_hi=to_float(r[5]), prev_cl=to_float(r[6]), first_date=r[7], last_date=r[8],
        n_days=r[9], suspect=bool(r[10]), suspect_date=r[11], suspect_note=r[12],
        history_from=r[13], source=r[14], quality=Quality(r[15]),
    )  # fmt: skip


def _stock_day(r: tuple[Any, ...]) -> StockDay:
    return StockDay(
        code=r[0], date=r[1], name=r[2], close=to_float(r[3]), chg_pct=to_float(r[4]),
        turnover=to_int(r[5]), turnover_is_estimate=bool(r[6]), mktcap=to_int(r[7]), label=r[8],
        near_kind=r[9], near_gap=to_float(r[10]), status=r[11], suspect=bool(r[12]),
        sector=r[13], theme=r[14], ret_5d=to_float(r[15]), ret_21d=to_float(r[16]),
        vol_mult=to_float(r[17]), source=r[18], quality=Quality(r[19]), extra=dict(r[20] or {}),
    )  # fmt: skip


class PgBoardRepo:
    """`BoardRepo` 의 Postgres 구현."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgBoardRepo()"

    def alltime(self, codes: Collection[str] | None = None) -> dict[str, AllTime]:
        wanted = code_filter(codes)
        if wanted == []:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_ALLTIME_COLS} FROM prv_board.alltime WHERE market = %s "  # noqa: S608
                "AND (%s::text[] IS NULL OR code = ANY(%s::text[])) ORDER BY code".encode(),
                (MARKET, wanted, wanted),
            ).fetchall()
        return {r[0]: _alltime(r) for r in rows}

    def put_alltime(self, rows: Sequence[AllTime], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        if not rows:
            return 0
        params = [
            (MARKET, a.code, a.hi, a.hi_date, a.cl, a.cl_date, a.prev_hi, a.prev_cl, a.first_date,
             a.last_date, a.n_days, a.suspect, a.suspect_date, a.suspect_note, a.history_from,
             a.source, a.quality.value, now, loaded_by)
            for a in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_board.alltime (market, code, hi, hi_date, cl, cl_date, prev_hi, "
                b"prev_cl, first_date, last_date, n_days, suspect, suspect_date, suspect_note, "
                b"history_from, source, quality, updated_at, loaded_by) VALUES (%s, %s, %s, %s, "
                b"%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (market, code) DO UPDATE SET hi = EXCLUDED.hi, "
                b"hi_date = EXCLUDED.hi_date, cl = EXCLUDED.cl, cl_date = EXCLUDED.cl_date, "
                b"prev_hi = EXCLUDED.prev_hi, prev_cl = EXCLUDED.prev_cl, "
                b"first_date = EXCLUDED.first_date, last_date = EXCLUDED.last_date, "
                b"n_days = EXCLUDED.n_days, suspect = EXCLUDED.suspect, "
                b"suspect_date = EXCLUDED.suspect_date, suspect_note = EXCLUDED.suspect_note, "
                b"history_from = EXCLUDED.history_from, source = EXCLUDED.source, "
                b"quality = EXCLUDED.quality, updated_at = EXCLUDED.updated_at, "
                b"loaded_by = EXCLUDED.loaded_by",
                params,
            )
        return len(params)

    def labels(self, asof: date, basis: str) -> dict[str, Label]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, trade_date, basis, kind, rank, source, quality FROM prv_board.label "
                b"WHERE market = %s AND trade_date = %s AND basis = %s ORDER BY code",
                (MARKET, asof, basis),
            ).fetchall()
        return {
            r[0]: Label(
                code=r[0], date=r[1], basis=r[2], kind=r[3], rank=r[4], source=r[5],
                quality=Quality(r[6]),
            )
            for r in rows
        }  # fmt: skip

    def put_day(self, rec: BoardDayRecord, *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        day = rec.trade_date
        with self._connect() as conn, conn.cursor() as cur:
            for table in ("label", "stock_day", "artifact"):
                cur.execute(
                    f"DELETE FROM prv_board.{table} WHERE market = %s AND trade_date = %s".encode(),  # noqa: S608
                    (MARKET, day),
                )
            if rec.labels:
                cur.executemany(
                    b"INSERT INTO prv_board.label (market, code, trade_date, basis, kind, rank, "
                    b"source, quality, computed_at, loaded_by) "
                    b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (MARKET, lb.code, day, lb.basis, lb.kind, lb.rank, lb.source,
                         lb.quality.value, now, loaded_by)
                        for lb in rec.labels
                    ],
                )  # fmt: skip
            if rec.stock_days:
                cur.executemany(
                    b"INSERT INTO prv_board.stock_day (market, code, trade_date, name, close, "
                    b"chg_pct, turnover, turnover_is_estimate, mktcap, label, near_kind, near_gap, "
                    b"status, suspect, sector, theme, ret_5d, ret_21d, vol_mult, extra, source, "
                    b"quality, computed_at, loaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    b"%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (MARKET, s.code, day, s.name, s.close, s.chg_pct, s.turnover,
                         s.turnover_is_estimate, s.mktcap, s.label, s.near_kind, s.near_gap,
                         s.status, s.suspect, s.sector, s.theme, s.ret_5d, s.ret_21d, s.vol_mult,
                         Jsonb(dict(s.extra)), s.source, s.quality.value, now, loaded_by)
                        for s in rec.stock_days
                    ],
                )  # fmt: skip
            if rec.artifacts:
                cur.executemany(
                    b"INSERT INTO prv_board.artifact (market, trade_date, name, payload, "
                    b"engine_version, input_digest, as_of, source, quality, generated_at, "
                    b"loaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (MARKET, day, a.name, Jsonb(dict(a.payload)), a.engine_version,
                         a.input_digest, a.as_of, a.source, a.quality.value, a.generated_at,
                         loaded_by)
                        for a in rec.artifacts
                    ],
                )  # fmt: skip
        return len(rec.labels) + len(rec.stock_days) + len(rec.artifacts)

    def stock_days(self, asof: date) -> dict[str, StockDay]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_STOCK_DAY_COLS} FROM prv_board.stock_day "  # noqa: S608 — 상수 열 목록
                "WHERE market = %s AND trade_date = %s ORDER BY code".encode(),
                (MARKET, asof),
            ).fetchall()
        return {r[0]: _stock_day(r) for r in rows}

    def artifact(self, asof: date, name: str) -> BoardArtifact | None:
        with self._connect() as conn:
            r = conn.execute(
                b"SELECT trade_date, name, payload, engine_version, input_digest, as_of, "
                b"generated_at, source, quality FROM prv_board.artifact "
                b"WHERE market = %s AND trade_date = %s AND name = %s",
                (MARKET, asof, name),
            ).fetchone()
        if r is None:
            return None
        return BoardArtifact(
            trade_date=r[0], name=r[1], payload=dict(r[2]), engine_version=r[3],
            input_digest=r[4], as_of=r[5], generated_at=r[6], source=r[7], quality=Quality(r[8]),
        )  # fmt: skip

    def last_day(self, upto: date) -> date | None:
        with self._connect() as conn:
            r = conn.execute(
                b"SELECT max(trade_date) FROM prv_board.artifact "
                b"WHERE market = %s AND trade_date <= %s",
                (MARKET, upto),
            ).fetchone()
        return None if r is None else r[0]

    def split_cleared(self) -> set[str]:
        with self._connect() as conn:
            return {
                r[0]
                for r in conn.execute(
                    b"SELECT code FROM prv_board.split_check WHERE market = %s AND verdict = "
                    b"'none'",
                    (MARKET,),
                )
            }

    def split_unknown(self) -> list[tuple[str, str]]:
        with self._connect() as conn:
            return [
                (r[0], r[1])
                for r in conn.execute(
                    b"SELECT code, note FROM prv_board.split_check "
                    b"WHERE market = %s AND verdict = 'unknown' ORDER BY code",
                    (MARKET,),
                )
            ]

    def put_split_check(self, rows: Sequence[SplitCheck], *, now: datetime) -> int:
        require_aware("now", now)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_board.split_check (market, code, jump_date, verdict, note, "
                b"source, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (market, code) DO UPDATE SET jump_date = EXCLUDED.jump_date, "
                b"verdict = EXCLUDED.verdict, note = EXCLUDED.note, source = EXCLUDED.source, "
                b"updated_at = EXCLUDED.updated_at",
                [(MARKET, c.code, c.jump_date, c.verdict, c.note, c.source, now) for c in rows],
            )
        return len(rows)
