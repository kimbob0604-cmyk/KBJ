"""GEX 저장소 — 0006 `prv_gex.*`(엔진 산출·틱·원문)·`ops.session_log`·`ops.health_events` 의 KBJ 쪽
쓰기·읽기(P7 묶음 FND-B, docs/p7_design.md §1.1·D-P7-8).

- **메서드 이름은 GX `data/store.py:PostgresSink` 그대로**(D-P7-8): 쓰기 `write_levels·
  write_metrics·write_strike_gex·write_option_iv·write_oi_changes·write_fut_ticks·write_opt_ticks·write_raw·
  write_session_log·write_health`, 엔진 읽기 `chain_max_ts·chain_latest·futures_latest·expiry_dates·
  metric_last·metric_history·krx_option_days·krx_iv_rows·krx_futures_settles·investor_latest·
  opt_tick_days·opt_tick_history·kis_rest_outputs`. legacy `PostgresSink` 도 같은 모양이라 KBJ
  서비스는 둘 중 무엇을 받아도 돈다(GX 통합 시험 `test_full_day` 가 shim 으로 그대로).
- 화면 읽기(API — ENG 리더): `levels_latest`·`strike_profile`·`metrics_series`·`flags_of`.
- SQL·행 변환은 GX 의 같은 함수를 `prv_gex.`(원문·틱·엔진)·`ops.`(세션 기록·health) 이름으로
  옮겼다(키·덮어쓰기 규칙·정규화 JSON·digest 그대로 — 같은 원문은 같은 행). 수집 표
  (chain_snapshots·fut_board·series_expiries·investor_flow·KRX 일별)는 **읽기만**(쓰기 주인은
  legacy poller·scheduler·`flows.market_minute` — §3.4). 메모리판만 시험 씨앗(`seed_*`)을 받는다.
- 스풀(연결 장애 때 디스크에 쌓기)은 이 저장소가 하지 않는다 — 실패는 그대로 올린다(에러를 삼키지
  않는다). 서비스가 `kbj.store.spool` 로 감싼다 [제안 — ENG].
- 이 모듈은 `kbj.core`·psycopg 만 쓴다(계약 ⑦).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, LiteralString, Protocol

from psycopg import sql

from kbj.core.gex.records import (
    ChainRecord,
    Flag,
    FuturesRecord,
    FuturesTickRecord,
    HealthEvent,
    InvestorRecord,
    LevelRecord,
    LevelScope,
    MetricRecord,
    OiChangeRecord,
    OptionIvRecord,
    OptionTickRecord,
    RawEnvelope,
    SessionLogRecord,
    StrikeGexRecord,
)
from kbj.store.repos._common import ConnFactory

__all__ = [
    "ENGINE_TABLES",
    "GexRepo",
    "GxTable",
    "MemoryGexRepo",
    "PgGexRepo",
    "digest",
    "level_row",
    "metric_row",
    "raw_row",
    "session_log_row",
    "to_json",
]

Row = tuple[Any, ...]
Tag = tuple[date | None, str | None]
_SESSIONS: Final = frozenset({"day", "night"})
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
KRX_MONTHLY_FAMILY: Final = "kospi200"


# ── 표 명세(GX data/store.py `Table` — 스키마 이름만 더했다) ─────────────────────────────────


@dataclass(frozen=True)
class GxTable:
    """INSERT 대상 표. 열 순서 = 행 튜플 순서. 키는 0006·0002 의 UNIQUE 와 같다."""

    schema: str
    name: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    upsert: bool = False  # True: 키 밖 열을 EXCLUDED 로 고친다, False: DO NOTHING
    jsonb: frozenset[str] = frozenset()
    keep_if_null: str | None = None  # 이 열이 NULL 인 새 행은 있는 행을 고치지 않는다

    def __post_init__(self) -> None:
        for ident in (self.schema, self.name, *self.columns):
            if not _IDENT.fullmatch(ident):
                raise ValueError(f"식별자는 소문자 snake_case 상수만: {ident!r}")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f"{self.name}: 열이 겹친다")
        if not set(self.key) <= set(self.columns) or not self.key:
            raise ValueError(f"{self.name}: 키 열은 열 목록 안에")
        # GX data/store.py `Table` 검증 그대로(P9 GXI — 승격한 test_store_sql 이 찾았다)
        if not self.jsonb <= set(self.columns):
            raise ValueError(f"{self.name}: jsonb 열은 열 목록 안에")
        keep = self.keep_if_null
        if keep is not None and (not self.upsert or keep not in self.update_columns):
            raise ValueError(f"{self.name}: keep_if_null 은 upsert 표의 키 밖 열")

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def ident(self) -> sql.Identifier:
        return sql.Identifier(self.schema, self.name)

    @property
    def update_columns(self) -> tuple[str, ...]:
        return tuple(c for c in self.columns if c not in self.key) if self.upsert else ()

    def insert_sql(self) -> sql.Composed:
        ph = sql.Placeholder()
        values = [sql.SQL("{}::jsonb").format(ph) if c in self.jsonb else ph for c in self.columns]
        if self.upsert:
            sets = [
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in self.update_columns
            ]
            action = sql.SQL("DO UPDATE SET {}").format(sql.SQL(", ").join(sets))
            if self.keep_if_null is not None:
                action = sql.SQL("{} WHERE EXCLUDED.{} IS NOT NULL").format(
                    action, sql.Identifier(self.keep_if_null)
                )
        else:
            action = sql.SQL("DO NOTHING")
        return sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) {}").format(
            self.ident,
            sql.SQL(", ").join(map(sql.Identifier, self.columns)),
            sql.SQL(", ").join(values),
            sql.SQL(", ").join(map(sql.Identifier, self.key)),
            action,
        )


_SNAP = ("ts", "trade_date", "session")
_TICK = (*_SNAP, "received_at", "tr_id", "code", "seq")
_TICK_QTY = (
    "price", "qty", "cum_vol", "cum_value", "cum_buy_qty", "cum_sell_qty", "oi", "oi_chg",
    "bid", "ask",
)  # fmt: skip

RAW_MESSAGES: Final = GxTable(
    "prv_gex", "raw_messages",
    (*_SNAP, "source", "tr_id", "key", "payload", "payload_text", "lossy", "digest"),
    key=("ts", "digest"), jsonb=frozenset({"payload"}),
)  # fmt: skip
FUT_TICKS: Final = GxTable("prv_gex", "fut_ticks", (*_TICK, *_TICK_QTY), key=("ts", "code", "seq"))
OPT_TICKS: Final = GxTable(
    "prv_gex", "opt_ticks",
    (*_TICK, "mrkt_cls", "expiry", "strike", "cp", *_TICK_QTY,
     "delta", "gamma", "vega", "theta", "rho", "iv"),
    key=("ts", "code", "seq"),
)  # fmt: skip
CHAIN_SNAPSHOTS: Final = GxTable(
    "prv_gex", "chain_snapshots",
    (*_SNAP, "mrkt_cls", "expiry", "strike", "cp", "source", "code", "last", "bid", "ask", "oi",
     "oi_chg", "volume", "iv_kis", "delta", "gamma", "theta", "vega", "rho", "quality"),
    key=("ts", "mrkt_cls", "expiry", "strike", "cp", "source"),
)  # fmt: skip
FUT_BOARD: Final = GxTable(
    "prv_gex", "fut_board",
    (*_SNAP, "market", "code", "source", "name", "price", "bid", "ask", "volume", "oi",
     "remaining_days", "quality"),
    key=("ts", "market", "code", "source"),
)  # fmt: skip
INVESTOR_FLOW: Final = GxTable(
    "prv_gex", "investor_flow",
    (*_SNAP, "market_code", "sector_code", "investor", "sell_qty", "buy_qty", "net_qty",
     "sell_value", "buy_value", "net_value", "quality"),
    key=("ts", "market_code", "sector_code", "investor"),
)  # fmt: skip
SERIES_EXPIRIES: Final = GxTable(
    "prv_gex", "series_expiries",
    (*_SNAP, "mrkt_cls", "expiry", "source", "last_trade_date", "calendar_date", "matches",
     "code", "quality"),
    key=("ts", "mrkt_cls", "expiry", "source"),
)  # fmt: skip
SESSION_LOG: Final = GxTable(
    "ops", "session_log",
    (*_SNAP, "service", "kind", "state", "prev_state", "detail", "digest"),
    key=("ts", "digest"), jsonb=frozenset({"detail"}),
)  # fmt: skip
# ws-gateway 가 health_events 에 쓰는 서비스 이름(엔진 HIRO 리셋 사유 — GX 와 같은 이름)
WS_GATEWAY_SERVICE: Final = "ws-gateway"
HEALTH_EVENTS: Final = GxTable(
    "ops", "health_events",
    (*_SNAP, "service", "kind", "level", "message", "detail", "digest"),
    key=("ts", "digest"), jsonb=frozenset({"detail"}),
)  # fmt: skip
LEVELS: Final = GxTable(
    "prv_gex", "levels",
    (*_SNAP, "scope", "name", "value", "detail", "quality", "reasons", "flag"),
    key=("ts", "scope", "name"), upsert=True, jsonb=frozenset({"detail", "reasons"}),
)  # fmt: skip
METRICS: Final = GxTable(
    "prv_gex", "metrics",
    (*_SNAP, "metric", "scope", "key", "value", "payload", "quality", "flag"),
    key=("ts", "metric", "scope", "key"), upsert=True, jsonb=frozenset({"payload"}),
)  # fmt: skip
STRIKE_GEX: Final = GxTable(
    "prv_gex", "strike_gex",
    (*_SNAP, "mrkt_cls", "expiry", "strike", "gex_call", "gex_put", "gex", "forward",
     "excluded_oi_ratio", "quality"),
    key=("ts", "mrkt_cls", "expiry", "strike"), upsert=True,
)  # fmt: skip
OPTION_IV: Final = GxTable(
    "prv_gex", "option_iv",
    (*_SNAP, "mrkt_cls", "expiry", "strike", "cp", "quote_source", "price", "price_kind",
     "prev_session", "oi", "iv", "source", "rescaled", "t_kis", "reason", "excluded", "delta",
     "gamma", "forward", "t_years", "quality"),
    key=("ts", "mrkt_cls", "expiry", "strike", "cp"), upsert=True,
)  # fmt: skip
OI_CHANGES: Final = GxTable(
    "prv_gex", "oi_changes",
    (*_SNAP, "mrkt_cls", "expiry", "strike", "cp", "oi", "prev_ts", "prev_oi", "change",
     "outlier", "quality", "flag"),
    key=("ts", "mrkt_cls", "expiry", "strike", "cp"), upsert=True, keep_if_null="change",
)  # fmt: skip
KRX_FUT_DAILY: Final = "krx_fut_daily"
KRX_OPT_DAILY: Final = "krx_opt_daily"
ENGINE_TABLES: Final[tuple[GxTable, ...]] = (LEVELS, METRICS, STRIKE_GEX, OPTION_IV, OI_CHANGES)


# ── 값 정리·해시(GX 그대로 — 같은 원문은 같은 digest) ───────────────────────────────────────


def clean_text(s: str) -> tuple[str, bool]:
    """text·jsonb 가 못 받는 문자(NUL, 짝 없는 서로게이트)를 U+FFFD·'?' 로 바꾼 값과 바꿨는지."""
    t = s.replace("\x00", "�")
    try:
        t.encode("utf-8")
    except UnicodeEncodeError:
        t = t.encode("utf-8", "replace").decode("utf-8")
    return t, t != s


def clean_texts(*values: str) -> tuple[list[str], bool]:
    out = [clean_text(v) for v in values]
    return [t for t, _ in out], any(lossy for _, lossy in out)


def _clean(v: object) -> tuple[object, bool]:
    if isinstance(v, str):
        return clean_text(v)
    if isinstance(v, float) and not math.isfinite(v):
        return str(v), True  # jsonb 는 NaN·Infinity 를 받지 않는다
    if isinstance(v, Mapping):
        out: dict[str, object] = {}
        lossy = False
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            ck, lk = clean_text(str(k))  # pyright: ignore[reportUnknownArgumentType]
            cx, lx = _clean(x)
            out[ck] = cx
            lossy = lossy or lk or lx
        return out, lossy
    if isinstance(v, list | tuple):
        items = [_clean(x) for x in v]  # pyright: ignore[reportUnknownVariableType]
        return [x for x, _ in items], any(lx for _, lx in items)
    return v, False


def to_json(v: object) -> tuple[str, bool]:
    """jsonb 로 넣을 정규화 JSON(키 정렬·공백 없음 — 해시가 늘 같다)과 값을 바꿨는지."""
    clean, lossy = _clean(v)
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return text, lossy


def digest(*parts: str) -> bytes:
    """길이를 앞에 붙여 이어 붙인 sha256 — ('ab','c') 와 ('a','bc') 가 다르다."""
    h = hashlib.sha256()
    for p in parts:
        b = p.encode("utf-8")
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.digest()


def _tag(trade_date: date | None, session: str | None) -> Tag:
    if trade_date is None or session not in _SESSIONS:
        return None, None
    return trade_date, session


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return ts.astimezone(UTC)


# ── 레코드 → 행(GX 순수 함수 그대로) ─────────────────────────────────────────────────────────


def attr_row(table: GxTable, rec: object) -> Row:
    return tuple(getattr(rec, c) for c in table.columns)


def raw_row(env: RawEnvelope) -> Row:
    """문자열 원문(웹소켓 프레임)은 payload_text, dict(REST·KRX 응답)는 payload(jsonb)."""
    td, ss = _tag(env.trade_date, env.session)
    (tr_id, key), lossy_ids = clean_texts(env.tr_id, env.key)
    if isinstance(env.payload, str):
        text, lossy = clean_text(env.payload)
        js: str | None = None
        body, kind = text, "text"
    else:
        js, lossy = to_json(env.payload)
        text = None
        body, kind = js, "json"
    h = digest(env.source, tr_id, key, kind, body)
    return (env.received_at, td, ss, env.source, tr_id, key, js, text, lossy or lossy_ids, h)


def health_row(ev: HealthEvent) -> Row:
    td, ss = _tag(ev.trade_date, ev.session)
    (service, kind, msg), _ = clean_texts(ev.service, ev.kind, ev.message)
    detail_json, _ = to_json(ev.detail)
    h = digest(service, kind, ev.level, msg, detail_json)
    return (ev.ts, td, ss, service, kind, ev.level, msg, detail_json, h)


def session_log_row(r: SessionLogRecord) -> Row:
    td, ss = _tag(r.trade_date, r.session)
    state = r.state.value if r.state is not None else None
    prev = r.prev_state.value if r.prev_state is not None else None
    (service, kind), _ = clean_texts(r.service, r.kind)
    detail, _ = to_json(r.detail)
    h = digest(service, kind, state or "", prev or "", detail)
    return (r.ts, td, ss, service, kind, state, prev, detail, h)


def level_row(r: LevelRecord) -> Row:
    detail, _ = to_json(r.detail)
    reasons, _ = to_json(list(r.reasons))
    return (
        r.ts, r.trade_date, r.session, r.scope, r.name, r.value, detail, r.quality, reasons,
        r.flag,
    )  # fmt: skip


def metric_row(r: MetricRecord) -> Row:
    payload, _ = to_json(r.payload)
    return (
        r.ts, r.trade_date, r.session, r.metric, r.scope, r.key, r.value, payload, r.quality,
        r.flag,
    )  # fmt: skip


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _record[M](model: type[M], cols: Sequence[str], row: Row) -> M:
    return model.model_validate(dict(zip(cols, row, strict=True)))  # type: ignore[attr-defined, no-any-return]


# ── Protocol(D-P7-8 — GX 싱크 메서드 이름 그대로) ────────────────────────────────────────────


class GexRepo(Protocol):
    # 쓰기 — gex.engine(엔진 5표)·gex.ws_gateway(틱)·gex.recorder(원문)·세션 기록·health
    def write_levels(self, rows: Sequence[LevelRecord]) -> None: ...
    def write_metrics(self, rows: Sequence[MetricRecord]) -> None: ...
    def write_strike_gex(self, rows: Sequence[StrikeGexRecord]) -> None: ...
    def write_option_iv(self, rows: Sequence[OptionIvRecord]) -> None: ...
    def write_oi_changes(self, rows: Sequence[OiChangeRecord]) -> None: ...
    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None: ...
    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None: ...
    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None: ...
    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None: ...
    def write_health(self, events: Sequence[HealthEvent]) -> None: ...

    # 엔진 읽기
    def chain_max_ts(
        self, trade_date: date, session: str, upto: datetime | None = None
    ) -> datetime | None: ...
    def chain_latest(self, trade_date: date, session: str, upto: datetime) -> list[ChainRecord]: ...
    def futures_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[FuturesRecord]: ...
    def expiry_dates(self, since: date) -> list[tuple[str, str, date, str]]: ...
    def metric_last(
        self, trade_date: date, session: str, metric: str, scope: str, key_prefix: str = ""
    ) -> list[MetricRecord]: ...
    def metric_history(
        self, metric: str, scope: str, key: str, first: date, last: date
    ) -> list[MetricRecord]: ...
    def krx_option_days(self, first: date, last: date) -> list[date]: ...
    def krx_iv_rows(
        self, trade_date: date, expiry: str
    ) -> list[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]]: ...
    def krx_futures_settles(self, first: date, last: date) -> list[tuple[date, str, Decimal]]: ...
    def investor_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[InvestorRecord]: ...
    def opt_tick_days(self, before: date, limit: int) -> list[date]: ...
    def opt_tick_history(
        self, first: date, last: date, lookback: timedelta
    ) -> list[tuple[date, str, Decimal, int, float]]: ...
    def kis_rest_outputs(
        self, tr_id: str, after: datetime, upto: datetime, output: str = "output1"
    ) -> list[tuple[datetime, date | None, str | None, dict[str, Any]]]: ...
    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        """ws-gateway 연결 사건(ops.health_events) — (start 전 마지막 사건 종류, [start, end] 안
        (시각, 종류)). 엔진 HIRO 리셋 사유(P7 ENG — GX `PostgresSink.ws_connection_events`)."""
        ...

    # 화면 읽기(API — 계약 ⑩: DB 만)
    def levels_latest(
        self, scope: LevelScope, upto: datetime | None = None, session: str | None = None
    ) -> list[LevelRecord]:
        """범위 하나의 가장 늦은 사이클(ts) 레벨 전부(플래그 무관 — 리더가 visible 만 싣는다).
        session 을 주면 그 세션(day·night)의 가장 늦은 사이클."""
        ...

    def strike_profile(self, ts: datetime) -> list[StrikeGexRecord]:
        """그 사이클의 행사가별 GEX((시장분류, 만기, 행사가) 순)."""
        ...

    def metrics_series(
        self, metric: str, start: datetime, end: datetime, scope: str = "all", key: str = ""
    ) -> list[MetricRecord]:
        """[start, end] 의 지표 시계열(ts 순)."""
        ...

    def flags_of(self, ts: datetime) -> dict[str, Flag]:
        """그 사이클에 저장된 지표·레벨 이름 → 계산 당시 기능 플래그."""
        ...


# ── Postgres ───────────────────────────────────────────────────────────────────────────────


class PgGexRepo:
    """Postgres 구현. 부를 때마다 짧게 연결을 연다(`ConnFactory`). 실패는 그대로 올린다."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._conn = conn_factory

    def _send(self, table: GxTable, rows: Sequence[Row]) -> None:
        if not rows:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(table.insert_sql(), rows)

    def _query(self, query: sql.Composed, params: Sequence[object]) -> list[Row]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(query, tuple(params))
            return [tuple(r) for r in cur.fetchall()]

    # 쓰기
    def write_levels(self, rows: Sequence[LevelRecord]) -> None:
        self._send(LEVELS, [level_row(r) for r in rows])

    def write_metrics(self, rows: Sequence[MetricRecord]) -> None:
        self._send(METRICS, [metric_row(r) for r in rows])

    def write_strike_gex(self, rows: Sequence[StrikeGexRecord]) -> None:
        self._send(STRIKE_GEX, [attr_row(STRIKE_GEX, r) for r in rows])

    def write_option_iv(self, rows: Sequence[OptionIvRecord]) -> None:
        self._send(OPTION_IV, [attr_row(OPTION_IV, r) for r in rows])

    def write_oi_changes(self, rows: Sequence[OiChangeRecord]) -> None:
        self._send(OI_CHANGES, [attr_row(OI_CHANGES, r) for r in rows])

    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None:
        self._send(FUT_TICKS, [attr_row(FUT_TICKS, r) for r in rows])

    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None:
        self._send(OPT_TICKS, [attr_row(OPT_TICKS, r) for r in rows])

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        self._send(RAW_MESSAGES, [raw_row(e) for e in envelopes])

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        self._send(SESSION_LOG, [session_log_row(r) for r in rows])

    def write_health(self, events: Sequence[HealthEvent]) -> None:
        self._send(HEALTH_EVENTS, [health_row(e) for e in events])

    # 엔진 읽기(GX SQL 그대로 — 표 이름만 스키마를 붙였다)
    def chain_max_ts(
        self, trade_date: date, session: str, upto: datetime | None = None
    ) -> datetime | None:
        text = "SELECT max(ts) FROM {} WHERE trade_date = %s AND session = %s AND quality <> %s"
        params: tuple[object, ...] = (trade_date, session, "invalid")
        if upto is not None:
            text += " AND ts <= %s"
            params = (*params, _utc(upto))
        rows = self._query(sql.SQL(text).format(CHAIN_SNAPSHOTS.ident), params)
        return rows[0][0] if rows and rows[0][0] is not None else None

    def _distinct_latest(
        self,
        table: GxTable,
        on: LiteralString,
        trade_date: date,
        session: str,
        upto: datetime,
        extra: LiteralString,
    ) -> list[Row]:
        q = sql.SQL(
            "SELECT DISTINCT ON (" + on + ") {} FROM {} "  # noqa: S608 — 상수 열 목록만
            "WHERE trade_date = %s AND session = %s AND ts <= %s AND quality <> %s" + extra + " "
            "ORDER BY " + on + ", ts DESC"
        ).format(sql.SQL(", ").join(map(sql.Identifier, table.columns)), table.ident)
        return self._query(q, (trade_date, session, _utc(upto), "invalid"))

    def chain_latest(self, trade_date: date, session: str, upto: datetime) -> list[ChainRecord]:
        on: LiteralString = "mrkt_cls, expiry, strike, cp, source"
        rows = self._distinct_latest(CHAIN_SNAPSHOTS, on, trade_date, session, upto, "")
        return [_record(ChainRecord, CHAIN_SNAPSHOTS.columns, r) for r in rows]

    def futures_latest(self, trade_date: date, session: str, upto: datetime) -> list[FuturesRecord]:
        on = "market, code, source"
        extra = " AND price IS NOT NULL"
        rows = self._distinct_latest(FUT_BOARD, on, trade_date, session, upto, extra)
        return [_record(FuturesRecord, FUT_BOARD.columns, r) for r in rows]

    def investor_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[InvestorRecord]:
        on = "market_code, sector_code, investor"
        rows = self._distinct_latest(INVESTOR_FLOW, on, trade_date, session, upto, "")
        return [_record(InvestorRecord, INVESTOR_FLOW.columns, r) for r in rows]

    def expiry_dates(self, since: date) -> list[tuple[str, str, date, str]]:
        q = sql.SQL(
            "SELECT DISTINCT ON (mrkt_cls, expiry) mrkt_cls, expiry, last_trade_date, source "
            "FROM {} WHERE trade_date >= %s AND quality <> %s "
            "ORDER BY mrkt_cls, expiry, (source = 'kis') DESC, ts DESC"
        ).format(SERIES_EXPIRIES.ident)
        return [(str(c), str(e), d, str(s)) for c, e, d, s in self._query(q, (since, "invalid"))]

    def metric_last(
        self, trade_date: date, session: str, metric: str, scope: str, key_prefix: str = ""
    ) -> list[MetricRecord]:
        where = sql.SQL(
            "trade_date = %s AND session = %s AND metric = %s AND scope = %s "
            "AND key LIKE %s ESCAPE '\\'"
        )
        q = sql.SQL(
            "SELECT {cols} FROM {t} WHERE {w} AND ts = (SELECT max(ts) FROM {t} WHERE {w}) "
            "ORDER BY key"
        ).format(
            cols=sql.SQL(", ").join(map(sql.Identifier, METRICS.columns)), t=METRICS.ident, w=where
        )
        params = (trade_date, session, metric, scope, _like_prefix(key_prefix)) * 2
        return [_record(MetricRecord, METRICS.columns, r) for r in self._query(q, params)]

    def metric_history(
        self, metric: str, scope: str, key: str, first: date, last: date
    ) -> list[MetricRecord]:
        q = sql.SQL(
            "SELECT {} FROM {} WHERE metric = %s AND scope = %s AND key = %s "
            "AND trade_date BETWEEN %s AND %s ORDER BY trade_date, ts"
        ).format(sql.SQL(", ").join(map(sql.Identifier, METRICS.columns)), METRICS.ident)
        rows = self._query(q, (metric, scope, key, first, last))
        return [_record(MetricRecord, METRICS.columns, r) for r in rows]

    def krx_option_days(self, first: date, last: date) -> list[date]:
        q = sql.SQL(
            "SELECT DISTINCT trade_date FROM {} WHERE family = %s AND session = %s "
            "AND trade_date BETWEEN %s AND %s ORDER BY 1"
        ).format(sql.Identifier("prv_gex", KRX_OPT_DAILY))
        return [r[0] for r in self._query(q, (KRX_MONTHLY_FAMILY, "day", first, last))]

    def krx_iv_rows(
        self, trade_date: date, expiry: str
    ) -> list[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]]:
        q = sql.SQL(
            "SELECT strike, cp, close, imp_volt, acc_trdvol FROM {} WHERE trade_date = %s "
            "AND session = %s AND family = %s AND expiry = %s ORDER BY strike, cp"
        ).format(sql.Identifier("prv_gex", KRX_OPT_DAILY))
        rows = self._query(q, (trade_date, "day", KRX_MONTHLY_FAMILY, expiry))
        return [(Decimal(k), str(cp), c, iv, vol) for k, cp, c, iv, vol in rows]

    def krx_futures_settles(self, first: date, last: date) -> list[tuple[date, str, Decimal]]:
        q = sql.SQL(
            "SELECT trade_date, expiry, setl_prc FROM {} WHERE family = %s AND session = %s "
            "AND expiry IS NOT NULL AND setl_prc IS NOT NULL AND trade_date BETWEEN %s AND %s "
            "ORDER BY trade_date, expiry"
        ).format(sql.Identifier("prv_gex", KRX_FUT_DAILY))
        rows = self._query(q, (KRX_MONTHLY_FAMILY, "day", first, last))
        return [(d, str(e), p) for d, e, p in rows]

    def opt_tick_days(self, before: date, limit: int) -> list[date]:
        if limit < 1:
            raise ValueError(f"limit 는 1 이상: {limit}")
        q = sql.SQL(
            "SELECT trade_date FROM (SELECT DISTINCT trade_date FROM {} WHERE trade_date < %s) d "
            "ORDER BY trade_date DESC LIMIT %s"
        ).format(OPT_TICKS.ident)
        return sorted(r[0] for r in self._query(q, (before, limit)))

    def opt_tick_history(
        self, first: date, last: date, lookback: timedelta
    ) -> list[tuple[date, str, Decimal, int, float]]:
        q = sql.SQL(
            "SELECT t.trade_date, t.cp, t.strike, t.qty, f.forward FROM {t} t "
            "JOIN LATERAL (SELECT o.forward FROM {o} o WHERE o.mrkt_cls = t.mrkt_cls "
            "AND o.expiry = t.expiry AND o.strike = t.strike AND o.cp = t.cp AND o.ts <= t.ts "
            "AND o.ts > t.ts - %s AND o.forward IS NOT NULL ORDER BY o.ts DESC LIMIT 1) f ON true "
            "WHERE t.trade_date BETWEEN %s AND %s AND t.qty > 0 AND t.mrkt_cls IS NOT NULL "
            "AND t.expiry IS NOT NULL AND t.strike IS NOT NULL AND t.cp IS NOT NULL "
            "ORDER BY t.trade_date, t.ts"
        ).format(t=OPT_TICKS.ident, o=OPTION_IV.ident)
        rows = self._query(q, (lookback, first, last))
        return [(d, str(cp), Decimal(k), int(qty), float(f)) for d, cp, k, qty, f in rows]

    def kis_rest_outputs(
        self, tr_id: str, after: datetime, upto: datetime, output: str = "output1"
    ) -> list[tuple[datetime, date | None, str | None, dict[str, Any]]]:
        if not _IDENT.fullmatch(output):
            raise ValueError(f"output 은 소문자 식별자: {output!r}")
        q = sql.SQL(
            "SELECT ts, trade_date, session, payload -> %s::text FROM {} WHERE tr_id = %s "
            "AND source = %s AND ts > %s AND ts <= %s ORDER BY ts"
        ).format(RAW_MESSAGES.ident)
        rows = self._query(q, (output, tr_id, "kis_rest", _utc(after), _utc(upto)))
        return [(ts, d, ss, o) for ts, d, ss, o in rows if isinstance(o, dict)]

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        names = list(kinds)
        before = self._query(
            sql.SQL(
                "SELECT kind FROM {} WHERE service = %s AND kind = ANY(%s) AND ts < %s "
                "ORDER BY ts DESC LIMIT 1"
            ).format(HEALTH_EVENTS.ident),
            (WS_GATEWAY_SERVICE, names, _utc(start)),
        )
        rows = self._query(
            sql.SQL(
                "SELECT ts, kind FROM {} WHERE service = %s AND kind = ANY(%s) "
                "AND ts >= %s AND ts <= %s ORDER BY ts"
            ).format(HEALTH_EVENTS.ident),
            (WS_GATEWAY_SERVICE, names, _utc(start), _utc(end)),
        )
        return (str(before[0][0]) if before else None), [(ts, str(k)) for ts, k in rows]

    # 화면 읽기
    def levels_latest(
        self, scope: LevelScope, upto: datetime | None = None, session: str | None = None
    ) -> list[LevelRecord]:
        cond = "scope = %s" + (" AND ts <= %s" if upto is not None else "")
        params: tuple[object, ...] = (scope,) if upto is None else (scope, _utc(upto))
        if session is not None:
            cond += " AND session = %s"
            params = (*params, session)
        q = sql.SQL(
            "SELECT {cols} FROM {t} WHERE scope = %s AND ts = (SELECT max(ts) FROM {t} WHERE "  # noqa: S608
            + cond
            + ") ORDER BY name"
        ).format(cols=sql.SQL(", ").join(map(sql.Identifier, LEVELS.columns)), t=LEVELS.ident)
        return [_record(LevelRecord, LEVELS.columns, r) for r in self._query(q, (scope, *params))]

    def strike_profile(self, ts: datetime) -> list[StrikeGexRecord]:
        q = sql.SQL("SELECT {} FROM {} WHERE ts = %s ORDER BY mrkt_cls, expiry, strike").format(
            sql.SQL(", ").join(map(sql.Identifier, STRIKE_GEX.columns)), STRIKE_GEX.ident
        )
        rows = self._query(q, (_utc(ts),))
        return [_record(StrikeGexRecord, STRIKE_GEX.columns, r) for r in rows]

    def metrics_series(
        self, metric: str, start: datetime, end: datetime, scope: str = "all", key: str = ""
    ) -> list[MetricRecord]:
        q = sql.SQL(
            "SELECT {} FROM {} WHERE metric = %s AND scope = %s AND key = %s "
            "AND ts BETWEEN %s AND %s ORDER BY ts"
        ).format(sql.SQL(", ").join(map(sql.Identifier, METRICS.columns)), METRICS.ident)
        rows = self._query(q, (metric, scope, key, _utc(start), _utc(end)))
        return [_record(MetricRecord, METRICS.columns, r) for r in rows]

    def flags_of(self, ts: datetime) -> dict[str, Flag]:
        q = sql.SQL(
            "SELECT name, flag FROM {l} WHERE ts = %s UNION SELECT metric, flag FROM {m} "
            "WHERE ts = %s"
        ).format(l=LEVELS.ident, m=METRICS.ident)
        return {str(n): f for n, f in self._query(q, (_utc(ts), _utc(ts)))}


