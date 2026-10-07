"""PostgreSQL + TimescaleDB 저장 (PLAN §4.5, docs/phase1_design.md §8). 스키마는 db/migrations.

- `PostgresSink` 는 poller `Sink` 프로토콜(services/poller/sink.py)을 그대로 구현한다. recorder 는
  같은 `write_raw` 를, auth 는 `AuthHealthSink` 어댑터를 쓴다
- 표마다 열 순서를 `Table` 상수 하나로 정한다. 레코드 → 행 튜플은 순수 함수(단위 테스트로 고정)
- SQL 은 상수 식별자로만 짓고(psycopg.sql) 값은 전부 파라미터로 보낸다. 묶음 하나 = 트랜잭션
  하나(executemany — psycopg 3 은 파이프라인으로 보낸다)
- 재적재 멱등: 유니크 키(001_init.sql)에 걸리면 시세·이벤트는 DO NOTHING, 나중에 값이 고쳐질 수
  있는 적재(분봉·KRX 일별·마스터·공백 판정)는 DO UPDATE. 이벤트성 표(raw·health·quarantine·
  session_log)는 내용 sha256(`digest`)을 키에 넣는다
- 연결 오류(`OperationalError`·`InterfaceError`)면 다시 연결해 `retries` 번 더 쓴다 — 멱등이라 첫
  시도가 사실 커밋된 경우에도 중복이 생기지 않는다. 데이터 오류면 바로 `StoreError`
- 로컬 디스크 큐(설계 §2 `db` 장애 시, `data/spool.py`): `spool` 을 주면 재시도까지 실패한 연결 오류
  묶음을 스풀에 넣고 돌아온다(예외 없음). 그 뒤 백오프(1초→30초) 동안은 DB 를 부르지 않고 바로
  스풀에 넣고, 백오프가 지나면 다음 쓰기(또는 `flush_spool`)가 스풀을 오래된 것부터 재적재한다.
  스풀에 남은 것이 있는 동안 새 묶음도 스풀 뒤에 붙인다 — 표마다 쓰는 순서가 지켜져 DO UPDATE
  표(분봉 등)에 옛 값이 새 값을 덮지 않는다. 재적재 한 번은 `replay_budget_s` 안에서 끊는다.
  재적재 묶음이 데이터 오류로 거부되면 한 행씩 다시 보내 들어가는 행은 넣고 거부된 행만 데드레터로
  (스풀 없는 경로에서 호출자가 한 건씩 살리는 것과 같게). 그러다 연결이 끊기면 묶음째 나중에
  (먼저 들어간 행은 유니크 키로 멱등).
  상한 초과로 버린 것·데드레터·스풀 시작·재적재 끝은 health(로그 + `on_health` + health_events 행 —
  DB 가 죽어 있으면 그 행도 스풀로)로 남긴다. 스풀이 없으면 연결 오류도 `StoreError`. 이전 실행이
  남긴 스풀이 있으면 처음부터 스풀 모드(`degraded`)로 시작해 알리고, 다 비우면 재적재 끝을 알린다
- 읽기(`krx_loaded`·`krx_option_listing` — scheduler KRX 적재의 이어 받기·마스터 대조, 무결측
  판정 입력 `series_dates`…`fut_tick_minutes`·개장 이중 확인 `fut_tick_count`·리포트
  `gap_reports`)는 스풀·백오프와 상관없이 DB 를 부른다(쌓을 수 없다). 연결 오류는 다시 연결해
  `retries` 번 더, 그래도 실패하거나 데이터 오류면 `StoreError` — 부르는 쪽이 처리한다
- 무결측 판정 쓰기(`replace_gap_report`)는 그 세션의 collection_gaps·collection_reports 를
  트랜잭션 하나에서 지우고 새로 쓴다 — 스풀 없이(판정은 다시 계산할 수 있다), 실패는 StoreError
- engine 산출(003_engine — `levels`·`metrics`·`strike_gex`·`option_iv`·`oi_changes`, Phase 3 설계
  §2)은 사이클 기준 시각(as_of)이 키에 들어 같은 사이클을 다시 계산하면 DO UPDATE(멱등) —
  `oi_changes` 의 첫 스냅샷 칸(증감 NULL)만은 저장된 칸을 덮지 않는다(`Table.keep_if_null`). engine
  입력 읽기(`chain_max_ts`·`chain_latest`·`futures_latest`·`expiry_dates`)는 위 읽기와 같다 —
  시리즈별 최신 행은 그 세션(귀속 거래일·세션) 행만, 검증 실패(invalid) 행은 뺀다. 일별 지표(IV
  랭크·IV − HV, metrics §5.4·§5.5)의 입력 읽기: 그 세션 마지막 사이클의 지표(`metric_last`)·일별
  이력(`metric_history`)·KRX 코스피200 월물 옵션 정규 행(`krx_option_days`·`krx_iv_rows`)·선물
  정규 정산가(`krx_futures_settles`). 플로우·선물(metrics §6·§7)의 입력 읽기: 그 세션 투자자별 최신
  행(`investor_latest`), 옵션 틱 거래일(`opt_tick_days`)·틱 표본과 그 앞 engine F(`opt_tick_history`
  — option_iv 를 LATERAL 로), KIS REST 원문의 output 한 칸(`kis_rest_outputs` — §7 선물 필드는 표에
  열이 없어 raw_messages 에서)
- 로그는 구조화 JSON 한 줄(서비스·거래일·세션·표·행 수). 접속 문자열·비밀번호·원문 payload 는
  싣지 않는다. 오류 문구는 첫 줄만, 접속 정보를 가리고 자른다. 해석 못 하는 접속 문자열은 만들 때
  문구 없이 거부하고, 접속 단계 오류는 종류·SQLSTATE 만 싣는다(libpq 가 조각을 인용한다)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NoReturn, Self
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from core.calendar import SessionInfo, State, TradingCalendar, day_bar_time, night_bar_time
from data.kis.master import MRKT_CLS_FAMILY, MasterRow
from data.kis.models import MinuteBar
from data.kis.ws import FuturesTick, OptionTick
from data.krx.models import KrxFuturesDaily, KrxOptionDaily
from data.spool import (
    DiskSpool,
    DropNotice,
    RejectedRows,
    ReplayResult,
    SpooledBatch,
    SpoolError,
    TransientWrite,
)
from services.auth.health import HealthEvent as AuthHealthEvent
from services.auth.health import Severity
from services.engine.records import (
    LevelRecord,
    MetricRecord,
    OiChangeRecord,
    OptionIvRecord,
    StrikeGexRecord,
)
from services.poller.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
)
from services.recorder.envelope import RawEnvelope

log = logging.getLogger("data.store")

Row = tuple[Any, ...]
Tag = tuple[date | None, str | None]  # (trade_date, session)
Tagger = Callable[[datetime], tuple[date | None, str | None]]
Connect = Callable[[], "psycopg.Connection[Any]"]
HealthHook = Callable[[AuthHealthEvent], None]

ERROR_MAX = 300
_SESSIONS = frozenset({"day", "night"})
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


# ── 표 명세 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Table:
    """INSERT 대상 표. 열 순서 = 행 튜플 순서. 키는 db/migrations 의 UNIQUE 와 같다."""

    name: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    upsert: bool = False  # True: 키 밖 열을 EXCLUDED 로 고친다, False: DO NOTHING
    jsonb: frozenset[str] = frozenset()
    # upsert 에서 이 열이 NULL 인 새 행은 이미 있는 행을 고치지 않는다(DO UPDATE … WHERE
    # EXCLUDED.<열> IS NOT NULL) — 같은 키의 정보가 적은 행이 저장된 행을 덮지 않게
    keep_if_null: str | None = None

    def __post_init__(self) -> None:
        for ident in (self.name, *self.columns):
            if not _IDENT.fullmatch(ident):
                raise ValueError(f"식별자는 소문자 snake_case 상수만: {ident!r}")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f"{self.name}: 열이 겹친다")
        if not set(self.key) <= set(self.columns) or not self.key:
            raise ValueError(f"{self.name}: 키 열은 열 목록 안에")
        if not self.jsonb <= set(self.columns):
            raise ValueError(f"{self.name}: jsonb 열은 열 목록 안에")
        keep = self.keep_if_null
        if keep is not None and (not self.upsert or keep not in self.update_columns):
            raise ValueError(f"{self.name}: keep_if_null 은 upsert 표의 키 밖 열")

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
            sql.Identifier(self.name),
            sql.SQL(", ").join(map(sql.Identifier, self.columns)),
            sql.SQL(", ").join(values),
            sql.SQL(", ").join(map(sql.Identifier, self.key)),
            action,
        )


_SNAP = ("ts", "trade_date", "session")

RAW_MESSAGES = Table(
    "raw_messages",
    (*_SNAP, "source", "tr_id", "key", "payload", "payload_text", "lossy", "digest"),
    key=("ts", "digest"),
    jsonb=frozenset({"payload"}),
)
CHAIN_SNAPSHOTS = Table(
    "chain_snapshots",
    (
        *_SNAP,
        "mrkt_cls",
        "expiry",
        "strike",
        "cp",
        "source",
        "code",
        "last",
        "bid",
        "ask",
        "oi",
        "oi_chg",
        "volume",
        "iv_kis",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
        "quality",
    ),
    key=("ts", "mrkt_cls", "expiry", "strike", "cp", "source"),
)
FUT_BOARD = Table(
    "fut_board",
    (
        *_SNAP,
        "market",
        "code",
        "source",
        "name",
        "price",
        "bid",
        "ask",
        "volume",
        "oi",
        "remaining_days",
        "quality",
    ),
    key=("ts", "market", "code", "source"),
)
INVESTOR_FLOW = Table(
    "investor_flow",
    (
        *_SNAP,
        "market_code",
        "sector_code",
        "investor",
        "sell_qty",
        "buy_qty",
        "net_qty",
        "sell_value",
        "buy_value",
        "net_value",
        "quality",
    ),
    key=("ts", "market_code", "sector_code", "investor"),
)
SERIES_EXPIRIES = Table(
    "series_expiries",
    (
        *_SNAP,
        "mrkt_cls",
        "expiry",
        "source",
        "last_trade_date",
        "calendar_date",
        "matches",
        "code",
        "quality",
    ),
    key=("ts", "mrkt_cls", "expiry", "source"),
)
HEALTH_EVENTS = Table(
    "health_events",
    (*_SNAP, "service", "kind", "level", "message", "detail", "digest"),
    key=("ts", "digest"),
    jsonb=frozenset({"detail"}),
)
QUARANTINE = Table(
    "quarantine",
    (*_SNAP, "source", "tr_id", "key", "payload", "lossy", "error", "digest"),
    key=("ts", "digest"),
    jsonb=frozenset({"payload"}),
)

MINUTE_BARS = Table(
    "minute_bars",
    (
        *_SNAP,
        "received_at",
        "code",
        "market",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "cum_value",
    ),
    key=("code", "market", "ts"),
    upsert=True,  # 적재 때 진행 중이던 마지막 봉은 다시 받으면 고친다
)
_KRX_OHLC = ("open", "high", "low", "close", "cmp_prev")
_KRX_ACC = ("acc_trdvol", "acc_trdval", "acc_opnint_qty")
KRX_FUT_DAILY = Table(
    "krx_fut_daily",
    (
        "trade_date",
        "session",
        "isu_cd",
        "ts",
        "isu_nm",
        "prod_nm",
        "family",
        "expiry",
        *_KRX_OHLC,
        "setl_prc",
        "spot_prc",
        *_KRX_ACC,
    ),
    key=("trade_date", "isu_cd", "session"),
    upsert=True,  # KRX 가 고쳐 다시 내면 최신 값
)
KRX_OPT_DAILY = Table(
    "krx_opt_daily",
    (
        "trade_date",
        "session",
        "isu_cd",
        "ts",
        "isu_nm",
        "prod_nm",
        "family",
        "expiry",
        "expiry_token",
        "strike",
        "cp",
        *_KRX_OHLC,
        "imp_volt",
        "nxtdd_bas_prc",
        *_KRX_ACC,
    ),
    key=("trade_date", "isu_cd", "session"),
    upsert=True,
)
MASTER_SNAPSHOTS = Table(
    "master_snapshots",
    (
        "trade_date",
        "session",
        "code",
        "ts",
        "kind",
        "isin",
        "name",
        "cp",
        "strike",
        "atm_cls",
        "family",
        "series",
        "expiry_token",
        "expiry",
        "underlying",
    ),
    key=("trade_date", "session", "code"),
    upsert=True,  # 같은 세션에 다시 내려받으면 최신 파일
)

_TICK = (
    *_SNAP,
    "received_at",
    "tr_id",
    "code",
    "seq",
)
_TICK_QTY = (
    "price",
    "qty",
    "cum_vol",
    "cum_value",
    "cum_buy_qty",
    "cum_sell_qty",
    "oi",
    "oi_chg",
    "bid",
    "ask",
)
FUT_TICKS = Table("fut_ticks", (*_TICK, *_TICK_QTY), key=("ts", "code", "seq"))
OPT_TICKS = Table(
    "opt_ticks",
    (
        *_TICK,
        "mrkt_cls",
        "expiry",
        "strike",
        "cp",
        *_TICK_QTY,
        "delta",
        "gamma",
        "vega",
        "theta",
        "rho",
        "iv",
    ),
    key=("ts", "code", "seq"),
)
SESSION_LOG = Table(
    "session_log",
    (*_SNAP, "service", "kind", "state", "prev_state", "detail", "digest"),
    key=("ts", "digest"),
    jsonb=frozenset({"detail"}),
)
COLLECTION_GAPS = Table(
    "collection_gaps",
    (
        "start_ts",
        "stream",
        "end_ts",
        "trade_date",
        "session",
        "expected",
        "received",
        "detected_at",
        "detail",
    ),
    key=("stream", "start_ts"),
    upsert=True,  # 같은 공백을 다시 판정하면(끝이 늘어남 등) 고친다
    jsonb=frozenset({"detail"}),
)

COLLECTION_REPORTS = Table(
    "collection_reports",
    (
        "trade_date",
        "session",
        "stream",
        "evaluated_at",
        "span_start",
        "span_end",
        "required",
        "status",
        "expected",
        "received",
        "gaps",
        "max_gap_s",
        "detail",
    ),
    key=("trade_date", "session", "stream"),
    upsert=True,  # 같은 세션을 다시 판정하면 최신 판정 (보통은 replace_gap_report 가 지우고 쓴다)
    jsonb=frozenset({"detail"}),
)

# ── engine 산출 (003_engine, Phase 3 설계 §2) — 같은 사이클을 다시 쓰면 최신 계산 ──

LEVELS = Table(
    "levels",
    (*_SNAP, "scope", "name", "value", "detail", "quality", "reasons", "flag"),  # flag: 004_flags
    key=("ts", "scope", "name"),
    upsert=True,
    jsonb=frozenset({"detail", "reasons"}),
)
METRICS = Table(
    "metrics",
    (*_SNAP, "metric", "scope", "key", "value", "payload", "quality", "flag"),
    key=("ts", "metric", "scope", "key"),
    upsert=True,
    jsonb=frozenset({"payload"}),
)
STRIKE_GEX = Table(
    "strike_gex",
    (
        *_SNAP,
        "mrkt_cls",
        "expiry",
        "strike",
        "gex_call",
        "gex_put",
        "gex",
        "forward",
        "excluded_oi_ratio",
        "quality",
    ),
    key=("ts", "mrkt_cls", "expiry", "strike"),
    upsert=True,
)
OPTION_IV = Table(
    "option_iv",
    (
        *_SNAP,
        "mrkt_cls",
        "expiry",
        "strike",
        "cp",
        "quote_source",
        "price",
        "price_kind",
        "prev_session",
        "oi",
        "iv",
        "source",
        "rescaled",
        "t_kis",
        "reason",
        "excluded",
        "delta",
        "gamma",
        "forward",
        "t_years",
        "quality",
    ),
    key=("ts", "mrkt_cls", "expiry", "strike", "cp"),
    upsert=True,
)
OI_CHANGES = Table(
    "oi_changes",
    (
        *_SNAP,
        "mrkt_cls",
        "expiry",
        "strike",
        "cp",
        "oi",
        "prev_ts",
        "prev_oi",
        "change",
        "outlier",
        "quality",
        "flag",  # 004_flags
    ),
    key=("ts", "mrkt_cls", "expiry", "strike", "cp"),
    upsert=True,  # 이상치로 고친 앞 칸·같은 사이클 재계산
    # 첫 스냅샷 칸(증감 없음)은 저장된 칸을 덮지 않는다 — 재기동한 engine 이 세션 중간에 같은 최신
    # 체인 행을 첫 스냅샷으로 다시 써도 증감·이상치 격리가 남는다(키 = 그 스냅샷 시각)
    keep_if_null="change",
)
ENGINE_TABLES: tuple[Table, ...] = (LEVELS, METRICS, STRIKE_GEX, OPTION_IV, OI_CHANGES)

TABLES: tuple[Table, ...] = (
    RAW_MESSAGES,
    FUT_TICKS,
    OPT_TICKS,
    CHAIN_SNAPSHOTS,
    FUT_BOARD,
    INVESTOR_FLOW,
    SERIES_EXPIRIES,
    MINUTE_BARS,
    KRX_FUT_DAILY,
    KRX_OPT_DAILY,
    MASTER_SNAPSHOTS,
    SESSION_LOG,
    HEALTH_EVENTS,
    COLLECTION_GAPS,
    QUARANTINE,
    COLLECTION_REPORTS,
    *ENGINE_TABLES,
)

# KRX 일별은 코스피200 계열만 적재한다(설계 §8) — 코스닥150 위클리는 세션 표기 없이 같은 ISU_CD 가
# 두 행씩 와서 (trade_date, isu_cd, session) 키가 겹친다
KRX_FAMILIES = frozenset(
    {"kospi200", "mini_kospi200", "kospi200_weekly_thu", "kospi200_weekly_mon"}
)
KrxKind = Literal["options", "futures"]
# engine 일별 지표(metrics §5.4·§5.5)가 읽는 KRX 상품군 — 코스피200 월물 옵션·선물(위클리·미니 제외)
KRX_MONTHLY_FAMILY = "kospi200"
KRX_TABLES: dict[KrxKind, Table] = {"options": KRX_OPT_DAILY, "futures": KRX_FUT_DAILY}


# ── 값 정리·해시 ─────────────────────────────────────────────────────────────


def clean_text(s: str) -> tuple[str, bool]:
    """text·jsonb 가 못 받는 문자(NUL, 짝 없는 서로게이트)를 U+FFFD·'?' 로 바꾼 값과 바꿨는지."""
    t = s.replace("\x00", "\ufffd")
    try:
        t.encode("utf-8")
    except UnicodeEncodeError:
        t = t.encode("utf-8", "replace").decode("utf-8")
    return t, t != s


def clean_texts(*values: str) -> tuple[list[str], bool]:
    """여러 text 열을 `clean_text` 로 — 바꾼 값들과 하나라도 바꿨는지."""
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
        for k, x in v.items():
            ck, lk = clean_text(str(k))
            cx, lx = _clean(x)
            out[ck] = cx
            lossy = lossy or lk or lx
        return out, lossy
    if isinstance(v, list | tuple):
        items = [_clean(x) for x in v]
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
    """거래일·세션은 둘 다 있거나 둘 다 없다(표 CHECK). 한쪽만이거나 모르는 세션이면 둘 다 NULL."""
    if trade_date is None or session not in _SESSIONS:
        return None, None
    return trade_date, session


# ── 레코드 → 행 (순수 함수) ──────────────────────────────────────────────────


def attr_row(table: Table, rec: object) -> Row:
    """열 이름 = 레코드 필드 이름인 표 (poller 레코드). 필드가 없으면 AttributeError."""
    return tuple(getattr(rec, c) for c in table.columns)


def raw_row(env: RawEnvelope) -> Row:
    """문자열 원문(웹소켓 프레임)은 payload_text, dict(REST·KRX 응답)는 payload(jsonb).

    tr_id·key 도 payload 처럼 정리한다 — NUL 하나면 묶음 전체가 거부된다. digest 는 넣는 값으로.
    """
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


def quarantine_row(r: QuarantineRecord) -> Row:
    td, ss = _tag(r.trade_date, r.session)
    payload, lossy = to_json(r.payload)
    (tr_id, key, error), lossy_text = clean_texts(r.tr_id, r.key, r.error)
    h = digest(r.source, tr_id, key, payload, error)
    return (r.ts, td, ss, r.source, tr_id, key, payload, lossy or lossy_text, error, h)


def health_row(ev: HealthEvent | AuthHealthEvent, tagger: Tagger | None = None) -> Row:
    """poller 이벤트는 그대로, auth 이벤트는 (at → ts, severity → level, detail 문장 → message).

    auth 이벤트엔 거래일·세션이 없어 tagger 가 있으면 그것으로 붙인다(실패하면 NULL).
    """
    if isinstance(ev, AuthHealthEvent):
        ts, level, message, detail = ev.at, ev.severity, ev.detail, {}
        td: date | None = None
        ss: str | None = None
        if tagger is not None:
            try:
                td, ss = tagger(ev.at)
            except Exception:  # 태깅 실패로 경고가 사라지지 않게
                td, ss = None, None
    else:
        ts, level, message, detail = ev.ts, ev.level, ev.message, ev.detail
        td, ss = ev.trade_date, ev.session
    td, ss = _tag(td, ss)
    (service, kind, msg), _ = clean_texts(ev.service, ev.kind, message)
    detail_json, _ = to_json(detail)
    h = digest(service, kind, level, msg, detail_json)
    return (ts, td, ss, service, kind, level, msg, detail_json, h)


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return ts.astimezone(UTC)


# ── poller 밖 입력 레코드 ────────────────────────────────────────────────────


class _Stamped(BaseModel):
    """시장 데이터 행 공통: ts(UTC)·trade_date·session (PLAN §4.5 공통)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date
    session: Literal["day", "night"]

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


