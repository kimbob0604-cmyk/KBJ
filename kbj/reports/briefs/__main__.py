"""`python -m kbj.reports.briefs preview --kind morning|closing --at <ISO>` — 미리보기(발송 없음).

DB(`KBJ_DATABASE_URL`)가 있으면 그 저장소를 읽고, 없으면 빈 메모리 저장소로 모양만 보여 준다(모든
재료가 '미준비' — 지어낸 값을 넣지 않는다). 본문은 표준 출력, 안내는 표준 오류로.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from kbj.config.settings import Settings
from kbj.reports.briefs.job import build, make_ctx
from kbj.store.repos import pg_repos
from kbj.store.repos.memory import memory_repos


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m kbj.reports.briefs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("preview", help="본문을 만들어 출력만 한다(발송 없음)")
    pv.add_argument("--kind", required=True, choices=("morning", "closing"))
    pv.add_argument(
        "--at", required=True, help="실행 시각 ISO(시간대 포함) 예: 2026-11-17T08:10:00+09:00"
    )
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    a = parse_args(argv)
    at = datetime.fromisoformat(a.at)
    if at.tzinfo is None:
        raise SystemExit("--at 에 시간대를 붙인다(예: +09:00) — naive 시각은 받지 않는다")
    settings = Settings()
    repos: Any
    if settings.database_url is None:
        print(
            "KBJ_DATABASE_URL 없음 — 빈 메모리 저장소로 모양만 본다(재료 전부 미준비)",
            file=sys.stderr,
        )
        repos = memory_repos()
    else:
        from kbj.store.db import connect

        repos = pg_repos(lambda: connect(settings, service="reports"))
    bc = make_ctx(repos, at, settings=settings)
    brief, text = build(a.kind, bc)
    print(text)
    ready = sum(1 for s in brief.sections if s.status == "ok")
    print(f"— {a.kind} · 섹션 {ready}개 실음 · 미준비 "
          f"{sum(1 for s in brief.sections if s.status == 'not_ready')} · "
          f"{len(text)}자(보내지 않음)",
          file=sys.stderr)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
