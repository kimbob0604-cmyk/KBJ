"""`python -m kbj.services.watchlist import --from sd-json|csv <파일> [--list main] [--dry-run]
[--no-verify] [--skip-rejects]` · `show [--list main]`(docs/p4_design.md §1.8, D-P4-13).

- import: 파일 → 계획(넣을 종목·거절) → 요약 출력 → (`--dry-run` 이 아니고 거절이 없으면) 목록을
  통째로 바꾼다. 거절(형식 오류·모르는 코드·중복)이 있으면 쓰지 않고 종료 코드 1 — `--skip-rejects`
  면 나머지만 쓴다. `--no-verify` 는 유니버스·DART 고유번호 표 대조를 건너뛴다(빈 DB 에서 처음 넣을
  때).
- show: 목록(정렬·코드·이름·출처·추가 시각).
- 종료 코드: 0 성공, 1 거절·빈 결과로 쓰지 않음, 2 파일·설정 오류. 출력에는 코드·이름·수만 (비밀
  없음).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import IO, Any, cast

from kbj.core.time import utcnow
from kbj.services.watchlist import (
    FORMATS,
    Format,
    WatchRepos,
    apply_plan,
    describe,
    known_codes,
    plan_import,
    read_file,
    source_of,
)
from kbj.store.db import StoreError
from kbj.store.legacy_import.sources import SourceError

SERVICE = "watchlist"


def _pg_repos() -> WatchRepos:  # pragma: no cover — 운영 진입점(Postgres 필요)
    from kbj.config.settings import Settings
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.database_url is None:
        raise SourceError("KBJ_DATABASE_URL 이 필요하다")

    def conn() -> Any:
        return connect(settings, service=SERVICE)

    return pg_repos(conn)


def _import(args: argparse.Namespace, repos: WatchRepos, now: datetime, out: IO[str]) -> int:
    fmt = cast(Format, args.fmt)
    rows, rejects, foreign = read_file(fmt, Path(args.path))
    known = None if args.no_verify else known_codes(repos, now)
    if known is not None and not known:
        print(
            "유니버스·DART 고유번호 표가 비어 코드를 대조할 수 없다 — krx.daily·filings.corp_code "
            "뒤에 다시, 또는 --no-verify",
            file=out,
        )
        return 2
    plan = plan_import(
        rows, rejects, list_name=args.list, source=source_of(fmt), known=known, foreign=foreign
    )
    for line in describe(plan):
        print(line, file=out)
    if not plan.items:
        print("넣을 종목이 없다 — 쓰지 않았다", file=out)
        return 1
    if plan.rejects and not args.skip_rejects:
        print("거절이 있어 쓰지 않았다 — 파일을 고치거나 --skip-rejects", file=out)
        return 1
    if args.dry_run:
        print("--dry-run — 쓰지 않았다", file=out)
        return 0
    n = apply_plan(repos, plan, now=now)
    print(f"목록 {plan.list_name} 을 {n}개로 바꿨다", file=out)
    return 0


def _show(args: argparse.Namespace, repos: WatchRepos, out: IO[str]) -> int:
    items = repos.journal.watchlist(args.list)
    print(f"목록 {args.list}: {len(items)}개", file=out)
    for i in items:
        added = i.added_at.isoformat() if i.added_at is not None else "-"
        print(f"{i.sort:>3} {i.code} {i.name_at_add or '-'} {i.source} {added}", file=out)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    repos: WatchRepos | None = None,
    now: Callable[[], datetime] = utcnow,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.watchlist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="파일로 관심종목 목록을 통째로 바꾼다")
    imp.add_argument("--from", dest="fmt", choices=FORMATS, required=True)
    imp.add_argument("path", help="SD server_watchlist.json 또는 CSV(code,name,note,added_at)")
    imp.add_argument("--list", default="main", help="목록 이름(기본 main)")
    imp.add_argument("--dry-run", action="store_true", help="요약만 — 쓰지 않는다")
    imp.add_argument("--no-verify", action="store_true", help="유니버스·DART 대조를 건너뛴다")
    imp.add_argument("--skip-rejects", action="store_true", help="거절을 빼고 나머지만 쓴다")
    shw = sub.add_parser("show", help="목록 보기")
    shw.add_argument("--list", default="main")
    args = ap.parse_args(argv)
    o = out or sys.stdout
    e = err or sys.stderr
    try:
        r = repos if repos is not None else _pg_repos()
        if args.cmd == "import":
            return _import(args, r, now(), o)
        return _show(args, r, o)
    except (SourceError, StoreError, ValueError) as exc:
        print(f"실패: {exc}", file=e)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
