"""개인 기록 쓰기 모듈(P8 — docs/p8_design.md D-P8-15·16). 표마다 쓰는 쪽 하나:
규칙·판(`rules` — 묶음 EVL)·분석 일지·매매(`journal` — 묶음 JNL)·공개 콜 기록 검사
(`calls` — 파일만, DB 없음 — ADR 0042). API 라우트와 CLI 가 같은 함수를 부른다.
로그인 원천 어댑터·수집기를 import 하지 않는다(저장소·엔진만).

CLI: `python -m kbj.services.personal <그룹> …` — 그룹 모듈(`kbj.services.personal.<그룹>`)의
`cli(argv, repos, …)` 를 부른다(그룹을 더할 때 이 파일을 고치지 않는다).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from typing import Any, Final

__all__ = ["GROUPS", "main"]

GROUPS: Final = ("rules", "journal", "calls")
SERVICE: Final = "personal"


def _pg_repos() -> Any:  # pragma: no cover — 운영 진입점(Postgres 필요)
    from kbj.config.settings import Settings
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.database_url is None:
        raise ValueError("KBJ_DATABASE_URL 이 필요하다")

    def conn() -> Any:
        return connect(settings, service=SERVICE)

    return pg_repos(conn)


class _NoDatabase(RuntimeError):
    """운영 저장소를 만들 수 없다(KBJ_DATABASE_URL 없음)."""


class _LazyRepos:
    """처음 속성을 읽을 때 Postgres 저장소를 만든다.

    `--help`·인수 오류는 DB 없이 끝난다(P8 WRAP — JNL 요청)."""

    def __init__(self) -> None:
        self._real: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._real is None:
            try:
                self._real = _pg_repos()
            except ValueError as e:
                raise _NoDatabase(str(e)) from e
        return getattr(self._real, name)


def main(argv: Sequence[str] | None = None, *, repos: Any = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in GROUPS:
        print(f"사용법: python -m kbj.services.personal {{{','.join(GROUPS)}}} …", file=sys.stderr)
        return 2
    try:
        mod = importlib.import_module(f"kbj.services.personal.{args[0]}")
    except ImportError:
        print(f"그룹 {args[0]} 은 아직 없다", file=sys.stderr)
        return 2
    fn = getattr(mod, "cli", None)
    if fn is None:
        print(f"그룹 {args[0]} 에 CLI 가 없다", file=sys.stderr)
        return 2
    r = repos if repos is not None else _LazyRepos()
    try:
        return int(fn(args[1:], r))
    except _NoDatabase as e:
        print(f"실패: {e}", file=sys.stderr)
        return 2
