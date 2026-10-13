"""P5 프런트 시험 픽스처 — 도메인별 모듈(docs/p5_design.md §1.1·§1.10).

모듈 하나 = 도메인 하나 = `web/test/fixtures/api/<DOMAIN>/**`. 각 모듈은 `DOMAIN` 과
`render() -> dict[str, str]`(도메인 폴더 기준 상대 경로 → JSON 글자, 결정적 — 가짜 시계·고정 시드
`tests/fixtures/synthetic/p5_world.py`)을 둔다. FND 는 빈 틀(파일 0)만 — 본문은 도메인 묶음이 자기
모듈만 채운다(macro = MAC, etf = ETF, us = US, program = F2, index_review = P5 2차 IDX — 테마는
사용자 별도 진행으로 P5 제외).
`uv run python -m tests.unit.api.make_web_fixtures --only <도메인>` 이 그 도메인 폴더만 다시 쓴다.
"""

from __future__ import annotations

from typing import Final

__all__ = ["DOMAINS"]

DOMAINS: Final[tuple[str, ...]] = ("macro", "etf", "us", "program", "index_review")
