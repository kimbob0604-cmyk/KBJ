"""`python -m kbj.services.public_export` — 공개 산출물 로컬 빌드·검사(docs/p3_design.md §1.7·§7).

    python -m kbj.services.public_export build --out <dir> [--now ISO] [--no-db]
    python -m kbj.services.public_export check <dir>

- build: `KBJ_PUBLIC_EXPORT_DATABASE_URL` 이 있으면 그 연결을 확인하고(읽기 전용·역할 구성원),
  없거나 `--no-db` 면 DB 없이 코드 계산·공개 설정 파일만 만든다(P3 은 읽는 공개 표가 없다 — 로컬
  빌드·CI 용). 시각은 `--now`(시간대 포함 ISO)로 고정할 수 있다(시험).
- check: manifest·sha256·봉투·로그인 등급 출처 이름·로그인 API 흔적 검사. 문제가 있으면 종료 1.
- 출력은 파일 이름·규칙뿐(값·접속 정보 없음). 종료 코드 0 통과, 1 실패, 2 사용법.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.masking import mask_text
from kbj.core.time import utcnow
from kbj.services.public_export.export import default_connect, export_all
from kbj.services.public_export.manifest import check_tree


def _parse_now(text: str | None) -> datetime:
    if text is None:
        return utcnow()
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        raise ValueError("--now 는 시간대가 붙은 ISO 시각")
    return ts


def _build(out: Path, now_text: str | None, no_db: bool) -> int:
    settings = Settings()
    try:
        now = _parse_now(now_text)
        cal = TradingCalendar.default()
        connect_fn = None if no_db else default_connect(settings)
        if connect_fn is None:
            print("DB 없이 만든다(공개 표 없음 — 코드 계산·공개 설정 파일만)", file=sys.stderr)
            manifest = export_all(None, out, now=now, cal=cal, settings=settings)
        else:
            with connect_fn() as conn:
                manifest = export_all(conn, out, now=now, cal=cal, settings=settings)
    except Exception as e:
        msg = mask_text(f"{type(e).__name__}: {e}", settings.secret_values())[:300]
        print(f"실패: {msg}", file=sys.stderr)
        return 1
    for f in manifest.files:
        print(f"{f.name}: quality={f.quality}")
    print(f"파일 {len(manifest.files)}개 + manifest.json → {out}")
    return 0


def _check(directory: Path) -> int:
    problems = check_tree(directory)
    for p in problems:
        print(f"실패: {p}", file=sys.stderr)
    print(f"{directory}: 문제 {len(problems)}건")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.public_export")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="공개 JSON 을 디렉터리에 만든다")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--now", default=None, help="고정 시각(시간대 포함 ISO) — 시험용")
    b.add_argument("--no-db", action="store_true", help="공개 DB 연결 확인을 건너뛴다")
    c = sub.add_parser("check", help="내보낸 디렉터리를 검사한다")
    c.add_argument("dir", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        return _build(args.out, args.now, args.no_db)
    return _check(args.dir)


if __name__ == "__main__":
    sys.exit(main())
