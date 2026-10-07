"""통합 시험용 TimescaleDB 컨테이너 (마크 `integration` — `docker` 는 별칭, tests/conftest.py).

GEXLAB `tests/integration/conftest.py` 방식 그대로(docs/p2_design.md §8.8):
- 세션마다 TimescaleDB 컨테이너를 **직접** 띄우고(고유 이름·127.0.0.1 임의 포트·임의 비밀번호)
  끝나면 `docker rm -f -v` 로 익명 볼륨까지 지운다(`remove_container`). 이 파일이 띄우지 않은
  컨테이너·볼륨은 건드리지 않는다
- Docker 가 없거나(명령·데몬) 이미지를 구할 수 없으면 건너뛴다 — 실패가 아니다
- 이미지는 `KBJ_TEST_TIMESCALE_IMAGE` 로 바꿀 수 있다(docs/secrets.md — 기본
  timescale/timescaledb:latest-pg16, docker-compose.yml 의 db 와 같다)
- 시나리오마다 빈 DB 를 새로 만든다(`fresh_database`) — 서로의 행이 섞이지 않게
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import sql

IMAGE = os.environ.get("KBJ_TEST_TIMESCALE_IMAGE") or "timescale/timescaledb:latest-pg16"
READY_TIMEOUT_S = 120.0
PULL_TIMEOUT_S = 900.0


@dataclass(frozen=True)
class PgContainer:
    name: str
    host: str
    port: int
    password: str

    def dsn(self, dbname: str = "postgres") -> str:
        return f"postgresql://postgres:{self.password}@{self.host}:{self.port}/{dbname}"

    def admin(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.dsn(), autocommit=True, connect_timeout=5)

    def fresh_database(self, prefix: str = "t") -> str:
        """빈 DB 를 새로 만들어 이름을 돌려준다(시험끼리 스키마를 나누지 않게)."""
        name = f"{prefix}_{uuid.uuid4().hex[:10]}"
        with self.admin() as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        return name


def _docker(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — 고정 인자로 docker CLI 만 부른다
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def remove_container(name: str) -> None:
    """이 시험이 띄운 컨테이너를 익명 볼륨까지 지운다(`-v` — timescale 이미지는 VOLUME 을
    선언한다)."""
    _docker("rm", "-f", "-v", name, timeout=120)


def _docker_ready() -> str | None:
    """Docker 를 쓸 수 없으면 이유를, 쓸 수 있으면 None."""
    if shutil.which("docker") is None:
        return "docker 명령이 없다"
    try:
        info = _docker("info", "--format", "{{.ServerVersion}}", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"docker info 실패: {type(e).__name__}"
    if info.returncode != 0:
        return "docker 데몬에 붙지 못했다"
    if _docker("image", "inspect", IMAGE).returncode != 0:
        try:
            pulled = _docker("pull", IMAGE, timeout=PULL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return f"{IMAGE} 내려받기 시간 초과"
        if pulled.returncode != 0:
            return f"{IMAGE} 이미지를 구하지 못했다"
    return None


def _host_port(name: str) -> int:
    out = _docker("port", name, "5432/tcp").stdout.strip().splitlines()
    for line in out:  # '127.0.0.1:49153'
        host_port = line.rsplit(":", 1)[-1]
        if host_port.isdigit():
            return int(host_port)
    raise RuntimeError("포트 매핑을 읽지 못했다")


def _wait_ready(pg: PgContainer) -> None:
    """컨테이너 안 pg_isready(TCP — 초기화용 임시 서버는 TCP 를 열지 않는다) 뒤 호스트에서 접속."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        ready = _docker("exec", pg.name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres")
        if ready.returncode == 0:
            try:
                with pg.admin() as c:
                    c.execute("SELECT 1")
                return
            except psycopg.OperationalError:
                pass
        time.sleep(0.5)
    raise RuntimeError(f"{pg.name}: {READY_TIMEOUT_S:.0f}초 안에 준비되지 않았다")


@pytest.fixture(scope="session")
def timescale() -> Iterator[PgContainer]:
    reason = _docker_ready()
    if reason is not None:
        pytest.skip(f"Docker 없음 — {reason}")
    name = f"kbj-test-pg-{uuid.uuid4().hex[:12]}"
    password = secrets.token_hex(16)
    run = _docker(
        "run", "--rm", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={password}",
        "-p", "127.0.0.1::5432", IMAGE,
    )  # fmt: skip
    if run.returncode != 0:
        remove_container(name)
        pytest.skip("컨테이너를 띄우지 못했다")
    try:
        pg = PgContainer(name, "127.0.0.1", _host_port(name), password)
        _wait_ready(pg)
        yield pg
    finally:
        remove_container(name)
