"""kbj/services/scheduler/claims.py — 데이터 키 선점·실행 기록(설계 §6.5).

메모리 저장소로 규칙을 고정한다. Postgres 쪽(`PgClaimStore`)은 같은 규칙을 SQL 로 옮긴 것이고,
SQL 문이 0002 의 열·부분 유일 인덱스와 맞는지는 문자열로 확인한다(통합 시험은 묶음 I 시뮬레이션·
Docker 단계).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kbj.data.spec import DataKey
from kbj.services.scheduler import claims as claims_mod
from kbj.services.scheduler.claims import (
    MemoryClaimStore,
    MemoryRunLog,
    RunRecord,
    make_run_id,
)
from kbj.store.db import StoreError

T0 = datetime(2026, 10, 6, 23, 5, tzinfo=UTC)
KEY = DataKey("KRX", "sto/stk_bydd_trd", "2026-10-06", "KRX")


def test_second_claim_of_the_same_key_is_refused() -> None:
    s = MemoryClaimStore()
    assert s.claim(KEY, "krx.daily", "krx.daily:2026-10-07:1", T0)
    assert not s.claim(KEY, "market.backfill", "market.backfill:2026-10-06:1", T0)
    assert s.refused == [(KEY, "market.backfill")]
    s.complete(KEY, "krx.daily:2026-10-07:1", 2800, T0 + timedelta(minutes=1))
    assert not s.claim(KEY, "krx.daily", "krx.daily:2026-10-07:2", T0)  # done 은 다시 안 받는다
    row = s.live(KEY)
    assert row is not None and row.status == "done" and row.rows == 2800 and row.done_at is not None


def test_same_job_keeps_its_claim_across_retries() -> None:
    s = MemoryClaimStore()
    assert s.claim(KEY, "krx.daily", "krx.daily:2026-10-07:1", T0)
    assert s.claim(KEY, "krx.daily", "krx.daily:2026-10-07:2", T0 + timedelta(minutes=10))
    row = s.live(KEY)
    assert row is not None and row.run_id == "krx.daily:2026-10-07:2"
    assert len(s.rows) == 1


def test_failure_releases_the_key_for_a_new_claim() -> None:
    s = MemoryClaimStore()
    s.claim(KEY, "krx.daily", "r1", T0)
    s.fail(KEY, "r1", "KRX 401 api_key=abcdef0123456789abcdef", T0)
    assert s.live(KEY) is None
    assert s.claim(KEY, "market.backfill", "r2", T0)
    failed = [r for r in s.rows if r.status == "failed"]
    assert len(failed) == 1 and "abcdef0123456789abcdef" not in str(failed[0].detail)


def test_venue_is_part_of_the_key() -> None:
    s = MemoryClaimStore()
    krx = DataKey("KIS", "stock_investor_daily", "2026-10-06", "KRX")
    nxt = krx._replace(venue="NXT")
    assert s.claim(krx, "market.close_collect", "r", T0)
    assert s.claim(nxt, "market.close_collect", "r", T0)
    assert not s.claim(krx, "x.y", "r2", T0)


def test_complete_without_claim_is_an_error_and_fail_after_done_is_noop() -> None:
    s = MemoryClaimStore()
    with pytest.raises(StoreError):
        s.complete(KEY, "r", 1, T0)
    s.claim(KEY, "j", "r", T0)
    s.complete(KEY, "r", 1, T0)
    s.fail(KEY, "r", "늦은 실패", T0)
    row = s.live(KEY)
    assert row is not None and row.status == "done"


def test_naive_time_rejected() -> None:
    with pytest.raises(ValueError):
        MemoryClaimStore().claim(KEY, "j", "r", datetime(2026, 10, 6))  # noqa: DTZ001


def test_run_log_latest_and_run_id() -> None:
    log = MemoryRunLog()
    assert make_run_id("krx.daily", "2026-10-02", 3) == "krx.daily:2026-10-02:3"
    with pytest.raises(ValueError):
        make_run_id("krx.daily", "2026-10-02", 0)
    log.record(RunRecord("j:a:1", "j", "a", 1, "failed"))
    log.record(RunRecord("j:a:2", "j", "a", 2, "running"))
    log.record(RunRecord("j:a:2", "j", "a", 2, "ok"))
    latest = log.latest("j", "a")
    assert latest is not None and (latest.attempt, latest.status) == (2, "ok")
    assert log.latest("j", "b") is None


def test_pg_sql_matches_0002_columns_and_partial_index() -> None:
    insert = claims_mod._INSERT.as_string(None)  # pyright: ignore[reportPrivateUsage]
    assert (
        "ON CONFLICT (source, dataset, as_of, venue) WHERE status IN ('claimed', 'done')" in insert
    )
    upsert = claims_mod._UPSERT_RUN.as_string(None)  # pyright: ignore[reportPrivateUsage]
    for col in (
        "run_id",
        "job",
        "as_of",
        "attempt",
        "status",
        "started_at",
        "finished_at",
        "detail",
        "source",
    ):
        assert col in upsert


def test_run_detail_strings_are_masked() -> None:
    got = claims_mod._clean_detail(  # pyright: ignore[reportPrivateUsage]
        {"reason": "token=eyJhbGciOiJIUzI1NiJ9.abc.def 실패", "rows": 3}
    )
    assert "eyJhbGciOiJIUzI1NiJ9" not in got["reason"] and got["rows"] == 3