class MinuteBarRecord(_Stamped):
    """`minute_bars` 한 행. ts = 봉 시각(UTC), received_at = 적재 때 받은 시각."""

    received_at: AwareDatetime
    code: str
    market: Literal["F", "CM"]  # 주간 F · 야간 CM
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None  # 봉 체결량
    cum_value: int | None = None  # 누적 거래대금(천원)

    @field_validator("received_at")
    @classmethod
    def _received_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _market_is_session(self) -> Self:
        if (self.market == "F") != (self.session == "day"):
            raise ValueError(f"시장 {self.market} 과 세션 {self.session} 이 맞지 않는다")
        return self

    @classmethod
    def from_kis(
        cls,
        bar: MinuteBar,
        *,
        code: str,
        market: Literal["F", "CM"],
        received_at: datetime,
        calendar: TradingCalendar,
    ) -> MinuteBarRecord:
        """KIS 분봉 → 행. 야간 CM 봉의 (야간 시작일, 24~30시)는 core.calendar 가 푼다(설계 §8)."""
        if market == "CM":
            ts, trade_date = night_bar_time(bar.stck_bsop_date, bar.stck_cntg_hour, calendar)
            session: Literal["day", "night"] = "night"
        else:
            ts, trade_date = day_bar_time(bar.stck_bsop_date, bar.stck_cntg_hour)
            session = "day"
        return cls(
            ts=ts,
            trade_date=trade_date,
            session=session,
            received_at=received_at,
            code=code,
            market=market,
            open=bar.futs_oprc,
            high=bar.futs_hgpr,
            low=bar.futs_lwpr,
            close=bar.futs_prpr,
            volume=bar.cntg_vol,
            cum_value=bar.acml_tr_pbmn,
        )


