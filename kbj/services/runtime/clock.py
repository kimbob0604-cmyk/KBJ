"""서비스 기본 시계 — 시각을 주입받지 못한 자리(서비스 진입점·하트비트 기본값)만 쓴다.

벽시계를 읽는 함수는 `kbj.core.time.utcnow` 하나다(CLAUDE.md §4 — 시험은 시계를 주입한다).
여기서는 그것을 다시 내보내기만 한다(두 벌 금지 — CLAUDE.md §3).
"""

from __future__ import annotations

from kbj.core.time import utcnow

__all__ = ["utcnow"]
