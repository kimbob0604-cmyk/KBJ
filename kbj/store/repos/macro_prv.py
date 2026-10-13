"""로그인 매크로 저장소 — 0014 `prv_macro.series_meta`·`series`·`calendar`(로그인 — Yahoo 12종·FRED
저작권 시리즈·ECOS 타기관 표·ForexFactory 예상치).

근거: docs/p5_design.md §1.1·§2·§3.3, D-P5-6·7·8.

- 로그인 작업(`kbj.services.collectors.macro_prv`)·API·브리핑이 쓴다. 공개 작업·공개 내보내기는 이
모듈을
  import 하지 않는다(계약 ⑭·⑧).
- 메타 tier 는 private 만(0014 CHECK). 공개 시리즈를 여기 쓰면 거부 — 한 시리즈는 한 표에만.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from kbj.core.p5_rows import MacroSeriesMeta
from kbj.store.repos._macro import MacroRepo, MemoryMacroBase, PgMacroBase

__all__ = ["MacroPrivateRepo", "MemoryMacroPrivateRepo", "PgMacroPrivateRepo"]

SCHEMA = "prv_macro"


class MacroPrivateRepo(MacroRepo, Protocol):
    """로그인 매크로 저장소(모양은 `kbj.store.repos._macro.MacroRepo`)."""


def check_private_meta(rows: Sequence[MacroSeriesMeta]) -> None:
    bad = [m.series_id for m in rows if m.tier != "private"]
    if bad:
        raise ValueError(f"공개 시리즈는 pub_macro 에 쓴다(D-P5-6): {bad[:3]}")


class PgMacroPrivateRepo(PgMacroBase):
    schema = SCHEMA

    def _check_meta(self, rows: Sequence[MacroSeriesMeta]) -> None:
        check_private_meta(rows)


class MemoryMacroPrivateRepo(MemoryMacroBase):
    schema = SCHEMA

    def _check_meta(self, rows: Sequence[MacroSeriesMeta]) -> None:
        check_private_meta(rows)
