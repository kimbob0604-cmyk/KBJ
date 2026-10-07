"""발급은 auth 에만 — 세 겹 가운데 이 레포 안에서 바로 확인할 수 있는 것(ADR 0004 §2.2, 설계 §3.5).

1. 런타임 가드: `KBJ_SERVICE` 가 `auth` 가 아니면(없어도) 발급자 생성자가 `RuntimeError`.
2. 위치: 발급 클래스는 `kbj/services/auth/issuer.py` 에만 있고, kbj 안 다른 패키지는
   `kbj.services.auth` 를 import 하지 않는다(import-linter 계약 ④ 는 묶음 I 가 pyproject 에 넣는다 —
   그 전에도 이 시험이 본다).
3. 문자열: KIS 발급 경로 문자열은 `kbj/services/auth/**` 밖에 없다(`check_canonical` 그룹
   `kis_oauth`).
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.services.auth.issuer import (
    KisApprovalKeyIssuer,
    KisTokenIssuer,
    require_issuer_process,
)
from kbj.services.auth.service import build_auth_service, serve
from kbj.services.runtime import MemoryHealthSink

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
ROOT = Path(__file__).resolve().parents[3]


def settings(service: str | None) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        service=service,
    )


def http() -> httpx.Client:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"요청이 나가면 안 된다: {req.method} {req.url.path}")

    return httpx.Client(base_url="https://kis.example", transport=httpx.MockTransport(refuse))


@pytest.mark.parametrize("service", [None, "scheduler", "notifier", "api", "auth-shadow", "AUTH"])
def test_issuers_refuse_outside_the_auth_process(service: str | None) -> None:
    # 대문자 'AUTH' 는 설정 검증이 막지만, 우회해 들어와도 auth 가 아니다(검증 없이 바꿔 본다)
    s = settings(None).model_copy(update={"service": service})
    with pytest.raises(RuntimeError, match="auth") as e1:
        KisTokenIssuer(http(), SecretStr(APP_KEY), SecretStr(APP_SECRET), settings=s)
    with pytest.raises(RuntimeError, match="auth"):
        KisApprovalKeyIssuer(http(), SecretStr(APP_KEY), SecretStr(APP_SECRET), settings=s)
    with pytest.raises(RuntimeError, match="auth"):
        build_auth_service(s, fakeredis.FakeRedis(), http(), MemoryHealthSink())
    assert APP_KEY not in str(e1.value) and APP_SECRET not in str(e1.value)


def test_process_settings_decide_when_none_are_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정을 주지 않으면 이 프로세스의 설정(환경변수 `KBJ_SERVICE`)을 본다."""
    monkeypatch.setenv("KBJ_SERVICE", "scheduler")
    with pytest.raises(RuntimeError, match="KBJ_SERVICE=scheduler"):
        KisTokenIssuer(http(), SecretStr(APP_KEY), SecretStr(APP_SECRET))
    with pytest.raises(RuntimeError):
        require_issuer_process()
    monkeypatch.setenv("KBJ_SERVICE", "auth")
    require_issuer_process()
    KisTokenIssuer(http(), SecretStr(APP_KEY), SecretStr(APP_SECRET))


def test_the_auth_process_can_build_and_serve_refuses_others() -> None:
    svc = build_auth_service(
        settings("auth"), fakeredis.FakeRedis(), http(), MemoryHealthSink(), ws_key=False
    )
    assert svc is not None
    stop = threading.Event()
    stop.set()
    with pytest.raises(RuntimeError, match="auth"):
        serve(
            settings("scheduler"),
            stop,
            redis=fakeredis.FakeRedis(),
            http=http(),
            sink=MemoryHealthSink(),
        )


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def test_no_other_kbj_module_imports_the_auth_package() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "kbj").rglob("*.py")
        if "services/auth/" not in p.as_posix()
        and any(m == "kbj.services.auth" or m.startswith("kbj.services.auth.") for m in _imports(p))
    ]
    assert offenders == []


def test_issuer_classes_live_only_in_issuer_py() -> None:
    defs = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "kbj").rglob("*.py")
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and node.name in {"KisTokenIssuer", "KisApprovalKeyIssuer"}
    ]
    assert defs == ["kbj/services/auth/issuer.py", "kbj/services/auth/issuer.py"]


def test_issue_path_strings_only_in_auth() -> None:
    pat = re.compile(r"oauth2/(?:tokenP|Approval)")
    where = sorted(
        str(p.relative_to(ROOT))
        for p in (ROOT / "kbj").rglob("*.py")
        if pat.search(p.read_text(encoding="utf-8"))
    )
    assert where == ["kbj/services/auth/issuer.py"]
