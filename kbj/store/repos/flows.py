"""수급 저장소 — 종목·시장 투자자 일별 원장, 장중 가집계 이력, 잠정→확정 차이, 검산 기록
(docs/p3_design.md §1.1·§3.5, D-P3-7, docs/metrics.md §2).

표: 0003 `prv_flows.stock_investor_daily`·`market_investor_daily`, 0008 `investor_intraday`·
`investor_revision`·`ledger_check`.

잠정 → 확정 규칙(D-P3-7, `plan_investor_upsert` — Pg·메모리가 같은 함수를 쓴다)
- 키 = (종목, 날짜, 투자자, 거래소). 같은 키의 원천이 여럿이면 원장 우선순위(krx > kis(ok) >
  kis(estimated) > kis.prelim — `kbj.core.rows.ledger_rank`)로 비교한다.
- 들어온 행보다 **앞선** 원천의 행이 이미 있으면 그 행은 쓰지 않는다(`skipped` — 마감 확정 뒤
  늦게 온 장중 잠정이 덮지 않는다).
- `revise=True`(마감 확정 수집): 같은 키의 **뒤처진** 원천 행(장중 잠정)을 지우고, 지우기 전 값과
  새 값의 차이를 `prv_flows.investor_revision` 에 같은 트랜잭션으로 남긴다.
- `revise=False`: 같은 원천의 같은 키만 덮어쓴다(장중 슬롯마다 오늘 행 갱신).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from kbj.core.quality import Quality
from kbj.core.rows import (
    IntradayInvestor,
    Investor,
    InvestorDay,
    LedgerCheck,
    Revision,
    ledger_rank,
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

FlowKey = tuple[str, date, Investor, str]  # (code, date, investor, venue)


@dataclass(frozen=True)
class StoredInvestorDay:
    """저장된 행 + 받은 시각(잠정 행의 `est_ts`)."""

    row: InvestorDay
    received_at: datetime | None


@dataclass(frozen=True)
class RevisionReport:
    """`upsert_investor_days` 결과. written = 쓴 행, skipped = 앞선 원천이 있어 안 쓴 행,
    revisions = 덮어쓴 잠정 행의 차이(revise=True 일 때만)."""

    written: int
    skipped: int
    revisions: tuple[Revision, ...]

    @property
    def revised(self) -> int:
        return len(self.revisions)

    @property
    def max_abs_diff(self) -> int | None:
        diffs = [abs(r.diff) for r in self.revisions if r.diff is not None]
        return max(diffs) if diffs else None


@dataclass(frozen=True)
class InvestorUpsertPlan:
    write: tuple[InvestorDay, ...]
    delete: tuple[InvestorDay, ...]  # 지울 (뒤처진 원천) 행
    revisions: tuple[Revision, ...]
    skipped: int


def flow_key(r: InvestorDay) -> FlowKey:
    return (r.code, r.date, r.investor, r.venue)


def _rank(r: InvestorDay) -> tuple[int, int, int]:
    return ledger_rank(r.source, r.quality)


def plan_investor_upsert(
    incoming: Sequence[InvestorDay],
    existing: Iterable[StoredInvestorDay],
    *,
    revise: bool,
    now: datetime,
) -> InvestorUpsertPlan:
    """들어온 행과 이미 있는 행으로 쓰기·지우기·차이 기록을 정한다(순수 함수)."""
    require_aware("now", now)
    by_key: dict[FlowKey, list[StoredInvestorDay]] = {}
    for e in existing:
        by_key.setdefault(flow_key(e.row), []).append(e)
    # 한 번의 호출 안에서 같은 PK 가 두 번 오면 뒤의 것이 이긴다(덮어쓰기와 같은 뜻)
    latest: dict[tuple[FlowKey, str], InvestorDay] = {}
    for r in incoming:
        latest[(flow_key(r), r.source.lower())] = r
    write: list[InvestorDay] = []
    delete: list[InvestorDay] = []
    revisions: list[Revision] = []
    skipped = 0
    for (key, src), r in latest.items():
        others = [e for e in by_key.get(key, []) if e.row.source.lower() != src]
        if any(_rank(e.row) < _rank(r) for e in others):
            skipped += 1
            continue
        write.append(r)
        if not revise:
            continue
        worse = sorted((e for e in others if _rank(e.row) > _rank(r)), key=lambda e: _rank(e.row))
        if not worse:
            continue
        est = worse[0]
        diff = (
            r.net_value - est.row.net_value
            if r.net_value is not None and est.row.net_value is not None
            else None
        )
        revisions.append(
            Revision(
                code=r.code,
                trade_date=r.date,
                investor=r.investor,
                venue=r.venue,
                est_value=est.row.net_value,
                est_ts=est.received_at,
                final_value=r.net_value,
                final_source=r.source,
                diff=diff,
                revised_at=now,
            )
        )
        delete.extend(e.row for e in worse)
    return InvestorUpsertPlan(tuple(write), tuple(delete), tuple(revisions), skipped)


def best_investor_days(rows: Iterable[InvestorDay]) -> dict[str, list[InvestorDay]]:
    """키마다 원장 우선순위로 한 행 → {code: (날짜, 투자자, 거래소) 순}."""
    best = pick_best(rows, flow_key, lambda r: row_rank(r.source, r.quality, r.venue))
    out: dict[str, list[InvestorDay]] = {}
    for key in sorted(best, key=lambda k: (k[0], k[1], k[2].value, k[3])):
        out.setdefault(key[0], []).append(best[key])
    return out


class FlowsRepo(Protocol):
    def upsert_investor_days(
        self,
        rows: Sequence[InvestorDay],
        *,
        revise: bool,
        loaded_by: str,
        now: datetime,
        received_at: datetime | None = None,
    ) -> RevisionReport:
        """종목 투자자 일별 원장 쓰기(위 규칙). 차이 기록은 같은 트랜잭션."""
        ...

    def window(
        self, codes: Collection[str] | None, end: date, n: int
    ) -> dict[str, list[InvestorDay]]:
        """end 이하 최근 n 거래일(표에 행이 있는 날) 창 {code: 행} — 키마다 우선순위 한 행."""
        ...

    def days(
        self, codes: Collection[str] | None, start: date, end: date
    ) -> dict[str, list[InvestorDay]]:
        """[start, end] 날짜 범위(같은 고르기 규칙)."""
        ...

    def upsert_market_days(
        self, rows: Sequence[InvestorDay], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        """시장별 투자자 일별(`market_investor_daily` — code 에 KIS 시장 코드)."""
        ...

    def market_days(self, end: date, n: int) -> dict[str, list[InvestorDay]]:
        """시장 코드 → end 이하 최근 n 날짜의 행(우선순위 한 행)."""
        ...

    def put_intraday(
        self,
        rows: Sequence[IntradayInvestor],
        *,
        loaded_by: str,
        received_at: datetime | None = None,
    ) -> int: ...

    def intraday(self, start: datetime, end: datetime) -> list[IntradayInvestor]:
        """[start, end) 장중 가집계(ts·code·investor 순)."""
        ...

    def revisions(self, day: date) -> list[Revision]: ...

    def put_checks(self, rows: Sequence[LedgerCheck]) -> int:
        """검산 실패 기록(같은 키는 덮어쓴다)."""
        ...

    def checks(self, day: date, domain: str | None = None) -> list[LedgerCheck]: ...


# ── Postgres ───────────────────────────────────────────────────────────────────────────

_INV_COLS = "code, trade_date, investor, source, venue, net_qty, net_value, quality, received_at"
_UPSERT_INV = """
INSERT INTO {table} ({key}, trade_date, investor, source, venue, net_qty, net_value, unit, quality,
    received_at, loaded_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, 'krw', %s, %s, %s)
