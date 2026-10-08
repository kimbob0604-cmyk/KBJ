"""시세 저장소 — 일봉·스냅·유니버스·장중 지수/업종/순위·D+1 대조(docs/p3_design.md §1.1·§3.4~§3.6).

ET `board/engine/db.py`(`iter_series`:158·`series_for`:176·`all_series`:195·`trading_days`:204·
`snapshot`:214) 의 SQLite 읽기를 Postgres 로 옮겼다. 표는 0003(`daily_bar`·`stock_snapshot`·
`universe`)과 0008(`index_intraday`·`sector_intraday`·`turnover_rank_intraday`·`eod_reconcile`).

읽기 규칙(D-P3-7 원장 우선순위 — `kbj.core.rows.row_rank`)
- 같은 (종목, 날짜) 에 원천이 여럿이면 **krx(ok) > kis(ok) > kis(estimated) > kis.prelim** 하나만
  돌려준다. invalid 는 다른 행이 없을 때만(엔진이 세고 뺀다). 거래소는 KRX > '' > TOTAL > NXT.
- `series`·`trading_days` 의 '거래일' 은 표에 행이 있는 날짜(전 종목 합집합 — ET 와 같다). 개별
  종목의 휴장·정지일은 그 종목 목록에 행이 없다.
- `snapshot(asof)` 는 asof 이하 가장 최근 스냅 날짜를 쓴다(ET `snapshot` — 수집일과 기준일이
  어긋나도 비지 않게). 반환 (스냅, 실제 날짜).

쓰기 규칙: 같은 기본 키는 덮어쓴다(ON CONFLICT DO UPDATE — 재실행 멱등). Postgres 오류는 그대로
올라간다(삼키지 않는다 — 절대 규칙 4).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb

from kbj.core.quality import Quality
from kbj.core.rows import (
    Bar,
    IndexBar,
    IndexQuote,
    RankRow,
    ReconcileRow,
    SectorQuote,
    Snap,
    UniverseRow,
    pick_best,
    row_rank,
)
from kbj.store.repos._common import (
    MARKET,
    ConnFactory,
    code_filter,
    require_aware,
    require_loaded_by,
    to_float,
    to_int,
)

Asset = Literal["stock", "etf", "etn"]


class MarketRepo(Protocol):
    def upsert_daily_bars(
        self,
        rows: Sequence[Bar],
        *,
        loaded_by: str,
        asset: Asset = "stock",
        received_at: datetime | None = None,
    ) -> int:
        """일봉을 쓴다(`prv_market.daily_bar`). 쓴 행 수."""
        ...

    def upsert_index_bars(
        self, rows: Sequence[IndexBar], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        """지수 일봉(asset=index). 이름은 쓰지 않는다(일봉 표에 이름 열이 없다)."""
        ...

    def trading_days(self, upto: date, n: int, *, asset: Asset = "stock") -> list[date]:
        """upto 이하 거래일(표에 행이 있는 날) 최신순 n 개 — ET `trading_days`."""
        ...

    def series(
        self, codes: Collection[str] | None, upto: date, n: int, *, asset: Asset = "stock"
    ) -> dict[str, list[Bar]]:
        """최근 n 거래일 창의 일봉 {code: 오름차순}. (종목, 날짜) 마다 원장 우선순위로 한 행."""
        ...

    def index_series(
        self, codes: Collection[str] | None, upto: date, n: int
    ) -> dict[str, list[IndexBar]]: ...

    def upsert_snapshots(
        self, rows: Sequence[Snap], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def snapshot(self, asof: date) -> tuple[dict[str, Snap], date | None]:
        """asof 이하 가장 최근 스냅 날짜의 {code: 스냅}(원장 우선순위로 한 행)과 그 날짜."""
        ...

    def snapshots(self, day: date, *, source: str | None = None) -> list[Snap]:
        """그날 스냅 전부(원천별 — D+1 대조용). source 를 주면 그 원천만(대소문자 무시)."""
        ...

    def set_snapshot_quality(
        self, day: date, code: str, *, source: str, venue: str, quality: Quality, note: str
    ) -> bool:
        """한 스냅 행의 품질을 바꾸고 사유를 extra.quality_note 에(대조 불일치 → invalid). 행이
        없으면 False."""
        ...

    def upsert_universe(
        self, rows: Sequence[UniverseRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def universe(self, asof: date) -> list[UniverseRow]:
        """원천마다 asof 이하 가장 최근 유니버스를 합쳐 종목마다 한 행(원천 순위). 코드 순."""
        ...

    def put_index_quotes(
        self, rows: Sequence[IndexQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def index_quotes(self, start: datetime, end: datetime) -> list[IndexQuote]:
        """[start, end) 의 장중 지수(ts·code 순)."""
        ...

    def put_sector_quotes(
        self, rows: Sequence[SectorQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def sector_quotes(self, start: datetime, end: datetime) -> list[SectorQuote]: ...

    def put_rank_rows(
        self, rows: Sequence[RankRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int: ...

    def rank_rows(self, start: datetime, end: datetime) -> list[RankRow]: ...

    def put_reconcile(self, rows: Sequence[ReconcileRow]) -> int: ...

    def reconcile(self, day: date) -> list[ReconcileRow]: ...


# ── 행 ↔ DB 변환 (Pg·메모리 공용) ────────────────────────────────────────────────────────


def bar_rank(b: Bar) -> tuple[int, ...]:
    return row_rank(b.source, b.quality, b.venue)


def snap_rank(s: Snap) -> tuple[int, ...]:
    return row_rank(s.source, s.quality, s.venue)


def snap_extra(s: Snap, note: str | None = None) -> dict[str, Any]:
    """스냅의 표에 없는 칸 → extra jsonb. status_flags 가 None 이면 키를 두지 않는다(모름)."""
    extra: dict[str, Any] = {}
    if s.kind is not None:
        extra["kind"] = s.kind
    if s.status_flags is not None:
        extra["status_flags"] = list(s.status_flags)
    if note:
        extra["quality_note"] = note
    return extra


def snap_from(
    *,
    code: str,
    day: date,
    name: str | None,
    segment: str | None,
    close: object,
    chg_pct: object,
    volume: object,
    turnover: object,
    turnover_is_estimate: bool,
    mktcap: object,
    shares: object,
    extra: dict[str, Any],
    source: str,
    venue: str,
    quality: str,
) -> Snap:
    flags = extra.get("status_flags")
    return Snap(
        code=code,
        date=day,
        name=name,
        market=segment,
        kind=extra.get("kind"),
        close=to_float(close),
        chg_pct=to_float(chg_pct),
        volume=to_int(volume),
        turnover=to_int(turnover),
        turnover_is_estimate=turnover_is_estimate,
        mktcap=to_int(mktcap),
        shares=to_int(shares),
        status_flags=None if flags is None else tuple(str(f) for f in flags),
        source=source,
        venue=venue,
        quality=Quality(quality),
    )


# ── Postgres ───────────────────────────────────────────────────────────────────────────

_UPSERT_BAR = """
INSERT INTO prv_market.daily_bar (market, asset, code, trade_date, source, venue, open, high, low,
    close, volume, turnover, adjusted, quality, received_at, loaded_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (market, asset, code, trade_date, source, venue) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
    volume = EXCLUDED.volume, turnover = EXCLUDED.turnover, adjusted = EXCLUDED.adjusted,
    quality = EXCLUDED.quality, received_at = EXCLUDED.received_at, loaded_by = EXCLUDED.loaded_by
"""
_DAYS = """
SELECT DISTINCT trade_date FROM prv_market.daily_bar
WHERE market = %s AND asset = %s AND trade_date <= %s ORDER BY trade_date DESC LIMIT %s
"""
_BARS = """
SELECT code, trade_date, open, high, low, close, volume, turnover, source, venue, quality
FROM prv_market.daily_bar
WHERE market = %s AND asset = %s AND trade_date BETWEEN %s AND %s
  AND (%s::text[] IS NULL OR code = ANY(%s::text[]))
ORDER BY code, trade_date
"""
_UPSERT_SNAP = """
INSERT INTO prv_market.stock_snapshot (market, code, trade_date, source, venue, name, segment,
    close, chg_pct, volume, turnover, turnover_is_estimate, mktcap, shares, extra, quality,
    received_at, loaded_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (market, code, trade_date, source, venue) DO UPDATE SET
    name = EXCLUDED.name, segment = EXCLUDED.segment, close = EXCLUDED.close,
    chg_pct = EXCLUDED.chg_pct, volume = EXCLUDED.volume, turnover = EXCLUDED.turnover,
    turnover_is_estimate = EXCLUDED.turnover_is_estimate, mktcap = EXCLUDED.mktcap,
    shares = EXCLUDED.shares, extra = EXCLUDED.extra, quality = EXCLUDED.quality,
    received_at = EXCLUDED.received_at, loaded_by = EXCLUDED.loaded_by
"""
_SNAP_COLS = (
    "code, trade_date, name, segment, close, chg_pct, volume, turnover, turnover_is_estimate, "
    "mktcap, shares, extra, source, venue, quality"
)
_UPSERT_UNIVERSE = """
INSERT INTO prv_market.universe (market, code, as_of, source, name, kind, listed_on, flags,
    quality, received_at, loaded_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (market, code, as_of, source) DO UPDATE SET
    name = EXCLUDED.name, kind = EXCLUDED.kind, listed_on = EXCLUDED.listed_on,
    flags = EXCLUDED.flags, quality = EXCLUDED.quality, received_at = EXCLUDED.received_at,
    loaded_by = EXCLUDED.loaded_by
"""
_UNIVERSE = """
WITH latest AS (
    SELECT source, max(as_of) AS as_of FROM prv_market.universe
    WHERE market = %s AND as_of <= %s GROUP BY source
)
SELECT u.code, u.as_of, u.name, u.kind, u.listed_on, u.source, u.quality, u.flags
FROM prv_market.universe u JOIN latest l ON u.source = l.source AND u.as_of = l.as_of
WHERE u.market = %s ORDER BY u.code, u.source
"""


def _snap_row(r: tuple[Any, ...]) -> Snap:
    return snap_from(
        code=r[0],
        day=r[1],
        name=r[2],
        segment=r[3],
        close=r[4],
        chg_pct=r[5],
        volume=r[6],
        turnover=r[7],
        turnover_is_estimate=bool(r[8]),
        mktcap=r[9],
        shares=r[10],
        extra=dict(r[11] or {}),
        source=r[12],
        venue=r[13],
        quality=r[14],
    )


def _bar_row(r: tuple[Any, ...]) -> Bar:
    return Bar(
        code=r[0],
        date=r[1],
        open=to_float(r[2]),
        high=to_float(r[3]),
        low=to_float(r[4]),
        close=to_float(r[5]),
        volume=to_int(r[6]),
        turnover=to_int(r[7]),
        source=r[8],
        venue=r[9],
        quality=Quality(r[10]),
    )


def group_best_bars(rows: Sequence[Bar]) -> dict[str, list[Bar]]:
    """(종목, 날짜) 마다 원장 우선순위로 한 행 → {code: 오름차순}."""
    best = pick_best(rows, lambda b: (b.code, b.date), bar_rank)
    out: dict[str, list[Bar]] = {}
    for (code, _), b in sorted(best.items(), key=lambda kv: kv[0]):
        out.setdefault(code, []).append(b)
    return out


def universe_flags(row: UniverseRow) -> dict[str, Any]:
    """유니버스 flags jsonb — 표에 시장(segment) 열이 없어 flags.segment 로 둔다."""
    flags = dict(row.flags)
    if row.market is not None:
        flags["segment"] = row.market
    return flags


def universe_from(
    code: str,
    as_of: date,
    name: str | None,
    kind: str | None,
    listed_on: date | None,
    source: str,
    quality: str,
    flags: dict[str, Any],
) -> UniverseRow:
    flags = dict(flags)
    seg = flags.pop("segment", None)
    return UniverseRow(
        code=code,
        as_of=as_of,
        name=name,
        market=None if seg is None else str(seg),
        kind=kind,
        listed_on=listed_on,
        source=source,
        quality=Quality(quality),
        flags=flags,
    )


def best_universe(rows: Sequence[UniverseRow]) -> list[UniverseRow]:
    best = pick_best(rows, lambda u: u.code, lambda u: row_rank(u.source, u.quality))
    return [best[c] for c in sorted(best)]


class PgMarketRepo:
    """`MarketRepo` 의 Postgres 구현. 부를 때마다 연결을 짧게 연다(`conn_factory`)."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgMarketRepo()"

    # 일봉
    def upsert_daily_bars(
        self,
        rows: Sequence[Bar],
        *,
        loaded_by: str,
        asset: Asset = "stock",
        received_at: datetime | None = None,
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (MARKET, asset, b.code, b.date, b.source, b.venue, b.open, b.high, b.low, b.close,
             b.volume, b.turnover, False, b.quality.value, received_at, loaded_by)
            for b in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT_BAR.encode(), params)
        return len(params)

    def upsert_index_bars(
        self, rows: Sequence[IndexBar], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (MARKET, "index", b.code, b.date, b.source, "", b.open, b.high, b.low, b.close,
             b.volume, b.turnover, False, b.quality.value, received_at, loaded_by)
            for b in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT_BAR.encode(), params)
        return len(params)

    def _days(self, asset: str, upto: date, n: int) -> list[date]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        with self._connect() as conn:
            return [r[0] for r in conn.execute(_DAYS.encode(), (MARKET, asset, upto, n))]

    def trading_days(self, upto: date, n: int, *, asset: Asset = "stock") -> list[date]:
        return self._days(asset, upto, n)

    def _bars(
        self, asset: str, codes: Collection[str] | None, upto: date, n: int
    ) -> list[tuple[Any, ...]]:
        days = self._days(asset, upto, n)
        if not days:
            return []
        wanted = code_filter(codes)
        if wanted == []:
            return []
        with self._connect() as conn:
            return conn.execute(
                _BARS.encode(), (MARKET, asset, days[-1], upto, wanted, wanted)
            ).fetchall()

    def series(
        self, codes: Collection[str] | None, upto: date, n: int, *, asset: Asset = "stock"
    ) -> dict[str, list[Bar]]:
        return group_best_bars([_bar_row(r) for r in self._bars(asset, codes, upto, n)])

    def index_series(
        self, codes: Collection[str] | None, upto: date, n: int
    ) -> dict[str, list[IndexBar]]:
        bars = group_best_bars([_bar_row(r) for r in self._bars("index", codes, upto, n)])
        return {
            c: [
                IndexBar(
                    code=b.code, date=b.date, name=None, open=b.open, high=b.high, low=b.low,
                    close=b.close, volume=b.volume, turnover=b.turnover, source=b.source,
                    quality=b.quality,
                )
                for b in rows
            ]
            for c, rows in bars.items()
        }  # fmt: skip

    # 스냅
    def upsert_snapshots(
        self, rows: Sequence[Snap], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (MARKET, s.code, s.date, s.source, s.venue, s.name, s.market, s.close, s.chg_pct,
             s.volume, s.turnover, s.turnover_is_estimate, s.mktcap, s.shares,
             Jsonb(snap_extra(s)), s.quality.value, received_at, loaded_by)
            for s in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT_SNAP.encode(), params)
        return len(params)

    def snapshot(self, asof: date) -> tuple[dict[str, Snap], date | None]:
        with self._connect() as conn:
            row = conn.execute(
                b"SELECT max(trade_date) FROM prv_market.stock_snapshot "
                b"WHERE market = %s AND trade_date <= %s",
                (MARKET, asof),
            ).fetchone()
            used: date | None = row[0] if row else None
            if used is None:
                return {}, None
            rows = conn.execute(
                f"SELECT {_SNAP_COLS} FROM prv_market.stock_snapshot "  # noqa: S608 — 상수 열 목록
                "WHERE market = %s AND trade_date = %s ORDER BY code".encode(),
                (MARKET, used),
            ).fetchall()
        snaps = [_snap_row(r) for r in rows]
        best = pick_best(snaps, lambda s: s.code, snap_rank)
        return dict(sorted(best.items())), used

    def snapshots(self, day: date, *, source: str | None = None) -> list[Snap]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SNAP_COLS} FROM prv_market.stock_snapshot "  # noqa: S608 — 상수 열 목록
                "WHERE market = %s AND trade_date = %s "
                "AND (%s::text IS NULL OR lower(source) = lower(%s::text)) "
                "ORDER BY code, source, venue".encode(),
                (MARKET, day, source, source),
            ).fetchall()
        return [_snap_row(r) for r in rows]

    def set_snapshot_quality(
        self, day: date, code: str, *, source: str, venue: str, quality: Quality, note: str
    ) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                b"UPDATE prv_market.stock_snapshot SET quality = %s, "
                b"extra = extra || jsonb_build_object('quality_note', %s::text) "
                b"WHERE market = %s AND trade_date = %s AND code = %s AND source = %s AND venue = "
                b"%s",
                (Quality(quality).value, note, MARKET, day, code, source, venue),
            )
            return cur.rowcount > 0

    # 유니버스
    def upsert_universe(
        self, rows: Sequence[UniverseRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (MARKET, u.code, u.as_of, u.source, u.name, u.kind, u.listed_on,
             Jsonb(universe_flags(u)), u.quality.value, received_at, loaded_by)
            for u in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT_UNIVERSE.encode(), params)
        return len(params)

    def universe(self, asof: date) -> list[UniverseRow]:
        with self._connect() as conn:
            rows = conn.execute(_UNIVERSE.encode(), (MARKET, asof, MARKET)).fetchall()
        return best_universe(
            [
                universe_from(r[0], r[1], r[2], r[3], r[4], r[5], r[6], dict(r[7] or {}))
                for r in rows
            ]
        )

    # 장중 이력
    def put_index_quotes(
        self, rows: Sequence[IndexQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (q.code, q.ts, q.source, q.name, q.value, q.chg_pct, q.turnover, q.volume,
             q.quality.value, received_at, loaded_by)
            for q in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_market.index_intraday (index_code, ts, source, name, value, "
                b"chg_pct, turnover, volume, quality, received_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (index_code, ts, source) DO UPDATE SET name = EXCLUDED.name, "
                b"value = EXCLUDED.value, chg_pct = EXCLUDED.chg_pct, turnover = "
                b"EXCLUDED.turnover, "
                b"volume = EXCLUDED.volume, quality = EXCLUDED.quality, "
                b"received_at = EXCLUDED.received_at, loaded_by = EXCLUDED.loaded_by",
                params,
            )
        return len(params)

    def index_quotes(self, start: datetime, end: datetime) -> list[IndexQuote]:
        require_aware("start", start)
        require_aware("end", end)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT index_code, ts, name, value, chg_pct, turnover, volume, source, quality "
                b"FROM prv_market.index_intraday WHERE ts >= %s AND ts < %s "
                b"ORDER BY ts, index_code, source",
                (start, end),
            ).fetchall()
        return [
            IndexQuote(
                code=r[0], ts=r[1], name=r[2], value=to_float(r[3]), chg_pct=to_float(r[4]),
                turnover=to_int(r[5]), volume=to_int(r[6]), source=r[7], quality=Quality(r[8]),
            )
            for r in rows
        ]  # fmt: skip

    def put_sector_quotes(
        self, rows: Sequence[SectorQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (q.market, q.code, q.ts, q.source, q.name, q.value, q.chg_pct, q.turnover,
             q.quality.value, received_at, loaded_by)
            for q in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_market.sector_intraday (market, sector_code, ts, source, name, "
                b"value, chg_pct, turnover, quality, received_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (market, sector_code, ts, source) DO UPDATE SET name = "
                b"EXCLUDED.name, "
                b"value = EXCLUDED.value, chg_pct = EXCLUDED.chg_pct, turnover = "
                b"EXCLUDED.turnover, "
                b"quality = EXCLUDED.quality, received_at = EXCLUDED.received_at, "
                b"loaded_by = EXCLUDED.loaded_by",
                params,
            )
        return len(params)

    def sector_quotes(self, start: datetime, end: datetime) -> list[SectorQuote]:
        require_aware("start", start)
        require_aware("end", end)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT market, sector_code, ts, name, value, chg_pct, turnover, source, quality "
                b"FROM prv_market.sector_intraday WHERE ts >= %s AND ts < %s "
                b"ORDER BY ts, market, sector_code, source",
                (start, end),
            ).fetchall()
        return [
            SectorQuote(
                market=r[0], code=r[1], ts=r[2], name=r[3], value=to_float(r[4]),
                chg_pct=to_float(r[5]), turnover=to_int(r[6]), source=r[7], quality=Quality(r[8]),
            )
            for r in rows
        ]  # fmt: skip

    def put_rank_rows(
        self, rows: Sequence[RankRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        params = [
            (q.market, q.ts, q.rank, q.venue, q.code, q.name, q.turnover, q.chg_pct, q.source,
             q.quality.value, received_at, loaded_by)
            for q in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_market.turnover_rank_intraday (market, ts, rank, venue, code, "
                b"name, turnover, chg_pct, source, quality, received_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (market, ts, rank, venue) DO UPDATE SET code = EXCLUDED.code, "
                b"name = EXCLUDED.name, turnover = EXCLUDED.turnover, chg_pct = EXCLUDED.chg_pct, "
                b"source = EXCLUDED.source, quality = EXCLUDED.quality, "
                b"received_at = EXCLUDED.received_at, loaded_by = EXCLUDED.loaded_by",
                params,
            )
        return len(params)

    def rank_rows(self, start: datetime, end: datetime) -> list[RankRow]:
        require_aware("start", start)
        require_aware("end", end)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT market, ts, rank, venue, code, name, turnover, chg_pct, source, quality "
                b"FROM prv_market.turnover_rank_intraday WHERE ts >= %s AND ts < %s "
                b"ORDER BY ts, market, venue, rank",
                (start, end),
            ).fetchall()
        return [
            RankRow(
                market=r[0], ts=r[1], rank=r[2], venue=r[3], code=r[4], name=r[5],
                turnover=to_int(r[6]), chg_pct=to_float(r[7]), source=r[8], quality=Quality(r[9]),
            )
            for r in rows
        ]  # fmt: skip

    # D+1 대조
    def put_reconcile(self, rows: Sequence[ReconcileRow]) -> int:
        if not rows:
            return 0
        params = [
            (r.trade_date, r.code, r.field, r.kis_value, r.krx_value, r.diff_pct, r.verdict,
             r.checked_at)
            for r in rows
        ]  # fmt: skip
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_market.eod_reconcile (trade_date, code, field, kis_value, "
                b"krx_value, diff_pct, verdict, checked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, "
                b"%s) "
                b"ON CONFLICT (trade_date, code, field) DO UPDATE SET kis_value = "
                b"EXCLUDED.kis_value, "
                b"krx_value = EXCLUDED.krx_value, diff_pct = EXCLUDED.diff_pct, "
                b"verdict = EXCLUDED.verdict, checked_at = EXCLUDED.checked_at",
                params,
            )
        return len(params)

    def reconcile(self, day: date) -> list[ReconcileRow]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT trade_date, code, field, kis_value, krx_value, diff_pct, verdict, "
                b"checked_at FROM prv_market.eod_reconcile WHERE trade_date = %s "
                b"ORDER BY code, field",
                (day,),
            ).fetchall()
        return [
            ReconcileRow(
                trade_date=r[0], code=r[1], field=r[2], kis_value=to_float(r[3]),
                krx_value=to_float(r[4]), diff_pct=to_float(r[5]), verdict=r[6], checked_at=r[7],
            )
            for r in rows
        ]  # fmt: skip
