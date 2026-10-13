"""공개 매크로 저장소 — 0014 `pub_macro.series_meta`·`series`·`calendar`(공개 — ECOS 한은 작성 표·
KOSIS·FRED 정부 시리즈·미 재무부·뉴욕연은, 공식 일정·FRED 발표일).

근거: docs/p5_design.md §1.1·§2·§3.3, D-P5-6·7·8, metrics §16.

- 공개 작업(`kbj.services.collectors.macro`)·공개 내보내기 입력·API 가 쓴다. **로그인 매크로
  (`kbj.store.repos.macro_prv`)를 import 하지 않는다**(계약 ⑭ — 공개 작업은 로그인 저장소를 모른다).
- 등급은 데이터셋 단위(D-P5-6): 메타 tier 가 public 이 아니거나, source 가 공개 출처가 아니거나,
  데이터셋이 FRED 저작권 시리즈면 거부(0014 CHECK 와 같은 규칙을 먼저).
- 캘린더: 공개 출처(`PUBLIC_CALENDAR_SOURCES`)만, 예상치(forecast)는 칸이 없다 — 있으면
거부(D-P5-8).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from kbj.core.p5_rows import (
    PUBLIC_MACRO_SOURCES,
    CalendarEvent,
    MacroSeriesMeta,
)
from kbj.store.repos._macro import MacroRepo, MemoryMacroBase, PgMacroBase

__all__ = ["MacroPublicRepo", "MemoryMacroPublicRepo", "PgMacroPublicRepo"]

SCHEMA = "pub_macro"


class MacroPublicRepo(MacroRepo, Protocol):
    """공개 매크로 저장소(모양은 `kbj.store.repos._macro.MacroRepo`)."""


def check_public_meta(rows: Sequence[MacroSeriesMeta]) -> None:
    bad = [
        m.series_id
        for m in rows
        if m.tier != "public"
        or m.source not in PUBLIC_MACRO_SOURCES
        or m.dataset == "FRED:restricted_series"
    ]
    if bad:
        raise ValueError(f"공개 매크로 표에 로그인 시리즈를 쓰지 않는다(D-P5-6): {bad[:3]}")


def check_public_calendar(rows: Sequence[CalendarEvent]) -> None:
    bad = [e.event_id for e in rows if not e.is_public]
    if bad:
        raise ValueError("공개 캘린더는 공개 출처만·예상치 없이(D-P5-8): " + ", ".join(bad[:3]))


class PgMacroPublicRepo(PgMacroBase):
    schema = SCHEMA

    def _check_meta(self, rows: Sequence[MacroSeriesMeta]) -> None:
        check_public_meta(rows)

    def _check_calendar(self, rows: Sequence[CalendarEvent]) -> None:
        check_public_calendar(rows)


class MemoryMacroPublicRepo(MemoryMacroBase):
    schema = SCHEMA

    def _check_meta(self, rows: Sequence[MacroSeriesMeta]) -> None:
        check_public_meta(rows)

    def _check_calendar(self, rows: Sequence[CalendarEvent]) -> None:
        check_public_calendar(rows)
