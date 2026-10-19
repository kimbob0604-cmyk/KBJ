"""운영 VM 배포 도구 시험 — scripts/vm·docker-compose.vm.yml·deploy/Caddyfile(ADR 0040).

- 셸: `bash -n` 문법, shellcheck(있으면), 값 출력 금지(정적 — `# 값-내부` 표시 줄만 값을 다룬다 /
  동적 — 가짜 .env 의 표시값이 출력에 없는지), `set -x`·`.env` source·`down -v` 없음.
- .env 템플릿: .env.example 이름표 그대로 + 자동 비밀만, 권한 600, KBJ_LIVE_TRADING=false, 멱등.
- compose 오버레이: caddy 하나(호스트 네트워크·비밀 없음), `compose config -q`(도커 있을 때).
- Caddyfile: 주소 분기(KBJ_DOMAIN / sslip.io), api 127.0.0.1:8000, 텔레그램 웹훅 경로를 막지 않음.
- 운영에 데모 계정이 없고 로그인이 켜진 채(기본 사용자·비밀번호 없음, Secure 쿠키)인지.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / "scripts" / "vm"
LOCAL = ROOT / "scripts" / "local"
SCRIPTS = sorted(VM.glob("*.sh"))
LOCAL_SCRIPTS = sorted(LOCAL.glob("*.sh"))
ENTRY = [p for p in SCRIPTS if p.name != "lib.sh"]
OVERLAY = ROOT / "docker-compose.vm.yml"
CADDYFILE = ROOT / "deploy" / "Caddyfile"
GUIDE = ROOT / "docs" / "deploy_oracle.md"
DEMO_PASSWORD = "kbj-demo-only"  # 운영 파일에 없어야 하는 데모 고정값(README)을 찾으려고
SENTINEL = "ZZSENTINEL"  # 가짜 .env 값 — 출력에 나오면 실패

# 값을 담는 셸 변수 — 이것을 echo/printf 하는 줄은 `# 값-내부` 표시가 있어야 한다
_VALUE_VAR = re.compile(
    r"\$\{?(?:val|value|out|pw\w*|pass\w*|hash|token|secret|sql|resp|parsed|url|cred|line)\b"
)
_PRINT = re.compile(r"\b(?:echo|printf|cat)\b")


def _bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — 고정 인자
        ["bash", "-c", script],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
    )


def test_scripts_present_and_executable() -> None:
    names = {p.name for p in SCRIPTS}
    want = {"install.sh", "start.sh", "stop.sh", "update.sh", "status.sh", "backup.sh", "lib.sh"}
    assert want <= names
    for p in ENTRY:
        assert p.stat().st_mode & stat.S_IXUSR, f"{p.name} 실행 권한"
        assert p.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")
        assert "set -euo pipefail" in p.read_text(encoding="utf-8") or p.name == "status.sh"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_bash_syntax(path: Path) -> None:
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert r.returncode == 0, r.stderr


def test_shellcheck_if_available() -> None:
    exe = shutil.which("shellcheck")
    if exe is None:
        pytest.skip("shellcheck 없음(uvx --from shellcheck-py shellcheck 로 돌릴 수 있다)")
    r = subprocess.run(  # noqa: S603
        [exe, "-x", "-P", "SCRIPTDIR", *map(str, [*SCRIPTS, *LOCAL_SCRIPTS])],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout[-3000:]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_trace_no_env_source_no_volume_delete(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    code = "\n".join(
        line.split(" #", 1)[0] for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"set\s+-[a-wyz]*x|xtrace", code), "set -x 금지(값이 로그에 남는다)"
    assert not re.search(r"(?:source|\.)\s+\S*\.env\b", code), ".env 를 source 하지 않는다"
    assert not re.search(r"\bcat\s+\S*\.env\b", code), ".env 를 출력하지 않는다"
    assert not re.search(r"down\s+[^\n]*-v\b|volume\s+rm|--volumes", code), (
        "데이터 볼륨을 지우지 않는다"
    )
    assert "LIVE_TRADING=true" not in code


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_value_printing_lines_are_marked(path: Path) -> None:
    """값 변수를 echo/printf 하는 줄은 치환·파일·파이프로만 — `# 값-내부` 표시가 있어야 한다."""
    bad = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if _PRINT.search(line) and _VALUE_VAR.search(line) and "# 값-내부" not in line:
            bad.append(f"{path.name}:{n}: {line.strip()[:80]}")
    assert not bad, bad


def test_marked_lines_only_in_value_handlers() -> None:
    marked = {p.name for p in SCRIPTS if "# 값-내부" in p.read_text(encoding="utf-8")}
    assert marked <= {"lib.sh", "set-password.sh", "telegram.sh"}
    assert not [p.name for p in LOCAL_SCRIPTS if "# 값-내부" in p.read_text(encoding="utf-8")]


# ── .env 템플릿·검사(동적 — 임시 폴더) ─────────────────────────────────────────────────


def _env_lines(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


@pytest.fixture
def vm_root(tmp_path: Path) -> Path:
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def _lib(root: Path, body: str) -> subprocess.CompletedProcess[str]:
    return _bash(
        f'source scripts/vm/lib.sh; KBJ_ROOT="{root}"; KBJ_ENV_FILE="{root}/.env"; {body}',
        env={"HOME": str(root)},
    )


def test_env_init_template(vm_root: Path) -> None:
    r = _lib(vm_root, "kbj_env_init")
    assert r.returncode == 0, r.stderr
    env_file = vm_root / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    env = _env_lines(env_file)
    example = _env_lines(ROOT / ".env.example")
    assert set(example) <= set(env), "이름표(.env.example) 그대로"
    assert set(env) - set(example) == {"KBJ_DOMAIN", "KBJ_SITE_HOST"}
    assert env["KBJ_LIVE_TRADING"] == "false" and env["KBJ_NOTIFY_ENABLED"] == "false"
    auto = ("KBJ_POSTGRES_PASSWORD", "KBJ_REDIS_PASSWORD", "KBJ_TELEGRAM_WEBHOOK_SECRET")
    for name in auto:
        assert re.fullmatch(r"[0-9a-f]{48,64}", env[name]), name
    assert re.fullmatch(
        r"postgresql://kbj_public_reader:[0-9a-f]{48}@db:5432/kbj",
        env["KBJ_PUBLIC_EXPORT_DATABASE_URL"],
    )
    # 사용자가 채울 키는 비어 있다
    for name in (
        "KBJ_KIS_APP_KEY",
        "KBJ_TELEGRAM_BOT_TOKEN",
        "KBJ_WEB_PASSWORD_HASH",
        "KBJ_DART_API_KEY",
    ):
        assert env[name] == ""
    # 값은 출력에 없다(이름만)
    for name in auto:
        assert env[name] not in r.stdout + r.stderr
    # 멱등: 다시 돌려도 같은 값
    before = env_file.read_text(encoding="utf-8")
    assert _lib(vm_root, "kbj_env_init").returncode == 0
    assert env_file.read_text(encoding="utf-8") == before


def test_env_set_get_roundtrip_and_quote(vm_root: Path) -> None:
    value = f"scrypt$n=16384$r=8$p=1${SENTINEL}salt${SENTINEL}dk"
    r = _lib(
        vm_root,
        f"kbj_env_init >/dev/null; kbj_env_set KBJ_WEB_PASSWORD_HASH '{value}'; "
        f'v="$(kbj_env_get KBJ_WEB_PASSWORD_HASH)"; [[ "$v" == \'{value}\' ]] && printf ok',
    )
    assert r.stdout == "ok", r.stderr
    env = _env_lines(vm_root / ".env")
    assert env["KBJ_WEB_PASSWORD_HASH"] == f"'{value}'"  # compose 가 $ 를 치환하지 않게 작은따옴표
    r = _lib(vm_root, 'kbj_env_set KBJ_X "a\'b"')
    assert r.returncode != 0 and SENTINEL not in r.stderr


def test_check_env_prints_names_not_values(vm_root: Path) -> None:
    sets = "; ".join(
        f"kbj_env_set {n} {SENTINEL}{i}"
        for i, n in enumerate(
            (
                "KBJ_WEB_USER",
                "KBJ_KIS_APP_KEY",
                "KBJ_KIS_APP_SECRET",
                "KBJ_TELEGRAM_BOT_TOKEN",
                "KBJ_DART_API_KEY",
            )
        )
    )
    r = _lib(vm_root, f"kbj_env_init; {sets}; kbj_check_env; echo rc=$?")
    out = r.stdout + r.stderr
    assert "rc=1" in out  # 비밀번호 해시·대화방 번호가 비었다
    assert "KBJ_WEB_PASSWORD_HASH" in out and "KBJ_TELEGRAM_CHAT_ID" in out
    assert "KBJ_KIS_APP_KEY" not in out.split("비어 있는 필수 이름:", 1)[1].splitlines()[0]
    assert SENTINEL not in out


def test_check_env_all_filled_passes_and_live_trading_blocks(vm_root: Path) -> None:
    fill = "; ".join(f"kbj_env_has {n} || kbj_env_set {n} {SENTINEL}" for n in _required_keys())
    r = _lib(vm_root, f"kbj_env_init >/dev/null; {fill}; kbj_check_env; echo rc=$?")
    assert "rc=0" in r.stdout and SENTINEL not in r.stdout + r.stderr
    r = _lib(
        vm_root,
        f"kbj_env_init >/dev/null; {fill}; kbj_env_set KBJ_LIVE_TRADING true; "
        "kbj_check_env; echo rc=$?",
    )
    assert "rc=1" in r.stdout and "KBJ_LIVE_TRADING" in r.stderr


def _required_keys() -> list[str]:
    text = (VM / "lib.sh").read_text(encoding="utf-8")
    block = re.search(r"KBJ_REQUIRED_KEYS=\((.*?)\)", text, re.S)
    assert block
    return block.group(1).split()


def test_required_keys_cover_compose_and_login() -> None:
    req = set(_required_keys())
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in set(re.findall(r"\$\{(KBJ_\w+):\?", compose)):
        assert name in req, f"compose 가 요구하는 {name}"
    assert {"KBJ_WEB_USER", "KBJ_WEB_PASSWORD_HASH"} <= req  # 로그인이 켜진 채로만 띄운다
    assert {"KBJ_KIS_APP_KEY", "KBJ_TELEGRAM_BOT_TOKEN", "KBJ_TELEGRAM_WEBHOOK_SECRET"} <= req
    example = _env_lines(ROOT / ".env.example")
    assert req <= set(example), "이름표에 없는 이름을 요구하지 않는다"


# ── compose 오버레이·Caddy ────────────────────────────────────────────────────────────


def _overlay() -> dict[str, Any]:
    return yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))


def test_overlay_only_adds_caddy_without_secrets() -> None:
    doc = _overlay()
    assert set(doc["services"]) == {"caddy"}
    caddy = doc["services"]["caddy"]
    assert re.fullmatch(r"caddy:2\.\d+-alpine", caddy["image"])
    assert caddy["profiles"] == ["app"] and caddy["network_mode"] == "host" and "ports" not in caddy
    assert set(caddy["environment"]) == {"TZ", "KBJ_SITE_HOST"}
    assert ":?" in caddy["environment"]["KBJ_SITE_HOST"]
    assert "env_file" not in caddy
    assert "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert set(doc["volumes"]) == {"caddy_data", "caddy_config"}


def _api() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"][
        "api"
    ]


def test_overlay_upstream_matches_api() -> None:
    api = _api()
    assert api["network_mode"] == "host"
    host, port = api["environment"]["KBJ_API_HOST"], api["environment"]["KBJ_API_PORT"]
    assert f"reverse_proxy {host}:{port}" in CADDYFILE.read_text(encoding="utf-8")


def _compose_config(base: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [*base, "--profile", "app", "config", "-q"]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, check=False)  # noqa: S603


def test_compose_config_with_overlay(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker 없음")
    env_file = tmp_path / "ci.env"
    env_file.write_text("KBJ_POSTGRES_PASSWORD=x\nKBJ_REDIS_PASSWORD=y\nKBJ_SITE_HOST=a.sslip.io\n")
    base = [docker, "compose", "--env-file", str(env_file)]
    base += ["-f", "docker-compose.yml", "-f", OVERLAY.name]
    r = _compose_config(base)
    if r.returncode != 0 and "unknown" in r.stderr and "compose" in r.stderr:
        pytest.skip("docker compose 플러그인 없음")
    assert r.returncode == 0, r.stderr
    env_file.write_text("KBJ_POSTGRES_PASSWORD=x\nKBJ_REDIS_PASSWORD=y\n")
    r = _compose_config(base)
    assert r.returncode != 0 and "KBJ_SITE_HOST" in r.stderr  # 주소 없이 띄우지 않는다


def test_caddyfile_site_branch_and_headers() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "{$KBJ_SITE_HOST} {" in code
    assert "admin off" in code and "protocols h1 h2" in code
    assert "Strict-Transport-Security" in code and "-Server" in code
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b(?!:8000)", code.replace("127.0.0.1", ""))
    # 경로를 막거나 바꾸지 않는다 — 텔레그램 웹훅이 같은 호스트로 그대로 간다
    assert not re.search(r"\b(?:handle_path|rewrite|uri strip|respond)\b", code)
    # 주소 분기는 lib.sh: KBJ_DOMAIN 이 있으면 그것, 없으면 <IP 대시>.sslip.io
    lib = (VM / "lib.sh").read_text(encoding="utf-8")
    assert "kbj_env_get KBJ_DOMAIN" in lib and ".sslip.io" in lib and "${ip//./-}" in lib


def test_site_host_branch(vm_root: Path) -> None:
    r = _lib(
        vm_root,
        "kbj_env_init >/dev/null; kbj_env_set KBJ_DOMAIN 'https://KBJ.Example.com/'; kbj_site_host",
    )
    assert r.stdout == "kbj.example.com", r.stderr
    r = _lib(
        vm_root, "kbj_env_set KBJ_DOMAIN ''; kbj_public_ip() { printf 203.0.113.7; }; kbj_site_host"
    )
    assert r.stdout == "203-0-113-7.sslip.io", r.stderr


def test_webhook_path_matches_notifier() -> None:
    from kbj.services.notifier.webhook import WEBHOOK_PATH

    assert WEBHOOK_PATH == "/telegram/webhook"
    assert WEBHOOK_PATH in CADDYFILE.read_text(encoding="utf-8")
    assert "setup-webhook --confirm" in (VM / "telegram.sh").read_text(encoding="utf-8")
    assert WEBHOOK_PATH in GUIDE.read_text(encoding="utf-8")


def test_gex_poller_detection_matches_registry() -> None:
    reg = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text(encoding="utf-8"))
    enabled = next(s for s in reg["services"] if s["name"] == "gex.poller").get("enabled", False)
    lib = (VM / "lib.sh").read_text(encoding="utf-8")
    prog = re.search(r"awk '(/\^  - name: gex.*?)' \\\n", lib, re.S)
    assert prog, "lib.sh 의 gex.poller 판정 awk"
    r = subprocess.run(  # noqa: S603
        ["awk", prog.group(1), str(ROOT / "config" / "jobs.yaml")],  # noqa: S607
        capture_output=True,
        check=False,
    )
    assert (r.returncode == 0) is bool(enabled)


# ── 운영에 데모 계정 없음·로그인 켜짐 ──────────────────────────────────────────────────


def _ops_files() -> list[Path]:
    files = [OVERLAY, CADDYFILE, GUIDE, *SCRIPTS, ROOT / "docker-compose.yml", ROOT / "Dockerfile"]
    files += sorted((ROOT / "deploy").rglob("*"))
    return [p for p in files if p.is_file()]


def test_demo_account_not_in_ops_files() -> None:
    for p in _ops_files():
        text = p.read_text(encoding="utf-8")
        assert DEMO_PASSWORD not in text, p
        assert "KBJ_DEMO" not in text, p
        assert "demo_server" not in text, p


def test_login_on_in_production() -> None:
    from kbj.config.settings import Settings

    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue] — pydantic-settings 인자
    assert s.web_user is None and s.web_password_hash is None  # 기본 계정 없음
    assert s.web_cookie_secure is True
    api = _api()
    assert api["command"] == ["python", "-m", "kbj.services.api", "serve"]
    env = api["environment"]
    assert (
        env["KBJ_WEB_USER"] == "${KBJ_WEB_USER:-}"
        and env["KBJ_WEB_PASSWORD_HASH"] == "${KBJ_WEB_PASSWORD_HASH:-}"
    )
    assert "KBJ_WEB_COOKIE_SECURE" not in env  # 운영은 기본 true(Secure·__Host-)
    for p in _ops_files():
        assert not re.search(
            r"KBJ_WEB_COOKIE_SECURE=?\s*['\"]?false", p.read_text(encoding="utf-8")
        ), p


def test_docs_present() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    for mark in "①②③④⑤⑥⑦⑧⑨⑩⑪":
        assert f"## {mark}" in guide, mark
    assert "sslip.io" in guide and "Ampere" in guide and "KBJ_LIVE_TRADING" in guide
    adr = list((ROOT / "docs" / "adr").glob("0040-*.md"))
    assert len(adr) == 1
    assert "docs/deploy_oracle.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "deploy_oracle.md" in (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")


# ── 로컬 실행(Mac 한 대 — ADR 0041, scripts/local/kbj.sh, docker-compose.local.yml) ─────────

LOCAL_OVERLAY = ROOT / "docker-compose.local.yml"


@pytest.mark.parametrize("path", LOCAL_SCRIPTS, ids=lambda p: p.name)
def test_local_script_syntax_executable(path: Path) -> None:
    assert path.stat().st_mode & stat.S_IXUSR
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert r.returncode == 0, r.stderr


def test_local_and_vm_scripts_are_bash32_safe() -> None:
    """맥 기본 bash 는 3.2 — bash 4+ 전용 문법·빈 배열 확장(set -u)을 쓰지 않는다."""
    banned = re.compile(
        r"\bmapfile\b|\breadarray\b|\bcoproc\b|declare\s+-A|local\s+-A|local\s+-n"
        r"|\$\{[A-Za-z_]+(,,|\^\^)\}|\|&|;;&"
    )
    bare_sudo = re.compile(r'(?<!\+)"\$\{KBJ_SUDO\[@\]\}"')
    for path in [*LOCAL_SCRIPTS, *(p for p in SCRIPTS if not p.name.startswith("oci_"))]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split(" #", 1)[0]
            if code.lstrip().startswith("#"):
                continue
            assert not banned.search(code), f"{path.name}:{n}: bash 4+ 전용 문법"
            assert not bare_sudo.search(code), f"{path.name}:{n}: 빈 배열 확장(KBJ_SUDO)"


def test_local_overlay_publishes_loopback_only_without_secrets() -> None:
    text = LOCAL_OVERLAY.read_text(encoding="utf-8")
    data = yaml.safe_load(text.replace("!reset null", "null"))
    # scheduler 는 놓친 작업 따라잡기(KBJ_SCHEDULER_CATCH_UP_H) 한 줄만 더한다 —
    # 게시·네트워크 변경 없음
    assert set(data["services"]) == {"api", "scheduler"}
    sched = data["services"]["scheduler"]
    assert set(sched) == {"environment"}
    assert sched["environment"] == {"KBJ_SCHEDULER_CATCH_UP_H": "20"}
    api = data["services"]["api"]
    assert api["ports"] == ["127.0.0.1:8000:8000"], "호스트 쪽은 127.0.0.1 에만 게시"
    assert "!reset null" in text and "network_mode" in api
    env = api["environment"]
    assert env["KBJ_API_CONTAINER_BIND"] == "true" and env["KBJ_WEB_COOKIE_SECURE"] == "false"
    # 접속 문자열은 비밀번호를 .env 에서 받는다(값 직접 기입 없음)
    assert "${KBJ_POSTGRES_PASSWORD" in env["KBJ_DATABASE_URL"]
    assert "@db:5432" in env["KBJ_DATABASE_URL"] and "@redis:6379" in env["KBJ_REDIS_URL"]
    assert not re.search(r"(?i)(token|secret|app_key|api_key)\s*[:=]\s*['\"]?[A-Za-z0-9]{8,}", text)


def test_local_overlay_compose_config(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker 없음")
    env_file = tmp_path / "ci.env"
    env_file.write_text("KBJ_POSTGRES_PASSWORD=x\nKBJ_REDIS_PASSWORD=y\n")
    base = [docker, "compose", "--env-file", str(env_file)]
    base += ["-f", "docker-compose.yml", "-f", LOCAL_OVERLAY.name]
    r = subprocess.run(  # noqa: S603
        [*base, "--profile", "app", "config", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    if r.returncode != 0 and ("unknown" in r.stderr or "!reset" in r.stderr):
        pytest.skip("docker compose 가 오래돼 !reset 을 모른다(2.24 이상 필요)")
    assert r.returncode == 0, r.stderr
    import json

    api = json.loads(r.stdout)["services"]["api"]
    assert api.get("network_mode") in (None, ""), "host 네트워크가 지워져야 한다"
    assert any(
        p.get("host_ip") == "127.0.0.1" and str(p.get("published")) == "8000" for p in api["ports"]
    )


def test_local_overlay_and_lib_mode(vm_root: Path) -> None:
    r = _lib(vm_root, "KBJ_MODE=local; kbj_cmd start; echo; kbj_cmd password")
    assert r.returncode == 0, r.stderr
    assert "scripts/local/kbj.sh start" in r.stdout and "scripts/local/kbj.sh password" in r.stdout
    r = _lib(vm_root, "kbj_cmd password; echo; kbj_cmd start")
    assert "scripts/vm/set-password.sh" in r.stdout and "scripts/vm/start.sh" in r.stdout


def test_container_bind_allows_insecure_cookie_only_with_flag() -> None:
    from pydantic import ValidationError

    from kbj.config.settings import Settings

    base = {"api_host": "0.0.0.0", "web_cookie_secure": False}  # noqa: S104
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **base)  # type: ignore[arg-type]
    ok = Settings(_env_file=None, api_container_bind=True, **base)  # type: ignore[arg-type]
    assert ok.web_cookie_secure is False
    # 기본값은 그대로 안전(TLS 뒤 __Host- 쿠키)
    assert Settings(_env_file=None).web_cookie_secure is True  # type: ignore[call-arg]


def test_local_mode_does_not_require_telegram(vm_root: Path) -> None:
    r = _bash(
        'source scripts/vm/lib.sh; printf "%s\\n" "${KBJ_REQUIRED_KEYS[@]}"',
        env={"HOME": str(vm_root), "KBJ_MODE": "local"},
    )
    assert r.returncode == 0, r.stderr
    required = r.stdout.split()
    assert "KBJ_KIS_APP_KEY" in required and "KBJ_WEB_PASSWORD_HASH" in required
    assert not [k for k in required if k.startswith("KBJ_TELEGRAM")]


def test_local_auto_registers_wake_and_launchd_agents(tmp_path: Path) -> None:
    """auto on = pmset(깨우기) + launchd 둘(자동 시작·잠자기 방지)을 올바른 모양으로 등록한다."""
    import plistlib

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("pmset", "launchctl"):
        (bin_dir / name).write_text(f'#!/bin/bash\necho "{name} $*" >> "{log}"\n', encoding="utf-8")
    (bin_dir / "sudo").write_text('#!/bin/bash\nexec "$@"\n', encoding="utf-8")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "KBJ_ENV_FILE": str(tmp_path / ".env"),
    }
    r = subprocess.run(  # noqa: S603
        ["bash", str(LOCAL / "kbj.sh"), "auto", "on"],  # noqa: S607
        capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr + r.stdout
    calls = log.read_text(encoding="utf-8")
    assert "pmset repeat wakeorpoweron MTWRF 06:00:00" in calls
    assert calls.count("launchctl load") == 2
    agents = home / "Library" / "LaunchAgents"
    up = plistlib.loads((agents / "com.kbj.autostart.plist").read_bytes())
    assert up["RunAtLoad"] is True
    assert up["ProgramArguments"][-1].endswith("scripts/local/autostart.sh")
    assert [d["Weekday"] for d in up["StartCalendarInterval"]] == [1, 2, 3, 4, 5]
    assert {(d["Hour"], d["Minute"]) for d in up["StartCalendarInterval"]} == {(6, 2)}
    awake = plistlib.loads((agents / "com.kbj.awake.plist").read_bytes())
    assert awake["ProgramArguments"][:2] == ["/usr/bin/caffeinate", "-i"]
    assert awake["RunAtLoad"] is False and awake["ProgramArguments"][-1] == "57600"
    off = subprocess.run(  # noqa: S603
        ["bash", str(LOCAL / "kbj.sh"), "auto", "off"],  # noqa: S607
        capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    assert off.returncode == 0, off.stderr
    assert not list(agents.glob("com.kbj.*.plist"))
    assert "pmset repeat cancel" in log.read_text(encoding="utf-8")
