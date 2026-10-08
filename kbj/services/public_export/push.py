"""public-data 브랜치 푸시와 Pages 디스패치 — **기본 꺼짐**(docs/p3_design.md §7.2·D-P3-16).

[사용자 승인 필요] 셋: GitHub Pages 활성(소스 = Actions), 배포 키(`KBJ_PUBLIC_DEPLOY_KEY_PATH` — 이
레포 Deploy keys 에 쓰기로 등록, 키 파일은 VM 에만), 디스패치 토큰(`KBJ_GITHUB_DISPATCH_TOKEN` — 이
레포 `actions:write` 만인 세분 토큰). 승인 전에는 `KBJ_PUBLIC_PUSH_ENABLED=false`(기본)로 둔다.

- `push_public_data`: 내보낸 디렉터리를 다시 검사(`check_tree`)하고, manifest 에 적힌 파일만 임시
  저장소에 담아 **고아 브랜치 `public-data` 에 커밋 1개로 강제 푸시**한다(이력을 쌓지 않는다 — 공개
  데이터의 최신본만). 꺼져 있으면 아무것도 하지 않고 `PushResult(pushed=False, reason="disabled")`.
- `dispatch_pages`: `pages.yml`(workflow_dispatch) 실행 요청. 응답 본문·토큰은 오류 문구에
  싣지 않는다.
- git 은 셸 없이 고정 인자로 부른다. 배포 키는 `GIT_SSH_COMMAND` 의 `-i` 로만 넘긴다.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import httpx
from pydantic import SecretStr

from kbj.core.masking import mask_text
from kbj.services.public_export.manifest import MANIFEST_NAME, check_tree, iso_kst, read_manifest

__all__ = [
    "GITHUB_API",
    "PAGES_WORKFLOW",
    "PUBLIC_DATA_BRANCH",
    "PUBLIC_REPO",
    "PushError",
    "PushResult",
    "dispatch_pages",
    "push_public_data",
    "ssh_remote",
]

PUBLIC_DATA_BRANCH: Final = "public-data"
PUBLIC_REPO: Final = "kimbob0604-cmyk/KBJ"  # 이 공개 레포 [확인 필요 — 이름이 바뀌면 여기]
PAGES_WORKFLOW: Final = "pages.yml"
GITHUB_API: Final = "https://api.github.com"
_AUTHOR: Final = "kbj-public-export"
_AUTHOR_EMAIL: Final = "kbj-public-export@users.noreply.github.com"
_GIT_TIMEOUT_S: Final = 120

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class PushError(RuntimeError):
    """푸시·디스패치 실패(가린 사유)."""


@dataclass(frozen=True)
class PushResult:
    pushed: bool
    reason: str  # "disabled" | "pushed"
    files: tuple[str, ...] = ()


def ssh_remote(repo: str) -> str:
    return f"git@github.com:{repo}.git"


def _git(
    args: Sequence[str], cwd: Path, env: dict[str, str], runner: Runner
) -> subprocess.CompletedProcess[str]:
    try:
        proc = runner(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        raise PushError("git 이 없다") from None
    except subprocess.TimeoutExpired:
        raise PushError(f"git {args[0]} {_GIT_TIMEOUT_S}초 초과") from None
    if proc.returncode != 0:
        first = ((proc.stderr or "").strip().splitlines() or [""])[0]
        raise PushError(mask_text(f"git {args[0]} 실패(종료 {proc.returncode}): {first}")[:300])
    return proc


def push_public_data(
    out_dir: Path,
    *,
    enabled: bool,
    remote: str,
    deploy_key_path: Path | None = None,
    now: datetime | None = None,
    runner: Runner = subprocess.run,
) -> PushResult:
    """`out_dir` 의 manifest 파일들을 `remote` 의 `public-data` 브랜치에 커밋 1개로 강제 푸시."""
    if not enabled:
        return PushResult(pushed=False, reason="disabled")
    problems = check_tree(out_dir)
    if problems:
        raise PushError("공개 산출물 검사 실패 — 푸시하지 않는다: " + "; ".join(problems[:5]))
    names = [str(e["name"]) for e in read_manifest(out_dir)["files"]]
    # git·ssh 자식 프로세스에 KBJ_* 설정(키·토큰·DSN)과 다른 git·ssh 에이전트 설정을 넘기지 않는다
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GIT_", "KBJ_")) and k != "SSH_AUTH_SOCK"
    }
    env.update(
        GIT_AUTHOR_NAME=_AUTHOR,
        GIT_AUTHOR_EMAIL=_AUTHOR_EMAIL,
        GIT_COMMITTER_NAME=_AUTHOR,
        GIT_COMMITTER_EMAIL=_AUTHOR_EMAIL,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    if now is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = iso_kst(now)
    if deploy_key_path is not None:
        if not deploy_key_path.is_file():
            raise PushError("배포 키 파일이 없다(KBJ_PUBLIC_DEPLOY_KEY_PATH)")
        # 호스트 키는 VM 의 known_hosts 로 확인한다(처음 한 번 사람이 등록 — accept-new 없음)
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {shlex.quote(str(deploy_key_path))} -o IdentitiesOnly=yes "
            "-o BatchMode=yes -o StrictHostKeyChecking=yes"
        )
    with tempfile.TemporaryDirectory(prefix="kbj-public-data-") as tmp:
        work = Path(tmp)
        for n in [*names, MANIFEST_NAME]:
            shutil.copyfile(out_dir / n, work / n)
        _git(["init", "-q", "-b", PUBLIC_DATA_BRANCH], work, env, runner)
        _git(["add", "--", *names, MANIFEST_NAME], work, env, runner)
        stamp = iso_kst(now) if now is not None else "now"
        _git(["commit", "-q", "-m", f"public-data {stamp}"], work, env, runner)
        _git(
            ["push", "--force", "--quiet", remote, f"HEAD:refs/heads/{PUBLIC_DATA_BRANCH}"],
            work,
            env,
            runner,
        )
    return PushResult(pushed=True, reason="pushed", files=tuple(names))


def dispatch_pages(
    token: SecretStr,
    *,
    repo: str = PUBLIC_REPO,
    ref: str = "main",
    client: httpx.Client | None = None,
) -> None:
    """`pages.yml` workflow_dispatch. 성공은 204. 실패는 `PushError`(상태 코드만)."""
    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{PAGES_WORKFLOW}/dispatches"
    headers = {
        "Authorization": f"Bearer {token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    own = client is None
    http: httpx.Client = client if client is not None else httpx.Client(timeout=10.0)
    try:
        resp = http.post(url, headers=headers, json={"ref": ref})
    except httpx.HTTPError as e:
        raise PushError(f"Pages 디스패치 요청 실패: {type(e).__name__}") from None
    finally:
        if own:
            http.close()
    if resp.status_code != 204:
        raise PushError(f"Pages 디스패치 거부: HTTP {resp.status_code}")
