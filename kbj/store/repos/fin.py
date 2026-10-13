"""재무 저장소 — 0011 `pub_fin.*`(공개 — 원천 DART).

근거: docs/p4_design.md §1.1·§3.4·§4.5·§4.6.

공개 작업(`fin.quarterly`·`fin.backfill`·`reports.dart_excel`)과 로그인 작업(`fin.valuation_band` —
PIT 읽기)·
API 가 쓴다. `pub_*` 만 다룬다(계약 ⑬ — 공개 작업은 이 모듈이나 `kbj.store.repos.public` 에서
가져온다).

- `quarters(corp, upto_rcept=t)` 는 **PIT 읽기**다: 정기보고서 접수일(rcept_dt) ≤ t 인 분기만 —
  접수일을
  모르는 행은 빠진다(D-P4-11, 미래 참조 0).
- 원시 행(`statement_raw`)은 같은 키를 다시 받으면 덮는다(정정 보고서 — 접수번호가 큰 쪽이 최신).
- 리포트는 (종목, 만든 날) 키. `prune_reports(keep=4)` 는 종목마다 최근 4개만 남긴다(ops.nightly).
- 분기 행의 층(D-P4-5): **주요계정 층(`tier='main'` — fnlttMultiAcnt, 전 상장사 묶음) 행은 같은 키의
  전체 재무제표 층(`full`) 행을 덮지 않는다** — 상세 대상 회사가 주요계정 묶음에 함께 들어 있어도
  D&A·EBITDA 등 전체 재무제표 값이 지워지지 않게. full 은 main·full 을 덮고, main 은 main 만 덮는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final, Protocol

from kbj.core.fin_rows import QuarterRow, RatioRow, ReportRow, SegmentRow, StatementRaw
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, select, upsert

__all__ = ["FinRepo", "MemoryFinRepo", "PgFinRepo"]

RAW: Final = Table(
    "pub_fin.statement_raw",
    StatementRaw,
    key=("corp_code", "bsns_year", "reprt_code", "fs_div"),
    extra=("n_rows", "received_at", "loaded_by"),
    json_cols=frozenset({"rows"}),
)
# 덮어쓰기 조건 — main 은 full 을 덮지 않는다(모듈 머리말)
_QUARTER_TIER_GUARD: Final = "cur.tier = 'main' OR EXCLUDED.tier = 'full'"

QUARTER: Final = Table(
    "pub_fin.quarterly",
    QuarterRow,
    key=("corp_code", "fiscal_year", "fiscal_q", "fs_div"),
    extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"reported_3m", "notes"}),
)
RATIO: Final = Table(
    "pub_fin.ratio",
    RatioRow,
    key=("corp_code", "fiscal_year", "fiscal_q"),
    extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"na_reasons", "notes"}),
)
REPORT: Final = Table(
    "pub_fin.report",
    ReportRow,
    key=("stock_code", "built_on"),
    extra=("xlsx_sha256", "loaded_by"),
    json_cols=frozenset({"payload", "warnings"}),
)
SEGMENT: Final = Table(
    "pub_fin.segment",
    SegmentRow,
    key=("corp_code", "bsns_year", "segment"),
    extra=("received_at", "loaded_by"),
    json_cols=frozenset({"amounts"}),
)


class FinRepo(Protocol):
    def put_raw(self, raw: StatementRaw, *, loaded_by: str, now: datetime) -> int: ...

    def raw(
        self, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str
    ) -> StatementRaw | None: ...

    def raw_keys(self, corp_code: str) -> set[tuple[int, str, str]]:
        """이미 받은 (연도, 보고서 코드, CFS/OFS) — 백필이 건너뛴다."""
        ...

    def upsert_quarters(self, rows: Sequence[QuarterRow], *, loaded_by: str, now: datetime) -> int:
        """같은 키는 덮는다 — 단 `tier='main'` 행은 있는 `full` 행을 덮지 않는다(모듈 머리말)."""
        ...

    def quarters(
        self, corp_code: str, *, fs_div: str | None = None, upto_rcept: date | None = None
    ) -> list[QuarterRow]:
        """(연도, 분기, fs_div) 오름차순. `upto_rcept` 를 주면 rcept_dt ≤ 그날만(PIT)."""
        ...

    def quarter_corps(self) -> set[str]:
        """분기 재무가 있는 회사."""
        ...

    def upsert_ratios(self, rows: Sequence[RatioRow], *, loaded_by: str, now: datetime) -> int: ...

    def ratios(self, corp_code: str) -> list[RatioRow]:
        """(연도, 분기) 오름차순."""
        ...

    def put_report(self, row: ReportRow, *, loaded_by: str) -> int: ...

    def report(self, stock_code: str, *, built_on: date | None = None) -> ReportRow | None:
        """built_on 을 안 주면 가장 최근 것."""
        ...

    def report_dates(self, stock_code: str) -> list[date]:
        """만든 날 오름차순."""
        ...

    def prune_reports(self, keep: int = 4) -> int:
        """종목마다 최근 `keep` 개만 남긴다. 지운 행 수."""
        ...

    def put_segments(self, rows: Sequence[SegmentRow], *, loaded_by: str, now: datetime) -> int: ...

    def segments(self, corp_code: str, bsns_year: int | None = None) -> list[SegmentRow]: ...


def _base(loaded_by: str, now: datetime | None) -> dict[str, object]:
    require_loaded_by(loaded_by)
    if now is not None:
        require_aware("now", now)
    return {"received_at": now, "computed_at": now, "loaded_by": loaded_by}


def _ext(table: Table[Any], base: dict[str, object], row: Any) -> dict[str, object]:
    out = {c: base[c] for c in table.extra if c in base}
    if table is RAW:
        out["n_rows"] = row.n_rows
    if table is REPORT:
        out["xlsx_sha256"] = row.xlsx_sha256
    return out


class PgFinRepo:
    """`FinRepo` 의 Postgres 구현."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgFinRepo()"

    def _put_each(self, table: Table[Any], rows: Sequence[Any], base: dict[str, object]) -> int:
        if not rows:
            return 0
        with self._connect() as conn:
            if table is RAW or table is REPORT:  # 행마다 다른 extra(n_rows·sha256)
                return sum(upsert(conn, table, [r], _ext(table, base, r)) for r in rows)
            guard = _QUARTER_TIER_GUARD if table is QUARTER else None
            return upsert(conn, table, rows, _ext(table, base, None), update_where=guard)

    def _get(
        self,
        table: Table[Any],
        where: str,
        params: Sequence[object],
        order: str = "",
        limit: int | None = None,
    ) -> list[Any]:
        with self._connect() as conn:
            return select(conn, table, where, params, order, limit)

    def put_raw(self, raw: StatementRaw, *, loaded_by: str, now: datetime) -> int:
        return self._put_each(RAW, [raw], _base(loaded_by, now))

    def raw(
        self, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str
    ) -> StatementRaw | None:
        got = self._get(
            RAW,
            "corp_code = %s AND bsns_year = %s AND reprt_code = %s AND fs_div = %s",
            (corp_code, bsns_year, reprt_code, fs_div),
        )
        return got[0] if got else None

    def raw_keys(self, corp_code: str) -> set[tuple[int, str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT bsns_year, reprt_code, fs_div FROM pub_fin.statement_raw "
                b"WHERE corp_code = %s",
                (corp_code,),
            ).fetchall()
        return {(int(r[0]), r[1], r[2]) for r in rows}

    def upsert_quarters(self, rows: Sequence[QuarterRow], *, loaded_by: str, now: datetime) -> int:
        return self._put_each(QUARTER, rows, _base(loaded_by, now))

    def quarters(
        self, corp_code: str, *, fs_div: str | None = None, upto_rcept: date | None = None
    ) -> list[QuarterRow]:
        return self._get(
            QUARTER,
            "corp_code = %s AND (%s::text IS NULL OR fs_div = %s) "
            "AND (%s::date IS NULL OR (rcept_dt IS NOT NULL AND rcept_dt <= %s))",
            (corp_code, fs_div, fs_div, upto_rcept, upto_rcept),
            "fiscal_year, fiscal_q, fs_div",
        )

    def quarter_corps(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(b"SELECT DISTINCT corp_code FROM pub_fin.quarterly").fetchall()
        return {r[0] for r in rows}

    def upsert_ratios(self, rows: Sequence[RatioRow], *, loaded_by: str, now: datetime) -> int:
        return self._put_each(RATIO, rows, _base(loaded_by, now))

    def ratios(self, corp_code: str) -> list[RatioRow]:
        return self._get(RATIO, "corp_code = %s", (corp_code,), "fiscal_year, fiscal_q")

    def put_report(self, row: ReportRow, *, loaded_by: str) -> int:
        return self._put_each(REPORT, [row], _base(loaded_by, None))

    def report(self, stock_code: str, *, built_on: date | None = None) -> ReportRow | None:
        got = self._get(
            REPORT,
            "stock_code = %s AND (%s::date IS NULL OR built_on = %s)",
            (stock_code, built_on, built_on),
            "built_on DESC",
            1,
        )
        return got[0] if got else None

    def report_dates(self, stock_code: str) -> list[date]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT built_on FROM pub_fin.report WHERE stock_code = %s ORDER BY built_on",
                (stock_code,),
            ).fetchall()
        return [r[0] for r in rows]

    def prune_reports(self, keep: int = 4) -> int:
        if keep < 1:
            raise ValueError("keep 은 1 이상")
        with self._connect() as conn:
            cur = conn.execute(
                b"DELETE FROM pub_fin.report r USING (SELECT stock_code, built_on, row_number() "
                b"OVER (PARTITION BY stock_code ORDER BY built_on DESC) AS n "
                b"FROM pub_fin.report) o "
                b"WHERE r.stock_code = o.stock_code AND r.built_on = o.built_on AND o.n > %s",
                (keep,),
            )
            return cur.rowcount

    def put_segments(self, rows: Sequence[SegmentRow], *, loaded_by: str, now: datetime) -> int:
        return self._put_each(SEGMENT, rows, _base(loaded_by, now))

    def segments(self, corp_code: str, bsns_year: int | None = None) -> list[SegmentRow]:
        return self._get(
            SEGMENT,
            "corp_code = %s AND (%s::int IS NULL OR bsns_year = %s)",
            (corp_code, bsns_year, bsns_year),
            "bsns_year, segment",
        )


class MemoryFinRepo:
    """`FinRepo` 의 메모리 구현 — Pg 와 같은 규칙."""

    def __init__(self) -> None:
        self.raw_t = MemoryTable(RAW)
        self.quarter_t = MemoryTable(QUARTER)
        self.ratio_t = MemoryTable(RATIO)
        self.report_t = MemoryTable(REPORT)
        self.segment_t = MemoryTable(SEGMENT)

    def __repr__(self) -> str:
        return "MemoryFinRepo()"

    @staticmethod
    def _put(t: MemoryTable[Any], rows: Sequence[Any], base: dict[str, object]) -> int:
        n = 0
        for r in rows:
            n += t.upsert([r], _ext(t.table, base, r))
        return n

    def put_raw(self, raw: StatementRaw, *, loaded_by: str, now: datetime) -> int:
        return self._put(self.raw_t, [raw], _base(loaded_by, now))

    def raw(
        self, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str
    ) -> StatementRaw | None:
        return self.raw_t.get((corp_code, bsns_year, reprt_code, fs_div))

    def raw_keys(self, corp_code: str) -> set[tuple[int, str, str]]:
        return {
            (r.bsns_year, r.reprt_code, r.fs_div)
            for r in self.raw_t.all()
            if r.corp_code == corp_code
        }

    def upsert_quarters(self, rows: Sequence[QuarterRow], *, loaded_by: str, now: datetime) -> int:
        base = _base(loaded_by, now)
        n = 0
        for r in rows:
            old = self.quarter_t.get((r.corp_code, r.fiscal_year, r.fiscal_q, r.fs_div))
            if old is not None and old.tier == "full" and r.tier == "main":
                n += 1  # Pg 와 같다 — 충돌 행은 세되 덮지 않는다(_QUARTER_TIER_GUARD)
                continue
            n += self.quarter_t.upsert([r], _ext(self.quarter_t.table, base, r))
        return n

    def quarters(
        self, corp_code: str, *, fs_div: str | None = None, upto_rcept: date | None = None
    ) -> list[QuarterRow]:
        return self.quarter_t.where(
            lambda r: (
                r.corp_code == corp_code
                and (fs_div is None or r.fs_div == fs_div)
                and (upto_rcept is None or r.known_by(upto_rcept))
            )
        )

    def quarter_corps(self) -> set[str]:
        return {r.corp_code for r in self.quarter_t.all()}

    def upsert_ratios(self, rows: Sequence[RatioRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.ratio_t, rows, _base(loaded_by, now))

    def ratios(self, corp_code: str) -> list[RatioRow]:
        return self.ratio_t.where(lambda r: r.corp_code == corp_code)

    def put_report(self, row: ReportRow, *, loaded_by: str) -> int:
        return self._put(self.report_t, [row], _base(loaded_by, None))

    def report(self, stock_code: str, *, built_on: date | None = None) -> ReportRow | None:
        rows = self.report_t.where(
            lambda r: r.stock_code == stock_code and (built_on is None or r.built_on == built_on)
        )
        return max(rows, key=lambda r: r.built_on) if rows else None

    def report_dates(self, stock_code: str) -> list[date]:
        return [r.built_on for r in self.report_t.all() if r.stock_code == stock_code]

    def prune_reports(self, keep: int = 4) -> int:
        if keep < 1:
            raise ValueError("keep 은 1 이상")
        by_code: dict[str, list[date]] = {}
        for r in self.report_t.all():
            by_code.setdefault(r.stock_code, []).append(r.built_on)
        doomed = {(c, d) for c, ds in by_code.items() for d in sorted(ds, reverse=True)[keep:]}
        return self.report_t.remove(lambda r: (r.stock_code, r.built_on) in doomed)

    def put_segments(self, rows: Sequence[SegmentRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.segment_t, rows, _base(loaded_by, now))

    def segments(self, corp_code: str, bsns_year: int | None = None) -> list[SegmentRow]:
        return self.segment_t.where(
            lambda r: r.corp_code == corp_code and (bsns_year is None or r.bsns_year == bsns_year)
        )
