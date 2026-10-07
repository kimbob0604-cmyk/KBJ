"""DB 가 멈췄다 돌아와도 잃지 않는다 — 로컬 디스크 큐 재적재 (phase1_design.md §2 `db` 장애 시).

TimescaleDB 컨테이너를 이 시험이 따로 띄우고(고유 이름·고정 호스트 포트 — 다시 켜도 같은 주소),
쓰는 도중 `docker stop` → 계속 쓴다(스풀, 예외 없음) → `docker start` → 스풀 재적재 → 행 확인.
끝나면 `docker rm -f -v`(익명 볼륨까지). 이 파일이 띄우지 않은 컨테이너는 건드리지 않는다.
Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

import secrets
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from data.spool import DiskSpool
from data.store import MinuteBarRecord, PostgresSink
from db.migrate import migrate
from services.poller.records import ChainRecord
from services.recorder.envelope import RawEnvelope
from tests.integration.conftest import (
    IMAGE,
    PgContainer,
    _docker,
    _docker_ready,
    _wait_ready,
    remove_container,
)

pytestmark = pytest.mark.integration

DAY = date(2026, 9, 28)
T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)  # 10:00 KST


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def restartable_pg() -> Iterator[PgContainer]:
    """멈췄다 다시 켤 컨테이너 (--rm 없이, 호스트 포트 고정)."""
    reason = _docker_ready()
    if reason is not None:
        pytest.skip(f"Docker 없음 — {reason}")
    name = f"gexlab-test-spool-{uuid.uuid4().hex[:12]}"
    password = secrets.token_hex(16)
    port = _free_port()
    run = _docker(
        "run",
        "-d",
        "--name",
        name,
        "-e",
        f"POSTGRES_PASSWORD={password}",
        "-p",
        f"127.0.0.1:{port}:5432",
        IMAGE,
    )
    try:
        if run.returncode != 0:
            pytest.skip(f"컨테이너를 띄우지 못했다: {run.stderr.strip()[:200]}")
        pg = PgContainer(name, "127.0.0.1", port, password)
        _wait_ready(pg)
        yield pg
    finally:
        remove_container(name)


def _chain(i: int) -> ChainRecord:
    return ChainRecord(
        ts=T0 + timedelta(seconds=i),
        trade_date=DAY,
        session="day",
        mrkt_cls="WKM",
        expiry="260904",
        strike=Decimal("1100.00") + Decimal("2.5") * i,
        cp="C",
        source="board",
        last=Decimal("12.35"),
        oi=100 + i,
    )


def _env(i: int) -> RawEnvelope:
    return RawEnvelope(
        received_at=T0 + timedelta(seconds=i),
        source="kis_ws",
        tr_id="H0IFCNT0",
        key="A01612",
        payload=f"0|H0IFCNT0|001|A01612^{i:06d}",
        trade_date=DAY,
        session="day",
    )


def _bar(close: str) -> MinuteBarRecord:
    return MinuteBarRecord(
        ts=T0,
        trade_date=DAY,
        session="day",
        received_at=T0,
        code="A01612",
        market="F",
        open=Decimal("1096.00"),
        high=Decimal("1097.00"),
        low=Decimal("1095.00"),
        close=Decimal(close),
    )


def _q(dsn: str, query: bytes) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, connect_timeout=5) as c:
        return c.execute(query).fetchall()


def test_db_restart_mid_run_loses_nothing(restartable_pg: PgContainer, tmp_path: Path) -> None:
    pg = restartable_pg
    dsn = pg.dsn(pg.fresh_database("spool"))
    migrate(dsn)
    spool = DiskSpool(tmp_path / "spool" / "recorder")
    sink = PostgresSink(
        dsn,
        service="spool-it",
        spool=spool,
        retries=0,
        connect_timeout=2,
        backoff_s=(0.2, 1.0),
    )
    try:
        sink.write_chain([_chain(0)])
        sink.write_raw([_env(0)])
        sink.write_minute_bars([_bar("1096.10")])
        assert not spool.pending

        assert _docker("stop", "-t", "5", pg.name, timeout=60).returncode == 0
        for i in range(1, 6):  # DB 가 없는 동안 — 예외 없이 스풀로
            sink.write_chain([_chain(i)])
            sink.write_raw([_env(i), _env(i + 100)])
            time.sleep(0.05)
        sink.write_minute_bars([_bar("1096.20")])  # 같은 봉을 고친 값 (DO UPDATE)
        sink.write_minute_bars([_bar("1096.30")])
        assert spool.pending and sink.degraded
        assert sink.stats.spooled_rows >= 5 + 10 + 2
        assert list((tmp_path / "spool" / "recorder").rglob("*.jsonl"))

        assert _docker("start", pg.name, timeout=60).returncode == 0
        _wait_ready(pg)
        deadline = time.monotonic() + 60
        while not sink.flush_spool():
            assert time.monotonic() < deadline, "스풀을 60초 안에 비우지 못했다"
            time.sleep(0.2)
        assert not sink.degraded and not spool.pending
        assert list((tmp_path / "spool" / "recorder").rglob("*.jsonl")) == []

        oi = [r[0] for r in _q(dsn, b"SELECT oi FROM chain_snapshots ORDER BY ts")]
        assert oi == [100 + i for i in range(6)]
        raw = _q(dsn, b"SELECT count(*), count(DISTINCT digest) FROM raw_messages")
        assert raw == [(11, 11)]
        bars = _q(dsn, b"SELECT close FROM minute_bars")
        assert bars == [(Decimal("1096.30"),)]  # 스풀 순서대로 → 마지막 값
        kinds = [r[0] for r in _q(dsn, b"SELECT kind FROM health_events ORDER BY ts, kind")]
        assert "db_spooling" in kinds and "db_spool_replayed" in kinds
        tagged = _q(
            dsn, b"SELECT trade_date, session FROM health_events WHERE kind = 'db_spooling'"
        )
        assert tagged == [(DAY, "day")]

        # 다시 써도(재적재가 두 번 돈 것과 같다) 늘지 않는다
        sink.write_chain([_chain(i) for i in range(6)])
        sink.write_raw([_env(i) for i in range(6)])
        assert _q(dsn, b"SELECT count(*) FROM chain_snapshots") == [(6,)]
        assert _q(dsn, b"SELECT count(*) FROM raw_messages") == [(11,)]
    finally:
        sink.close()
