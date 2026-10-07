"""`filings.corp_code` — DART 고유번호 전체를 `pub_filings.corp_code` 에 갈아 넣는다(설계 §1.7, P2).

원본: SD `server.py:init_dart_corp_map_db`:10267·`_load_dart_corp_code_map`:10453, ET
`board/ingest/dart.py:corp_codes`:75. 받기·파싱은 `kbj.data.public.dart`(묶음 C) 하나만 쓴다.

- 데이터 키 `DART:corpCode @ run_date` 는 실행기가 먼저 선점한다. 선점한 키가 없으면 받지 않는다.
- 갈아 넣기: 새 목록을 넣고(같은 corp_code 는 고침) 새 목록에 없는 회사는 지운다 — 한 트랜잭션.
- **줄어듦 가드**: 새 목록이 지금 표의 절반보다 작으면 갈아 넣지 않고 실패한다(잘린 zip·빈 응답이
  표를 비우지 않게 — 조용히 덮지 않는다). 처음(표가 빔)은 그대로 넣는다.
- 모든 행은 source=DART, quality=ok, received_at=받은 시각(절대 규칙 1).
- DART 오류(키 없음·한도 초과·형식 오류)는 그대로 올라가고 실행기가 실패로 기록·재시도한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final, Protocol

import psycopg

from kbj.data.public.dart.client import DartClient
from kbj.data.public.dart.corp_code import CorpCode
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.db import StoreError

__all__ = [
    "CorpCodeShrink",
    "CorpCodeStore",
    "MemoryCorpCodeStore",
    "PgCorpCodeStore",
    "collect",
    "dedupe",
    "run",
]

MIN_KEEP_RATIO: Final = 0.5
SOURCE: Final = "DART"


class CorpCodeShrink(RuntimeError):
    """새 목록이 너무 작다 — 갈아 넣지 않았다."""


class CorpCodeStore(Protocol):
    def count(self) -> int: ...

    def replace(self, rows: Sequence[CorpCode], received_at: datetime) -> int: ...


def check_shrink(old: int, new: int) -> None:
    if new == 0:
        raise CorpCodeShrink("새 corp_code 목록이 비었다 — 갈아 넣지 않는다")
    if old > 0 and new < old * MIN_KEEP_RATIO:
        raise CorpCodeShrink(
            f"새 corp_code 목록 {new}건이 지금 {old}건의 {MIN_KEEP_RATIO:.0%} 보다 작다 "
            "— 갈아 넣지 않는다"
        )


def dedupe(rows: Sequence[CorpCode]) -> list[CorpCode]:
    """같은 corp_code 가 두 번 오면 modify_date 가 늦은 것(같으면 뒤의 것)."""
    best: dict[str, CorpCode] = {}
    for r in rows:
        cur = best.get(r.corp_code)
        if cur is None or (r.modify_date or date.min) >= (cur.modify_date or date.min):
            best[r.corp_code] = r
    return [best[k] for k in sorted(best)]


class MemoryCorpCodeStore:
    """시험용 — 규칙은 Pg 와 같다."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[CorpCode, datetime]] = {}

    def count(self) -> int:
        return len(self.rows)

    def replace(self, rows: Sequence[CorpCode], received_at: datetime) -> int:
        clean = dedupe(rows)
        check_shrink(self.count(), len(clean))
        self.rows = {r.corp_code: (r, received_at) for r in clean}
        return len(clean)


_COPY: Final = (
    "COPY _corp_new (corp_code, stock_code, corp_name, modify_date, source, quality, received_at) "
    "FROM STDIN"
)
_UPSERT: Final = (
    "INSERT INTO pub_filings.corp_code "
    "(corp_code, stock_code, corp_name, modify_date, source, quality, received_at) "
    "SELECT corp_code, stock_code, corp_name, modify_date, source, quality, received_at "
    "FROM _corp_new "
    "ON CONFLICT (corp_code) DO UPDATE SET stock_code = EXCLUDED.stock_code, "
    "corp_name = EXCLUDED.corp_name, modify_date = EXCLUDED.modify_date, "
    "quality = EXCLUDED.quality, received_at = EXCLUDED.received_at"
)
_DELETE_GONE: Final = (
    "DELETE FROM pub_filings.corp_code c WHERE NOT EXISTS "
    "(SELECT 1 FROM _corp_new n WHERE n.corp_code = c.corp_code)"
)


class PgCorpCodeStore:
    """`pub_filings.corp_code`(0004).

    한 트랜잭션: 임시 표에 COPY → 줄어듦 가드 → upsert → 지우기.
    """

    def __init__(self, connect_fn: Callable[[], psycopg.Connection[Any]]) -> None:
        self._connect = connect_fn

    def __repr__(self) -> str:
        return "PgCorpCodeStore()"

    def count(self) -> int:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM pub_filings.corp_code")
                row = cur.fetchone()
        except psycopg.Error as e:
            raise StoreError(
                f"corp_code 조회 실패: {type(e).__name__}", cause=type(e).__name__
            ) from None
        return int(row[0]) if row else 0

    def replace(self, rows: Sequence[CorpCode], received_at: datetime) -> int:
        clean = dedupe(rows)
        at = received_at.astimezone(UTC)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM pub_filings.corp_code")
                got = cur.fetchone()
                check_shrink(int(got[0]) if got else 0, len(clean))
                cur.execute(
                    "CREATE TEMP TABLE _corp_new (LIKE pub_filings.corp_code INCLUDING DEFAULTS) "
                    "ON COMMIT DROP"
                )
                with cur.copy(_COPY) as cp:
                    for r in clean:
                        cp.write_row(
                            (
                                r.corp_code,
                                r.stock_code,
                                r.corp_name,
                                r.modify_date,
                                SOURCE,
                                "ok",
                                at,
                            )
                        )
                cur.execute(_UPSERT)
                cur.execute(_DELETE_GONE)
        except psycopg.Error as e:
            kind = type(e).__name__ + (f"({e.sqlstate})" if e.sqlstate else "")
            raise StoreError(f"corp_code 갈아 넣기 실패: {kind}", cause=kind) from None
        return len(clean)


def collect(fetch: Callable[[], Sequence[CorpCode]], store: CorpCodeStore, *, now: datetime) -> int:
    """받아서 갈아 넣고 넣은 행 수를 돌려준다."""
    rows = list(fetch())
    if any(r.source != SOURCE for r in rows):
        raise ValueError("corp_code 행의 source 는 DART 여야 한다")
    return store.replace(rows, now)


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.corp_code:run`)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    store: CorpCodeStore = ctx.resources.get("corp_code_store") or PgCorpCodeStore(
        ctx.resource("connect")
    )
    injected: Callable[[], Sequence[CorpCode]] | None = ctx.resources.get("corp_code_fetch")
    client: DartClient | None = None
    if injected is not None:
        fetch = injected
    else:
        if ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_DART_API_KEY)")
        client = DartClient.from_settings(ctx.settings, ctx.resource("redis"))
        fetch = client.corp_codes
    try:
        n = collect(fetch, store, now=ctx.now)
    finally:
        if client is not None:
            client.close()
    return JobResult("ok", collected=ctx.keys, rows=n, detail={"rows": n})