def krx_loadable(r: KrxFuturesDaily | KrxOptionDaily) -> bool:
    """적재 대상인가: 코스피200 계열이고 세션을 안다."""
    return r.family in KRX_FAMILIES and r.session is not None


def krx_futures_row(r: KrxFuturesDaily, ts: datetime) -> Row:
    """KRX 선물 일별 → 행. ts = 조회 시각. 적재 대상이 아니면 ValueError."""
    if not krx_loadable(r):
        raise ValueError(f"적재 대상이 아니다: {r.isu_nm!r}")
    return (
        r.bas_dd,
        r.session,
        r.isu_cd,
        _utc(ts),
        r.isu_nm,
        r.prod_nm,
        r.family,
        r.expiry,
        r.tdd_opnprc,
        r.tdd_hgprc,
        r.tdd_lwprc,
        r.tdd_clsprc,
        r.cmpprevdd_prc,
        r.setl_prc,
        r.spot_prc,
        r.acc_trdvol,
        r.acc_trdval,
        r.acc_opnint_qty,
    )


def krx_option_row(r: KrxOptionDaily, ts: datetime) -> Row:
    """KRX 옵션 일별 → 행. 야간 IMP_VOLT '0.00' 은 모델에서 이미 None(→ NULL)."""
    if not krx_loadable(r):
        raise ValueError(f"적재 대상이 아니다: {r.isu_nm!r}")
    return (
        r.bas_dd,
        r.session,
        r.isu_cd,
        _utc(ts),
        r.isu_nm,
        r.prod_nm,
        r.family,
        r.expiry,
        r.expiry_token,
        r.strike,
        r.cp,
        r.tdd_opnprc,
        r.tdd_hgprc,
        r.tdd_lwprc,
        r.tdd_clsprc,
        r.cmpprevdd_prc,
        r.imp_volt,
        r.nxtdd_bas_prc,
        r.acc_trdvol,
        r.acc_trdval,
        r.acc_opnint_qty,
    )


def master_row(r: MasterRow, *, ts: datetime, trade_date: date, session: str) -> Row:
    """마스터 한 줄 → 행. 선물·스프레드는 cp·strike·atm_cls 가 NULL."""
    if session not in _SESSIONS:
        raise ValueError(f"session 은 day·night: {session!r}")
    return (
        trade_date,
        session,
        r.code,
        _utc(ts),
        r.kind,
        r.isin,
        r.name,
        r.cp or None,
        r.strike,
        r.moneyness or None,
        r.family,
        r.series,
        r.expiry_token,
        r.expiry,
        r.underlying,
    )


# ── 웹소켓 체결 (ws-gateway) ────────────────────────────────────────────────

_FAMILY_MRKT_CLS = {family: cls for cls, family in MRKT_CLS_FAMILY.items()}


class _TickRecord(_Stamped):
    """체결 틱 공통. ts = 체결 시각(UTC — 원문 HHMMSS 를 세션 날짜로 푼 것은 ws-gateway 몫),
    seq = ws-gateway 수신 순번(같은 초·같은 종목 체결을 가른다 — 키 (ts, code, seq))."""

    received_at: AwareDatetime
    tr_id: str
    code: str
    seq: int = Field(ge=0)
    price: Decimal
    qty: int | None = None
    cum_vol: int | None = None
    cum_value: int | None = None
    cum_buy_qty: int | None = None  # 누적 매수 체결수량 (HIRO-lite, PLAN §5.5)
    cum_sell_qty: int | None = None
    oi: int | None = None
    oi_chg: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None

    @field_validator("received_at")
    @classmethod
    def _received_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)


def _tick_fields(tick: FuturesTick | OptionTick) -> dict[str, Any]:
    return {
        "session": tick.session,
        "tr_id": tick.tr_id,
        "code": tick.code,
        "price": tick.price,
        "qty": tick.qty,
        "cum_vol": tick.cum_vol,
        "cum_value": tick.cum_value,
        "cum_buy_qty": tick.cum_buy_qty,
        "cum_sell_qty": tick.cum_sell_qty,
        "oi": tick.oi,
        "oi_chg": tick.oi_chg,
        "bid": tick.bid,
        "ask": tick.ask,
    }


class FuturesTickRecord(_TickRecord):
    """`fut_ticks` 한 행 (H0IFCNT0 주간 · H0MFCNT0 야간)."""

    @classmethod
    def from_tick(
        cls, tick: FuturesTick, *, ts: datetime, trade_date: date, seq: int, received_at: datetime
    ) -> FuturesTickRecord:
        return cls(
            ts=ts, trade_date=trade_date, seq=seq, received_at=received_at, **_tick_fields(tick)
        )


class OptionTickRecord(_TickRecord):
    """`opt_ticks` 한 행 (H0IOCNT0 주간 · H0EUCNT0 야간). 시리즈 정보는 마스터에서."""

    mrkt_cls: str | None = None  # '' 월물 · WKM · WKI (미니 등은 None)
    expiry: str | None = Field(default=None, pattern=r"^\d{6}$")
    strike: Decimal | None = None
    cp: Literal["C", "P"] | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    vega: Decimal | None = None
    theta: Decimal | None = None
    rho: Decimal | None = None
    iv: Decimal | None = None  # %, KIS 0 은 값 없음 → None

    @classmethod
    def from_tick(
        cls,
        tick: OptionTick,
        *,
        ts: datetime,
        trade_date: date,
        seq: int,
        received_at: datetime,
        master: MasterRow | None = None,
    ) -> OptionTickRecord:
        """master 는 이 종목코드의 마스터 줄(없으면 만기·행사가·콜풋 NULL)."""
        series: dict[str, Any] = {}
        if master is not None:
            if master.code != tick.code:
                raise ValueError(f"마스터 코드 {master.code} ≠ 틱 코드 {tick.code}")
            series = {
                "mrkt_cls": _FAMILY_MRKT_CLS.get(master.family),
                "expiry": master.expiry,
                "strike": master.strike,
                "cp": master.cp or None,
            }
        return cls(
            ts=ts,
            trade_date=trade_date,
            seq=seq,
            received_at=received_at,
            delta=tick.delta,
            gamma=tick.gamma,
            vega=tick.vega,
            theta=tick.theta,
            rho=tick.rho,
            iv=tick.iv_kis,
            **_tick_fields(tick),
            **series,
        )


# ── 세션 기록·공백 (scheduler) ──────────────────────────────────────────────


class SessionLogRecord(BaseModel):
    """`session_log` 한 행 — 상태 전이, 캘린더/데이터 불일치('예상 밖 개장' 등, 설계 §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: AwareDatetime
    trade_date: date | None = None
    session: Literal["day", "night"] | None = None
    service: str = "scheduler"
    kind: str  # transition · calendar_mismatch · unexpected_open …
    state: State | None = None
    prev_state: State | None = None
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)

    @classmethod
    def transition(
        cls, ts: datetime, info: SessionInfo, prev: State | None, **detail: Any
    ) -> SessionLogRecord:
        """`core.calendar.state_at` 결과로 상태 전이 한 건."""
        return cls(
            ts=ts,
            trade_date=info.trade_date,
            session=info.session,
            kind="transition",
            state=info.state,
            prev_state=prev,
            detail=detail,
        )


class CollectionGapRecord(BaseModel):
    """`collection_gaps` 한 행 (설계 §9). 같은 (stream, start_ts) 를 다시 쓰면 고친다."""

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
    """`collection_reports` 한 행 — 세션 하나·스트림 하나의 무결측 판정 (설계 §9, 002)."""

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


def session_log_row(r: SessionLogRecord) -> Row:
    td, ss = _tag(r.trade_date, r.session)
    state = r.state.value if r.state is not None else None
    prev = r.prev_state.value if r.prev_state is not None else None
    (service, kind), _ = clean_texts(r.service, r.kind)
    detail, _ = to_json(r.detail)
    h = digest(service, kind, state or "", prev or "", detail)
    return (r.ts, td, ss, service, kind, state, prev, detail, h)


def gap_row(r: CollectionGapRecord) -> Row:
    detail, _ = to_json(r.detail)
    return (
        r.start_ts,
        r.stream,
        r.end_ts,
        r.trade_date,
        r.session,
        r.expected,
        r.received,
        r.detected_at,
        detail,
    )


def report_row(r: CollectionReportRecord) -> Row:
    detail, _ = to_json(r.detail)
    (stream,), _ = clean_texts(r.stream)
    return (
        r.trade_date,
        r.session,
        stream,
        r.evaluated_at,
        r.span_start,
        r.span_end,
        r.required,
        r.status,
        r.expected,
        r.received,
        r.gaps,
        r.max_gap_s,
        detail,
    )


def _like_prefix(prefix: str) -> str:
    """LIKE 앞머리 패턴 — `\\`·`%`·`_` 를 글자 그대로(ESCAPE '\\')."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _metric_record(cols: Sequence[str], row: Row) -> MetricRecord:
    """`metrics` 한 행 → MetricRecord(numeric → float, jsonb → dict)."""
    return MetricRecord.model_validate(dict(zip(cols, row, strict=True)))


