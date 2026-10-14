"""GEX 수집 쓰기 싱크 + 읽기 — GX `data/store.py:PostgresSink` 의 수집 경로 몫(P9 GXI, ADR 0036).

- 쓰기 + 디스크 스풀은 `kbj.services.recorder.store.SpooledGexStore`(P7 ING — GX 싱크의 '쓰기 +
  스풀' 을 KBJ 로 다시 세운 것) 위에 수집 표를 더한다(스풀 코드 한 벌 — 두 벌 금지). 메서드 이름은
  GX 싱크 그대로: poller `Sink`(`write_chain`·`write_futures`·`write_expiries`·`write_raw`·
  `write_quarantine`·`write_health`), 분봉(`write_minute_bars`), 마스터(`write_master`), KRX 일별
  (`write_krx_futures`·`write_krx_options`).
- 표 정의·행 함수는 `kbj.store.repos.gex_ingest`(+ 체인·선물·만기는 `kbj.store.repos.gex`).
- 읽기·판정 교체(`krx_loaded`·`krx_option_listing`·`series_dates`…`fut_tick_count`·`gap_reports`·
  `replace_gap_report`)는 `PgGexIngestRepo` 로 넘긴다 — 스풀·백오프와 상관없이 DB 를 부르고 실패는
  `StoreError`(GX 와 같다).
- health 는 두 가지를 받는다: poller 레코드(`kbj.core.gex.records.HealthEvent` — 수집 사건)와 서비스
  이벤트(`kbj.services.runtime.HealthEvent` — 스풀·단계 오류). 둘 다 `ops.health_events` 한 표.
- 투자자별(`write_investor`)은 없다 — `prv_gex.investor_flow` 의 쓰기 주인은 flows.market_minute
  (ADR 0019, D-P7-5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Final

import psycopg

from kbj.config.settings import Settings
from kbj.core.gex.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    MinuteBarRecord,
    QuarantineRecord,
)
from kbj.core.gex.records import HealthEvent as RecordHealthEvent
from kbj.data.private.kis.master import MasterRow
from kbj.data.private.krx.models import KrxFuturesDaily, KrxOptionDaily
from kbj.services.recorder.store import SpooledGexStore, service_health_row
from kbj.services.runtime import HealthEvent, Tagger
from kbj.store.db import StoreError, connect
from kbj.store.repos._common import ConnFactory
from kbj.store.repos.gex import (
    CHAIN_SNAPSHOTS,
    FUT_BOARD,
    HEALTH_EVENTS,
    SERIES_EXPIRIES,
    GxTable,
    attr_row,
    health_row,
)
from kbj.store.repos.gex_ingest import (
    INGEST_TABLES,
    KRX_FUT_DAILY,
    KRX_OPT_DAILY,
    MASTER_SNAPSHOTS,
    MINUTE_BARS,
    QUARANTINE,
    CollectionGapRecord,
    CollectionReportRecord,
    KrxKind,
    PgGexIngestRepo,
    krx_futures_row,
    krx_loadable,
    krx_option_row,
    master_row,
    quarantine_row,
)
from kbj.store.spool import DiskSpool

__all__ = ["GexIngestStore", "open_ingest_store"]

Row = tuple[Any, ...]
_SESSIONS: Final = frozenset({"day", "night"})


def _tag(trade_date: date | None, session: str | None) -> tuple[date | None, str | None]:
    if trade_date is None or session not in _SESSIONS:
        return None, None
    return trade_date, session


class GexIngestStore(SpooledGexStore):
    """수집 표 쓰기(스풀) + 읽기. `SpooledGexStore` 의 원문·체결·세션 기록·health 쓰기도 그대로
    있다."""

    tables_by_name: ClassVar[Mapping[str, GxTable]] = {
        **SpooledGexStore.tables_by_name,
        **{t.name: t for t in INGEST_TABLES},
    }

    def __init__(self, conn_factory: ConnFactory, **kwargs: Any) -> None:
        super().__init__(conn_factory, **kwargs)
        self.reads = PgGexIngestRepo(conn_factory)

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return f"GexIngestStore(service={self.service!r})"

    # ── poller Sink ──

    def _attrs(self, table: GxTable, rows: Sequence[Any]) -> None:
        if rows:
            first = rows[0]
            tag = _tag(first.trade_date, first.session)
            self._write(table, [attr_row(table, r) for r in rows], tag)

    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        self._attrs(CHAIN_SNAPSHOTS, rows)

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self._attrs(FUT_BOARD, rows)

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None:
        self._attrs(SERIES_EXPIRIES, rows)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        if rows:
            tag = _tag(rows[0].trade_date, rows[0].session)
            self._write(QUARANTINE, [quarantine_row(r) for r in rows], tag)

    def write_health(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        events: Sequence[RecordHealthEvent | HealthEvent],
        *,
        tagger: Tagger | None = None,
    ) -> None:
        """poller 레코드 health 와 서비스 health 를 함께 받는다(GX `health_row` 와 같다)."""
        if not events:
            return
        tg = tagger if tagger is not None else self._tagger
        out = [
            health_row(e) if isinstance(e, RecordHealthEvent) else service_health_row(e, tg)
            for e in events
        ]
        self._write(HEALTH_EVENTS, out, (out[0][1], out[0][2]))

    # ── 분봉·마스터·KRX 일별 ──

    def write_minute_bars(self, rows: Sequence[MinuteBarRecord]) -> None:
        self._attrs(MINUTE_BARS, rows)

    def write_master(
        self, rows: Sequence[MasterRow], *, ts: datetime, trade_date: date, session: str
    ) -> None:
        """PRE_DAY 는 (그날, day), PRE_NIGHT 는 (next_trading_day, night) 로 부른다."""
        out = [master_row(r, ts=ts, trade_date=trade_date, session=session) for r in rows]
        if out:
            self._write(MASTER_SNAPSHOTS, out, _tag(trade_date, session))

    def write_krx_futures(self, rows: Sequence[KrxFuturesDaily], *, ts: datetime) -> int:
        """코스피200 계열만 쓰고 쓴 행 수를 돌려준다(나머지는 건너뜀). ts = 조회 시각."""
        keep = [krx_futures_row(r, ts) for r in rows if krx_loadable(r)]
        if keep:
            self._write(KRX_FUT_DAILY, keep, _tag(keep[0][0], keep[0][1]))
        return len(keep)

    def write_krx_options(self, rows: Sequence[KrxOptionDaily], *, ts: datetime) -> int:
        """코스피200 계열만 쓰고 쓴 행 수를 돌려준다. 야간 IMP_VOLT 는 NULL 로 남는다."""
        keep = [krx_option_row(r, ts) for r in rows if krx_loadable(r)]
        if keep:
            self._write(KRX_OPT_DAILY, keep, _tag(keep[0][0], keep[0][1]))
        return len(keep)

    # ── 읽기·판정 교체(스풀 없이 — PgGexIngestRepo) ──

    def krx_loaded(self, kind: KrxKind, trade_date: date) -> bool:
        return self.reads.krx_loaded(kind, trade_date)

    def krx_option_listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]:
        return self.reads.krx_option_listing(trade_date)

    def series_dates(
        self, trade_date: date, session: str
    ) -> list[tuple[str, str, str, date, datetime]]:
        return self.reads.series_dates(trade_date, session)

    def board_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]:
        return self.reads.board_times(trade_date, session)

    def fut_board_times(self, trade_date: date, session: str) -> list[datetime]:
        return self.reads.fut_board_times(trade_date, session)

    def underlying_times(self, trade_date: date, session: str) -> list[datetime]:
        return self.reads.underlying_times(trade_date, session)

    def investor_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]:
        return self.reads.investor_times(trade_date, session)

    def fill1_times(self, trade_date: date, session: str) -> list[tuple[str, datetime]]:
        return self.reads.fill1_times(trade_date, session)

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        return self.reads.ws_connection_events(start, end, kinds)

    def minute_bar_times(
        self, trade_date: date, session: str
    ) -> list[tuple[str, datetime, int | None]]:
        return self.reads.minute_bar_times(trade_date, session)

    def fut_tick_minutes(self, trade_date: date, session: str) -> list[tuple[str, datetime, int]]:
        return self.reads.fut_tick_minutes(trade_date, session)

    def fut_tick_count(self, start: datetime, end: datetime) -> int:
        return self.reads.fut_tick_count(start, end)

    def gap_reports(self, first: date, last: date) -> list[CollectionReportRecord]:
        return self.reads.gap_reports(first, last)

    def replace_gap_report(
        self,
        trade_date: date,
        session: str,
        gaps: Sequence[CollectionGapRecord],
        reports: Sequence[CollectionReportRecord],
    ) -> None:
        self.reads.replace_gap_report(trade_date, session, gaps, reports)


def open_ingest_store(
    settings: Settings,
    *,
    service: str,
    tagger: Tagger | None = None,
    spool: bool = True,
    on_health: Callable[[HealthEvent], None] | None = None,
) -> GexIngestStore:
    """서비스·작업 진입점용: `KBJ_DATABASE_URL` 연결 공장 + `spool_dir/<서비스>` 디스크 큐(GX
    `PostgresSink.from_settings(service=…, spool=True)` 자리). 접속은 쓸 때마다 짧게 연다."""
    if settings.database_url is None:
        raise StoreError("KBJ_DATABASE_URL 이 없다")
    name = service.replace("-", "_")

    def conn() -> psycopg.Connection[Any]:
        return connect(settings, service=name)

    disk = (
        DiskSpool(Path(settings.spool_dir) / service, max_bytes=settings.spool_max_mb << 20)
        if spool
        else None
    )
    return GexIngestStore(conn, service=service, spool=disk, tagger=tagger, on_health=on_health)
