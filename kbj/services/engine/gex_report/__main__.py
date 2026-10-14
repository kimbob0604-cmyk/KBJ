"""`python -m kbj.services.engine.gex_report {nogap,shadow} …` — 하위 명령을 고르고 나머지
인자를 넘긴다."""

from __future__ import annotations

import sys
from collections.abc import Sequence

USAGE = (
    "usage: python -m kbj.services.engine.gex_report {nogap,shadow} [옵션…]\n\n"
    "  nogap   N거래일 연속 무결측 판정(collection_reports — probe #61)\n"
    "  shadow  1주 섀도 운영 무오류 판정(engine 산출·health — probe #62)\n\n"
    "하위 명령마다 --help 로 옵션을 본다. 종료 코드 0 충족 · 1 미충족 · 2 설정·DB 오류."
)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    if cmd == "nogap":
        from kbj.services.engine.gex_report.nogap import main as run

        return run(rest)
    if cmd == "shadow":
        from kbj.services.engine.gex_report.shadow import main as run

        return run(rest)
    print(f"모르는 하위 명령: {cmd!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
