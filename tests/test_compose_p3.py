"""P3 compose·이미지 구조(정적 — 데몬 없이). docs/p3_design.md §1.9·D-P3-3·§5.4,
docs/secrets.md §5(묶음 S).

- `api` 서비스: 같은 이미지·profile app, 명령 `python -m kbj.services.api serve`,
  **호스트 네트워크** + 루프백 127.0.0.1:8000(TLS 역방향 프록시가 127.0.0.1 에서 들어온다 —
  forwarded_allow_ips 와 맞다). 주입은 secrets.md §5 표 그대로(KIS·KRX·텔레그램 봇 토큰 없음 —
  계약 ⑩), 비밀에 기본값 없음.
- scheduler: KIS 앱키·KRX 키(P3 수집 작업), 공개 내보내기 DSN(앱 DSN 과 다른 이름), 푸시는
  기본 false. 배포 키·디스패치 토큰은 아직 넣지 않는다(사용자 승인 사항).
- 이미지: node 단계가 로그인 SPA 를 빌드하고 결과만 앱 이미지로(KBJ_WEB_DIST_DIR), 설치
  스크립트는 돌리지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SECRET_ENV = re.compile(r"KBJ_\w*(KEY|SECRET|TOKEN|PASSWORD|HASH|_ID|_IDS|_URL|USER)$")


def _services() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def _env(name: str) -> dict[str, str]:
    return {k: str(v) for k, v in _services()[name]["environment"].items()}


def test_api_service_shape() -> None:
    api = _services()["api"]
    assert api["profiles"] == ["app"]
    assert api["image"] == "kbj-app:local" and api["build"]["dockerfile"] == "Dockerfile"
    assert api["command"] == ["python", "-m", "kbj.services.api", "serve"]
    assert api["network_mode"] == "host" and "ports" not in api
    env = _env("api")
    assert env["KBJ_SERVICE"] == "api" and env["TZ"] == "UTC"
    assert env["KBJ_API_HOST"] == "127.0.0.1" and env["KBJ_API_PORT"] == "8000"
    assert "env_file" not in api
    deps = api["depends_on"]
    assert deps["migrate"] == {"condition": "service_completed_successfully"}
    assert deps["db"]["condition"] == deps["redis"]["condition"] == "service_healthy"
    assert "/api/health" in " ".join(api["healthcheck"]["test"])


def test_api_gets_only_what_secrets_md_lists() -> None:
    env = _env("api")
    want = {
        "TZ",
        "KBJ_SERVICE",
        "KBJ_DATABASE_URL",
        "KBJ_REDIS_URL",
        "KBJ_API_HOST",
        "KBJ_API_PORT",
        "KBJ_WEB_USER",
        "KBJ_WEB_PASSWORD_HASH",
        "KBJ_TELEGRAM_WEBHOOK_SECRET",
        "KBJ_PUBLIC_BASE_URL",
        "KBJ_GIT_COMMIT",
    }
    assert set(env) == want
    # 호스트 네트워크라 DB·Redis 는 루프백 포트로(비밀번호는 .env 필수 — 기본값 없음)
    assert env["KBJ_DATABASE_URL"].startswith("postgresql://kbj:${KBJ_POSTGRES_PASSWORD:?")
    assert env["KBJ_DATABASE_URL"].endswith("@127.0.0.1:5432/kbj")
    assert env["KBJ_REDIS_URL"].startswith("redis://:${KBJ_REDIS_PASSWORD:?")
    assert env["KBJ_REDIS_URL"].endswith("@127.0.0.1:6379/0")
    for key in env:
        assert not key.startswith(("KBJ_KIS_", "KBJ_KRX_", "KBJ_DART_")), key
    assert "KBJ_TELEGRAM_BOT_TOKEN" not in env  # 발송은 notifier 만


def test_db_and_redis_ports_match_the_api_loopback_urls() -> None:
    s = _services()
    assert "127.0.0.1:5432:5432" in s["db"]["ports"]
    assert "127.0.0.1:6379:6379" in s["redis"]["ports"]


def test_scheduler_gets_p3_collection_keys_and_public_export_dsn() -> None:
    env = _env("scheduler")
    for key in ("KBJ_KIS_APP_KEY", "KBJ_KIS_APP_SECRET", "KBJ_KRX_API_KEY"):
        assert env[key] == f"${{{key}:-}}", key
    assert env["KBJ_PUBLIC_EXPORT_DATABASE_URL"] == "${KBJ_PUBLIC_EXPORT_DATABASE_URL:-}"
    assert env["KBJ_PUBLIC_PUSH_ENABLED"] == "${KBJ_PUBLIC_PUSH_ENABLED:-false}"
    # 사용자 승인 전: 배포 키·디스패치 토큰은 compose 에 없다
    assert "KBJ_PUBLIC_DEPLOY_KEY_PATH" not in env and "KBJ_GITHUB_DISPATCH_TOKEN" not in env
    assert "KBJ_WEB_PASSWORD_HASH" not in env


def test_new_secrets_have_no_default_values() -> None:
    for name in ("api", "scheduler"):
        for key, value in _env(name).items():
            if not SECRET_ENV.search(key):
                continue
            for m in re.finditer(r"\$\{(\w+)(:[-?])([^}]*)\}", value):
                op, rest = m.group(2), m.group(3)
                assert op == ":?" or rest == "", f"{name}.{key} 에 기본값이 있다"


def test_image_builds_the_login_spa_without_install_scripts() -> None:
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM node:22[\w.-]* AS web$", docker, re.M)
    assert "npm ci --ignore-scripts" in docker
    assert "npm run build:login" in docker
    assert "COPY --from=web /web/dist-login ./web/dist-login" in docker
    assert "KBJ_WEB_DIST_DIR=/app/web/dist-login" in docker
    # 앱 단계에는 node·node_modules 가 없다(결과만 복사)
    app_stage = docker.split("FROM python:", 1)[1]
    assert "node_modules" not in app_stage and "npm " not in app_stage
    ignore = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {"web/node_modules", "web/dist-public", "web/dist-login"} <= ignore
