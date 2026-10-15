"""대테마 분류 공개 저장소 — 0023 `pub_themes.theme` 읽기·쓰기(ADR 0039).

- 쓰기는 `replace_themes` 하나: 대테마 분류 판을 **한 트랜잭션에서 통째로** 바꾼다(지우고 넣기 —
  중간 실패면 옛 판 유지). 쓰기 주인은 공개 작업 `theme.classes_sync` 하나(config/jobs.yaml).
- 공개 작업이 import 한다 — 이 모듈은 공개 표 이름만 둔다(계약 ㉑·tests/unit/test_public_writers.py
  — 로그인 스키마 글자 0). 공개 내보내기는 이 모듈도 쓰지 않는다(계약 ⑧ — 자기 SQL).
- 이 모듈은 `kbj.core`·psycopg 만 쓴다(계약 ⑦).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Protocol

from kbj.core.theme_rows import ThemeRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = ["MemoryThemePublicRepo", "PgThemePublicRepo", "ThemePublicRepo"]

T_THEME: Final = Table(
    "pub_themes.theme", ThemeRow, key=("id",), extra=("received_at", "loaded_by")
)


class ThemePublicRepo(Protocol):
    def replace_themes(
        self, rows: Sequence[ThemeRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """대테마 분류를 통째로 바꾼다(한 트랜잭션). 넣은 행 수."""
        ...

    def themes(self) -> list[ThemeRow]:
        """대테마(원천 순서)."""
        ...


def _check(rows: Sequence[ThemeRow], loaded_by: str, received_at: datetime) -> None:
    require_loaded_by(loaded_by)
    require_aware("received_at", received_at)
    ids = [r.id for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("pub_themes.theme: 기본 키(id)가 겹친다")


class PgThemePublicRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgThemePublicRepo()"

    def replace_themes(
        self, rows: Sequence[ThemeRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at)
        extra: dict[str, object] = {"received_at": received_at, "loaded_by": loaded_by}
        with self._connect() as conn, conn.transaction():
            delete(conn, T_THEME, "TRUE", ())
            return upsert(conn, T_THEME, rows, extra)

    def themes(self) -> list[ThemeRow]:
        with self._connect() as conn:
            return select(conn, T_THEME, order="ord, id")


class MemoryThemePublicRepo:
    """메모리판(시험이 표를 들여다볼 수 있게 `table`)."""

    def __init__(self) -> None:
        self.table: MemoryTable[ThemeRow] = MemoryTable(T_THEME)

    def replace_themes(
        self, rows: Sequence[ThemeRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(rows, loaded_by, received_at)
        staged: MemoryTable[ThemeRow] = MemoryTable(T_THEME)
        n = staged.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})
        self.table = staged  # 한 번에 바꾼다(트랜잭션과 같은 뜻)
        return n

    def themes(self) -> list[ThemeRow]:
        return sorted(self.table.all(), key=lambda r: (r.ord, r.id))