def level_row(r: LevelRecord) -> Row:
    """`levels` 행 — detail·reasons 는 정규화 JSON(jsonb)."""
    detail, _ = to_json(r.detail)
    reasons, _ = to_json(list(r.reasons))
    return (
        r.ts,
        r.trade_date,
        r.session,
        r.scope,
        r.name,
        r.value,
        detail,
        r.quality,
        reasons,
        r.flag,
    )


def metric_row(r: MetricRecord) -> Row:
    """`metrics` 행 — payload 는 정규화 JSON(jsonb)."""
    payload, _ = to_json(r.payload)
    return (
        r.ts,
        r.trade_date,
        r.session,
        r.metric,
        r.scope,
        r.key,
        r.value,
        payload,
        r.quality,
        r.flag,
    )


# ── 싱크 ─────────────────────────────────────────────────────────────────────


class StoreError(RuntimeError):
    """저장 실패. 문구의 접속 정보는 가렸다. cause = 오류 종류(SQLSTATE) — 행 값 없는 짧은 사유."""

    def __init__(self, message: str, *, cause: str = "") -> None:
        super().__init__(message)
        self.cause = cause


class _Unavailable(Exception):
    """재시도까지 연결 오류 — 스풀이 있으면 스풀로, 없으면 StoreError."""

    def __init__(self, error: psycopg.Error, connecting: bool) -> None:
        super().__init__(type(error).__name__)
        self.error = error
        self.connecting = connecting


@dataclass
class StoreStats:
    batches: int = 0
    rows: int = 0  # 보낸 행 (유니크 키에 걸려 버려진 행 포함)
    reconnects: int = 0
    failures: int = 0
    by_table: dict[str, int] = field(default_factory=dict[str, int])
    spooled_batches: int = 0  # 스풀에 넣은 묶음 (DB 대신)
    spooled_rows: int = 0
    replayed_batches: int = 0  # 스풀에서 DB 로 다시 넣은 묶음
    replayed_rows: int = 0
    dropped_rows: int = 0  # 스풀 상한으로 버린 행
    dead_rows: int = 0  # 재적재 데이터 오류로 데드레터로 옮긴 행


def _parse_dsn(dsn: str) -> dict[str, Any] | None:
    """접속 문자열을 libpq 로 해석한다. 못 하면 None — 오류 문구는 버린다(틀린 조각을 인용한다)."""
    try:
        return conninfo_to_dict(dsn)
    except psycopg.Error:
        return None


def _secrets_of(dsn: str, password: object) -> tuple[str, ...]:
    """오류 문구에서 가릴 문자열: 접속 문자열 전체와 비밀번호(URL 인코딩 형태 포함)."""
    out = [dsn]
    if isinstance(password, str) and password:
        out += [password, quote(password, safe=""), quote(password)]
    return tuple(sorted({s for s in out if s}, key=len, reverse=True))


def _connect_dsn(dsn: str, service: str, timeout: int) -> psycopg.Connection[Any]:
    conn = psycopg.connect(
        dsn, autocommit=True, connect_timeout=timeout, application_name=f"gexlab-{service}"
    )
    conn.execute(sql.SQL("SET TIME ZONE 'UTC'"))  # 읽는 시각도 UTC (표시는 KST 로 따로)
    return conn


