"""공개 투자 콜 기록(`calls/`) 검사·목록 CLI(ADR 0042). 계산·규칙은 `kbj.engines.journal.calls`.

`python -m kbj.services.personal calls check [--against <git 기준>] [--today YYYY-MM-DD]`
- 모든 콜 파일을 읽어 형식·칸 모순·id 중복을 본다.
- `--against` 를 주면 그 git 기준(예: `origin/main`, 푸시 전 커밋)의 같은 파일과 비교한다:
  판단 칸·논리 본문이 바뀌었는지, 콜이 지워졌는지, 새 콜이 소급 기록인지(`MAX_BACKDATE_DAYS`).
  CI `calls` 잡이 부른다.

`python -m kbj.services.personal calls list` — id·날짜·방향·상태·기대 변화율 표.

DB 를 쓰지 않는다(저장소 인자는 받기만 한다). 종료 코드: 0 통과, 1 문제 있음, 2 사용법·git 오류.
시계는 주입한다(시험).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any, Final

from kbj.config.files import REPO_ROOT
from kbj.core.time import kst_date, utcnow
from kbj.engines.journal.calls import (
    CallProblem,
    CallRecord,
    check_call,
    check_new_call,
    duplicate_ids,
    immutable_changes,
    parse_call,
)

__all__ = ["CALLS_DIR", "CheckResult", "check_tree", "cli", "load_tree"]

CALLS_DIR: Final = "calls"
ALLOWED_TOP: Final = frozenset({"README.md", "TEMPLATE.md"})


class GitError(RuntimeError):
    """git 기준을 읽지 못했다(기준 이름·저장소 상태 문제)."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    records: tuple[CallRecord, ...]
    problems: tuple[CallProblem, ...]


def _list_working(repo: Path) -> dict[str, str]:
    """작업 트리의 콜 파일 → 본문. 키는 레포 루트 기준 posix 경로."""
    root = repo / CALLS_DIR
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        out[rel] = p.read_text(encoding="utf-8")
    return out


def _git(repo: Path, *args: str) -> str:
    try:
        cp = subprocess.run(  # noqa: S603 — 인자는 고정 명령 + 검증한 기준 이름
            ["git", "-C", str(repo), *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as e:
        raise GitError("git 이 없다") from e
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {args[0]} 실패: {e.stderr.strip().splitlines()[-1:] or ''}") from e
    return cp.stdout


def _list_ref(repo: Path, ref: str) -> dict[str, str]:
    """git 기준의 콜 파일 → 본문."""
    if not ref or ref.startswith("-"):
        raise GitError("기준 이름이 올바르지 않다")
    _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    names = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", CALLS_DIR).splitlines()
    return {n: _git(repo, "show", f"{ref}:{n}") for n in names if n.endswith(".md")}


def _parse_all(files: dict[str, str]) -> tuple[dict[str, CallRecord], list[CallProblem]]:
    recs: dict[str, CallRecord] = {}
    probs: list[CallProblem] = []
    for rel, text in files.items():
        parts = rel.split("/")
        if len(parts) == 2 and parts[1] in ALLOWED_TOP:
            continue
        if len(parts) == 2:
            probs.append(CallProblem(rel, None, "콜 파일은 calls/<YYYY>/ 아래에 둔다"))
            continue
        res = parse_call(rel, text)
        if isinstance(res, list):
            probs.extend(res)
        else:
            recs[rel] = res
    return recs, probs


def load_tree(files: dict[str, str]) -> tuple[dict[str, CallRecord], list[CallProblem]]:
    """경로 → 본문 묶음을 읽는다(시험·CLI 공용)."""
    return _parse_all(files)


def check_tree(
    current: dict[str, str],
    today: date,
    *,
    base: dict[str, str] | None = None,
) -> CheckResult:
    """현재 콜 묶음을 검사한다. `base` 를 주면 그 버전과 비교한다."""
    recs, probs = _parse_all(current)
    for r in recs.values():
        probs.extend(check_call(r, today))
    probs.extend(duplicate_ids(recs.values()))
    if base is not None:
        old_recs, _old_probs = _parse_all(base)  # 기준의 형식 문제는 이번 변경 탓이 아니다
        for rel in base:
            parts = rel.split("/")
            if len(parts) > 2 and rel not in current:
                probs.append(
                    CallProblem(rel, None, "낸 콜은 지우지 않는다(틀린 콜도 기록이다 — 닫는다)")
                )
        for rel, r in recs.items():
            old = old_recs.get(rel)
            if old is None:
                if rel not in base:
                    probs.extend(check_new_call(r, today))
            else:
                probs.extend(immutable_changes(old, r))
    return CheckResult(tuple(recs.values()), tuple(probs))


def _pct(x: Any) -> str:
    return f"{float(x) * 100:+.1f}%"


def cli(
    argv: Sequence[str],
    repos: Any,
    *,
    now: Callable[[], datetime] = utcnow,
    repo: Path = REPO_ROOT,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    """`calls check [--against REF] [--today D]` · `calls list`."""
    o = out if out is not None else sys.stdout
    e = err if err is not None else sys.stderr
    ap = argparse.ArgumentParser(prog="python -m kbj.services.personal calls")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="콜 파일 형식·모순·고친 흔적 검사")
    c.add_argument("--against", help="비교할 git 기준(예: origin/main)")
    c.add_argument("--today", type=date.fromisoformat, help="검사일(기본: 오늘 KST)")
    sub.add_parser("list", help="콜 목록")
    args = ap.parse_args(list(argv))

    current = _list_working(repo)
    today: date = getattr(args, "today", None) or kst_date(now())

    if args.cmd == "list":
        recs, probs = load_tree(current)
        for r in sorted(recs.values(), key=lambda x: (x.opened, x.id)):
            print(
                f"{r.opened}  {r.market}:{r.code:<6}  {r.direction:<5}  {r.status:<6}  "
                f"목표 {_pct(r.implied_move)}  {r.id}",
                file=o,
            )
        print(
            f"콜 {len(recs)}개" + (f" · 읽지 못한 파일 문제 {len(probs)}개" if probs else ""),
            file=o,
        )
        return 1 if probs else 0

    base: dict[str, str] | None = None
    if args.against:
        try:
            base = _list_ref(repo, args.against)
        except GitError as ex:
            print(f"실패: {ex}", file=e)
            return 2
    res = check_tree(current, today, base=base)
    for p in res.problems:
        print(str(p), file=e)
    vs = f" · 기준 {args.against}" if args.against else ""
    print(f"콜 {len(res.records)}개 · 문제 {len(res.problems)}개{vs}", file=o)
    return 1 if res.problems else 0
