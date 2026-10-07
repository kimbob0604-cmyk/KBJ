"""서비스 기본 시계 — 시각을 주입받지 못한 자리(서비스 진입점·하트비트 기본값)만 쓴다.

벽시계를 읽는 함수는 하나로 모은다(CLAUDE.md §4 — 시험은 시계를 주입한다). 묶음 B 의
`kbj.core.time.utcnow` 가 생기면 이 함수는 그것을 다시 내보내는 한 줄로 바꾼다(두 벌 금지).
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """지금(UTC, aware)."""
    return datetime.now(tz=UTC)
