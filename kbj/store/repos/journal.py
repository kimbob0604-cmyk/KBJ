"""관심종목 저장소 — 0013 `prv_journal.watchlist`(개인 데이터 — 로그인).

근거: docs/p4_design.md §1.1·§3.4·§3.8, D-P4-13.

- 읽기: API(`/api/watchlist`·관심종목 칩)·알림 작업(관심종목 잠정실적·리비전)·상세 수집
  대상(D-P4-5).
- 쓰기: 가져오기 CLI(`python -m kbj.services.watchlist import`)와 legacy 이관만. 쓰기 API 는 P8.
  `replace_watchlist` 는 그 목록을 **통째로 바꾼다**(한 트랜잭션 — 사용자가 올린 파일이 정본).
- 공개 작업·공개 내보내기는 이 모듈을 import 하지 않는다(계약 ⑬·⑧ — 공개판은 관심종목을 모른다).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Protocol

from kbj.core.fin_rows import WatchItem
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = ["JournalRepo", "MemoryJournalRepo", "PgJournalRepo"]

WATCHLIST: Final = Table(
    "prv_journal.watchlist", WatchItem, key=("list_name", "code"), extra=("updated_at", "loaded_by")
)


class JournalRepo(Protocol):
    def watchlist(self, list_name: str = "main") -> list[WatchItem]:
        """(sort, code) 순."""
        ...

    def watch_codes(self, list_name: str = "main") -> set[str]: ...

    def replace_watchlist(
        self, list_name: str, items: Sequence[WatchItem], *, loaded_by: str, now: datetime
    ) -> int:
        """그 목록을 통째로 바꾼다. 다른 목록 이름의 항목이 섞이면 ValueError. 쓴 행 수."""
        ...


def _check(list_name: str, items: Sequence[WatchItem], loaded_by: str, now: datetime) -> None:
    require_loaded_by(loaded_by)
    require_aware("now", now)
    bad = [i.code for i in items if i.list_name != list_name]
    if bad:
        raise ValueError(f"다른 목록의 항목이 섞였다: {bad[:3]}")
    codes = [i.code for i in items]
    if len(set(codes)) != len(codes):
        raise ValueError("같은 종목이 두 번 있다")


def _sorted(items: Sequence[WatchItem]) -> list[WatchItem]:
    return sorted(items, key=lambda i: (i.sort, i.code))


class PgJournalRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgJournalRepo()"

    def watchlist(self, list_name: str = "main") -> list[WatchItem]:
        with self._connect() as conn:
            return select(conn, WATCHLIST, "list_name = %s", (list_name,), "sort, code")

    def watch_codes(self, list_name: str = "main") -> set[str]:
        return {i.code for i in self.watchlist(list_name)}

    def replace_watchlist(
        self, list_name: str, items: Sequence[WatchItem], *, loaded_by: str, now: datetime
    ) -> int:
        _check(list_name, items, loaded_by, now)
        with self._connect() as conn, conn.transaction():
            delete(conn, WATCHLIST, "list_name = %s", (list_name,))
            return upsert(conn, WATCHLIST, items, {"updated_at": now, "loaded_by": loaded_by})


class MemoryJournalRepo:
    def __init__(self) -> None:
        self.watch_t = MemoryTable(WATCHLIST)

    def __repr__(self) -> str:
        return "MemoryJournalRepo()"

    def watchlist(self, list_name: str = "main") -> list[WatchItem]:
        return _sorted(self.watch_t.where(lambda i: i.list_name == list_name))

    def watch_codes(self, list_name: str = "main") -> set[str]:
        return {i.code for i in self.watchlist(list_name)}

    def replace_watchlist(
        self, list_name: str, items: Sequence[WatchItem], *, loaded_by: str, now: datetime
    ) -> int:
        _check(list_name, items, loaded_by, now)
        self.watch_t.remove(lambda i: i.list_name == list_name)
        return self.watch_t.upsert(items, {"updated_at": now, "loaded_by": loaded_by})
