"""GEX 수집 저장소 — GX `data/store.py:PostgresSink` 의 수집 표 쓰기·읽기를 KBJ 로(P9 묶음 GXI,
docs/p9_design.md §7.2 GXI-3, ADR 0036).

- 표: `prv_gex.minute_bars`·`krx_fut_daily`·`krx_opt_daily`·`master_snapshots`(0006),
  `ops.quarantine`·`ops.collection_gaps`·`ops.collection_reports`(0006). 체인·선물·만기
  (`chain_snapshots`·`fut_board`·`series_expiries`)는 `kbj.store.repos.gex` 의 표 정의를 그대로
  쓴다.
  열 순서·키·덮어쓰기 규칙은 GX 표 상수와 같다(같은 원문은 같은 행 — 멱등).
- 행 함수(`quarantine_row`·`krx_*_row`·`master_row`·`gap_row`·`report_row`)는 GX 순수 함수 그대로다.
  원천 자료형(KIS 마스터 행·KRX 일별 행)은 `kbj.data` 에 있어 이 모듈이 import 하지 않는다(계약 ⑦) —
  필요한 칸만 프로토콜(`KrxFuturesLike`·`KrxOptionLike`·`MasterRowLike`)로 받는다.
- 무결측 판정 레코드(`CollectionGapRecord`·`CollectionReportRecord`)의 정본은 여기다(GX
  `data.store` 에 있던 것).
- 읽기(`PgGexIngestRepo`): KRX 이어 받기(`krx_loaded`·`krx_option_listing`), 무결측 판정 입력
  (`series_dates`…`fut_tick_count`), 리포트(`gap_reports`), 판정 교체(`replace_gap_report` —
  트랜잭션
  하나). 부를 때마다 짧게 연결을 연다. 실패는 `StoreError`(가린 문구)로 올린다 — 스풀·백오프는 쓰지
  않는다(GX 와 같다: 읽기는 쌓을 수 없고 판정은 다시 계산할 수 있다).
- 쓰기 + 디스크 스풀은 서비스 쪽 싱크(`kbj.services.gex_poller.store.GexIngestStore`)가 이 표
  정의·행 함수로 한다(`kbj.services.recorder.store.SpooledGexStore` 위 — 스풀 코드 한 벌).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, Protocol, Self

import psycopg
from psycopg import sql
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from kbj.core.gex.records import QuarantineRecord
from kbj.store.db import StoreError
from kbj.store.repos._common import ConnFactory
from kbj.store.repos.gex import (
    CHAIN_SNAPSHOTS,
    FUT_BOARD,
    FUT_TICKS,
    HEALTH_EVENTS,
    INVESTOR_FLOW,
    LEVELS,
    METRICS,
    OI_CHANGES,
    RAW_MESSAGES,
    SERIES_EXPIRIES,
    WS_GATEWAY_SERVICE,
    GxTable,
    clean_texts,
    digest,
    to_json,
)

__all__ = [
    "COLLECTION_GAPS",
    "COLLECTION_REPORTS",
    "INGEST_TABLES",
    "KRX_FAMILIES",
    "KRX_FUT_DAILY",
    "KRX_MONTHLY_FAMILY",
    "KRX_OPT_DAILY",
    "KRX_TABLES",
    "MASTER_SNAPSHOTS",
    "MINUTE_BARS",
    "QUARANTINE",
    "CollectionGapRecord",
    "CollectionReportRecord",
    "GapStatus",
    "KrxFuturesLike",
    "KrxKind",
    "KrxOptionLike",
    "MasterRowLike",
    "PgGexIngestRepo",
    "gap_row",
    "krx_futures_row",
    "krx_loadable",
    "krx_option_row",
    "master_row",
    "quarantine_row",
    "report_row",
]

Row = tuple[Any, ...]
Tag = tuple[date | None, str | None]
_SESSIONS: Final = frozenset({"day", "night"})
_SNAP: Final = ("ts", "trade_date", "session")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_TRANSIENT = (psycopg.OperationalError, psycopg.InterfaceError)


# ── 표 명세(GX data/store.py 상수 — 스키마 이름만 더했다) ───────────────────────────────────

QUARANTINE: Final = GxTable(
    "ops", "quarantine",
    (*_SNAP, "source", "tr_id", "key", "payload", "lossy", "error", "digest"),
    key=("ts", "digest"), jsonb=frozenset({"payload"}),
)  # fmt: skip
MINUTE_BARS: Final = GxTable(
    "prv_gex", "minute_bars",
    (*_SNAP, "received_at", "code", "market", "open", "high", "low", "close", "volume",
     "cum_value"),
    key=("code", "market", "ts"),
    upsert=True,  # 적재 때 진행 중이던 마지막 봉은 다시 받으면 고친다
)  # fmt: skip
_KRX_OHLC = ("open", "high", "low", "close", "cmp_prev")
_KRX_ACC = ("acc_trdvol", "acc_trdval", "acc_opnint_qty")
KRX_FUT_DAILY: Final = GxTable(
    "prv_gex", "krx_fut_daily",
    ("trade_date", "session", "isu_cd", "ts", "isu_nm", "prod_nm", "family", "expiry",
     *_KRX_OHLC, "setl_prc", "spot_prc", *_KRX_ACC),
    key=("trade_date", "isu_cd", "session"),
    upsert=True,  # KRX 가 고쳐 다시 내면 최신 값
)  # fmt: skip
KRX_OPT_DAILY: Final = GxTable(
    "prv_gex", "krx_opt_daily",
    ("trade_date", "session", "isu_cd", "ts", "isu_nm", "prod_nm", "family", "expiry",
     "expiry_token", "strike", "cp", *_KRX_OHLC, "imp_volt", "nxtdd_bas_prc", *_KRX_ACC),
    key=("trade_date", "isu_cd", "session"),
    upsert=True,
)  # fmt: skip
MASTER_SNAPSHOTS: Final = GxTable(
    "prv_gex", "master_snapshots",
    ("trade_date", "session", "code", "ts", "kind", "isin", "name", "cp", "strike", "atm_cls",
     "family", "series", "expiry_token", "expiry", "underlying"),
    key=("trade_date", "session", "code"),
    upsert=True,  # 같은 세션에 다시 내려받으면 최신 파일
)  # fmt: skip
COLLECTION_GAPS: Final = GxTable(
    "ops", "collection_gaps",
    ("start_ts", "stream", "end_ts", "trade_date", "session", "expected", "received",
     "detected_at", "detail"),
    key=("stream", "start_ts"),
    upsert=True,  # 같은 공백을 다시 판정하면(끝이 늘어남 등) 고친다
    jsonb=frozenset({"detail"}),
)  # fmt: skip
COLLECTION_REPORTS: Final = GxTable(
    "ops", "collection_reports",
    ("trade_date", "session", "stream", "evaluated_at", "span_start", "span_end", "required",
     "status", "expected", "received", "gaps", "max_gap_s", "detail"),
    key=("trade_date", "session", "stream"),
    upsert=True,  # 같은 세션을 다시 판정하면 최신 판정(보통은 replace_gap_report 가 지우고 쓴다)
    jsonb=frozenset({"detail"}),
)  # fmt: skip

# 수집 경로(poller·분봉·KRX 파생·마스터·무결측)가 쓰는 표 — 쓰기 주인 문구는 docs/p9_design.md GXI
# 기록
INGEST_TABLES: Final[tuple[GxTable, ...]] = (
    CHAIN_SNAPSHOTS,
    FUT_BOARD,
    SERIES_EXPIRIES,
    QUARANTINE,
    MINUTE_BARS,
    KRX_FUT_DAILY,
    KRX_OPT_DAILY,
    MASTER_SNAPSHOTS,
    COLLECTION_GAPS,
    COLLECTION_REPORTS,
)

# KRX 일별은 코스피200 계열만 적재한다(GX 설계 §8) — 코스닥150 위클리는 세션 표기 없이 같은 ISU_CD
# 가
# 두 행씩 와서 (trade_date, isu_cd, session) 키가 겹친다
KRX_FAMILIES: Final = frozenset(
    {"kospi200", "mini_kospi200", "kospi200_weekly_thu", "kospi200_weekly_mon"}
)
KrxKind = Literal["options", "futures"]
KRX_MONTHLY_FAMILY: Final = "kospi200"
KRX_TABLES: Final[dict[KrxKind, GxTable]] = {"options": KRX_OPT_DAILY, "futures": KRX_FUT_DAILY}


def _tag(trade_date: date | None, session: str | None) -> Tag:
    if trade_date is None or session not in _SESSIONS:
        return None, None
    return trade_date, session


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return ts.astimezone(UTC)


# ── 원천 행 모양(kbj.data.private.krx.models — 계약 ⑦ 때문에 프로토콜로) ─────────────────────


class _KrxDailyLike(Protocol):
    @property
    def bas_dd(self) -> date: ...
    @property
    def session(self) -> str | None: ...
    @property
    def isu_cd(self) -> str: ...
    @property
    def isu_nm(self) -> str: ...
    @property
    def prod_nm(self) -> str: ...
    @property
    def family(self) -> str: ...
    @property
    def tdd_opnprc(self) -> Decimal | None: ...
    @property
    def tdd_hgprc(self) -> Decimal | None: ...
    @property
    def tdd_lwprc(self) -> Decimal | None: ...
    @property
    def tdd_clsprc(self) -> Decimal | None: ...
    @property
    def cmpprevdd_prc(self) -> Decimal | None: ...
    @property
    def acc_trdvol(self) -> int | None: ...
    @property
    def acc_trdval(self) -> int | None: ...
    @property
    def acc_opnint_qty(self) -> int | None: ...


class MasterRowLike(Protocol):
    """KIS 마스터 한 줄(`kbj.data.private.kis.master.MasterRow`)의 적재 칸."""

    @property
    def code(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def isin(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def cp(self) -> str: ...
    @property
    def strike(self) -> Decimal | None: ...
    @property
    def moneyness(self) -> str: ...
    @property
    def family(self) -> str: ...
    @property
    def series(self) -> str: ...
    @property
    def expiry_token(self) -> str | None: ...
    @property
    def expiry(self) -> str | None: ...
    @property
    def underlying(self) -> str: ...


class KrxFuturesLike(_KrxDailyLike, Protocol):
    @property
    def expiry(self) -> str | None: ...
    @property
    def setl_prc(self) -> Decimal | None: ...
    @property
    def spot_prc(self) -> Decimal | None: ...


class KrxOptionLike(_KrxDailyLike, Protocol):
    @property
    def expiry(self) -> str: ...
    @property
    def expiry_token(self) -> str: ...
    @property
    def strike(self) -> Decimal: ...
    @property
    def cp(self) -> str: ...
    @property
    def imp_volt(self) -> Decimal | None: ...
    @property
    def nxtdd_bas_prc(self) -> Decimal | None: ...


# ── 행 함수(GX 순수 함수 그대로) ─────────────────────────────────────────────────────────


def quarantine_row(r: QuarantineRecord) -> Row:
    td, ss = _tag(r.trade_date, r.session)
    payload, lossy = to_json(r.payload)
    (tr_id, key, error), lossy_text = clean_texts(r.tr_id, r.key, r.error)
    h = digest(r.source, tr_id, key, payload, error)
    return (r.ts, td, ss, r.source, tr_id, key, payload, lossy or lossy_text, error, h)


def krx_loadable(r: KrxFuturesLike | KrxOptionLike) -> bool:
    """적재 대상인가: 코스피200 계열이고 세션을 안다."""
    return r.family in KRX_FAMILIES and r.session is not None


def krx_futures_row(r: KrxFuturesLike, ts: datetime) -> Row:
    """KRX 선물 일별 → 행. ts = 조회 시각. 적재 대상이 아니면 ValueError."""
    if not krx_loadable(r):
        raise ValueError(f"적재 대상이 아니다: {r.isu_nm!r}")
    return (
        r.bas_dd, r.session, r.isu_cd, _utc(ts), r.isu_nm, r.prod_nm, r.family, r.expiry,
        r.tdd_opnprc, r.tdd_hgprc, r.tdd_lwprc, r.tdd_clsprc, r.cmpprevdd_prc, r.setl_prc,
        r.spot_prc, r.acc_trdvol, r.acc_trdval, r.acc_opnint_qty,
    )  # fmt: skip


def krx_option_row(r: KrxOptionLike, ts: datetime) -> Row:
    """KRX 옵션 일별 → 행. 야간 IMP_VOLT '0.00' 은 모델에서 이미 None(→ NULL)."""
    if not krx_loadable(r):
        raise ValueError(f"적재 대상이 아니다: {r.isu_nm!r}")
    return (
        r.bas_dd, r.session, r.isu_cd, _utc(ts), r.isu_nm, r.prod_nm, r.family, r.expiry,
        r.expiry_token, r.strike, r.cp, r.tdd_opnprc, r.tdd_hgprc, r.tdd_lwprc, r.tdd_clsprc,
        r.cmpprevdd_prc, r.imp_volt, r.nxtdd_bas_prc, r.acc_trdvol, r.acc_trdval,
        r.acc_opnint_qty,
    )  # fmt: skip


def master_row(r: MasterRowLike, *, ts: datetime, trade_date: date, session: str) -> Row:
    """마스터 한 줄 → 행. 선물·스프레드는 cp·strike·atm_cls 가 NULL."""
    if session not in _SESSIONS:
        raise ValueError(f"session 은 day·night: {session!r}")
    return (
        trade_date, session, r.code, _utc(ts), r.kind, r.isin, r.name, r.cp or None, r.strike,
        r.moneyness or None, r.family, r.series, r.expiry_token, r.expiry, r.underlying,
    )  # fmt: skip


# ── 무결측 판정 레코드(GX data/store.py — 정본) ──────────────────────────────────────────


class CollectionGapRecord(BaseModel):
    """`ops.collection_gaps` 한 행(GX 설계 §9). 같은 (stream, start_ts) 를 다시 쓰면 고친다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stream: str
    start_ts: AwareDatetime
    end_ts: AwareDatetime
    trade_date: date
    session: Literal["day", "night"]
    expected: int | None = None
    received: int | None = None
    detected_at: AwareDatetime
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("start_ts", "end_ts", "detected_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_ts < self.start_ts:
            raise ValueError("end_ts < start_ts")
        return self


GapStatus = Literal["ok", "gaps", "unverified"]


class CollectionReportRecord(BaseModel):
    """`ops.collection_reports` 한 행 — 세션 하나·스트림 하나의 무결측 판정(GX 설계 §9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_date: date
    session: Literal["day", "night"]
    stream: str
    evaluated_at: AwareDatetime
    span_start: AwareDatetime
    span_end: AwareDatetime
    required: bool
    status: GapStatus
    expected: int | None = None
    received: int | None = None
    gaps: int = Field(default=0, ge=0)
    max_gap_s: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("evaluated_at", "span_start", "span_end")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.span_end < self.span_start:
            raise ValueError("span_end < span_start")
        return self


def gap_row(r: CollectionGapRecord) -> Row:
    detail, _ = to_json(r.detail)
    return (
        r.start_ts, r.stream, r.end_ts, r.trade_date, r.session, r.expected, r.received,
        r.detected_at, detail,
    )  # fmt: skip


def report_row(r: CollectionReportRecord) -> Row:
    detail, _ = to_json(r.detail)
    (stream,), _ = clean_texts(r.stream)
    return (
        r.trade_date, r.session, stream, r.evaluated_at, r.span_start, r.span_end, r.required,
        r.status, r.expected, r.received, r.gaps, r.max_gap_s, detail,
    )  # fmt: skip


# ── 읽기·판정 교체(Pg) ───────────────────────────────────────────────────────────────────


def _kind(e: psycopg.Error) -> str:
    return f"{type(e).__name__}({e.sqlstate})" if e.sqlstate else type(e).__name__


class PgGexIngestRepo:
    """수집 경로 읽기 + 무결측 판정 교체. 부를 때마다 짧게 연결을 연다(`ConnFactory`).

    실패는 `StoreError`(종류·SQLSTATE 만 — 접속 정보·값을 싣지 않는다). `kbj.store.db.connect` 가
    이미 `StoreError` 를 내면 그대로 올린다.
    """

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._conn = conn_factory

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgGexIngestRepo()"

    def _query(self, table: GxTable, query: sql.Composed, params: Sequence[object]) -> list[Row]:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(query, tuple(params))
                return [tuple(r) for r in cur.fetchall()]
        except StoreError:
            raise
        except psycopg.Error as e:
            kind = _kind(e)
            raise StoreError(f"{table.name}: {kind}", cause=kind) from None

    # ── KRX 적재 이어 받기·마스터 대조 ──

    def krx_loaded(self, kind: KrxKind, trade_date: date) -> bool:
        """그 거래일(BAS_DD) KRX 일별 행이 DB 에 있나 — 있으면 다시 부르지 않는다(이어 받기)."""
        table = KRX_TABLES[kind]
        query = sql.SQL("SELECT EXISTS (SELECT 1 FROM {} WHERE trade_date = %s)").format(
            table.ident
        )
        rows = self._query(table, query, (trade_date,))
        return bool(rows and rows[0][0])

    def krx_option_listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]:
        """그 거래일 KRX 옵션 상장 목록 (상품군, 만기 6자리, 콜풋, 행사가) — 주간·야간 행을
        하나로."""
        query = sql.SQL(
            "SELECT DISTINCT family, expiry, cp, strike FROM {} WHERE trade_date = %s "
            "ORDER BY family, expiry, cp, strike"
        ).format(KRX_OPT_DAILY.ident)
        rows = self._query(KRX_OPT_DAILY, query, (trade_date,))
        return [(str(f), str(e), str(c), Decimal(k)) for f, e, c, k in rows]

    # ── 무결측 판정 입력 ──

    def series_dates(
        self, trade_date: date, session: str
    ) -> list[tuple[str, str, str, date, datetime]]:
        """그 세션 series_expiries (시장분류, 만기, 원천, 최종거래일, 기록 시각) — 추적 만기용."""
        query = sql.SQL(
            "SELECT mrkt_cls, expiry, source, last_trade_date, ts FROM {} "
            "WHERE trade_date = %s AND session = %s ORDER BY ts"
        ).format(SERIES_EXPIRIES.ident)
        rows = self._query(SERIES_EXPIRIES, query, (trade_date, session))
        return [(str(c), str(e), str(s), d, ts) for c, e, s, d, ts in rows]

    def board_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]:
        """그 세션 전광판 스냅샷 (시장분류, 만기, ts) — 검증 실패 행은 뺀다."""
        query = sql.SQL(
            "SELECT DISTINCT mrkt_cls, expiry, ts FROM {} WHERE trade_date = %s AND session = %s "
            "AND source = %s AND quality <> %s ORDER BY ts"
        ).format(CHAIN_SNAPSHOTS.ident)
        rows = self._query(CHAIN_SNAPSHOTS, query, (trade_date, session, "board", "invalid"))
        return [(str(c), str(e), ts) for c, e, ts in rows]

    def fut_board_times(self, trade_date: date, session: str) -> list[datetime]:
        """그 세션 선물 전광판·단건 스냅샷 시각 — 검증 실패 행은 뺀다."""
        query = sql.SQL(
            "SELECT DISTINCT ts FROM {} WHERE trade_date = %s AND session = %s "
            "AND quality <> %s ORDER BY ts"
        ).format(FUT_BOARD.ident)
        return [r[0] for r in self._query(FUT_BOARD, query, (trade_date, session, "invalid"))]

    def underlying_times(self, trade_date: date, session: str) -> list[datetime]:
        """그 세션 기초자산 조회 성공 응답 시각 — 원문 일 키(`underlying|<파라미터>`)와 `rt_cd`
        0."""
        query = sql.SQL(
            "SELECT DISTINCT ts FROM {} WHERE trade_date = %s AND session = %s AND source = %s "
            "AND key LIKE %s AND payload->>'rt_cd' = %s ORDER BY ts"
        ).format(RAW_MESSAGES.ident)
        params = (trade_date, session, "kis_rest", "underlying|%", "0")
        return [r[0] for r in self._query(RAW_MESSAGES, query, params)]

    def investor_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]:
        """그 세션 투자자별 (시장, 업종, ts) — 쓰기 주인은 flows.market_minute(ADR 0019)."""
        query = sql.SQL(
            "SELECT DISTINCT market_code, sector_code, ts FROM {} "
            "WHERE trade_date = %s AND session = %s AND quality <> %s ORDER BY ts"
        ).format(INVESTOR_FLOW.ident)
        rows = self._query(INVESTOR_FLOW, query, (trade_date, session, "invalid"))
        return [(str(m), str(sc), ts) for m, sc, ts in rows]

    def fill1_times(self, trade_date: date, session: str) -> list[tuple[str, datetime]]:
        """그 세션 보강 1 성공 응답 (시리즈 라벨, ts) — 원문 일
        키(`fill1:<시장분류|M>:<만기>:…`)."""
        query = sql.SQL(
            "SELECT DISTINCT split_part(key, ':', 2) || ':' || split_part(key, ':', 3), ts "
            "FROM {} WHERE trade_date = %s AND session = %s AND source = %s AND key LIKE %s "
            "AND payload->>'rt_cd' = %s ORDER BY ts"
        ).format(RAW_MESSAGES.ident)
        params = (trade_date, session, "kis_rest", "fill1:%", "0")
        return [(str(k), ts) for k, ts in self._query(RAW_MESSAGES, query, params)]

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        """ws-gateway 연결 사건 — (start 전 마지막 사건 종류, [start, end] 안 (시각, 종류))."""
        names = list(kinds)
        before = self._query(
            HEALTH_EVENTS,
            sql.SQL(
                "SELECT kind FROM {} WHERE service = %s AND kind = ANY(%s) AND ts < %s "
                "ORDER BY ts DESC LIMIT 1"
            ).format(HEALTH_EVENTS.ident),
            (WS_GATEWAY_SERVICE, names, _utc(start)),
        )
        rows = self._query(
            HEALTH_EVENTS,
            sql.SQL(
                "SELECT ts, kind FROM {} WHERE service = %s AND kind = ANY(%s) "
                "AND ts >= %s AND ts <= %s ORDER BY ts"
            ).format(HEALTH_EVENTS.ident),
            (WS_GATEWAY_SERVICE, names, _utc(start), _utc(end)),
        )
        return (str(before[0][0]) if before else None), [(ts, str(k)) for ts, k in rows]

    def minute_bar_times(
        self, trade_date: date, session: str
    ) -> list[tuple[str, datetime, int | None]]:
        """그 세션 분봉 (종목, 봉 시각, 체결량)."""
        query = sql.SQL(
            "SELECT code, ts, volume FROM {} WHERE trade_date = %s AND session = %s "
            "ORDER BY code, ts"
        ).format(MINUTE_BARS.ident)
        rows = self._query(MINUTE_BARS, query, (trade_date, session))
        return [(str(c), ts, None if v is None else int(v)) for c, ts, v in rows]

    def fut_tick_minutes(self, trade_date: date, session: str) -> list[tuple[str, datetime, int]]:
        """그 세션 웹소켓 선물 체결 — (종목, 분, 체결 수)."""
        query = sql.SQL(
            "SELECT code, date_trunc('minute', ts) AS m, count(*) FROM {} "
            "WHERE trade_date = %s AND session = %s GROUP BY code, m ORDER BY code, m"
        ).format(FUT_TICKS.ident)
        rows = self._query(FUT_TICKS, query, (trade_date, session))
        return [(str(c), m, int(n)) for c, m, n in rows]

    def fut_tick_count(self, start: datetime, end: datetime) -> int:
        """체결 시각이 [start, end) 인 웹소켓 선물 체결 수 — 개장 이중 확인(GX 설계 §6)."""
        query = sql.SQL("SELECT count(*) FROM {} WHERE ts >= %s AND ts < %s").format(
            FUT_TICKS.ident
        )
        rows = self._query(FUT_TICKS, query, (_utc(start), _utc(end)))
        return int(rows[0][0]) if rows else 0

    def gap_reports(self, first: date, last: date) -> list[CollectionReportRecord]:
        """[first, last] 거래일의 무결측 판정 리포트(`gex_report nogap`)."""
        cols = COLLECTION_REPORTS.columns
        query = sql.SQL(
            "SELECT {} FROM {} WHERE trade_date >= %s AND trade_date <= %s "
            "ORDER BY trade_date, session, stream"
        ).format(sql.SQL(", ").join(map(sql.Identifier, cols)), COLLECTION_REPORTS.ident)
        rows = self._query(COLLECTION_REPORTS, query, (first, last))
        return [
            CollectionReportRecord.model_validate(dict(zip(cols, r, strict=True))) for r in rows
        ]

    # ── 섀도 점검 읽기(`gex_report shadow` — GX data/store.py 그대로) ──

    def engine_output_counts(
        self, first: date, last: date
    ) -> list[tuple[str, str, str, int, int, int, int, int]]:
        """귀속 거래일 [first, last] engine 산출의 (표, 산출 이름, 플래그)별 (행, 사이클 수(ts),
        invalid 행, 예외 행, null 행). 예외 행 = payload·detail 에 `error` 가 있는 행(지표·레벨
        격리가 남긴 invalid 행), null 행 = 예외 행을 뺀 값 없는 행(oi_changes 는 증감 없는 칸)."""
        query = sql.SQL(
            "SELECT 'metrics', metric, flag, count(*), count(DISTINCT ts), "
            "count(*) FILTER (WHERE quality = %s), count(*) FILTER (WHERE payload ? %s), "
            "count(*) FILTER (WHERE value IS NULL AND NOT payload ? %s) "
            "FROM {m} WHERE trade_date BETWEEN %s AND %s GROUP BY metric, flag "
            "UNION ALL "
            "SELECT 'levels', name, flag, count(*), count(DISTINCT ts), "
            "count(*) FILTER (WHERE quality = %s), count(*) FILTER (WHERE detail ? %s), "
            "count(*) FILTER (WHERE value IS NULL AND NOT detail ? %s) "
            "FROM {lv} WHERE trade_date BETWEEN %s AND %s GROUP BY name, flag "
            "UNION ALL "
            "SELECT 'oi_changes', 'oi_changes', flag, count(*), count(DISTINCT ts), "
            "count(*) FILTER (WHERE quality = %s), 0, count(*) FILTER (WHERE change IS NULL) "
            "FROM {oi} WHERE trade_date BETWEEN %s AND %s GROUP BY flag "
            "ORDER BY 1, 2, 3"
        ).format(m=METRICS.ident, lv=LEVELS.ident, oi=OI_CHANGES.ident)
        head = ("invalid", "error", "error", first, last)
        params = (*head, *head, "invalid", first, last)
        rows = self._query(METRICS, query, params)
        return [
            (str(t), str(n), str(f), int(a), int(b), int(c), int(d), int(e))
            for t, n, f, a, b, c, d, e in rows
        ]

    def engine_null_groups(
        self, first: date, last: date, keys: Sequence[str]
    ) -> list[tuple[str, str, str, str, str, dict[str, Any], int]]:
        """귀속 거래일 [first, last] 의 값 없는(null) 산출 행(예외 행은 뺀다)을 (표, 이름, 플래그,
        범위, 품질, payload·detail 의 keys 칸만 — 없으면 JSON null)으로 묶어 센다. oi_changes 의
        증감 없는 칸은 {'first_snapshot': 직전 스냅샷이 없다}."""
        names = list(keys)
        if not names or not all(_IDENT.fullmatch(k) for k in names):
            raise ValueError(f"keys 는 소문자 식별자 하나 이상: {names!r}")

        def project(col: str) -> sql.Composed:
            parts = [
                sql.SQL("{}, {} -> {}").format(sql.Literal(k), sql.Identifier(col), sql.Literal(k))
                for k in names
            ]
            return sql.SQL("jsonb_build_object({})").format(sql.SQL(", ").join(parts))

        query = sql.SQL(
            "SELECT 'metrics', metric, flag, scope, quality, {pm}, count(*) FROM {m} "
            "WHERE trade_date BETWEEN %s AND %s AND value IS NULL AND NOT payload ? %s "
            "GROUP BY 1, 2, 3, 4, 5, 6 "
            "UNION ALL "
            "SELECT 'levels', name, flag, scope, quality, {pl}, count(*) FROM {lv} "
            "WHERE trade_date BETWEEN %s AND %s AND value IS NULL AND NOT detail ? %s "
            "GROUP BY 1, 2, 3, 4, 5, 6 "
            "UNION ALL "
            "SELECT 'oi_changes', 'oi_changes', flag, '', quality, "
            "jsonb_build_object('first_snapshot', prev_ts IS NULL), count(*) FROM {oi} "
            "WHERE trade_date BETWEEN %s AND %s AND change IS NULL GROUP BY 1, 2, 3, 4, 5, 6 "
            "ORDER BY 1, 2, 3, 4, 5"
        ).format(
            pm=project("payload"),
            pl=project("detail"),
            m=METRICS.ident,
            lv=LEVELS.ident,
            oi=OI_CHANGES.ident,
        )
        params = (first, last, "error", first, last, "error", first, last)
        rows = self._query(METRICS, query, params)
        return [
            (str(t), str(n), str(f), str(s), str(q), dict(p), int(c))
            for t, n, f, s, q, p, c in rows
        ]

    def engine_failures(
        self, first: date, last: date, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> list[tuple[str, str, int]]:
        """engine health 중 kinds 의 (종류, 문구)별 건수 — 귀속 거래일 [first, last] 이거나 태그가
        없고(POST_DAY 등 세션 밖) 시각이 [start, end) 인 것."""
        query = sql.SQL(
            "SELECT kind, message, count(*) FROM {} WHERE service = %s AND kind = ANY(%s) "
            "AND ((trade_date BETWEEN %s AND %s) OR (trade_date IS NULL AND ts >= %s AND ts < %s)) "
            "GROUP BY kind, message ORDER BY kind, message"
        ).format(HEALTH_EVENTS.ident)
        params = ("engine", list(kinds), first, last, _utc(start), _utc(end))
        rows = self._query(HEALTH_EVENTS, query, params)
        return [(str(k), str(m), int(n)) for k, m, n in rows]

    # ── 판정 교체 ──

    def replace_gap_report(
        self,
        trade_date: date,
        session: str,
        gaps: Sequence[CollectionGapRecord],
        reports: Sequence[CollectionReportRecord],
    ) -> None:
        """그 세션의 collection_gaps·collection_reports 를 지우고 새로 쓴다 — 트랜잭션 하나.
        스풀·백오프를 쓰지 않는다(판정은 다시 계산할 수 있다). 실패는 StoreError."""
        if session not in _SESSIONS:
            raise ValueError(f"session 은 day·night: {session!r}")
        if any((r.trade_date, r.session) != (trade_date, session) for r in [*gaps, *reports]):
            raise ValueError("다른 세션의 공백·리포트가 섞였다")
        where = sql.SQL("DELETE FROM {} WHERE trade_date = %s AND session = %s")
        key = (trade_date, session)
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(where.format(COLLECTION_GAPS.ident), key)
                cur.execute(where.format(COLLECTION_REPORTS.ident), key)
                if gaps:
                    cur.executemany(COLLECTION_GAPS.insert_sql(), [gap_row(g) for g in gaps])
                if reports:
                    cur.executemany(
                        COLLECTION_REPORTS.insert_sql(), [report_row(r) for r in reports]
                    )
        except StoreError:
            raise
        except _TRANSIENT as e:
            raise StoreError(
                f"collection_reports: DB 연결 실패 — {type(e).__name__}", cause=type(e).__name__
            ) from None
        except psycopg.Error as e:
            kind = _kind(e)
            raise StoreError(f"collection_reports: {kind}", cause=kind) from None
