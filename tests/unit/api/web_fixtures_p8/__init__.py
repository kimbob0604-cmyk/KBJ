"""P8 프런트 시험 픽스처 — 도메인별 모듈(docs/p8_design.md §1.1·§1.8).

모듈 하나 = 도메인 하나 = `web/test/fixtures/api/<DOMAIN>/**`(rules·backtest·journal —
P3~P7 폴더와 겹치지 않는다). 각 모듈은 `DOMAIN` 과 `render() -> dict[str, str]`(도메인 폴더
기준 상대 경로 → JSON 글자, 결정적 — 가짜 시계·고정 시드 `tests/fixtures/synthetic/p8_world.py`)을
둔다. FND 는 빈 틀(파일 0)만 — 본문은 도메인 묶음이 자기 모듈만 채운다(rules = EVL,
backtest = BKT, journal = JNL — 포트폴리오·상관·추천 성과 포함).
`uv run python -m tests.unit.api.make_web_fixtures --only <도메인>` 이 그 도메인 폴더만 다시 쓴다.
"""

from __future__ import annotations

from typing import Final

__all__ = ["DOMAINS", "folder"]

DOMAINS: Final[tuple[str, ...]] = ("rules", "backtest", "journal")


def folder(domain: str) -> str:
    """도메인 → 픽스처 폴더 이름(도메인 이름 그대로 — API 경로 `/api/<도메인>/*` 와 같다)."""
    return domain
