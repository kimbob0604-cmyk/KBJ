"""public-data 푸시·Pages 디스패치 — 기본 꺼짐, 켜면 고아 브랜치 커밋 1개 강제 푸시.

네트워크 없음: 원격은 임시 bare 저장소, 디스패치는 httpx.MockTransport.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kbj.core.time import KST
from kbj.services.public_export.export import write_files
from kbj.services.public_export.manifest import MANIFEST_NAME, PublicFile
from kbj.services.public_export.push import (
    PUBLIC_DATA_BRANCH,
    PushError,
    dispatch_pages,
    push_public_data,
    ssh_remote,
)

NOW = datetime(2026, 10, 7, 5, 30, tzinfo=KST)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git 이 없다")


def _out(tmp: Path) -> Path:
    d = tmp / "out"
    f = PublicFile(
        name="calendar.json",
        source="KBJ 거래 캘린더(코드 계산)",
        as_of=NOW,
        quality="ok",
        data={"days": []},
    )
    write_files([f], d, now=NOW)
    return d


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_disabled_does_nothing(tmp_path: Path) -> None:
    calls: list[Any] = []

    def runner(*a: Any, **k: Any) -> Any:
        calls.append(a)
        raise AssertionError("부르면 안 된다")

    r = push_public_data(tmp_path / "nowhere", enabled=False, remote="x", runner=runner)
    assert (r.pushed, r.reason) == (False, "disabled")
    assert calls == []


def test_refuses_to_push_bad_tree(tmp_path: Path) -> None:
    d = _out(tmp_path)
    (d / "calendar.json").write_text(json.dumps({"source": "KRX"}), encoding="utf-8")
    with pytest.raises(PushError, match="검사 실패"):
        push_public_data(d, enabled=True, remote=str(tmp_path / "remote.git"))


def test_missing_deploy_key_file(tmp_path: Path) -> None:
    d = _out(tmp_path)
    with pytest.raises(PushError, match="배포 키"):
        push_public_data(d, enabled=True, remote="r", deploy_key_path=tmp_path / "no.key")


@needs_git
def test_pushes_single_orphan_commit_and_force_replaces(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    d = _out(tmp_path)
    (d / "notes.txt").write_text("not listed", encoding="utf-8")
    r1 = push_public_data(d, enabled=True, remote=str(remote), now=NOW)
    assert r1.pushed and r1.files == ("calendar.json",)
    ref = f"refs/heads/{PUBLIC_DATA_BRANCH}"
    assert _git("rev-list", "--count", ref, cwd=remote).strip() == "1"
    tree = set(_git("ls-tree", "--name-only", ref, cwd=remote).split())
    assert tree == {"calendar.json", MANIFEST_NAME}  # manifest 에 적힌 것만
    first = _git("rev-parse", ref, cwd=remote).strip()
    # 다시 내보내고 푸시 → 여전히 커밋 1개(이력을 쌓지 않는다), 내용은 새것
    later = datetime(2026, 10, 8, 5, 30, tzinfo=KST)
    f = PublicFile(
        name="calendar.json",
        source="KBJ 거래 캘린더(코드 계산)",
        as_of=later,
        quality="ok",
        data={"days": [1]},
    )
    write_files([f], d, now=later)
    push_public_data(d, enabled=True, remote=str(remote), now=later)
    assert _git("rev-list", "--count", ref, cwd=remote).strip() == "1"
    assert _git("rev-parse", ref, cwd=remote).strip() != first
    author = _git("log", "-1", "--format=%an %ad", "--date=iso-strict", ref, cwd=remote)
    assert author.startswith("kbj-public-export 2026-10-08T05:30:00+09:00")


@needs_git
def test_push_failure_is_masked(tmp_path: Path) -> None:
    d = _out(tmp_path)
    with pytest.raises(PushError, match="git push 실패"):
        push_public_data(d, enabled=True, remote=str(tmp_path / "missing.git"), now=NOW)


def test_git_env_drops_kbj_settings_and_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git·ssh 자식 프로세스에 KBJ_* 설정(키·토큰)·ssh 에이전트·바깥 GIT_* 를 넘기지 않는다."""
    monkeypatch.setenv("KBJ_KIS_APP_SECRET", "sec-SECRET")
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent.sock")
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    d = _out(tmp_path)
    key = tmp_path / "deploy.key"
    key.write_text("x", encoding="utf-8")
    envs: list[dict[str, str]] = []

    def runner(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        envs.append(dict(kw["env"]))
        return subprocess.CompletedProcess(args, 0, "", "")

    r = push_public_data(d, enabled=True, remote="r", deploy_key_path=key, now=NOW, runner=runner)
    assert r.pushed and len(envs) == 4  # init·add·commit·push
    for env in envs:
        assert not [k for k in env if k.startswith("KBJ_")]
        assert "SSH_AUTH_SOCK" not in env and "GIT_DIR" not in env
        assert "StrictHostKeyChecking=yes" in env["GIT_SSH_COMMAND"]


def test_ssh_remote() -> None:
    assert ssh_remote("o/r") == "git@github.com:o/r.git"


def test_dispatch_ok_and_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        dispatch_pages(SecretStr("tok-SECRET"), repo="o/r", client=c)
    (req,) = seen
    assert req.method == "POST"
    assert req.url.path == "/repos/o/r/actions/workflows/pages.yml/dispatches"
    assert req.headers["authorization"] == "Bearer tok-SECRET"
    assert json.loads(req.content) == {"ref": "main"}


@pytest.mark.parametrize("status", [401, 404, 422, 500])
def test_dispatch_rejected_without_leaking(status: int) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="tok-SECRET echoed body")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c, pytest.raises(PushError) as ei:
        dispatch_pages(SecretStr("tok-SECRET"), repo="o/r", client=c)
    assert str(status) in str(ei.value)
    assert "tok-SECRET" not in str(ei.value)


def test_dispatch_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom tok-SECRET")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c, pytest.raises(PushError) as ei:
        dispatch_pages(SecretStr("tok-SECRET"), client=c)
    assert "tok-SECRET" not in str(ei.value)
