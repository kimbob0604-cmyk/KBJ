"""docker-compose.demo.yml·Dockerfile.demo — 데모는 운영과 따로, 키·.env·외부 포트 없이.

규칙 함수는 scripts/check_public_outputs.py ④ 와 같은 것(`compose_problems`)을 쓴다
(docs/p9_design.md §2.5).
이미지 빌드·실행은 하지 않는다(메인이 `docker compose -f docker-compose.demo.yml up` 으로 확인).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import check_public_outputs as cpo

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker-compose.demo.yml"
DOCKERFILE = ROOT / "Dockerfile.demo"


def _doc() -> dict[str, Any]:
    raw: Any = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw  # pyright: ignore[reportUnknownVariableType]


def test_compose_follows_demo_rules() -> None:
    doc = _doc()
    assert cpo.compose_problems(doc) == []
    svc = doc["services"]["demo"]
    assert doc["name"] == "kbj-demo" and list(doc["services"]) == ["demo"]
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"])
    assert "env_file" not in svc and "volumes" not in svc and "volumes" not in doc
    assert svc["image"].startswith("kbj-demo:")
    assert svc["build"]["dockerfile"] == "Dockerfile.demo"
    assert svc["read_only"] is True
    env = svc["environment"]
    assert env["KBJ_DEMO_CONTAINER"] == "1"
    assert set(env) == {"KBJ_DEMO_CONTAINER", "KBJ_DEMO_PASSWORD", "KBJ_DEMO_AT"}
    # 고정 데모값은 기본값으로만(.env 없이 뜬다) — 합성 데이터 전용(§11 Q3)
    assert env["KBJ_DEMO_PASSWORD"] == "${KBJ_DEMO_PASSWORD:-kbj-demo-only}"
    assert svc["command"][:2] == ["python", "scripts/demo_server.py"]
    assert "--bind" in svc["command"] and "0.0.0.0" in svc["command"]  # noqa: S104 — 컨테이너 안만


@pytest.mark.parametrize(
    ("patch", "word"),
    [
        ({"ports": ["8765:8765"]}, "127.0.0.1"),
        ({"env_file": [".env"]}, "env_file"),
        ({"volumes": ["./:/app"]}, "volumes"),
        ({"image": "kbj-app:local"}, "이미지"),
        ({"environment": {"KIS_APP_KEY": "x"}}, "KIS_APP_KEY"),
        ({"environment": ["KBJ_WEB_PASSWORD=x"]}, "KBJ_WEB_PASSWORD"),
        ({"build": {"context": ".", "dockerfile": "Dockerfile"}}, "Dockerfile.demo"),
    ],
)
def test_compose_rule_catches_each_violation(patch: dict[str, Any], word: str) -> None:
    doc = _doc()
    doc["services"]["demo"] = {**doc["services"]["demo"], **patch}
    assert any(word in p for p in cpo.compose_problems(doc))


def test_ops_files_have_no_demo_account() -> None:
    """운영 compose·운영 이미지 입력(Dockerfile·.dockerignore·kbj·config)에 데모 계정 값이 없다.

    §11 Q3."""
    found = [f for f in cpo.stage_demo(ROOT) if f.rule == "demo_account_in_ops"]
    assert found == []
    ops_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert (
        "tests" in ops_ignore and "scripts" in ops_ignore
    )  # 운영 이미지에 데모 서버·합성 세계가 없다


def test_dockerfile_demo_shape() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev --group demo" in text  # dev·legacy 그룹 없음
    assert "npm ci --ignore-scripts" in text
    assert "USER kbj" in text and "KBJ_DEMO_CONTAINER=1" in text
    assert "kbj-demo-only" not in text  # 데모값은 이미지에 굽지 않는다(compose 가 환경변수로)
    lines = [x.strip() for x in text.splitlines() if not x.lstrip().startswith("#")]
    assert not any(
        x.startswith(("ARG", "COPY .env", "ADD")) for x in lines
    )  # 빌드 인자·.env 복사 없음
    env_lines = " ".join(
        x for x in lines if x.startswith("ENV") or x.startswith(("KBJ_", "PATH", "PYTHON"))
    )
    assert not any(w in env_lines for w in ("KEY", "SECRET", "TOKEN", "PASSWORD"))
    assert "COPY tests ./tests" in text and "COPY scripts/demo_server.py" in text


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker 없음")
def test_compose_config_is_valid() -> None:
    """`docker compose config -q` — 이미지 빌드 없이 파일 형식·보간만 검사한다(.env 없이)."""
    p = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(COMPOSE), "config", "-q"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if p.returncode != 0 and "is not a docker command" in p.stderr:
        pytest.skip("docker compose 플러그인 없음")
    assert p.returncode == 0, p.stderr