_TRANSIENT = (psycopg.OperationalError, psycopg.InterfaceError)
_BY_NAME = {t.name: t for t in TABLES}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class PostgresSink:
    """TimescaleDB 싱크 (poller `Sink` 프로토콜 + recorder·health 쓰기).

    한 연결을 잠금으로 나눠 쓴다(스레드 여럿이 써도 묶음 단위로 차례차례). `dsn` 이나 `connect`
    (테스트용 연결 공장) 중 하나를 준다. `spool` 을 주면 DB 장애 동안 디스크에 쌓는다(모듈 설명).
    `tagger`(시각 → 거래일·세션)는 묶음 태그가 없는 싱크 자신의 health(재적재 끝·데드레터·버림·
    이전 실행 스풀)에 붙인다 — 서비스는 `services.runtime.tagger_for(cal)` 을 준다.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        service: str = "store",
        connect: Connect | None = None,
        retries: int = 1,
        connect_timeout: int = 5,
        spool: DiskSpool | None = None,
        on_health: HealthHook | None = None,
        backoff_s: tuple[float, float] = (1.0, 30.0),
        replay_budget_s: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utcnow,
        tagger: Tagger | None = None,
    ) -> None:
        if retries < 0:
            raise ValueError("retries >= 0")
        if not 0 < backoff_s[0] <= backoff_s[1] or replay_budget_s <= 0:
            raise ValueError("0 < 백오프 처음 <= 최대, replay_budget_s > 0")
        self.service = service
        self.retries = retries
        self.stats = StoreStats()
        self._connect: Connect
        if dsn is not None and connect is None:
            params = _parse_dsn(dsn)
            if params is None:  # except 밖에서 올린다 — 원래 예외를 __context__ 에도 남기지 않게
                raise StoreError("DATABASE_URL 형식 오류 — 접속 문자열을 해석하지 못했다")
            url = dsn

            def connect_dsn() -> psycopg.Connection[Any]:
                return _connect_dsn(url, service, connect_timeout)

            self._connect = connect_dsn
            self._secrets = _secrets_of(dsn, params.get("password"))
        elif connect is not None and dsn is None:
            self._connect = connect
            self._secrets = ()
        else:
            raise ValueError("dsn 과 connect 중 하나만 준다")
        self._conn: psycopg.Connection[Any] | None = None
        self._lock = threading.Lock()
        self._sql = {t.name: t.insert_sql() for t in TABLES}
        self._spool = spool
        self._on_health = on_health
        self._backoff = backoff_s
        self._delay = 0.0  # 지금 백오프 길이 (0 = DB 정상으로 본다)
        self._down_until: float | None = None
        self._budget = replay_budget_s
        self._mono = monotonic
        self._now = now
        self._spooling = False  # 스풀 모드(연결 오류 뒤 ~ 스풀을 다 비울 때까지)
        self._hooks: list[AuthHealthEvent] = []  # 잠금 밖에서 on_health 로 넘길 것
        self._tagger = tagger
        if spool is not None and spool.pending:
            self._left_over(spool)

    def _left_over(self, spool: DiskSpool) -> None:
        """이전 실행이 남긴 스풀 — 처음부터 스풀 모드(degraded)로, 다 비우면 `db_spool_replayed`.
        알림 행은 스풀 뒤에 붙는다(여기서 DB 를 부르지 않는다). on_health 는 다음 쓰기·flush 에."""
        self._spooling = True
        st = spool.status()
        detail = (
            f"이전 실행이 남긴 스풀 {st.segments}세그먼트 {st.bytes}B({','.join(st.tables)}) — "
            "DB 로 재적재한다"
        )
        self._notice("db_spool_pending_at_start", detail, "warning", (None, None))

    @classmethod
    def from_settings(
        cls, *, service: str = "store", spool: bool = False, **kw: Any
    ) -> PostgresSink:
        """Settings 의 DATABASE_URL. spool=True 면 `spool_dir/<service>` 에 디스크 큐."""
        from config.settings import Settings

        settings = Settings()
        url = settings.database_url
        if url is None:
            raise StoreError("DATABASE_URL 이 없다")
        if spool and "spool" not in kw:
            kw["spool"] = DiskSpool(
                Path(settings.spool_dir) / service, max_bytes=settings.spool_max_mb << 20
            )
        return cls(url.get_secret_value(), service=service, **kw)

    def __repr__(self) -> str:  # 접속 문자열을 보이지 않는다
        return f"PostgresSink(service={self.service!r})"

    def __enter__(self) -> PostgresSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._drop()
            if self._spool is not None:
                self._spool.close()

    # ── poller Sink 프로토콜 ──

    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        self._write_attrs(CHAIN_SNAPSHOTS, rows)

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self._write_attrs(FUT_BOARD, rows)

    def write_investor(self, rows: Sequence[InvestorRecord]) -> None:
        self._write_attrs(INVESTOR_FLOW, rows)

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None:
        self._write_attrs(SERIES_EXPIRIES, rows)

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        if envelopes:
            first = envelopes[0]
            tag = _tag(first.trade_date, first.session)
            self._write(RAW_MESSAGES, [raw_row(e) for e in envelopes], tag)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        if rows:
            tag = _tag(rows[0].trade_date, rows[0].session)
            self._write(QUARANTINE, [quarantine_row(r) for r in rows], tag)

    def write_health(
        self, events: Sequence[HealthEvent | AuthHealthEvent], *, tagger: Tagger | None = None
    ) -> None:
        if events:
            out = [health_row(e, tagger) for e in events]
            self._write(HEALTH_EVENTS, out, (out[0][1], out[0][2]))

    # ── ws-gateway 체결 ──

    def write_fut_ticks(self, rows: Sequence[FuturesTickRecord]) -> None:
        self._write_attrs(FUT_TICKS, rows)

    def write_opt_ticks(self, rows: Sequence[OptionTickRecord]) -> None:
        self._write_attrs(OPT_TICKS, rows)

    # ── scheduler 기록 ──

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        if rows:
            out = [session_log_row(r) for r in rows]
            self._write(SESSION_LOG, out, (out[0][1], out[0][2]))

    def write_gaps(self, rows: Sequence[CollectionGapRecord]) -> None:
        if rows:
            self._write(
                COLLECTION_GAPS,
                [gap_row(r) for r in rows],
                _tag(rows[0].trade_date, rows[0].session),
            )

    # ── scheduler 적재 ──

    def write_minute_bars(self, rows: Sequence[MinuteBarRecord]) -> None:
        self._write_attrs(MINUTE_BARS, rows)

    def write_krx_futures(self, rows: Sequence[KrxFuturesDaily], *, ts: datetime) -> int:
        """코스피200 계열만 쓰고 쓴 행 수를 돌려준다(나머지는 건너뜀). ts = 조회 시각."""
        keep = [krx_futures_row(r, ts) for r in rows if krx_loadable(r)]
        self._write(KRX_FUT_DAILY, keep, _tag(keep[0][0], keep[0][1]) if keep else (None, None))
        return len(keep)

    def write_krx_options(self, rows: Sequence[KrxOptionDaily], *, ts: datetime) -> int:
        """코스피200 계열만 쓰고 쓴 행 수를 돌려준다. 야간 IMP_VOLT 는 NULL 로 남는다."""
        keep = [krx_option_row(r, ts) for r in rows if krx_loadable(r)]
        self._write(KRX_OPT_DAILY, keep, _tag(keep[0][0], keep[0][1]) if keep else (None, None))
        return len(keep)

    def krx_loaded(self, kind: KrxKind, trade_date: date) -> bool:
        """그 거래일(BAS_DD) KRX 일별 행이 DB 에 있나 — 있으면 다시 부르지 않는다(이어 받기).
        묶음 하나 = 트랜잭션 하나라 반쯤 들어간 날은 없다."""
        table = KRX_TABLES[kind]
        query = sql.SQL("SELECT EXISTS (SELECT 1 FROM {} WHERE trade_date = %s)").format(
            sql.Identifier(table.name)
        )
        rows = self._query(table, query, (trade_date,))
        return bool(rows and rows[0][0])

    def krx_option_listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]:
        """그 거래일 KRX 옵션 상장 목록 (상품군, 만기 6자리, 콜풋, 행사가) — 주간·야간 행을 하나로.
        마스터 ⊇ KRX 대조(설계 §5) 입력."""
        query = sql.SQL(
            "SELECT DISTINCT family, expiry, cp, strike FROM {} WHERE trade_date = %s "
            "ORDER BY family, expiry, cp, strike"
        ).format(sql.Identifier(KRX_OPT_DAILY.name))
        rows = self._query(KRX_OPT_DAILY, query, (trade_date,))
        return [(str(f), str(e), str(c), Decimal(k)) for f, e, c, k in rows]

    def write_master(
        self, rows: Sequence[MasterRow], *, ts: datetime, trade_date: date, session: str
    ) -> None:
        """PRE_DAY 는 (그날, day), PRE_NIGHT 는 (next_trading_day, night) 로 부른다."""
        out = [master_row(r, ts=ts, trade_date=trade_date, session=session) for r in rows]
        self._write(MASTER_SNAPSHOTS, out, _tag(trade_date, session))

    # ── 무결측 판정 (services/gaps.py·scheduler·scripts/nogap_report.py) ──
    # 읽기는 krx_loaded 와 같다 — 스풀·백오프와 상관없이 DB 를 부르고 실패는 StoreError

    def series_dates(
        self, trade_date: date, session: str
    ) -> list[tuple[str, str, str, date, datetime]]:
        """그 세션 series_expiries (시장분류, 만기, 원천, 최종거래일, 기록 시각) — 추적 만기용."""
        query = sql.SQL(
            "SELECT mrkt_cls, expiry, source, last_trade_date, ts FROM {} "
            "WHERE trade_date = %s AND session = %s ORDER BY ts"
        ).format(sql.Identifier(SERIES_EXPIRIES.name))
        rows = self._query(SERIES_EXPIRIES, query, (trade_date, session))
        return [(str(c), str(e), str(s), d, ts) for c, e, s, d, ts in rows]

    def board_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]:
        """그 세션 전광판 스냅샷 (시장분류, 만기, ts) — 검증 실패 행은 뺀다."""
        query = sql.SQL(
            "SELECT DISTINCT mrkt_cls, expiry, ts FROM {} WHERE trade_date = %s AND session = %s "
            "AND source = %s AND quality <> %s ORDER BY ts"
        ).format(sql.Identifier(CHAIN_SNAPSHOTS.name))
        rows = self._query(CHAIN_SNAPSHOTS, query, (trade_date, session, "board", "invalid"))
        return [(str(c), str(e), ts) for c, e, ts in rows]

    def fut_board_times(self, trade_date: date, session: str) -> list[datetime]:
        """그 세션 선물 전광판·단건 스냅샷 시각 — 검증 실패 행은 뺀다."""
        query = sql.SQL(
            "SELECT DISTINCT ts FROM {} WHERE trade_date = %s AND session = %s "
            "AND quality <> %s ORDER BY ts"
        ).format(sql.Identifier(FUT_BOARD.name))
        return [r[0] for r in self._query(FUT_BOARD, query, (trade_date, session, "invalid"))]

    def underlying_times(self, trade_date: date, session: str) -> list[datetime]:
        """그 세션 기초자산 조회 성공 응답 시각 — 응답 필드가 미실측이라 표 없이 원문만 남아(poller
        `_on_underlying`) 원문 일 키(`underlying|<파라미터>`)와 본문 `rt_cd` 0 으로 센다."""
        query = sql.SQL(
            "SELECT DISTINCT ts FROM {} WHERE trade_date = %s AND session = %s AND source = %s "
            "AND key LIKE %s AND payload->>'rt_cd' = %s ORDER BY ts"
        ).format(sql.Identifier(RAW_MESSAGES.name))
        params = (trade_date, session, "kis_rest", "underlying|%", "0")
        return [r[0] for r in self._query(RAW_MESSAGES, query, params)]

    def investor_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]:
        """그 세션 투자자별 (시장, 업종, ts)."""
        query = sql.SQL(
            "SELECT DISTINCT market_code, sector_code, ts FROM {} "
            "WHERE trade_date = %s AND session = %s AND quality <> %s ORDER BY ts"
        ).format(sql.Identifier(INVESTOR_FLOW.name))
        rows = self._query(INVESTOR_FLOW, query, (trade_date, session, "invalid"))
        return [(str(m), str(sc), ts) for m, sc, ts in rows]

    def fill1_times(self, trade_date: date, session: str) -> list[tuple[str, datetime]]:
        """그 세션 보강 1 성공 응답 (시리즈 라벨, ts) — chain_snapshots 에선 보강 2 와 구분되지
        않아 poller 가 원문에 붙인 일 키(`fill1:<시장분류|M>:<만기>:<행사가>:<콜풋>|…`)와 본문
        `rt_cd` 0 으로 센다."""
        query = sql.SQL(
            "SELECT DISTINCT split_part(key, ':', 2) || ':' || split_part(key, ':', 3), ts "
            "FROM {} WHERE trade_date = %s AND session = %s AND source = %s AND key LIKE %s "
            "AND payload->>'rt_cd' = %s ORDER BY ts"
        ).format(sql.Identifier(RAW_MESSAGES.name))
        params = (trade_date, session, "kis_rest", "fill1:%", "0")
        return [(str(k), ts) for k, ts in self._query(RAW_MESSAGES, query, params)]

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        """ws-gateway 연결 사건 — (start 전 마지막 사건 종류, [start, end] 안 (시각, 종류))."""
        table = sql.Identifier(HEALTH_EVENTS.name)
        before_q = sql.SQL(
            "SELECT kind FROM {} WHERE service = %s AND kind = ANY(%s) AND ts < %s "
            "ORDER BY ts DESC LIMIT 1"
        ).format(table)
        events_q = sql.SQL(
            "SELECT ts, kind FROM {} WHERE service = %s AND kind = ANY(%s) "
            "AND ts >= %s AND ts <= %s ORDER BY ts"
        ).format(table)
        names = list(kinds)
        before = self._query(HEALTH_EVENTS, before_q, ("ws-gateway", names, _utc(start)))
        rows = self._query(HEALTH_EVENTS, events_q, ("ws-gateway", names, _utc(start), _utc(end)))
        return (str(before[0][0]) if before else None), [(ts, str(k)) for ts, k in rows]

    def minute_bar_times(
        self, trade_date: date, session: str
    ) -> list[tuple[str, datetime, int | None]]:
        """그 세션 분봉 (종목, 봉 시각, 체결량)."""
        query = sql.SQL(
            "SELECT code, ts, volume FROM {} WHERE trade_date = %s AND session = %s "
            "ORDER BY code, ts"
        ).format(sql.Identifier(MINUTE_BARS.name))
        rows = self._query(MINUTE_BARS, query, (trade_date, session))
        return [(str(c), ts, None if v is None else int(v)) for c, ts, v in rows]

    def fut_tick_minutes(self, trade_date: date, session: str) -> list[tuple[str, datetime, int]]:
        """그 세션 웹소켓 선물 체결 — (종목, 분, 체결 수)."""
        query = sql.SQL(
            "SELECT code, date_trunc('minute', ts) AS m, count(*) FROM {} "
            "WHERE trade_date = %s AND session = %s GROUP BY code, m ORDER BY code, m"
        ).format(sql.Identifier(FUT_TICKS.name))
        rows = self._query(FUT_TICKS, query, (trade_date, session))
        return [(str(c), m, int(n)) for c, m, n in rows]

    def fut_tick_count(self, start: datetime, end: datetime) -> int:
        """체결 시각이 [start, end) 인 웹소켓 선물 체결 수 — 개장 이중 확인(설계 §6)."""
        query = sql.SQL("SELECT count(*) FROM {} WHERE ts >= %s AND ts < %s").format(
            sql.Identifier(FUT_TICKS.name)
        )
        rows = self._query(FUT_TICKS, query, (_utc(start), _utc(end)))
        return int(rows[0][0]) if rows else 0

    def gap_reports(self, first: date, last: date) -> list[CollectionReportRecord]:
        """[first, last] 거래일의 무결측 판정 리포트 (scripts/nogap_report.py)."""
        cols = COLLECTION_REPORTS.columns
        query = sql.SQL(
            "SELECT {} FROM {} WHERE trade_date >= %s AND trade_date <= %s "
            "ORDER BY trade_date, session, stream"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.Identifier(COLLECTION_REPORTS.name),
        )
        rows = self._query(COLLECTION_REPORTS, query, (first, last))
        return [
            CollectionReportRecord.model_validate(dict(zip(cols, r, strict=True))) for r in rows
        ]

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
        key = [(trade_date, session)]
        steps: list[tuple[Table, sql.Composed, list[Row]]] = [
            (COLLECTION_GAPS, where.format(sql.Identifier(COLLECTION_GAPS.name)), key),
            (COLLECTION_REPORTS, where.format(sql.Identifier(COLLECTION_REPORTS.name)), key),
            (COLLECTION_GAPS, self._sql[COLLECTION_GAPS.name], [gap_row(g) for g in gaps]),
            (
                COLLECTION_REPORTS,
                self._sql[COLLECTION_REPORTS.name],
                [report_row(r) for r in reports],
            ),
        ]
        self._atomic(steps, (trade_date, session))
        self._count(COLLECTION_GAPS, len(gaps))
        self._count(COLLECTION_REPORTS, len(reports))

    # ── engine 산출·입력 (003_engine, Phase 3 설계 §1·§2) ──

    def write_levels(self, rows: Sequence[LevelRecord]) -> None:
        if rows:
            self._write(
                LEVELS, [level_row(r) for r in rows], _tag(rows[0].trade_date, rows[0].session)
            )

    def write_metrics(self, rows: Sequence[MetricRecord]) -> None:
        if rows:
            out = [metric_row(r) for r in rows]
            self._write(METRICS, out, _tag(rows[0].trade_date, rows[0].session))

    def write_strike_gex(self, rows: Sequence[StrikeGexRecord]) -> None:
        self._write_attrs(STRIKE_GEX, rows)

    def write_option_iv(self, rows: Sequence[OptionIvRecord]) -> None:
        self._write_attrs(OPTION_IV, rows)

    def write_oi_changes(self, rows: Sequence[OiChangeRecord]) -> None:
        self._write_attrs(OI_CHANGES, rows)

    def chain_max_ts(
        self, trade_date: date, session: str, upto: datetime | None = None
    ) -> datetime | None:
        """그 세션 체인 행의 가장 늦은 수신 시각(upto 가 있으면 그때까지) — engine 따라잡기(10초,
        알림 여유보다 오래된 행). 검증 실패 행은 뺀다."""
        text = "SELECT max(ts) FROM {} WHERE trade_date = %s AND session = %s AND quality <> %s"
        params: tuple[object, ...] = (trade_date, session, "invalid")
        if upto is not None:
            text += " AND ts <= %s"
            params = (*params, _utc(upto))
        query = sql.SQL(text).format(sql.Identifier(CHAIN_SNAPSHOTS.name))
        rows = self._query(CHAIN_SNAPSHOTS, query, params)
        return rows[0][0] if rows and rows[0][0] is not None else None

    def chain_latest(self, trade_date: date, session: str, upto: datetime) -> list[ChainRecord]:
        """그 세션의 (시장분류, 만기, 행사가, 콜풋, 원천)별 upto 까지 최신 체인 행.

        세션 태그로만 고른다 — 야간 계산에 주간 전광판 행이 섞이지 않는다(설계 §1 분기 B).
        검증 실패(invalid) 행은 빼서 그 앞의 성한 행을 쓴다.
        """
        cols = CHAIN_SNAPSHOTS.columns
        query = sql.SQL(
            "SELECT DISTINCT ON (mrkt_cls, expiry, strike, cp, source) {} FROM {} "
            "WHERE trade_date = %s AND session = %s AND ts <= %s AND quality <> %s "
            "ORDER BY mrkt_cls, expiry, strike, cp, source, ts DESC"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.Identifier(CHAIN_SNAPSHOTS.name),
        )
        params = (trade_date, session, _utc(upto), "invalid")
        rows = self._query(CHAIN_SNAPSHOTS, query, params)
        return [ChainRecord.model_validate(dict(zip(cols, r, strict=True))) for r in rows]

    def futures_latest(self, trade_date: date, session: str, upto: datetime) -> list[FuturesRecord]:
        """그 세션의 (시장, 종목, 원천)별 upto 까지 최신 선물 행(값 있는 성한 행) — S_ref 입력."""
        cols = FUT_BOARD.columns
        query = sql.SQL(
            "SELECT DISTINCT ON (market, code, source) {} FROM {} "
            "WHERE trade_date = %s AND session = %s AND ts <= %s AND quality <> %s "
            "AND price IS NOT NULL ORDER BY market, code, source, ts DESC"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.Identifier(FUT_BOARD.name),
        )
        params = (trade_date, session, _utc(upto), "invalid")
        rows = self._query(FUT_BOARD, query, params)
        return [FuturesRecord.model_validate(dict(zip(cols, r, strict=True))) for r in rows]

    def expiry_dates(self, since: date) -> list[tuple[str, str, date, str]]:
        """시리즈별 최종거래일 (시장분류, 만기, 최종거래일, 원천) — since 뒤 기록 중 KIS 값이 캘린더
        값보다 먼저, 같은 원천이면 늦은 기록(무결측 판정 `series_dates` 와 같은 우선순위)."""
        query = sql.SQL(
            "SELECT DISTINCT ON (mrkt_cls, expiry) mrkt_cls, expiry, last_trade_date, source "
            "FROM {} WHERE trade_date >= %s AND quality <> %s "
            "ORDER BY mrkt_cls, expiry, (source = 'kis') DESC, ts DESC"
        ).format(sql.Identifier(SERIES_EXPIRIES.name))
        rows = self._query(SERIES_EXPIRIES, query, (since, "invalid"))
        return [(str(c), str(e), d, str(src)) for c, e, d, src in rows]

    # ── engine 일별 입력 (Phase 3 항목 2 — metrics §5.4·§5.5, POST_DAY) ──

    def metric_last(
        self, trade_date: date, session: str, metric: str, scope: str, key_prefix: str = ""
    ) -> list[MetricRecord]:
        """그 세션 metric(scope, key 가 key_prefix 로 시작)의 가장 늦은 사이클(ts) 행들, key 순 —
        일별 지표의 '주간 마감' 값(마지막 사이클의 월물 `atm_iv`)."""
        cols = METRICS.columns
        where = sql.SQL(
            "trade_date = %s AND session = %s AND metric = %s AND scope = %s "
            "AND key LIKE %s ESCAPE '\\'"
        )
        query = sql.SQL(
            "SELECT {cols} FROM {t} WHERE {w} AND ts = (SELECT max(ts) FROM {t} WHERE {w}) "
            "ORDER BY key"
        ).format(
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            t=sql.Identifier(METRICS.name),
            w=where,
        )
        params = (trade_date, session, metric, scope, _like_prefix(key_prefix)) * 2
        return [_metric_record(cols, r) for r in self._query(METRICS, query, params)]

    def metric_history(
        self, metric: str, scope: str, key: str, first: date, last: date
    ) -> list[MetricRecord]:
        """(metric, scope, key) 의 귀속 거래일 [first, last] 행 — 일별 이력(`atm_iv_daily`)."""
        cols = METRICS.columns
        query = sql.SQL(
            "SELECT {} FROM {} WHERE metric = %s AND scope = %s AND key = %s "
            "AND trade_date BETWEEN %s AND %s ORDER BY trade_date, ts"
        ).format(sql.SQL(", ").join(map(sql.Identifier, cols)), sql.Identifier(METRICS.name))
        rows = self._query(METRICS, query, (metric, scope, key, first, last))
        return [_metric_record(cols, r) for r in rows]

    def krx_option_days(self, first: date, last: date) -> list[date]:
        """코스피200 월물 옵션 정규(주간) KRX 일별 행이 있는 거래일 [first, last]."""
        query = sql.SQL(
            "SELECT DISTINCT trade_date FROM {} WHERE family = %s AND session = %s "
            "AND trade_date BETWEEN %s AND %s ORDER BY 1"
        ).format(sql.Identifier(KRX_OPT_DAILY.name))
        rows = self._query(KRX_OPT_DAILY, query, (KRX_MONTHLY_FAMILY, "day", first, last))
        return [r[0] for r in rows]

    def krx_iv_rows(
        self, trade_date: date, expiry: str
    ) -> list[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]]:
        """그날 코스피200 월물 옵션 한 만기의 정규 행 (행사가, 콜풋, 종가, IMP_VOLT %, 거래량)."""
        query = sql.SQL(
            "SELECT strike, cp, close, imp_volt, acc_trdvol FROM {} WHERE trade_date = %s "
            "AND session = %s AND family = %s AND expiry = %s ORDER BY strike, cp"
        ).format(sql.Identifier(KRX_OPT_DAILY.name))
        params = (trade_date, "day", KRX_MONTHLY_FAMILY, expiry)
        return [
            (Decimal(k), str(cp), c, iv, vol)
            for k, cp, c, iv, vol in self._query(KRX_OPT_DAILY, query, params)
        ]

    def krx_futures_settles(self, first: date, last: date) -> list[tuple[date, str, Decimal]]:
        """코스피200 선물 정규(주간) 정산가 (거래일, 결제월 YYYYMM, 정산가) — [first, last]."""
        query = sql.SQL(
            "SELECT trade_date, expiry, setl_prc FROM {} WHERE family = %s AND session = %s "
            "AND expiry IS NOT NULL AND setl_prc IS NOT NULL AND trade_date BETWEEN %s AND %s "
            "ORDER BY trade_date, expiry"
        ).format(sql.Identifier(KRX_FUT_DAILY.name))
        rows = self._query(KRX_FUT_DAILY, query, (KRX_MONTHLY_FAMILY, "day", first, last))
        return [(d, str(e), p) for d, e, p in rows]

    # ── engine 플로우·선물 입력 (Phase 3 항목 3 — metrics §6·§7) ──

    def investor_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[InvestorRecord]:
        """그 세션의 (시장, 업종, 투자자)별 upto 까지 최신 투자자별 행(검증 실패 행은 뺀다) —
        §6.2 시계열 그대로·§6.3 딜러 가정 점검(주간 마지막 행)."""
        cols = INVESTOR_FLOW.columns
        query = sql.SQL(
            "SELECT DISTINCT ON (market_code, sector_code, investor) {} FROM {} "
            "WHERE trade_date = %s AND session = %s AND ts <= %s AND quality <> %s "
            "ORDER BY market_code, sector_code, investor, ts DESC"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.Identifier(INVESTOR_FLOW.name),
        )
        params = (trade_date, session, _utc(upto), "invalid")
        rows = self._query(INVESTOR_FLOW, query, params)
        return [InvestorRecord.model_validate(dict(zip(cols, r, strict=True))) for r in rows]

    def opt_tick_days(self, before: date, limit: int) -> list[date]:
        """옵션 체결 틱이 있는 거래일 중 before 앞의 가장 최근 limit 개(오름차순) — §6.4 대량 체결
        기준의 창(기록이 20거래일 미만이면 비활성)."""
        if limit < 1:
            raise ValueError(f"limit 는 1 이상: {limit}")
        query = sql.SQL(
            "SELECT trade_date FROM (SELECT DISTINCT trade_date FROM {} WHERE trade_date < %s) d "
            "ORDER BY trade_date DESC LIMIT %s"
        ).format(sql.Identifier(OPT_TICKS.name))
        rows = self._query(OPT_TICKS, query, (before, limit))
        return sorted(r[0] for r in rows)

    def opt_tick_history(
        self, first: date, last: date, lookback: timedelta
    ) -> list[tuple[date, str, Decimal, int, float]]:
        """[first, last] 거래일 옵션 체결 틱 (거래일, 콜풋, 행사가, 1틱 체결량, 그 틱 앞 lookback
        안의 같은 종목 가장 최근 engine F) — §6.4 머니니스 구간(K/F − 1)의 표본. 시리즈를 모르는 틱
        (마스터 밖)·체결량 없는 틱·F 를 못 찾은 틱은 뺀다."""
        query = sql.SQL(
            "SELECT t.trade_date, t.cp, t.strike, t.qty, f.forward FROM {t} t "
            "JOIN LATERAL (SELECT o.forward FROM {o} o WHERE o.mrkt_cls = t.mrkt_cls "
            "AND o.expiry = t.expiry AND o.strike = t.strike AND o.cp = t.cp AND o.ts <= t.ts "
            "AND o.ts > t.ts - %s AND o.forward IS NOT NULL ORDER BY o.ts DESC LIMIT 1) f ON true "
            "WHERE t.trade_date BETWEEN %s AND %s AND t.qty > 0 AND t.mrkt_cls IS NOT NULL "
            "AND t.expiry IS NOT NULL AND t.strike IS NOT NULL AND t.cp IS NOT NULL "
            "ORDER BY t.trade_date, t.ts"
        ).format(t=sql.Identifier(OPT_TICKS.name), o=sql.Identifier(OPTION_IV.name))
        rows = self._query(OPT_TICKS, query, (lookback, first, last))
        return [(d, str(cp), Decimal(k), int(q), float(f)) for d, cp, k, q, f in rows]

    def kis_rest_outputs(
        self, tr_id: str, after: datetime, upto: datetime, output: str = "output1"
    ) -> list[tuple[datetime, date | None, str | None, dict[str, Any]]]:
        """(after, upto] 에 녹화된 KIS REST 응답(raw_messages)의 output 한 칸 (ts, 거래일, 세션,
        그 칸) — 시각 순. §7 선물 필드(분봉 조회 output1)를 원문에서 읽는다(표에 열이 없다). 그
        칸이 객체가 아닌 응답은 뺀다."""
        if not _IDENT.fullmatch(output):
            raise ValueError(f"output 은 소문자 식별자: {output!r}")
        query = sql.SQL(
            "SELECT ts, trade_date, session, payload -> %s::text FROM {} WHERE tr_id = %s "
            "AND source = %s AND ts > %s AND ts <= %s ORDER BY ts"
        ).format(sql.Identifier(RAW_MESSAGES.name))
        params = (output, tr_id, "kis_rest", _utc(after), _utc(upto))
        rows = self._query(RAW_MESSAGES, query, params)
        return [(ts, d, ss, o) for ts, d, ss, o in rows if isinstance(o, dict)]

    # ── 섀도 운영 점검 (scripts/shadow_report.py — Phase 3 설계 §4, metrics §8) ──

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
        ).format(
            m=sql.Identifier(METRICS.name),
            lv=sql.Identifier(LEVELS.name),
            oi=sql.Identifier(OI_CHANGES.name),
        )
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
        증감 없는 칸은 {'first_snapshot': 직전 스냅샷이 없다}. 명세가 허용한 null 인지는 부르는
        쪽이 칸으로 가린다."""
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
            m=sql.Identifier(METRICS.name),
            lv=sql.Identifier(LEVELS.name),
            oi=sql.Identifier(OI_CHANGES.name),
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
        없고(POST_DAY 등 세션 밖) 시각이 [start, end) 인 것. 같은 (종류, 대상) health 는 engine 이
        10분에 한 번만 내므로 건수는 묶음 수다."""
        query = sql.SQL(
            "SELECT kind, message, count(*) FROM {} WHERE service = %s AND kind = ANY(%s) "
            "AND ((trade_date BETWEEN %s AND %s) OR (trade_date IS NULL AND ts >= %s AND ts < %s)) "
            "GROUP BY kind, message ORDER BY kind, message"
        ).format(sql.Identifier(HEALTH_EVENTS.name))
        params = ("engine", list(kinds), first, last, _utc(start), _utc(end))
        rows = self._query(HEALTH_EVENTS, query, params)
        return [(str(k), str(m), int(n)) for k, m, n in rows]

    # ── 스풀 ──

    @property
    def spool(self) -> DiskSpool | None:
        return self._spool

    def flush_spool(self) -> bool:
        """백오프가 지났으면 스풀을 재적재한다(한 번에 `replay_budget_s` 까지). 비었으면 True.

        서비스는 쓰기가 없어도 스풀이 비워지게 주기적으로(초 단위) 부른다.
        """
        if self._spool is None:
            return True
        try:
            with self._lock:
                if self._spool.pending and not self._down():
                    self._drain()
                return not self._spool.pending
        finally:
            self._dispatch_hooks()

    @property
    def degraded(self) -> bool:
        """DB 대신 스풀에 쓰는 중인가 (health 용)."""
        return self._spooling

    # ── 내부 ──

    def _need_spool(self) -> DiskSpool:
        if self._spool is None:
            raise RuntimeError("스풀 없이 부르지 않는다")
        return self._spool

    def _write_attrs(self, table: Table, rows: Sequence[Any]) -> None:
        if rows:
            tag = _tag(rows[0].trade_date, rows[0].session)
            self._write(table, [attr_row(table, r) for r in rows], tag)

    def _connection(self) -> psycopg.Connection[Any]:
        c = self._conn
        if c is None or c.closed or c.broken:
            self._drop()
            c = self._conn = self._connect()
        return c

    def _drop(self) -> None:
        c, self._conn = self._conn, None
        if c is not None:
            try:
                c.close()
            except Exception as e:  # 이미 끊긴 연결 — 닫기 실패는 무시하고 새로 연다
                log.debug("close 실패: %s", type(e).__name__)

    def _write(self, table: Table, rows: Sequence[Row], tag: Tag) -> None:
        """묶음 하나. 스풀이 있으면 연결 오류·백오프·밀린 스풀 동안 스풀에 넣는다."""
        if not rows:
            return
        try:
            self._write_locked(table, rows, tag)
        finally:
            self._dispatch_hooks()

    def _write_locked(self, table: Table, rows: Sequence[Row], tag: Tag) -> None:
        with self._lock:
            sp = self._spool
            if sp is not None and (sp.pending or self._down()):
                if not self._down():
                    self._drain()
                if sp.pending or self._down():
                    self._to_spool(table, rows, tag)
                    return
            try:
                self._send(table, rows, tag)
            except _Unavailable as u:
                if sp is None:
                    self._fail(table, len(rows), tag, u.error, connecting=u.connecting)
                self.stats.failures += 1
                self._log(
                    logging.ERROR, "db_write_failed", table, len(rows), tag, u.error, u.connecting
                )
                self._went_down(tag, table, u)
                self._to_spool(table, rows, tag)
                return
            self._up()
            self._count(table, len(rows))
            self._recovered()

    def _send(self, table: Table, rows: Sequence[Row], tag: Tag) -> None:
        """트랜잭션 하나로 보낸다. 연결 오류는 다시 연결해 재시도, 그래도 실패면 `_Unavailable`,
        나머지(데이터·제약 오류)는 바로 StoreError."""
        query = self._sql[table.name]
        attempt = 0
        while True:
            connecting = True
            try:
                conn = self._connection()
                connecting = False
                with conn.transaction(), conn.cursor() as cur:
                    cur.executemany(query, rows)
                return
            except _TRANSIENT as e:  # 끊김·접속 실패 — 새 연결로 다시
                self._drop()
                if attempt >= self.retries:
                    raise _Unavailable(e, connecting) from None
                attempt += 1
                self.stats.reconnects += 1
                self._log(logging.WARNING, "db_reconnect", table, len(rows), tag, e, connecting)
            except psycopg.Error as e:  # 데이터·제약 오류 — 다시 보내도 같다
                self._fail(table, len(rows), tag, e, connecting=connecting)

    def _query(self, table: Table, query: sql.Composed, params: tuple[Any, ...]) -> list[Row]:
        """읽기 한 번 (잠금 안). 연결 오류는 다시 연결해 retries 번 더, 그래도 실패면 StoreError.
        데이터 오류도 StoreError. 스풀·백오프를 보지 않는다 — 읽기는 쌓아 둘 수 없다."""
        tag: Tag = (None, None)
        with self._lock:
            attempt = 0
            while True:
                connecting = True
                try:
                    conn = self._connection()
                    connecting = False
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                        return [tuple(r) for r in cur.fetchall()]
                except _TRANSIENT as e:
                    self._drop()
                    if attempt >= self.retries:
                        self._fail(table, 0, tag, e, connecting=connecting, event="db_read_failed")
                    attempt += 1
                    self.stats.reconnects += 1
                    self._log(logging.WARNING, "db_reconnect", table, 0, tag, e, connecting)
                except psycopg.Error as e:
                    self._fail(table, 0, tag, e, connecting=connecting, event="db_read_failed")

    def _atomic(self, steps: Sequence[tuple[Table, sql.Composed, Sequence[Row]]], tag: Tag) -> None:
        """여러 문장을 트랜잭션 하나로 (잠금 안). 연결 오류는 다시 연결해 retries 번 더, 그래도
        실패하거나 데이터 오류면 StoreError. 스풀을 쓰지 않는다."""
        head = steps[-1][0]
        n = sum(len(rows) for _, _, rows in steps)
        with self._lock:
            attempt = 0
            while True:
                connecting = True
                try:
                    conn = self._connection()
                    connecting = False
                    with conn.transaction(), conn.cursor() as cur:
                        for _, query, rows in steps:
                            if rows:
                                cur.executemany(query, rows)
                    return
                except _TRANSIENT as e:
                    self._drop()
                    if attempt >= self.retries:
                        self._fail(head, n, tag, e, connecting=connecting)
                    attempt += 1
                    self.stats.reconnects += 1
                    self._log(logging.WARNING, "db_reconnect", head, n, tag, e, connecting)
                except psycopg.Error as e:
                    self._fail(head, n, tag, e, connecting=connecting)

    def _count(self, table: Table, n: int) -> None:
        self.stats.batches += 1
        self.stats.rows += n
        self.stats.by_table[table.name] = self.stats.by_table.get(table.name, 0) + n

    # 백오프: 연결 오류 뒤 이 시각까지 DB 를 부르지 않는다
    def _down(self) -> bool:
        return self._down_until is not None and self._mono() < self._down_until

    def _up(self) -> None:
        self._delay = 0.0
        self._down_until = None

    def _bump_backoff(self) -> None:
        first, most = self._backoff
        self._delay = first if self._delay <= 0 else min(self._delay * 2, most)
        self._down_until = self._mono() + self._delay

    def _went_down(self, tag: Tag, table: Table, u: _Unavailable) -> None:
        self._bump_backoff()
        if not self._spooling:
            self._spooling = True
            detail = (
                f"DB 쓰기 실패({type(u.error).__name__}) — {table.name} 부터 로컬 스풀에 쌓는다, "
                f"{self._delay:g}초 뒤 재적재 시도"
            )
            self._notice("db_spooling", detail, "warning", tag)

    def _to_spool(self, table: Table, rows: Sequence[Row], tag: Tag) -> None:
        sp = self._need_spool()
        try:
            drops = sp.append(table.name, table.columns, rows, tag)
        except SpoolError as e:
            self.stats.failures += 1
            self._log_event(
                logging.CRITICAL, "spool_write_failed", table.name, len(rows), tag, str(e)
            )
            self._hook(
                AuthHealthEvent(
                    "spool_write_failed",
                    f"{table.name} {len(rows)}행 스풀 실패: {e}",
                    self._now(),
                    "critical",
                    service=self.service,
                )
            )
            raise StoreError(f"{table.name}: DB·스풀 모두 실패 — {e}") from None
        if not any(d.reason == "too_large" for d in drops):
            self.stats.spooled_batches += 1
            self.stats.spooled_rows += len(rows)
            self._log_event(logging.INFO, "db_spooled", table.name, len(rows), tag, None)
        for d in drops:
            self._dropped(d, tag)

    def _dropped(self, d: DropNotice, tag: Tag) -> None:
        self.stats.dropped_rows += d.rows
        span = ""
        if d.oldest_at is not None and d.newest_at is not None:
            span = f", 스풀 시각 {d.oldest_at.isoformat()} ~ {d.newest_at.isoformat()}"
        what = {
            "oldest": "스풀 상한 — 가장 오래된 세그먼트를 버렸다",
            "too_large": "묶음 하나가 스풀 상한보다 커서 버렸다",
            "dead_full": "데드레터 상한 — 들어가지 않는 묶음을 버렸다",
        }[d.reason]
        detail = f"{what}: {','.join(d.tables)} {d.batches}묶음 {d.rows}행 {d.bytes}B{span}"
        self._notice("spool_dropped", detail, "critical", tag)

    def _drain(self) -> bool:
        """스풀을 오래된 것부터 DB 로 (잠금 안). 다 비우면 True."""
        sp = self._need_spool()

        def write(b: SpooledBatch) -> None:
            table = _BY_NAME.get(b.table)
            if table is None or table.columns != b.columns:
                raise StoreError(f"{b.table}: 표·열 정의가 스풀과 다르다")
            try:
                self._send(table, b.rows, b.tag)
            except _Unavailable:
                raise TransientWrite from None
            except StoreError as e:
                if len(b.rows) <= 1:
                    raise
                self._salvage(table, b, e.cause or type(e).__name__)
                return
            self._count(table, len(b.rows))

        res = sp.replay(write, deadline=self._mono() + self._budget, clock=self._mono)
        self._replayed(res)
        if res.stopped == "transient":
            self._bump_backoff()
            return False
        if sp.pending:
            return False
        self._up()
        self._recovered()
        return True

    def _salvage(self, table: Table, b: SpooledBatch, cause: str) -> None:
        """데이터 오류로 거부된 재적재 묶음을 한 행씩 다시 보낸다. 거부된 행은 `RejectedRows` 로
        (스풀이 그 행들만 데드레터로), 도중 연결 오류면 `TransientWrite`(묶음째 나중에 — 먼저 들어간
        행은 유니크 키로 멱등이라 겹치지 않는다)."""
        bad: list[Row] = []
        for row in b.rows:
            try:
                self._send(table, [row], b.tag)
            except _Unavailable:
                raise TransientWrite from None
            except StoreError:
                bad.append(row)
        if len(bad) < len(b.rows):
            self._count(table, len(b.rows) - len(bad))
        if bad:
            raise RejectedRows(bad, cause)

    def _recovered(self) -> None:
        """스풀 모드였고 스풀이 비었으면 끝났다고 알린다."""
        if not self._spooling or (self._spool is not None and self._spool.pending):
            return
        self._spooling = False
        s = self.stats
        detail = (
            f"DB 복구 — 스풀을 모두 재적재했다 (누적 {s.replayed_batches}묶음 "
            f"{s.replayed_rows}행, 버림 {s.dropped_rows}행, 데드레터 {s.dead_rows}행)"
        )
        self._notice("db_spool_replayed", detail, "info", (None, None))

    def _tag_at(self, t: datetime) -> Tag:
        """묶음 태그가 없는 싱크 자신의 health·로그 태그: tagger(시각). 없거나 실패하면 NULL."""
        if self._tagger is None:
            return None, None
        try:
            td, ss = self._tagger(t)
        except Exception:  # 태깅 실패로 알림이 사라지지 않게
            return None, None
        return _tag(td, ss)

    def _replayed(self, res: ReplayResult) -> None:
        self.stats.replayed_batches += res.batches
        self.stats.replayed_rows += res.rows
        self.stats.dead_rows += res.dead_rows
        tag = self._tag_at(self._now())
        if res.batches or res.dead_batches or res.corrupt_lines:
            rec = {
                "service": self.service,
                "component": "store",
                "event": "db_spool_replay",
                "trade_date": tag[0].isoformat() if tag[0] else None,
                "session": tag[1],
                "batches": res.batches,
                "rows": res.rows,
                "dead_batches": res.dead_batches,
                "corrupt_lines": res.corrupt_lines,
                "stopped": res.stopped,
            }
            log.info(json.dumps(rec, ensure_ascii=False))
        if res.dead_batches or res.corrupt_lines:
            detail = (
                f"재적재 안 되는 묶음 {res.dead_batches}개({res.dead_rows}행)·깨진 줄 "
                f"{res.corrupt_lines}개를 데드레터로 옮겼다: {'; '.join(res.errors)}"
            )
            self._notice("spool_dead_letter", detail, "critical", tag)
        for d in res.drops:
            self._dropped(d, tag)

    def _notice(self, kind: str, detail: str, severity: Severity, tag: Tag) -> None:
        """스풀 health: 로그 + on_health + health_events 행(DB 가 죽어 있으면 스풀로). 태그가 없으면
        (재적재 끝·이전 실행 스풀 등) tagger 로 붙인다."""
        ev = AuthHealthEvent(kind, detail, self._now(), severity, service=self.service)
        if tag == (None, None):
            tag = self._tag_at(ev.at)
        level = {"info": logging.INFO, "warning": logging.WARNING}.get(severity, logging.CRITICAL)
        rec = {
            "service": self.service,
            "component": "store",
            "event": kind,
            "trade_date": tag[0].isoformat() if tag[0] else None,
            "session": tag[1],
            "detail": ev.detail,
        }
        log.log(level, json.dumps(rec, ensure_ascii=False))
        self._hook(ev)
        row = health_row(ev, lambda _: tag)
        sp = self._spool
        try:
            if sp is not None and (sp.pending or self._down()):
                drops = sp.append(HEALTH_EVENTS.name, HEALTH_EVENTS.columns, [row], tag)
                self.stats.spooled_batches += 1
                self.stats.spooled_rows += 1
                for d in drops:
                    self.stats.dropped_rows += d.rows  # 여기서 또 health 를 만들지 않는다(로그만)
                    log.critical(json.dumps({**rec, "event": "spool_dropped", "rows": d.rows}))
            else:
                self._send(HEALTH_EVENTS, [row], tag)
                self._count(HEALTH_EVENTS, 1)
        except (SpoolError, StoreError, _Unavailable) as e:
            log.error(
                json.dumps({**rec, "event": "health_write_failed", "error": type(e).__name__})
            )

    def _hook(self, ev: AuthHealthEvent) -> None:
        """on_health 는 잠금을 푼 뒤 부른다(`_dispatch_hooks`) — 거기서 이 싱크에 다시 써도 된다."""
        if self._on_health is not None:
            self._hooks.append(ev)

    def _dispatch_hooks(self) -> None:
        if self._on_health is None or not self._hooks:
            return
        with self._lock:
            events, self._hooks = self._hooks, []
        for ev in events:
            try:
                self._on_health(ev)
            except Exception as e:
                log.error("on_health 실패: %s", type(e).__name__)

    def _fail(
        self,
        table: Table,
        n: int,
        tag: Tag,
        e: psycopg.Error,
        *,
        connecting: bool,
        event: str = "db_write_failed",
    ) -> NoReturn:
        self.stats.failures += 1
        self._log(logging.ERROR, event, table, n, tag, e, connecting)
        state = getattr(e, "sqlstate", None)
        cause = f"{type(e).__name__}({state})" if state else type(e).__name__
        raise StoreError(
            f"{table.name}: {self._describe(e, connecting=connecting)}", cause=cause
        ) from None

    def _describe(self, e: BaseException, *, connecting: bool) -> str:
        """예외 종류·SQLSTATE·첫 줄(접속 정보 가림, ERROR_MAX 자). DETAIL(실패 행)은 뺀다.

        접속 단계 오류는 문구를 싣지 않는다 — libpq 는 접속 문자열을 잘못 나눈 조각(URL 비밀번호의
        '@' 뒤가 호스트로 가는 등)을 인용해, 비밀번호 전체만 가려서는 일부가 샌다. 서버가 거절한
        경우(비밀번호 틀림 28P01·DB 없음 3D000 등)는 SQLSTATE 로 가른다.
        """
        state = getattr(e, "sqlstate", None)
        head = f"{type(e).__name__}({state})" if state else type(e).__name__
        if connecting:
            return f"{head}: 접속 실패 (문구는 접속 정보가 섞일 수 있어 싣지 않는다)"
        lines = str(e).strip().splitlines()
        msg = lines[0] if lines else ""
        for s in self._secrets:
            msg = msg.replace(s, "***")
        text = f"{head}: {msg}"
        return text if len(text) <= ERROR_MAX else text[: ERROR_MAX - 1] + "…"

    def _log(
        self,
        level: int,
        event: str,
        table: Table,
        n: int,
        tag: Tag,
        error: BaseException,
        connecting: bool,
    ) -> None:
        self._log_event(
            level, event, table.name, n, tag, self._describe(error, connecting=connecting)
        )

    def _log_event(
        self, level: int, event: str, table: str, n: int, tag: Tag, error: str | None
    ) -> None:
        rec: dict[str, object] = {
            "service": self.service,
            "component": "store",
            "event": event,
            "trade_date": tag[0].isoformat() if tag[0] else None,
            "session": tag[1],
            "table": table,
            "rows": n,
        }
        if error is not None:
            rec["error"] = error
        log.log(level, json.dumps(rec, ensure_ascii=False))


class AuthHealthSink:
    """auth 서비스 `HealthSink`(emit 한 건) → health_events. tagger 로 거래일·세션을 붙인다."""

    def __init__(self, store: PostgresSink, tagger: Tagger | None = None) -> None:
        self._store = store
        self._tagger = tagger

    def emit(self, event: AuthHealthEvent) -> None:
        self._store.write_health([event], tagger=self._tagger)
