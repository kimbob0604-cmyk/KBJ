"""CI: pytest junit XML 의 실패·오류를 GitHub Actions 주석(::error)으로 올린다.

로그 본문을 받지 못하는 환경(API 로 주석만 읽힌다)에서도 어느 시험이 왜 실패했는지 보이게 한다.
쓰는 법: python scripts/ci_junit_annotations.py <junit.xml> — 파일이 없으면 조용히 끝낸다.
시험 출력에는 합성 값만 있다(실데이터·비밀 없음 — CLAUDE.md). 주석 한 개당 끝부분 3,000자만.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LIMIT = 3000


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("쓰는 법: ci_junit_annotations.py <junit.xml>", file=sys.stderr)
        return 2
    path = Path(argv[0])
    if not path.is_file():
        return 0
    root = ET.parse(path).getroot()  # noqa: S314 — CI 가 방금 만든 파일
    for case in root.iter("testcase"):
        for tag in ("failure", "error"):
            node = case.find(tag)
            if node is None:
                continue
            title = f"{case.get('classname', '')}::{case.get('name', '')} ({tag})"
            body = (node.get("message", "") + "\n" + (node.text or ""))[-LIMIT:]
            print(f"::error title={_escape(title)}::{_escape(body)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
