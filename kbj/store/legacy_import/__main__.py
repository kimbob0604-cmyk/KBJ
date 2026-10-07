"""`python -m kbj.store.legacy_import` — 옛 SQLite·JSON → Postgres 이관(docs/p2_design.md §8.6).

종료 코드: 0 모두 맞음 · 1 불일치·실패가 하나라도 · 2 인자 오류(모르는 원본·매핑 없는 단계·접속 정보
없음). 접속 문자열은 `KBJ_DATABASE_URL`(Settings)에서만 읽고 출력하지 않는다. `--dry-run` 은 DB 에
붙지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from kbj.core.time import utcnow
from kbj.store.legacy_import import run
from kbj.store.legacy_import.targets import PgTarget


def _pairs(values: Sequence[str] | None, flag: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for v in values or ():
        name, sep, path = v.partition("=")
        if not sep or not name or not path:
            raise ValueError(f"{flag} 는 이름=경로 꼴이어야 한다")
        if name in out:
            raise ValueError(f"{flag} {name} 를 두 번 줬다")
        out[name] = Path(path)
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m kbj.store.legacy_import",
        description="옛 SQLite·JSON 을 읽기 전용으로 열어 KBJ Postgres 로 옮긴다(멱등·검증).",
    )
    p.add_argument("--source", action="append", metavar="이름=SQLite",
                   help="sd·board·us·backtest·us_backtest·etf")  # fmt: skip
    p.add_argument("--json", action="append", metavar="이름=경로",
                   help="inbox(inbox.json)·stockflows(state 폴더)·krflows(flows.json)")  # fmt: skip
    p.add_argument("--phase", required=True, help="이 단계 매핑만(P2)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="DB 없이 읽고 변환·집계만")
    mode.add_argument("--verify-only", action="store_true", help="쓰지 않고 대조만")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        sources = _pairs(args.source, "--source")
        jsons = _pairs(args.json, "--json")
        both = set(sources) & set(jsons)
        if both:
            raise ValueError(f"--source 와 --json 에 같은 이름: {', '.join(sorted(both))}")
        if not sources and not jsons:
            raise ValueError("--source 나 --json 을 하나 이상 준다")
    except ValueError as e:
        print(f"인자 오류: {e}", file=sys.stderr)
        return 2
    all_sources = {**sources, **jsons}
    if args.dry_run:
        try:
            report = run(all_sources, None, phase=args.phase, now=utcnow, dry_run=True)
        except ValueError as e:
            print(f"인자 오류: {e}", file=sys.stderr)
            return 2
    else:
        from kbj.config.settings import Settings
        from kbj.store.db import StoreError, connect

        try:
            conn = connect(Settings(), autocommit=True, service="legacy-import")
        except StoreError as e:  # 문구에 접속 정보가 없다(kbj.store.db)
            print(f"DB 오류: {e}", file=sys.stderr)
            return 2
        try:
            report = run(
                all_sources,
                PgTarget(conn),
                phase=args.phase,
                now=utcnow,
                verify_only=args.verify_only,
            )
        except ValueError as e:
            print(f"인자 오류: {e}", file=sys.stderr)
            return 2
        finally:
            conn.close()
    for line in report.lines():
        print(line)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