# ── 메모리(시험) ───────────────────────────────────────────────────────────────────────────


def _key(table: GxTable, row: Row) -> tuple[object, ...]:
    return tuple(row[table.columns.index(c)] for c in table.key)


@dataclass
class _MemTable:
    table: GxTable
    rows: dict[tuple[object, ...], Row] = field(default_factory=dict[tuple[object, ...], Row])

    def put(self, rows: Iterable[Row]) -> None:
        t = self.table
        for r in rows:
            k = _key(t, r)
            if k in self.rows and not t.upsert:
                continue  # DO NOTHING
            if (
                k in self.rows
                and t.keep_if_null is not None
                and r[t.columns.index(t.keep_if_null)] is None
            ):
                continue
            self.rows[k] = r

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.table.columns, r, strict=True)) for r in self.rows.values()]


class MemoryGexRepo:
    """메모리 구현 — Pg 와 같은 키·덮어쓰기 규칙. 수집 표는 `seed_*` 로만 채운다(시험)."""

    def __init__(self) -> None:
        self.tables: dict[str, _MemTable] = {
            t.qualified: _MemTable(t)
            for t in (
                RAW_MESSAGES, FUT_TICKS, OPT_TICKS, CHAIN_SNAPSHOTS, FUT_BOARD, INVESTOR_FLOW,
                SERIES_EXPIRIES, SESSION_LOG, HEALTH_EVENTS, *ENGINE_TABLES,
            )
        }  # fmt: skip
        # KRX 일별(legacy scheduler 가 쓴다) — 엔진이 읽는 칸만
        self.krx_options: list[dict[str, Any]] = []
        self.krx_futures: list[dict[str, Any]] = []

    def _t(self, table: GxTable) -> _MemTable:
        return self.tables[table.qualified]

    def _rows(self, table: GxTable) -> list[dict[str, Any]]:
        return self._t(table).dicts()

    # 시험 씨앗(수집 표 — 쓰기 주인은 legacy·flows.market_minute)
    def seed_chain(self, rows: Sequence[ChainRecord]) -> None:
        self._t(CHAIN_SNAPSHOTS).put(attr_row(CHAIN_SNAPSHOTS, r) for r in rows)

    def seed_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self._t(FUT_BOARD).put(attr_row(FUT_BOARD, r) for r in rows)

    def seed_investor(self, rows: Sequence[InvestorRecord]) -> None:
        self._t(INVESTOR_FLOW).put(attr_row(INVESTOR_FLOW, r) for r in rows)

    def seed_expiries(self, rows: Sequence[Any]) -> None:
        """ExpiryRecord 들."""
        self._t(SERIES_EXPIRIES).put(attr_row(SERIES_EXPIRIES, r) for r in rows)

    # 쓰기
    def write_levels(self, rows: Sequence[LevelRecord]) -> None:
        self._t(LEVELS).put(level_row(r) for r in rows)

    def write_metrics(self, rows: Sequence[MetricRecord]) -> None:
        self._t(METRICS).put(metric_row(r) for r in rows)

    def write_strike_gex(self, rows: Sequence[StrikeGexRecord]) -> None:
        self._t(STRIKE_GEX).put(attr_row(STRIKE_GEX, r) for r in rows)

    def write_option_iv(self, rows: Sequence[OptionIvRecord]) -> None:
        self._t(OPTION_IV).put(attr_row(OPTION_IV, r) for r in rows)

    def write_oi_changes(self, rows: Sequence[OiChangeRecord]) -> None:
        self._t(OI_CHANGES).put(attr_row(OI_CHANGES, r) for r in rows)

    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None:
        self._t(FUT_TICKS).put(attr_row(FUT_TICKS, r) for r in rows)

    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None:
        self._t(OPT_TICKS).put(attr_row(OPT_TICKS, r) for r in rows)

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        self._t(RAW_MESSAGES).put(raw_row(e) for e in envelopes)

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        self._t(SESSION_LOG).put(session_log_row(r) for r in rows)

    def write_health(self, events: Sequence[HealthEvent]) -> None:
        self._t(HEALTH_EVENTS).put(health_row(e) for e in events)

    # 엔진 읽기
    def _live(
        self, table: GxTable, trade_date: date, session: str, upto: datetime | None
    ) -> list[dict[str, Any]]:
        return [
            r
            for r in self._rows(table)
            if r["trade_date"] == trade_date
            and r["session"] == session
            and r["quality"] != "invalid"
            and (upto is None or r["ts"] <= upto)
        ]

    def _latest_by(self, rows: Iterable[dict[str, Any]], on: Sequence[str]) -> list[dict[str, Any]]:
        best: dict[tuple[object, ...], dict[str, Any]] = {}
        for r in rows:
            k = tuple(r[c] for c in on)
            if k not in best or r["ts"] > best[k]["ts"]:
                best[k] = r
        return [best[k] for k in sorted(best, key=lambda k: tuple(str(x) for x in k))]

    def chain_max_ts(
        self, trade_date: date, session: str, upto: datetime | None = None
    ) -> datetime | None:
        ts = [r["ts"] for r in self._live(CHAIN_SNAPSHOTS, trade_date, session, upto)]
        return max(ts, default=None)

    def chain_latest(self, trade_date: date, session: str, upto: datetime) -> list[ChainRecord]:
        rows = self._live(CHAIN_SNAPSHOTS, trade_date, session, upto)
        on = ("mrkt_cls", "expiry", "strike", "cp", "source")
        return [ChainRecord.model_validate(r) for r in self._latest_by(rows, on)]

    def futures_latest(self, trade_date: date, session: str, upto: datetime) -> list[FuturesRecord]:
        rows = [
            r for r in self._live(FUT_BOARD, trade_date, session, upto) if r["price"] is not None
        ]
        on = ("market", "code", "source")
        return [FuturesRecord.model_validate(r) for r in self._latest_by(rows, on)]

    def investor_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[InvestorRecord]:
        rows = self._live(INVESTOR_FLOW, trade_date, session, upto)
        on = ("market_code", "sector_code", "investor")
        return [InvestorRecord.model_validate(r) for r in self._latest_by(rows, on)]

    def expiry_dates(self, since: date) -> list[tuple[str, str, date, str]]:
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for r in self._rows(SERIES_EXPIRIES):
            if r["trade_date"] < since or r["quality"] == "invalid":
                continue
            k = (r["mrkt_cls"], r["expiry"])
            rank = (r["source"] == "kis", r["ts"])
            old = best.get(k)
            if old is None or rank > (old["source"] == "kis", old["ts"]):
                best[k] = r
        return [
            (c, e, best[(c, e)]["last_trade_date"], best[(c, e)]["source"]) for c, e in sorted(best)
        ]

    def metric_last(
        self, trade_date: date, session: str, metric: str, scope: str, key_prefix: str = ""
    ) -> list[MetricRecord]:
        rows = [
            m
            for m in self._metric_records()
            if m.trade_date == trade_date
            and m.session == session
            and m.metric == metric
            and m.scope == scope
            and m.key.startswith(key_prefix)
        ]
        if not rows:
            return []
        last = max(m.ts for m in rows)
        return sorted((m for m in rows if m.ts == last), key=lambda m: m.key)

    def metric_history(
        self, metric: str, scope: str, key: str, first: date, last: date
    ) -> list[MetricRecord]:
        out = [
            m
            for m in self._metric_records()
            if m.metric == metric and m.scope == scope and m.key == key
            and first <= m.trade_date <= last
        ]  # fmt: skip
        return sorted(out, key=lambda m: (m.trade_date, m.ts))

    def krx_option_days(self, first: date, last: date) -> list[date]:
        return sorted(
            {
                r["trade_date"]
                for r in self.krx_options
                if r["family"] == KRX_MONTHLY_FAMILY
                and r["session"] == "day"
                and first <= r["trade_date"] <= last
            }
        )

    def krx_iv_rows(
        self, trade_date: date, expiry: str
    ) -> list[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]]:
        rows = [
            r
            for r in self.krx_options
            if r["trade_date"] == trade_date
            and r["session"] == "day"
            and r["family"] == KRX_MONTHLY_FAMILY
            and r["expiry"] == expiry
        ]
        rows.sort(key=lambda r: (r["strike"], r["cp"]))
        return [
            (Decimal(r["strike"]), str(r["cp"]), r.get("close"), r.get("imp_volt"),
             r.get("acc_trdvol"))
            for r in rows
        ]  # fmt: skip

    def krx_futures_settles(self, first: date, last: date) -> list[tuple[date, str, Decimal]]:
        rows = [
            r
            for r in self.krx_futures
            if r["family"] == KRX_MONTHLY_FAMILY
            and r["session"] == "day"
            and r.get("expiry") is not None
            and r.get("setl_prc") is not None
            and first <= r["trade_date"] <= last
        ]
        rows.sort(key=lambda r: (r["trade_date"], r["expiry"]))
        return [(r["trade_date"], str(r["expiry"]), r["setl_prc"]) for r in rows]

    def opt_tick_days(self, before: date, limit: int) -> list[date]:
        if limit < 1:
            raise ValueError(f"limit 는 1 이상: {limit}")
        days = sorted({r["trade_date"] for r in self._rows(OPT_TICKS) if r["trade_date"] < before})
        return days[-limit:]

    def opt_tick_history(
        self, first: date, last: date, lookback: timedelta
    ) -> list[tuple[date, str, Decimal, int, float]]:
        ivs = [r for r in self._rows(OPTION_IV) if r["forward"] is not None]
        out: list[tuple[datetime, date, str, Decimal, int, float]] = []
        for t in self._rows(OPT_TICKS):
            if not (first <= t["trade_date"] <= last) or not t["qty"] or t["qty"] <= 0:
                continue
            if any(t[c] is None for c in ("mrkt_cls", "expiry", "strike", "cp")):
                continue
            cands = [
                o
                for o in ivs
                if (o["mrkt_cls"], o["expiry"], o["strike"], o["cp"])
                == (t["mrkt_cls"], t["expiry"], t["strike"], t["cp"])
                and t["ts"] - lookback < o["ts"] <= t["ts"]
            ]
            if not cands:
                continue
            f = max(cands, key=lambda o: o["ts"])["forward"]
            out.append((t["ts"], t["trade_date"], str(t["cp"]), Decimal(t["strike"]),
                        int(t["qty"]), float(f)))  # fmt: skip
        out.sort(key=lambda x: (x[1], x[0]))
        return [(d, cp, k, q, f) for _, d, cp, k, q, f in out]

    def kis_rest_outputs(
        self, tr_id: str, after: datetime, upto: datetime, output: str = "output1"
    ) -> list[tuple[datetime, date | None, str | None, dict[str, Any]]]:
        if not _IDENT.fullmatch(output):
            raise ValueError(f"output 은 소문자 식별자: {output!r}")
        out: list[tuple[datetime, date | None, str | None, dict[str, Any]]] = []
        for r in sorted(self._rows(RAW_MESSAGES), key=lambda r: r["ts"]):
            if r["tr_id"] != tr_id or r["source"] != "kis_rest" or not (after < r["ts"] <= upto):
                continue
            payload = json.loads(r["payload"]) if r["payload"] is not None else None
            o = payload.get(output) if isinstance(payload, dict) else None
            if isinstance(o, dict):
                out.append((r["ts"], r["trade_date"], r["session"], o))  # pyright: ignore[reportUnknownArgumentType]
        return out

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        names = set(kinds)
        rows = sorted(
            (
                (r["ts"], str(r["kind"]))
                for r in self._rows(HEALTH_EVENTS)
                if r["service"] == WS_GATEWAY_SERVICE and r["kind"] in names
            ),
            key=lambda x: x[0],
        )
        before = [k for ts, k in rows if ts < start]
        return (before[-1] if before else None), [(ts, k) for ts, k in rows if start <= ts <= end]

    # 화면 읽기
    def _levels(self) -> list[LevelRecord]:
        out: list[LevelRecord] = []
        for r in self._rows(LEVELS):
            d = dict(r)
            d["detail"] = json.loads(d["detail"])
            d["reasons"] = tuple(json.loads(d["reasons"]))
            out.append(LevelRecord.model_validate(d))
        return out

    def levels_latest(
        self, scope: LevelScope, upto: datetime | None = None, session: str | None = None
    ) -> list[LevelRecord]:
        rows = [
            r
            for r in self._levels()
            if r.scope == scope and (upto is None or r.ts <= upto)
            and (session is None or r.session == session)
        ]  # fmt: skip
        if not rows:
            return []
        last = max(r.ts for r in rows)
        return sorted((r for r in rows if r.ts == last), key=lambda r: r.name)

    def strike_profile(self, ts: datetime) -> list[StrikeGexRecord]:
        rows = [r for r in self._rows(STRIKE_GEX) if r["ts"] == ts]
        rows.sort(key=lambda r: (r["mrkt_cls"], r["expiry"], r["strike"]))
        return [StrikeGexRecord.model_validate(r) for r in rows]

    def _metric_records(self) -> list[MetricRecord]:
        out: list[MetricRecord] = []
        for r in self._rows(METRICS):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(MetricRecord.model_validate(d))
        return out

    def metrics_series(
        self, metric: str, start: datetime, end: datetime, scope: str = "all", key: str = ""
    ) -> list[MetricRecord]:
        rows = [
            m
            for m in self._metric_records()
            if m.metric == metric and m.scope == scope and m.key == key and start <= m.ts <= end
        ]
        return sorted(rows, key=lambda m: m.ts)

    def flags_of(self, ts: datetime) -> dict[str, Flag]:
        out: dict[str, Flag] = {r.name: r.flag for r in self._levels() if r.ts == ts}
        out.update({m.metric: m.flag for m in self._metric_records() if m.ts == ts})
        return out
