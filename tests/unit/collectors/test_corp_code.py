"""`filings.corp_code` 수집기 — 가짜 DART(합성 corpCode zip) + 메모리 저장소(설계 §1.7).

corpCode.xml 은 공개 등급이지만 키가 없어 합성 zip(DART 원본 모양 — 묶음 C 의 `corp_zip`)으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.data.public.dart.client import DartClient
from kbj.data.public.dart.corp_code import CorpCode
from kbj.data.spec import DataKey
from kbj.services.collectors import corp_code as cc
from kbj.services.scheduler.handlers import JobContext
from tests.unit.data.public._support import FakeServer, RecordingLimiter, corp_zip

T0 = datetime(2026, 10, 5, 18, 5, tzinfo=UTC)  # 03:05 KST
KEY = DataKey("DART", "corpCode", "2026-10-06")
ROWS = [
    ("00126380", "가상전자", "029460", "20260901"),
    ("00999999", "비상장회사", "", "20250101"),
    ("00888888", "영문코드회사", "0001A0", "20260102"),
]


def client_for(server: FakeServer) -> DartClient:
    return DartClient(
        "k" * 40, transport=server.transport(), limiter=RecordingLimiter(), retries=1, backoff_s=0
    )


def ctx(**resources: Any) -> JobContext:
    return JobContext(
        job="filings.corp_code",
        as_of="2026-10-06",
        run_id="filings.corp_code:2026-10-06:1",
        attempt=1,
        now=T0,
        keys=(KEY,),
        resources=resources,
    )


def test_collects_from_fake_dart_into_store() -> None:
    server = FakeServer(lambda req: (200, corp_zip(ROWS)))
    client = client_for(server)
    store = cc.MemoryCorpCodeStore()
    result = cc.run(ctx(corp_code_store=store, corp_code_fetch=client.corp_codes))
    assert result.status == "ok" and result.collected == (KEY,) and result.rows == 3
    assert server.paths() == ["/api/corpCode.xml"]
    row, received = store.rows["00126380"]
    assert (row.stock_code, row.source, received) == ("029460", "DART", T0)
    assert store.rows["00999999"][0].stock_code is None
    assert "k" * 40 not in str(result)  # 키는 요청에만(결과·기록에 없음)


def test_replace_removes_companies_that_disappeared() -> None:
    store = cc.MemoryCorpCodeStore()
    store.replace(
        [
            CorpCode(corp_code=f"{i:08d}", stock_code=None, corp_name=f"c{i}", modify_date=None)
            for i in range(4)
        ],
        T0,
    )
    keep = [
        CorpCode(corp_code=f"{i:08d}", stock_code=None, corp_name=f"c{i}", modify_date=None)
        for i in range(3)
    ]
    assert store.replace(keep, T0) == 3
    assert sorted(store.rows) == ["00000000", "00000001", "00000002"]


def test_shrink_guard_refuses_to_wipe_the_table() -> None:
    store = cc.MemoryCorpCodeStore()
    many = [
        CorpCode(corp_code=f"{i:08d}", stock_code=None, corp_name="c", modify_date=None)
        for i in range(100)
    ]
    store.replace(many, T0)
    with pytest.raises(cc.CorpCodeShrink):
        store.replace(many[:40], T0)
    with pytest.raises(cc.CorpCodeShrink):
        store.replace([], T0)
    assert store.count() == 100  # 그대로


def test_dedupe_keeps_latest_modify_date() -> None:
    a = CorpCode(
        corp_code="00000001", stock_code=None, corp_name="old", modify_date=date(2025, 1, 1)
    )
    b = CorpCode(
        corp_code="00000001", stock_code=None, corp_name="new", modify_date=date(2026, 1, 1)
    )
    assert [r.corp_name for r in cc.dedupe([b, a])] == ["new"]


def test_dart_error_propagates_to_runner() -> None:
    server = FakeServer(lambda req: (200, "<html>점검 중</html>"))
    client = client_for(server)
    with pytest.raises(Exception, match="DART"):
        cc.run(ctx(corp_code_store=cc.MemoryCorpCodeStore(), corp_code_fetch=client.corp_codes))


def test_no_claimed_key_means_no_fetch() -> None:
    called: list[int] = []

    def fetch() -> Sequence[CorpCode]:
        called.append(1)
        return []

    c = JobContext("filings.corp_code", "2026-10-06", "r", 1, T0, keys=())
    result = cc.run(
        JobContext(
            c.job,
            c.as_of,
            c.run_id,
            c.attempt,
            c.now,
            (),
            None,
            {"corp_code_store": cc.MemoryCorpCodeStore(), "corp_code_fetch": fetch},
        )
    )
    assert result.status == "skipped" and called == []


def test_missing_settings_is_an_error_not_a_guess() -> None:
    with pytest.raises(RuntimeError, match="설정"):
        cc.run(ctx(corp_code_store=cc.MemoryCorpCodeStore()))


class _Cur:
    def __init__(self, old: int) -> None:
        self.old = old
        self.sql: list[str] = []
        self.copied: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def execute(self, q: str, params: Any = None) -> None:
        self.sql.append(q)

    def fetchone(self) -> tuple[int]:
        return (self.old,)

    def copy(self, q: str) -> _Cur:
        self.sql.append(q)
        return self

    def write_row(self, row: tuple[Any, ...]) -> None:
        self.copied.append(row)


class _Conn:
    def __init__(self, cur: _Cur) -> None:
        self.cur = cur

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def cursor(self) -> _Cur:
        return self.cur


def test_pg_store_copies_upserts_and_deletes_in_one_transaction() -> None:
    cur = _Cur(old=2)
    store = cc.PgCorpCodeStore(lambda: _Conn(cur))  # type: ignore[arg-type, return-value]
    rows = [
        CorpCode(corp_code=f"{i:08d}", stock_code=None, corp_name="c", modify_date=None)
        for i in range(3)
    ]
    assert store.replace(rows, T0) == 3
    joined = "\n".join(cur.sql)
    assert "COPY _corp_new" in joined and "ON CONFLICT (corp_code) DO UPDATE" in joined
    assert "DELETE FROM pub_filings.corp_code" in joined
    assert all(r[4:6] == ("DART", "ok") and r[6] == T0 for r in cur.copied)
    small = _Cur(old=100)
    with pytest.raises(cc.CorpCodeShrink):
        cc.PgCorpCodeStore(lambda: _Conn(small)).replace(rows, T0)  # type: ignore[arg-type, return-value]
    assert not any("COPY" in q for q in small.sql)
