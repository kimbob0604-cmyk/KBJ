"""Postgres 연결 — GEXLAB `data/store.py` 의 연결·재시도·오류 가림 부분 승격(p2_design §1.8).

- 접속 문자열은 `Settings.database_url`(`KBJ_DATABASE_URL`, SecretStr) 하나에서만 꺼낸다. 접속
  문자열·비밀번호는 로그·예외 문구에 싣지 않는다: 해석 못 하는 접속 문자열은 문구 없이 거부하고
  (libpq 가 틀린 조각을 인용한다), 접속 단계 오류는 종류·SQLSTATE 만, 그 밖의 문구는 접속 문자열과
  비밀번호(URL 인코딩 형태 포함)를 가리고 첫 줄만(GX `_secrets_of`·`ERROR_MAX`).
- 연결마다 `SET TIME ZONE 'UTC'`(읽는 시각도 UTC — 표시는 KST 로 따로)와 `application_name`
  (`kbj-<서비스>`). `search_path` 를 주면 그 스키마만(이름은 `pub_*`·`prv_*`·`ops` 만 받는다 —
  Postgres 기본 스키마 `public` 은 쓰지 않는다, ADR 0002).
- 연결 오류(`OperationalError`)는 `retries` 번 더(대기는 주입한 `sleep` — 시험은 시계를 주입한다).
  그래도 실패하면 `StoreError`. 데이터 오류는 여기 오지 않는다(쓰는 쪽 몫).
- `PgHealthStore` 는 `kbj.services.runtime.health.HealthStore` 프로토콜 구현 — health 이벤트를
  `ops.health_events`(0002 — GX 열 그대로)에 쓴다. 같은 이벤트를 두 번 써도 `(ts, digest)` 로 한 줄.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from pydantic import SecretStr

ERROR_MAX = 300
_SCHEMA = re.compile(r"(?:pub|prv)_[a-z][a-z0-9_]*|ops")
_SESSIONS = frozenset({"day", "night"})
_LEVELS = frozenset({"info", "warning", "error", "critical"})

Tagger = Callable[[datetime], tuple[date | None, str | None]]


class StoreError(RuntimeError):
    """저장소 실패. 문구의 접속 정보는 가렸다. cause = 오류 종류(SQLSTATE) — 값 없는 짧은 사유."""

    def __init__(self, message: str, *, cause: str = "") -> None:
        super().__init__(message)
        self.cause = cause


class _HasDatabaseUrl(Protocol):
    @property
    def database_url(self) -> SecretStr | None: ...


def parse_dsn(dsn: str) -> dict[str, Any] | None:
    """접속 문자열을 libpq 로 해석한다. 못 하면 None — 오류 문구는 버린다(틀린 조각을 인용한다)."""
    try:
        return conninfo_to_dict(dsn)
    except psycopg.Error:
        return None


def secrets_of(dsn: str, password: object) -> tuple[str, ...]:
    """오류 문구에서 가릴 문자열: 접속 문자열 전체와 비밀번호(URL 인코딩 형태 포함). 긴 것부터."""
    out = [dsn]
    if isinstance(password, str) and password:
        out += [password, quote(password, safe=""), quote(password)]
    return tuple(sorted({s for s in out if s}, key=len, reverse=True))


def scrub_error(text: str, secrets: Sequence[str]) -> str:
    """오류 문구 첫 줄에서 비밀을 가리고 `ERROR_MAX` 자로 자른다."""
    first = (text.strip().splitlines() or [""])[0]
    for s in secrets:
        first = first.replace(s, "***")
    first = re.sub(r"postgres(?:ql)?://\S+", "postgresql://***", first)
    return first[:ERROR_MAX]


def _error_kind(e: psycopg.Error) -> str:
    return f"{type(e).__name__}({e.sqlstate})" if e.sqlstate else type(e).__name__


def connect(
    settings: _HasDatabaseUrl,
    *,
    autocommit: bool = False,
    search_path: Sequence[str] | None = None,
    service: str = "store",
    connect_timeout: int = 5,
    retries: int = 0,
    retry_wait_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> psycopg.Connection[Any]:
    """`Settings.database_url` 로 연결한다. 실패는 `StoreError`(접속 정보 없음)."""
    if retries < 0 or retry_wait_s < 0:
        raise ValueError("retries >= 0, retry_wait_s >= 0")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]*", service):
        raise ValueError(f"service 이름 형식: {service!r}")
    path = tuple(search_path or ())
    for name in path:
        if not _SCHEMA.fullmatch(name):
            raise ValueError(f"search_path 는 pub_*·prv_*·ops 만: {name!r}")
    url = settings.database_url
    if url is None:
        raise StoreError("KBJ_DATABASE_URL 이 없다")
    dsn = url.get_secret_value()
    params = parse_dsn(dsn)
    if params is None:  # except 밖에서 올린다 — 원래 예외를 __context__ 에도 남기지 않게
        raise StoreError("KBJ_DATABASE_URL 형식 오류 — 접속 문자열을 해석하지 못했다")
    secrets = secrets_of(dsn, params.get("password"))
    attempt = 0
    while True:
        try:
            conn = psycopg.connect(
                dsn,
                autocommit=autocommit,
                connect_timeout=connect_timeout,
                application_name=f"kbj-{service}",
            )
        except psycopg.OperationalError as e:
            kind = _error_kind(e)
            if attempt < retries:
                attempt += 1
                sleep(retry_wait_s * attempt)
                continue
            # 접속 단계 문구는 libpq 가 호스트·사용자 조각을 인용한다 — 종류·SQLSTATE 만
            raise StoreError(f"DB 접속 실패: {kind}", cause=kind) from None
        except psycopg.Error as e:
            kind = _error_kind(e)
            raise StoreError(f"DB 접속 실패: {kind}", cause=kind) from None
        try:
            conn.execute(sql.SQL("SET TIME ZONE 'UTC'"))
            if path:
                conn.execute(
                    sql.SQL("SET search_path TO {}").format(
                        sql.SQL(", ").join(sql.Identifier(p) for p in path)
                    )
                )
            if not autocommit:
                conn.commit()  # 세션 설정만 커밋 — 호출자는 빈 트랜잭션 상태에서 시작한다
        except psycopg.Error as e:
            conn.close()
            kind = _error_kind(e)
            raise StoreError(
                f"DB 세션 설정 실패: {scrub_error(str(e), secrets)}", cause=kind
            ) from None
        return conn


# ── health 이벤트 저장 (ops.health_events) ─────────────────────────────────────────────────


class _Event(Protocol):
    """runtime `HealthEvent` 의 모양 — 저장소는 서비스 계층(kbj.services)을 import 하지 않는다."""

    @property
    def kind(self) -> str: ...
    @property
    def detail(self) -> str: ...
    @property
    def at(self) -> datetime: ...
    @property
    def severity(self) -> str: ...
    @property
    def service(self) -> str: ...


def _clean_text(s: str) -> str:
    """text 가 못 받는 문자(NUL, 짝 없는 서로게이트)를 바꾼다(GX `clean_text`)."""
    t = s.replace("\x00", "�")
    try:
        t.encode("utf-8")
    except UnicodeEncodeError:
        t = t.encode("utf-8", "replace").decode("utf-8")
    return t


def event_digest(*parts: str) -> bytes:
    """길이를 앞에 붙여 이어 붙인 sha256 — ('ab','c') 와 ('a','bc') 가 다르다(GX `digest`)."""
    h = hashlib.sha256()
    for p in parts:
        b = p.encode("utf-8")
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.digest()


def health_row(ev: _Event, tagger: Tagger | None = None) -> tuple[Any, ...]:
    """이벤트 → `ops.health_events` 행. 거래일·세션은 tagger 로(실패·한쪽만이면 둘 다 NULL)."""
    at = ev.at
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    td: date | None = None
    ss: str | None = None
    if tagger is not None:
        try:
            td, ss = tagger(at)
        except Exception:  # 태깅 실패로 경고가 사라지지 않게(GX health_row)
            td, ss = None, None
    if td is None or ss not in _SESSIONS:
        td, ss = None, None
    level = ev.severity if ev.severity in _LEVELS else "warning"
    service, kind, message = (_clean_text(x) for x in (ev.service, ev.kind, ev.detail))
    detail_json = json.dumps({}, separators=(",", ":"))
    h = event_digest(service, kind, level, message, detail_json)
    return (at.astimezone(UTC), td, ss, service, kind, level, message, Jsonb({}), h)


_INSERT_HEALTH = sql.SQL(
    "INSERT INTO ops.health_events "
    "(ts, trade_date, session, service, kind, level, message, detail, digest) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ts, digest) DO NOTHING"
)


class PgHealthStore:
    """health 이벤트 → `ops.health_events`. 연결 공장을 받아 쓸 때마다 짧게 연다.

    실패는 `StoreError`(접속 정보 없음) — 서비스 쪽 `ServiceHealthSink` 가 로그만 남기고 넘어간다
    (절대 규칙 4).
    """

    def __init__(self, connect_fn: Callable[[], psycopg.Connection[Any]]) -> None:
        self._connect = connect_fn

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgHealthStore()"

    def write_health(self, events: Sequence[_Event], *, tagger: Tagger | None = None) -> int:
        rows = [health_row(ev, tagger) for ev in events]
        if not rows:
            return 0
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany(_INSERT_HEALTH, rows)
                return max(cur.rowcount, 0)
        except psycopg.Error as e:
            kind = _error_kind(e)
            raise StoreError(f"health 저장 실패: {kind}", cause=kind) from None
