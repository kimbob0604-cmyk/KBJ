"""docker compose 스택 (PLAN §4.1·§6.5, docs/phase1_design.md §2·§10 통합).

- `docker compose config -q` 가 통과한다(.env 없이도 — env_file 은 required: false)
- 실제 기동: 이 시험만의 프로젝트 이름(gexlab-it-<무작위>)으로 redis·db·migrate·recorder·engine
  을 띄워 Redis ws.raw 에 원문을 발행하면 raw_messages 에 들어가고, db 를 멈춘 동안 발행한 원문도
  디스크 스풀을 거쳐 db 가 돌아온 뒤 들어간다. recorder·engine 은 하트비트 healthcheck 로 healthy
  가 된다.
  외부(KIS·KRX) 호출이 없는 서비스만 띄운다. 끝나면 `down -v` 와 이미지 삭제 — 이 시험이 만든
  것만 지운다. compose 파일은 임시 폴더로 복사해 쓰고(.env 도 거기 시험용으로), 저장소의 .env 는
  읽지 않는다. engine 이 붙이는 기능 플래그 파일(./config/features.yaml — 읽기 전용 마운트)은 저장소
  것을 임시 폴더의 같은 자리로 복사한다
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from services.bus import WS_RAW, encode_envelope
from services.recorder.envelope import RawEnvelope
from tests.integration.conftest import _docker, _docker_ready, compose_available

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"


@pytest.mark.integration
def test_compose_config_is_valid_without_an_env_file(tmp_path: Path) -> None:
    reason = compose_available()
    if reason is not None:
        pytest.skip(reason)
    shutil.copy(COMPOSE, tmp_path / "docker-compose.yml")  # .env 없는 폴더
    override = ["-f", str(ROOT / "tests" / "integration" / "docker-compose.test.yml")]
    for extra in ([], ["--profile", "tools"], override):  # 시험 스택 덧씌우기(test_full_day)도
        r = _docker("compose", "-f", str(tmp_path / "docker-compose.yml"), *extra, "config", "-q")
        assert r.returncode == 0, r.stderr[-500:]
        assert r.stdout == ""


@dataclass
class Stack:
    project: str
    workdir: Path

    def compose(self, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
        return _docker(
            "compose",
            "-p",
            self.project,
            "-f",
            str(self.workdir / "docker-compose.yml"),
            "-f",
            str(self.workdir / "override.yml"),
            *args,
            timeout=timeout,
        )

    def psql(self, query: str) -> str:
        r = self.compose(
            "exec", "-T", "db", "psql", "-U", "gexlab", "-d", "gexlab", "-tAc", query, timeout=30
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    def publish(self, channel: str, message: str) -> int:
        r = self.compose("exec", "-T", "redis", "redis-cli", "PUBLISH", channel, message)
        assert r.returncode == 0, r.stderr[-300:]
        return int(r.stdout.strip() or "0")

    def wait(self, pred: object, what: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not pred():  # type: ignore[operator]
            if time.monotonic() > deadline:
                logs = self.compose("logs", "--no-color", "--tail", "40", "recorder").stdout
                pytest.fail(f"{what}: {timeout:.0f}초 안에 되지 않았다\n{logs[-3000:]}")
            time.sleep(0.5)


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    reason = _docker_ready() or compose_available()
    if reason is not None:
        pytest.skip(f"Docker 없음 — {reason}")
    tag = f"gexlab-it-app:{uuid.uuid4().hex[:12]}"
    # KBJ P2: 맥락은 레포 루트(kbj 포함), Dockerfile 은 이 폴더 것(MIGRATION.md P2)
    built = _docker(
        "build", "-q", "-t", tag, "-f", str(ROOT / "Dockerfile"), str(ROOT.parents[1]), timeout=900
    )
    if built.returncode != 0:
        pytest.skip(f"앱 이미지를 만들지 못했다: {built.stderr.strip()[-300:]}")
    pw = secrets.token_hex(16)
    shutil.copy(COMPOSE, tmp_path / "docker-compose.yml")
    (tmp_path / "config").mkdir()  # engine 이 읽기 전용으로 붙이는 기능 플래그 파일(설계 §4)
    shutil.copy(ROOT / "config" / "features.yaml", tmp_path / "config" / "features.yaml")
    (tmp_path / ".env").write_text(
        f"POSTGRES_PASSWORD={pw}\nDATABASE_URL=postgresql://gexlab:{pw}@db:5432/gexlab\n",
        encoding="utf-8",
    )
    (tmp_path / "override.yml").write_text(
        f"""services:
  migrate:
    build: !reset null
    image: {tag}
    pull_policy: never
  recorder:
    build: !reset null
    image: {tag}
    pull_policy: never
    healthcheck:
      interval: 2s
      start_period: 5s
  engine:
    build: !reset null
    image: {tag}
    pull_policy: never
    healthcheck:
      interval: 2s
      start_period: 5s
