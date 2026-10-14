"""GX `data.store` 이름 → KBJ 정본(시험 전용 — P9 GXI 승격 시험이 `store.<이름>` 을 그대로 쓰게).

GX 수집 싱크(`PostgresSink`)는 KBJ 에서 `kbj.services.gex_poller.store.GexIngestStore`(쓰기 + 스풀 —
`SpooledGexStore` 위) + `kbj.store.repos.gex_ingest.PgGexIngestRepo`(읽기)로 나뉘었다. 표·행 함수·
레코드는 `kbj.store.repos.gex`·`gex_ingest`·`kbj.core.gex.records` 에 있다. 이 모듈은 이름만 잇는다
—
값·동작을 바꾸지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from kbj.core.gex.records import (
    FuturesTickRecord,
    MinuteBarRecord,
    OptionTickRecord,
    SessionLogRecord,
)
from kbj.core.gex.records import HealthEvent as RecordHealthEvent
from kbj.services.gex_poller.store import GexIngestStore
from kbj.services.recorder.store import service_health_row
from kbj.services.runtime import HealthEvent as ServiceHealthEvent
from kbj.store.db import StoreError
from kbj.store.repos.gex import (
    CHAIN_SNAPSHOTS,
    ENGINE_TABLES,
    FUT_BOARD,
    FUT_TICKS,
    HEALTH_EVENTS,
    INVESTOR_FLOW,
    LEVELS,
    METRICS,
    OI_CHANGES,
    OPT_TICKS,
    OPTION_IV,
    RAW_MESSAGES,
    SERIES_EXPIRIES,
    SESSION_LOG,
    STRIKE_GEX,
    GxTable,
    attr_row,
    clean_text,
    clean_texts,
    digest,
    raw_row,
    session_log_row,
    to_json,
)
from kbj.store.repos.gex import health_row as record_health_row
from kbj.store.repos.gex_ingest import (
    COLLECTION_GAPS,
    COLLECTION_REPORTS,
    KRX_FAMILIES,
    KRX_FUT_DAILY,
    KRX_MONTHLY_FAMILY,
    KRX_OPT_DAILY,
    KRX_TABLES,
    MASTER_SNAPSHOTS,
    MINUTE_BARS,
    QUARANTINE,
    CollectionGapRecord,
    CollectionReportRecord,
    GapStatus,
    KrxKind,
    PgGexIngestRepo,
    gap_row,
    krx_futures_row,
    krx_loadable,
    krx_option_row,
    master_row,
    quarantine_row,
    report_row,
)

__all__ = [
    "CHAIN_SNAPSHOTS",
    "COLLECTION_GAPS",
    "COLLECTION_REPORTS",
    "ENGINE_TABLES",
    "FUT_BOARD",
    "FUT_TICKS",
    "HEALTH_EVENTS",
    "INVESTOR_FLOW",
    "KRX_FAMILIES",
    "KRX_FUT_DAILY",
    "KRX_MONTHLY_FAMILY",
    "KRX_OPT_DAILY",
    "KRX_TABLES",
    "LEVELS",
    "MASTER_SNAPSHOTS",
    "METRICS",
    "MINUTE_BARS",
    "OI_CHANGES",
    "OPTION_IV",
    "OPT_TICKS",
    "QUARANTINE",
    "RAW_MESSAGES",
    "SERIES_EXPIRIES",
    "SESSION_LOG",
    "STRIKE_GEX",
    "TABLES",
    "CollectionGapRecord",
    "CollectionReportRecord",
    "FuturesTickRecord",
    "GapStatus",
    "GexIngestStore",
    "KrxKind",
    "MinuteBarRecord",
    "OptionTickRecord",
    "PgGexIngestRepo",
    "Row",
    "SessionLogRecord",
    "StoreError",
    "Table",
    "Tag",
    "attr_row",
    "clean_text",
    "clean_texts",
    "digest",
    "gap_row",
    "health_row",
    "krx_futures_row",
    "krx_loadable",
    "krx_option_row",
    "master_row",
    "quarantine_row",
    "raw_row",
    "report_row",
    "session_log_row",
    "sink",
    "table",
    "to_json",
]

Row = tuple[Any, ...]
Tag = tuple[date | None, str | None]
Tagger = Callable[[datetime], tuple[date | None, str | None]]
Table = GxTable
TABLES: tuple[GxTable, ...] = tuple(GexIngestStore.tables_by_name.values())


def table(name: str, columns: tuple[str, ...], **kw: Any) -> GxTable:
    """GX `Table(이름, 열, …)` 자리 — KBJ `GxTable` 은 스키마를 먼저 받는다(시험용 prv_gex)."""
    return GxTable("prv_gex", name, columns, **kw)


def health_row(ev: RecordHealthEvent | ServiceHealthEvent, tagger: Tagger | None = None) -> Row:
    """GX `health_row` — poller 레코드는 그대로, 서비스(auth) 이벤트는 tagger 로 거래일·세션."""
    if isinstance(ev, RecordHealthEvent):
        return record_health_row(ev)
    return service_health_row(ev, tagger)


def sink(connect: Callable[[], Any], **kw: Any) -> GexIngestStore:
    """GX `PostgresSink(connect=…, service=…, spool=…, on_health=…, …)` 자리 — KBJ 수집 싱크."""
    kw.setdefault("service", "poller")
    return GexIngestStore(connect, **kw)
