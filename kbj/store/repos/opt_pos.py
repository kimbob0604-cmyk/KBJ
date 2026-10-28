"""옵션 행사가별 투자자 추정 포지션 저장소(`prv_gex.opt_pos_strike`·`opt_pos_day` — 0030, ADR 0044).

쓰기 주인은 작업 `gex.option_positions` 하나(그날 행 교체 — 한 트랜잭션). 읽기는 둘:
- 작업 입력(원천 표 읽기만 — 쓰기 주인이 아니다): `investor_flow` 분 행·`chain_snapshots` 분 시세·
  그날 마지막 체인(종가·OI·KIS 델타)·`option_iv` 마지막 델타·`fut_board` 근월물 마지막 가격.
- 리더 입력: 저장된 하루 행·KRX 확정 OI(`krx_opt_daily`).

이 모듈은 `kbj.core` 와 psycopg 만 쓴다(계약 ⑦) — 행 자료형도 여기 둔다(엔진 자료형으로 옮기는 것은
작업·리더).
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Protocol

from kbj.core.time import KST
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by, to_float, to_int

__all__ = [
    "KRX_FAMILY",
    "ChainClose",
    "ChainQuote",
    "FlowRow",
    "MemoryOptPosRepo",
    "OptPosDayRow",
    "OptPosRepo",
    "OptPosStrikeRow",
    "PgOptPosRepo",
]

# mrkt_cls → KRX 옵션 일별 family(0006 krx_opt_daily — 코스피200 계열)
KRX_FAMILY: Final = {"": "kospi200", "WKM": "kospi200_weekly_mon", "WKI": "kospi200_weekly_thu"}
StrikeKey = tuple[str, str, float, str]  # (mrkt_cls, 만기, 행사가, C/P)


@dataclass(frozen=True)
class FlowRow:
    ts: datetime
    market_code: str
    sector_code: str
    investor: str
    buy_qty: int | None
    buy_value: int | None
    sell_qty: int | None
    sell_value: int | None
    net_qty: int | None
    quality: str


@dataclass(frozen=True)
class ChainQuote:
    ts: datetime
    mrkt_cls: str
    expiry: str
    strike: float
    cp: str
    last: float | None


@dataclass(frozen=True)
class ChainClose:
    mrkt_cls: str
    expiry: str
    strike: float
    cp: str
    last: float | None
    oi: int | None
    delta_kis: float | None
    delta_self: float | None = None


@dataclass(frozen=True)
class OptPosStrikeRow:
    trade_date: date
    investor: str
    mrkt_cls: str
    expiry: str
    strike: float
    cp: str
    buy_qty: float
    sell_qty: float
    close_price: float | None = None
    board_oi: int | None = None
    delta: float | None = None
    delta_source: str | None = None
    quality: str = "estimated"


@dataclass(frozen=True)
class OptPosDayRow:
    trade_date: date
    investor: str
    market_code: str
    sector_code: str
    minutes: int
    gap_minutes: int
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    total_buy: int | None = None
    total_sell: int | None = None
    unallocated_buy: float = 0.0
    unallocated_sell: float = 0.0
    imprecise_qty: float = 0.0
    single_qty: float = 0.0
    reverted: int = 0
    skipped: int = 0
    futures_net: int | None = None
    fut_price: float | None = None
    reasons: tuple[str, ...] = ()
    quality: str = "estimated"
    computed_at: datetime | None = None


class OptPosRepo(Protocol):
    # 작업 입력(원천 읽기)
    def flow_points(
        self, day: date, pairs: Collection[tuple[str, str]], investors: Collection[str]
    ) -> list[FlowRow]:
        """investor_flow 그날 정규장(day) 행 — (투자자, 시장, 업종, ts) 순."""
        ...

    def chain_quotes(self, day: date, classes: Collection[str]) -> list[ChainQuote]:
        """chain_snapshots 그날 정규장 — 분 버킷마다 (시리즈·행사가·콜풋) 가장 늦은 행."""
        ...

    def chain_close(self, day: date, classes: Collection[str]) -> list[ChainClose]:
        """그날 (시리즈·행사가·콜풋) 마지막 체인 행(last·oi·KIS delta) + option_iv 자체 delta."""
        ...

    def futures_close(self, day: date) -> float | None:
        """그날 정규장 fut_board(F·board) 마지막 시각의 잔존일 가장 짧은 종목 가격."""
        ...

    # 쓰기
    def replace_day(
        self,
        day: date,
        strikes: Sequence[OptPosStrikeRow],
        days: Sequence[OptPosDayRow],
        *,
        computed_at: datetime,
        loaded_by: str,
    ) -> int: ...

    # 리더 입력
    def latest_day(self, investor: str, upto: date) -> date | None: ...

    def strike_rows(self, investor: str, first: date, last: date) -> list[OptPosStrikeRow]: ...

    def day_rows(self, investor: str, first: date, last: date) -> list[OptPosDayRow]: ...

    def krx_oi(self, day: date) -> dict[StrikeKey, int]:
        """KRX 확정 OI(그날 정규 행) — (mrkt_cls, 만기, 행사가, C/P) → 계약."""
        ...


def _check(day: date, strikes: Sequence[OptPosStrikeRow], days: Sequence[OptPosDayRow],
           computed_at: datetime, loaded_by: str) -> None:  # fmt: skip
    require_loaded_by(loaded_by)
    require_aware("computed_at", computed_at)
    if computed_at is None:
        raise ValueError("computed_at 이 없다")
    bad = [r for r in (*strikes, *days) if r.trade_date != day]
    if bad:
        raise ValueError(f"다른 날 행: {bad[0].trade_date} ≠ {day}")
    for r in strikes:
        if r.buy_qty < 0 or r.sell_qty < 0:
            raise ValueError(f"음수 배분: {r}")


# ── Postgres ───────────────────────────────────────────────────────────────────────────────

_STRIKE_COLS: Final = (
    "trade_date, investor, mrkt_cls, expiry, strike, cp, buy_qty, sell_qty, close_price, "
    "board_oi, delta, delta_source, quality"
)
_DAY_COLS: Final = (
    "trade_date, investor, market_code, sector_code, minutes, gap_minutes, first_ts, last_ts, "
    "total_buy, total_sell, unallocated_buy, unallocated_sell, imprecise_qty, single_qty, "
    "reverted, skipped, futures_net, fut_price, reasons, quality, computed_at"
)


class PgOptPosRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgOptPosRepo()"

    def _rows(self, q: bytes, params: Sequence[object]) -> list[tuple[Any, ...]]:
        with self._connect() as conn:
            return [tuple(r) for r in conn.execute(q, tuple(params)).fetchall()]

    def flow_points(
        self, day: date, pairs: Collection[tuple[str, str]], investors: Collection[str]
    ) -> list[FlowRow]:
        if not pairs or not investors:
            return []
        rows = self._rows(
            b"SELECT ts, market_code, sector_code, investor, buy_qty, buy_value, sell_qty, "
            b"sell_value, net_qty, quality FROM prv_gex.investor_flow WHERE trade_date = %s "
            b"AND session = 'day' AND (market_code || ':' || sector_code) = ANY(%s) "
            b"AND investor = ANY(%s) ORDER BY investor, market_code, sector_code, ts",
            (day, sorted(f"{m}:{s}" for m, s in pairs), sorted(investors)),
        )
        return [FlowRow(r[0], r[1], r[2], r[3], to_int(r[4]), to_int(r[5]), to_int(r[6]),
                        to_int(r[7]), to_int(r[8]), r[9]) for r in rows]  # fmt: skip

    def chain_quotes(self, day: date, classes: Collection[str]) -> list[ChainQuote]:
        if not classes:
            return []
        rows = self._rows(
            b"SELECT DISTINCT ON (date_trunc('minute', ts), mrkt_cls, expiry, strike, cp) "
            b"ts, mrkt_cls, expiry, strike, cp, last FROM prv_gex.chain_snapshots "
            b"WHERE trade_date = %s AND session = 'day' AND mrkt_cls = ANY(%s) "
            b"ORDER BY date_trunc('minute', ts), mrkt_cls, expiry, strike, cp, ts DESC",
            (day, sorted(classes)),
        )
        return [ChainQuote(r[0], r[1], r[2], float(r[3]), r[4], to_float(r[5])) for r in rows]

    def chain_close(self, day: date, classes: Collection[str]) -> list[ChainClose]:
        if not classes:
            return []
        rows = self._rows(
            b"WITH c AS (SELECT DISTINCT ON (mrkt_cls, expiry, strike, cp) mrkt_cls, expiry, "
            b"strike, cp, last, oi, delta FROM prv_gex.chain_snapshots WHERE trade_date = %s "
            b"AND session = 'day' AND mrkt_cls = ANY(%s) ORDER BY mrkt_cls, expiry, strike, cp, "
            b"ts DESC), v AS (SELECT DISTINCT ON (mrkt_cls, expiry, strike, cp) mrkt_cls, expiry, "
            b"strike, cp, delta FROM prv_gex.option_iv WHERE trade_date = %s AND session = 'day' "
            b"AND delta IS NOT NULL ORDER BY mrkt_cls, expiry, strike, cp, ts DESC) "
            b"SELECT c.mrkt_cls, c.expiry, c.strike, c.cp, c.last, c.oi, c.delta, v.delta FROM c "
            b"LEFT JOIN v USING (mrkt_cls, expiry, strike, cp) "
            b"ORDER BY c.mrkt_cls, c.expiry, c.strike, c.cp",
            (day, sorted(classes), day),
        )
        return [ChainClose(r[0], r[1], float(r[2]), r[3], to_float(r[4]), to_int(r[5]),
                           to_float(r[6]), to_float(r[7])) for r in rows]  # fmt: skip

    def futures_close(self, day: date) -> float | None:
        rows = self._rows(
            b"SELECT price FROM prv_gex.fut_board WHERE trade_date = %s AND session = 'day' "
            b"AND market = 'F' AND source = 'board' AND price IS NOT NULL "
            b"ORDER BY ts DESC, remaining_days ASC NULLS LAST LIMIT 1",
            (day,),
        )
        return to_float(rows[0][0]) if rows else None

    def replace_day(
        self,
        day: date,
        strikes: Sequence[OptPosStrikeRow],
        days: Sequence[OptPosDayRow],
        *,
        computed_at: datetime,
        loaded_by: str,
    ) -> int:
        _check(day, strikes, days, computed_at, loaded_by)
        s_par = [
            (r.trade_date, r.investor, r.mrkt_cls, r.expiry, Decimal(str(r.strike)), r.cp,
             r.buy_qty, r.sell_qty, r.close_price, r.board_oi, r.delta, r.delta_source,
             r.quality, computed_at, loaded_by)
            for r in strikes
        ]  # fmt: skip
        d_par = [
            (r.trade_date, r.investor, r.market_code, r.sector_code, r.minutes, r.gap_minutes,
             r.first_ts, r.last_ts, r.total_buy, r.total_sell, r.unallocated_buy,
             r.unallocated_sell, r.imprecise_qty, r.single_qty, r.reverted, r.skipped,
             r.futures_net, r.fut_price, json.dumps(list(r.reasons), ensure_ascii=False),
             r.quality, computed_at, loaded_by)
            for r in days
        ]  # fmt: skip
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(b"DELETE FROM prv_gex.opt_pos_strike WHERE trade_date = %s", (day,))
            cur.execute(b"DELETE FROM prv_gex.opt_pos_day WHERE trade_date = %s", (day,))
            if s_par:
                cur.executemany(
                    b"INSERT INTO prv_gex.opt_pos_strike (" + _STRIKE_COLS.encode()
                    + b", computed_at, loaded_by) VALUES (" + b", ".join([b"%s"] * 15) + b")",
                    s_par,
                )  # fmt: skip
            if d_par:
                cur.executemany(
                    b"INSERT INTO prv_gex.opt_pos_day (" + _DAY_COLS.encode()
                    + b", loaded_by) VALUES (" + b", ".join([b"%s"] * 22) + b")",
                    d_par,
                )  # fmt: skip
        return len(s_par) + len(d_par)

    def latest_day(self, investor: str, upto: date) -> date | None:
        rows = self._rows(
            b"SELECT max(trade_date) FROM prv_gex.opt_pos_day WHERE investor = %s "
            b"AND trade_date <= %s",
            (investor, upto),
        )
        return rows[0][0] if rows else None

    def strike_rows(self, investor: str, first: date, last: date) -> list[OptPosStrikeRow]:
        rows = self._rows(
            b"SELECT " + _STRIKE_COLS.encode() + b" FROM prv_gex.opt_pos_strike "
            b"WHERE investor = %s AND trade_date BETWEEN %s AND %s "
            b"ORDER BY trade_date, mrkt_cls, expiry, cp, strike",
            (investor, first, last),
        )
        return [OptPosStrikeRow(r[0], r[1], r[2], r[3], float(r[4]), r[5], float(r[6]),
                                float(r[7]), to_float(r[8]), to_int(r[9]), to_float(r[10]),
                                r[11], r[12]) for r in rows]  # fmt: skip

    def day_rows(self, investor: str, first: date, last: date) -> list[OptPosDayRow]:
        rows = self._rows(
            b"SELECT " + _DAY_COLS.encode() + b" FROM prv_gex.opt_pos_day "
            b"WHERE investor = %s AND trade_date BETWEEN %s AND %s "
            b"ORDER BY trade_date, market_code, sector_code",
            (investor, first, last),
        )
        out: list[OptPosDayRow] = []
        for r in rows:
            reasons = r[18] if isinstance(r[18], list) else json.loads(r[18] or "[]")
            out.append(
                OptPosDayRow(r[0], r[1], r[2], r[3], int(r[4]), int(r[5]), r[6], r[7],
                             to_int(r[8]), to_int(r[9]), float(r[10]), float(r[11]),
                             float(r[12]), float(r[13]), int(r[14]), int(r[15]), to_int(r[16]),
                             to_float(r[17]), tuple(str(x) for x in reasons), r[19], r[20])
            )  # fmt: skip
        return out

    def krx_oi(self, day: date) -> dict[StrikeKey, int]:
        fam = {v: k for k, v in KRX_FAMILY.items()}
        rows = self._rows(
            b"SELECT family, expiry, strike, cp, acc_opnint_qty FROM prv_gex.krx_opt_daily "
            b"WHERE trade_date = %s AND session = 'day' AND family = ANY(%s) "
            b"AND acc_opnint_qty IS NOT NULL",
            (day, sorted(fam)),
        )
        return {(fam[r[0]], r[1], float(r[2]), r[3]): int(r[4]) for r in rows}


# ── 메모리(시험) ───────────────────────────────────────────────────────────────────────────


@dataclass
class MemoryOptPosRepo:
    """시험용 — 원천 행(flows·chain·close·fut·krx)을 시험이 직접 넣는다."""

    flows: list[FlowRow] = field(default_factory=list)
    quotes: list[ChainQuote] = field(default_factory=list)
    closes: dict[date, list[ChainClose]] = field(default_factory=dict)
    fut_price: dict[date, float] = field(default_factory=dict)
    krx: dict[date, dict[StrikeKey, int]] = field(default_factory=dict)
    strikes: list[OptPosStrikeRow] = field(default_factory=list)
    days: list[OptPosDayRow] = field(default_factory=list)
    writes: int = 0

    def flow_points(
        self, day: date, pairs: Collection[tuple[str, str]], investors: Collection[str]
    ) -> list[FlowRow]:
        want, invs = set(pairs), set(investors)
        rows = [
            r for r in self.flows
            if _kst_day(r.ts) == day and (r.market_code, r.sector_code) in want
            and r.investor in invs
        ]  # fmt: skip
        return sorted(rows, key=lambda r: (r.investor, r.market_code, r.sector_code, r.ts))

    def chain_quotes(self, day: date, classes: Collection[str]) -> list[ChainQuote]:
        best: dict[tuple[datetime, str, str, float, str], ChainQuote] = {}
        for q in self.quotes:
            if _kst_day(q.ts) != day or q.mrkt_cls not in classes:
                continue
            k = (q.ts.replace(second=0, microsecond=0), q.mrkt_cls, q.expiry, q.strike, q.cp)
            if k not in best or best[k].ts < q.ts:
                best[k] = q
        return sorted(best.values(), key=lambda q: (q.ts, q.mrkt_cls, q.expiry, q.strike, q.cp))

    def chain_close(self, day: date, classes: Collection[str]) -> list[ChainClose]:
        return [c for c in self.closes.get(day, []) if c.mrkt_cls in classes]

    def futures_close(self, day: date) -> float | None:
        return self.fut_price.get(day)

    def replace_day(
        self,
        day: date,
        strikes: Sequence[OptPosStrikeRow],
        days: Sequence[OptPosDayRow],
        *,
        computed_at: datetime,
        loaded_by: str,
    ) -> int:
        _check(day, strikes, days, computed_at, loaded_by)
        self.strikes = [r for r in self.strikes if r.trade_date != day] + list(strikes)
        stamped = [
            OptPosDayRow(**{**r.__dict__, "computed_at": computed_at}) for r in days
        ]  # fmt: skip
        self.days = [r for r in self.days if r.trade_date != day] + stamped
        self.writes += 1
        return len(strikes) + len(days)

    def latest_day(self, investor: str, upto: date) -> date | None:
        ds = [r.trade_date for r in self.days if r.investor == investor and r.trade_date <= upto]
        return max(ds, default=None)

    def strike_rows(self, investor: str, first: date, last: date) -> list[OptPosStrikeRow]:
        rows = [r for r in self.strikes
                if r.investor == investor and first <= r.trade_date <= last]  # fmt: skip
        return sorted(rows, key=lambda r: (r.trade_date, r.mrkt_cls, r.expiry, r.cp, r.strike))

    def day_rows(self, investor: str, first: date, last: date) -> list[OptPosDayRow]:
        rows = [r for r in self.days if r.investor == investor and first <= r.trade_date <= last]
        return sorted(rows, key=lambda r: (r.trade_date, r.market_code, r.sector_code))

    def krx_oi(self, day: date) -> dict[StrikeKey, int]:
        return dict(self.krx.get(day, {}))


def _kst_day(ts: datetime) -> date:
    return ts.astimezone(KST).date()