""",
        encoding="utf-8",
    )
    st = Stack(f"gexlab-it-{uuid.uuid4().hex[:10]}", tmp_path)
    try:
        yield st
    finally:
        st.compose("down", "-v", "--remove-orphans", "-t", "5", timeout=180)
        _docker("rmi", "-f", tag, timeout=60)


def _env(i: int) -> str:
    env = RawEnvelope(
        received_at=datetime(2026, 9, 28, 0, 30, i, tzinfo=UTC),
        source="kis_ws",
        tr_id="H0IFCNT0",
        key="A01612",
        payload=f"0|H0IFCNT0|001|A01612^0930{i:02d}^1100.05",
        trade_date=date(2026, 9, 28),
        session="day",
    )
    return encode_envelope(env)


@pytest.mark.integration
def test_stack_records_raw_messages_and_survives_a_db_outage(stack: Stack) -> None:
    up = stack.compose(
        "up", "-d", "--wait", "--wait-timeout", "150", "recorder", "engine", timeout=240
    )
    assert up.returncode == 0, up.stderr[-1500:]
    out = stack.compose("ps", "-a", "--format", "json").stdout.strip()
    ps = json.loads(out) if out.startswith("[") else [json.loads(x) for x in out.splitlines()]
    by = {p["Service"]: p for p in ps}
    assert by["migrate"]["State"] == "exited" and by["migrate"]["ExitCode"] == 0
    assert by["recorder"]["Health"] == "healthy"
    assert by["engine"]["Health"] == "healthy"  # 빈 DB 에서도 하트비트 — 003·004 가 적용됐다
    assert stack.psql("SELECT count(*) FROM schema_migrations WHERE version = '004'") == "1"
    # 기능 플래그 파일을 읽고 떴다(모르는 이름이면 기동하지 않는다 — 설계 §4)
    assert '"flags_loaded"' in stack.compose("logs", "--no-color", "engine").stdout

    def count() -> int:
        out = stack.psql("SELECT count(*) FROM raw_messages")
        return int(out) if out.isdigit() else -1

    stack.wait(lambda: stack.publish(WS_RAW, _env(1)) == 1 or None, "recorder 구독", 30)
    stack.wait(lambda: count() >= 1, "raw_messages 1행", 30)

    assert stack.compose("stop", "-t", "5", "db", timeout=60).returncode == 0
    assert stack.publish(WS_RAW, _env(2)) == 1  # recorder 는 살아 있다
    stack.wait(
        lambda: "db_spooling" in stack.compose("logs", "--no-color", "recorder").stdout,
        "스풀 시작 로그",
        30,
    )
    assert stack.compose("start", "db", timeout=60).returncode == 0
    stack.wait(lambda: count() == 2, "스풀 재적재", 90)
    kinds = stack.psql("SELECT string_agg(kind, ',' ORDER BY ts) FROM health_events")
    assert "db_spooling" in kinds and "db_spool_replayed" in kinds
    payloads = stack.psql("SELECT string_agg(payload_text, '|' ORDER BY ts) FROM raw_messages")
    assert "093001" in payloads and "093002" in payloads
