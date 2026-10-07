"""docker-compose.yml·.env.example 정적 검사 (PLAN §4.1·§6.5·§11.2·§11.3, 설계 §2).

Docker 없이 YAML 만 읽는다. `docker compose config -q`·실제 기동은
tests/integration/test_compose.py, 시험 스택 덧씌우기(docker-compose.test.yml)로 하루를 돌리는 것은
tests/integration/test_full_day.py
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES: dict[str, Any] = COMPOSE["services"]
APP = {
    "auth": "services.auth",
    "scheduler": "services.scheduler",
    "recorder": "services.recorder",
    "poller": "services.poller",
    "ws-gateway": "services.ws_gateway",
    "engine": "services.engine",  # Phase 3 설계 §1
}
MULTI_ARCH = {"redis": "redis:7-alpine", "db": "timescale/timescaledb:latest-pg16"}
_SECRETISH = re.compile(r"PASSWORD|SECRET|TOKEN|APP_KEY|API_KEY|DATABASE_URL", re.I)


def test_the_phase1_services_and_the_engine_are_all_there() -> None:
    assert set(SERVICES) == {"redis", "db", "migrate", *APP, "probe"}
    for name, image in MULTI_ARCH.items():
        assert SERVICES[name]["image"] == image
    for name in (*APP, "migrate", "probe"):
        assert SERVICES[name]["build"] == ".", name  # Dockerfile(python:3.12-slim, 멀티아키텍처)


@pytest.mark.parametrize(("service", "module"), APP.items())
def test_app_services_run_their_entry_point_with_a_heartbeat_healthcheck(
    service: str, module: str
) -> None:
    s = SERVICES[service]
    assert s["command"] == ["python", "-m", module]
    assert importlib.util.find_spec(f"{module}.__main__") is not None
    test = s["healthcheck"]["test"]
    assert test[:5] == ["CMD", "python", "-m", "services.healthcheck", service]
    assert s["restart"] == "always"
    deps = s["depends_on"]
    assert deps["redis"]["condition"] == "service_healthy"
    assert deps["migrate"]["condition"] == "service_completed_successfully"


def test_engine_calls_no_kis_so_it_waits_only_for_redis_and_migrate() -> None:
    """engine 은 DB·Redis 만 읽는다(Phase 3 설계 §1) — auth·scheduler 를 기다리지 않는다. 같은 앱
    이미지(멀티아키텍처)·restart always·하트비트 healthcheck 는 위 시험들이 본다."""
    s = SERVICES["engine"]
    assert set(s["depends_on"]) == {"redis", "migrate"}
    assert s["image"] == SERVICES["poller"]["image"] == "gexlab-app:local"
    assert s["healthcheck"]["test"][-1] == "60"


def test_heartbeat_names_match_the_services() -> None:
    """healthcheck 가 보는 이름 = 각 서비스가 하트비트에 쓰는 이름."""
    assert importlib.import_module("services.recorder.service").SERVICE == "recorder"
    assert importlib.import_module("services.scheduler.service").SERVICE == "scheduler"
    assert importlib.import_module("services.poller.service").SERVICE == "poller"
    assert importlib.import_module("services.ws_gateway.service").SERVICE == "ws-gateway"
    assert importlib.import_module("services.engine.service").SERVICE == "engine"
    src = (ROOT / "services" / "auth" / "service.py").read_text(encoding="utf-8")
    assert 'Heartbeater(redis, "auth"' in src


def test_infrastructure_healthchecks_and_one_shot_migrate() -> None:
    assert SERVICES["redis"]["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert "pg_isready" in " ".join(SERVICES["db"]["healthcheck"]["test"])
    assert SERVICES["db"]["restart"] == SERVICES["redis"]["restart"] == "always"
    m = SERVICES["migrate"]
    assert m["command"] == ["python", "-m", "db.migrate"] and m["restart"] == "no"
    assert m["depends_on"]["db"]["condition"] == "service_healthy"
    assert "pgdata:/var/lib/postgresql/data" in SERVICES["db"]["volumes"]


def test_probe_stays_behind_the_tools_profile() -> None:
    assert SERVICES["probe"]["profiles"] == ["tools"]
    assert all("profiles" not in s for n, s in SERVICES.items() if n != "probe")


def test_every_service_is_utc_and_reads_secrets_only_from_the_env_file() -> None:
    for name, s in SERVICES.items():
        env = s.get("environment", {})
        assert env.get("TZ") == "UTC", name
        for key, value in env.items():
            if _SECRETISH.search(key):  # 비밀은 값으로 쓰지 않는다 — .env 보간만
                assert str(value).startswith("${"), (name, key)
        if name in (*APP, "migrate", "probe"):
            assert s["env_file"] == [{"path": ".env", "required": False}], name
    assert "ports" not in str(COMPOSE)  # 공개 포트 없음 (PLAN §11.2)


def test_spool_lives_on_a_volume_under_the_configured_directory() -> None:
    for name in APP:
        s = SERVICES[name]
        assert s["environment"]["SPOOL_DIR"] == "/app/state/spool"
        assert "spool:/app/state/spool" in s["volumes"]
        assert s["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert set(COMPOSE["volumes"]) == {"pgdata", "redis", "spool"}


def test_the_engine_reads_the_flag_file_from_the_host_read_only() -> None:
    """기능 플래그(config/features.yaml)는 engine 이 30초마다 다시 읽는다 — 이미지 안의 사본이
    아니라 호스트 파일을 읽기 전용으로 붙인다(설계 §4). 다른 서비스는 붙이지 않는다."""
    mount = "./config/features.yaml:/app/config/features.yaml:ro"
    assert mount in SERVICES["engine"]["volumes"]
    assert "spool:/app/state/spool" in SERVICES["engine"]["volumes"]
    assert (ROOT / "config" / "features.yaml").is_file()
    others = [n for n in APP if n != "engine" and mount in SERVICES[n].get("volumes", [])]
    assert others == []


def test_env_example_has_placeholders_only_and_loads(tmp_path: Path) -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    for key in ("REDIS_URL", "DATABASE_URL", "POSTGRES_PASSWORD", "KIS_APP_KEY", "KIS_APP_SECRET"):
        assert values[key] == "", key  # 자리만 — 값은 없다
    copy = tmp_path / "env"
    copy.write_text(text, encoding="utf-8")
    s = Settings(_env_file=copy)  # pyright: ignore[reportCallIssue]
    assert s.redis_url in (None, "") and s.database_url is None and s.spool_max_mb == 1024
    assert s.kis_app_key is None and s.live_trading is False
    url = Settings(_env_file=None, database_url="postgresql://x").database_url  # pyright: ignore[reportCallIssue]
    assert isinstance(url, SecretStr)


def _seconds(v: str) -> float:
    m = re.fullmatch(r"(\d+)(s|m)", v)
    assert m is not None, v
    return float(m.group(1)) * (60 if m.group(2) == "m" else 1)


def test_ws_gateway_gets_time_to_drain_its_queue_before_sigkill() -> None:
    """작업 스레드 join 제한(WORKER_JOIN_S) + 여유(웹소켓 닫기·싱크 닫기) < stop_grace_period."""
    from services.ws_gateway.service import WORKER_JOIN_S

    grace = _seconds(SERVICES["ws-gateway"]["stop_grace_period"])
    assert grace >= WORKER_JOIN_S + 15
    assert _seconds(SERVICES["recorder"]["stop_grace_period"]) == 30  # 나머지는 x-app 기본


def test_the_integration_override_opens_only_loopback_ports_for_redis_and_db() -> None:
    """tests/integration/docker-compose.test.yml — 시험 프로세스가 붙을 redis·db 만, 127.0.0.1 의
    임의 호스트 포트로만 연다(PLAN §11.2 — 밖으로 열지 않는다). 운영 compose 에는 포트가 없다."""
    path = ROOT / "tests" / "integration" / "docker-compose.test.yml"
    override: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    services: dict[str, Any] = override["services"]
    assert set(services) == {"redis", "db"}
    for name, s in services.items():
        assert s["ports"] and all(re.fullmatch(r"127\.0\.0\.1::\d+", p) for p in s["ports"]), name
        assert SERVICES[name]["image"] == MULTI_ARCH[name]  # 같은 이미지를 쓴다
    assert "ports" not in str(COMPOSE)


def _override() -> dict[str, Any]:
    path = ROOT / "tests" / "integration" / "docker-compose.test.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def test_the_integration_stack_is_not_declared_unhealthy_before_up_wait_gives_up() -> None:
    """`up --wait` 는 컨테이너가 unhealthy 가 되는 순간 실패한다. 덧씌우기로 healthcheck 를 1초마다
    보게 하면 unhealthy 판정(start_period + retries × interval)도 그만큼 당겨진다 — 느린 기계에서
    TimescaleDB 첫 초기화(initdb → 재시작)가 그보다 길면 스택이 뜨지 못한다. 판정까지는
    `--wait-timeout`(STACK_WAIT_S)보다 길어야 한다."""
    from tests.integration.test_full_day import STACK_WAIT_S

    for name, s in _override().items():
        hc = {**SERVICES[name]["healthcheck"], **s.get("healthcheck", {})}
        start = _seconds(hc["start_period"]) if "start_period" in hc else 0.0
        horizon = start + int(hc["retries"]) * _seconds(hc["interval"])
        assert horizon >= STACK_WAIT_S, (name, horizon)


def test_the_integration_stack_does_not_restart_itself() -> None:
    """운영의 restart: always 를 이어받으면, 시험 프로세스가 `down -v` 전에 죽었을 때(SIGKILL·CI
    취소) 남은 gexlab-fd-* 컨테이너가 Docker 데몬이 뜰 때마다 다시 켜진다."""
    for name, s in _override().items():
        assert SERVICES[name]["restart"] == "always"  # 운영은 그대로
        assert s["restart"] == "no", name