ON CONFLICT ({key}, trade_date, investor, source, venue) DO UPDATE SET
    net_qty = EXCLUDED.net_qty, net_value = EXCLUDED.net_value, quality = EXCLUDED.quality,
    received_at = EXCLUDED.received_at, loaded_by = EXCLUDED.loaded_by
"""
_STOCK_TABLE = "prv_flows.stock_investor_daily"
_MARKET_TABLE = "prv_flows.market_investor_daily"


def _inv_row(r: tuple[Any, ...]) -> StoredInvestorDay:
    return StoredInvestorDay(
        InvestorDay(
            code=r[0],
            date=r[1],
            investor=Investor(r[2]),
            source=r[3],
            venue=r[4],
            net_qty=to_int(r[5]),
            net_value=to_int(r[6]),
            quality=Quality(r[7]),
        ),
        r[8],
    )


def _inv_params(r: InvestorDay, received_at: datetime | None, loaded_by: str) -> tuple[Any, ...]:
    return (
        r.code, r.date, r.investor.value, r.source, r.venue, r.net_qty, r.net_value,
        r.quality.value, received_at, loaded_by,
    )  # fmt: skip


class PgFlowsRepo:
    """`FlowsRepo` 의 Postgres 구현."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgFlowsRepo()"

    def upsert_investor_days(
        self,
        rows: Sequence[InvestorDay],
        *,
        revise: bool,
        loaded_by: str,
        now: datetime,
        received_at: datetime | None = None,
    ) -> RevisionReport:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        require_aware("now", now)
        if not rows:
            return RevisionReport(0, 0, ())
        codes = sorted({r.code for r in rows})
        dates = sorted({r.date for r in rows})
        with self._connect() as conn:
            # 이미 있는 행을 잠그고 판단한다(같은 키를 동시에 쓰는 작업은 선점 ops.data_claim 이
            # 막는다 — 이 잠금은 그 위의 한 겹)
            existing = [
                _inv_row(r)
                for r in conn.execute(
                    f"SELECT {_INV_COLS} FROM {_STOCK_TABLE} "  # noqa: S608 — 상수 표·열
                    "WHERE code = ANY(%s) AND trade_date = ANY(%s) FOR UPDATE".encode(),
                    (codes, dates),
                )
            ]
            plan = plan_investor_upsert(rows, existing, revise=revise, now=now)
            with conn.cursor() as cur:
                if plan.delete:
                    cur.executemany(
                        f"DELETE FROM {_STOCK_TABLE} WHERE code = %s AND trade_date = %s "  # noqa: S608
                        "AND investor = %s AND source = %s AND venue = %s".encode(),
                        [
                            (d.code, d.date, d.investor.value, d.source, d.venue)
                            for d in plan.delete
                        ],
                    )
                if plan.write:
                    cur.executemany(
                        _UPSERT_INV.format(table=_STOCK_TABLE, key="code").encode(),
                        [_inv_params(r, received_at, loaded_by) for r in plan.write],
                    )
                if plan.revisions:
                    cur.executemany(
                        b"INSERT INTO prv_flows.investor_revision (code, trade_date, investor, "
                        b"venue, est_value, est_ts, final_value, final_source, diff, revised_at) "
                        b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        b"ON CONFLICT (code, trade_date, investor, venue) DO UPDATE SET "
                        b"est_value = EXCLUDED.est_value, est_ts = EXCLUDED.est_ts, "
                        b"final_value = EXCLUDED.final_value, final_source = "
                        b"EXCLUDED.final_source, "
                        b"diff = EXCLUDED.diff, revised_at = EXCLUDED.revised_at",
                        [
                            (v.code, v.trade_date, v.investor.value, v.venue, v.est_value,
                             v.est_ts, v.final_value, v.final_source, v.diff, v.revised_at)
                            for v in plan.revisions
                        ],
                    )  # fmt: skip
        return RevisionReport(len(plan.write), plan.skipped, plan.revisions)

    def _range(
        self, table: str, key: str, codes: Collection[str] | None, start: date, end: date
    ) -> list[InvestorDay]:
        wanted = code_filter(codes)
        if wanted == []:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {key}, trade_date, investor, source, venue, net_qty, net_value, quality, "  # noqa: S608
                f"received_at FROM {table} WHERE trade_date BETWEEN %s AND %s "
                f"AND (%s::text[] IS NULL OR {key} = ANY(%s::text[])) "
                f"ORDER BY {key}, trade_date, investor".encode(),
                (start, end, wanted, wanted),
            ).fetchall()
        return [_inv_row(r).row for r in rows]

    def _window_start(self, table: str, end: date, n: int) -> date | None:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        with self._connect() as conn:
            days = [
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT trade_date FROM {table} WHERE trade_date <= %s "  # noqa: S608
                    "ORDER BY trade_date DESC LIMIT %s".encode(),
                    (end, n),
                )
            ]
        return days[-1] if days else None

    def window(
        self, codes: Collection[str] | None, end: date, n: int
    ) -> dict[str, list[InvestorDay]]:
        start = self._window_start(_STOCK_TABLE, end, n)
        if start is None:
            return {}
        return best_investor_days(self._range(_STOCK_TABLE, "code", codes, start, end))

    def days(
        self, codes: Collection[str] | None, start: date, end: date
    ) -> dict[str, list[InvestorDay]]:
        return best_investor_days(self._range(_STOCK_TABLE, "code", codes, start, end))

    def upsert_market_days(
        self, rows: Sequence[InvestorDay], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                _UPSERT_INV.format(table=_MARKET_TABLE, key="market_code").encode(),
                [_inv_params(r, received_at, loaded_by) for r in rows],
            )
        return len(rows)

    def market_days(self, end: date, n: int) -> dict[str, list[InvestorDay]]:
        start = self._window_start(_MARKET_TABLE, end, n)
        if start is None:
            return {}
        return best_investor_days(self._range(_MARKET_TABLE, "market_code", None, start, end))

    def put_intraday(
        self,
        rows: Sequence[IntradayInvestor],
        *,
        loaded_by: str,
        received_at: datetime | None = None,
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_flows.investor_intraday (code, ts, investor, venue, net_qty, "
                b"net_value, rank, source, quality, received_at, loaded_by) "
                b"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (code, ts, investor, venue) DO UPDATE SET net_qty = "
                b"EXCLUDED.net_qty, "
                b"net_value = EXCLUDED.net_value, rank = EXCLUDED.rank, source = EXCLUDED.source, "
                b"quality = EXCLUDED.quality, received_at = EXCLUDED.received_at, "
                b"loaded_by = EXCLUDED.loaded_by",
                [
                    (q.code, q.ts, q.investor.value, q.venue, q.net_qty, q.net_value, q.rank,
                     q.source, q.quality.value, received_at, loaded_by)
                    for q in rows
                ],
            )  # fmt: skip
        return len(rows)

    def intraday(self, start: datetime, end: datetime) -> list[IntradayInvestor]:
        require_aware("start", start)
        require_aware("end", end)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, ts, investor, venue, net_value, net_qty, rank, source, quality "
                b"FROM prv_flows.investor_intraday WHERE ts >= %s AND ts < %s "
                b"ORDER BY ts, code, investor, venue",
                (start, end),
            ).fetchall()
        return [
            IntradayInvestor(
                code=r[0], ts=r[1], investor=Investor(r[2]), venue=r[3], net_value=to_int(r[4]),
                net_qty=to_int(r[5]), rank=r[6], source=r[7], quality=Quality(r[8]),
            )
            for r in rows
        ]  # fmt: skip

    def revisions(self, day: date) -> list[Revision]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT code, trade_date, investor, venue, est_value, est_ts, final_value, "
                b"final_source, diff, revised_at FROM prv_flows.investor_revision "
                b"WHERE trade_date = %s ORDER BY code, investor, venue",
                (day,),
            ).fetchall()
        return [
            Revision(
                code=r[0], trade_date=r[1], investor=Investor(r[2]), venue=r[3],
                est_value=to_int(r[4]), est_ts=r[5], final_value=to_int(r[6]), final_source=r[7],
                diff=to_int(r[8]), revised_at=r[9],
            )
            for r in rows
        ]  # fmt: skip

    def put_checks(self, rows: Sequence[LedgerCheck]) -> int:
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                b"INSERT INTO prv_flows.ledger_check (domain, trade_date, code, check_id, "
                b"residual, detail, checked_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                b"ON CONFLICT (domain, trade_date, code, check_id) DO UPDATE SET "
                b"residual = EXCLUDED.residual, detail = EXCLUDED.detail, "
                b"checked_at = EXCLUDED.checked_at",
                [
                    (c.domain, c.trade_date, c.code, c.check_id, c.residual, Jsonb(dict(c.detail)),
                     c.checked_at)
                    for c in rows
                ],
            )  # fmt: skip
        return len(rows)

    def checks(self, day: date, domain: str | None = None) -> list[LedgerCheck]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT domain, trade_date, code, check_id, residual, detail, checked_at "
                b"FROM prv_flows.ledger_check WHERE trade_date = %s "
                b"AND (%s::text IS NULL OR domain = %s::text) ORDER BY domain, code, check_id",
                (day, domain, domain),
            ).fetchall()
        return [
            LedgerCheck(
                domain=r[0], trade_date=r[1], code=r[2], check_id=r[3], residual=to_float(r[4]),
                detail=dict(r[5] or {}), checked_at=r[6],
            )
            for r in rows
        ]  # fmt: skip
