"""테마 마스터 로그인 저장소 — 0023 `prv_themes.*` 읽기·쓰기(ADR 0039).

- 쓰기는 `replace_master` 하나: 검증한 판(`ThemeMaster`)의 로그인 표 일곱을 **한 트랜잭션에서
  통째로** 바꾸고(지우고 넣기) 적재 기록을 더한다. 중간에 실패하면 아무것도 바뀌지 않는다(옛 판
  유지). 쓰기 주인은 작업 `theme.master_sync` 하나(config/jobs.yaml — 등록부 시험). 판의 대테마
  행(`themes`)은 여기 쓰지 않는다 — 공개 표 `pub_themes.theme` 는 공개 작업 `theme.classes_sync` 가
  `kbj.store.repos.theme_pub` 로 쓴다(로그인 원천을 받는 작업은 공개 표에 쓰지 않는다 — ADR 0002).
- 공개 작업·공개 내보내기는 이 모듈을 import 하지 않는다(계약 ㉑). 로그인 API 가 이 모듈과
  `theme_pub` 로 두 등급을 다 읽는다.
- 이 모듈은 `kbj.core`·psycopg 만 쓴다(계약 ⑦).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Final, Protocol

from kbj.core.theme_rows import (
    IndustryGroupRow,
    MasterLoad,
    MemberRow,
    NaverExcludedRow,
    NaverThemeRow,
    StockRow,
    ThemeLinkRow,
    ThemeMaster,
    TypeOverrideRow,
)
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = ["MemoryThemeRepo", "PgThemeRepo", "ThemeRepo"]

_RECV: Final = ("received_at", "loaded_by")
T_LOAD: Final = Table(
    "prv_themes.master_load", MasterLoad, key=("built_at",), extra=_RECV,
    json_cols=frozenset({"counts", "warnings"}),
)  # fmt: skip
T_NAVER: Final = Table("prv_themes.naver_theme", NaverThemeRow, key=("no",), extra=_RECV)
T_LINK: Final = Table(
    "prv_themes.theme_link", ThemeLinkRow, key=("theme_id", "naver_no"), extra=_RECV
)
T_EXCLUDED: Final = Table(
    "prv_themes.naver_excluded", NaverExcludedRow, key=("naver_no",), extra=_RECV
)
T_MEMBER: Final = Table("prv_themes.member", MemberRow, key=("naver_no", "code"), extra=_RECV)
T_STOCK: Final = Table("prv_themes.stock", StockRow, key=("code",), extra=_RECV)
T_GROUP: Final = Table(
    "prv_themes.industry_group", IndustryGroupRow, key=("id",), extra=_RECV,
    json_cols=frozenset({"industries"}),
)  # fmt: skip
T_OVERRIDE: Final = Table("prv_themes.type_override", TypeOverrideRow, key=("code",), extra=_RECV)
# 판을 바꿀 때 통째로 비우는 표(적재 기록은 쌓는다)
_REPLACED: Final[tuple[Table[Any], ...]] = (
    T_NAVER, T_LINK, T_EXCLUDED, T_MEMBER, T_STOCK, T_GROUP, T_OVERRIDE,
)  # fmt: skip


def _parts(m: ThemeMaster) -> list[tuple[Table[Any], Sequence[Any]]]:
    return [
        (T_NAVER, m.naver_themes), (T_LINK, m.links),
        (T_EXCLUDED, m.excluded), (T_MEMBER, m.members), (T_STOCK, m.stocks),
        (T_GROUP, m.groups), (T_OVERRIDE, m.overrides),
    ]  # fmt: skip


class ThemeRepo(Protocol):
    # 쓰기 — theme.master_sync
    def replace_master(self, master: ThemeMaster, *, loaded_by: str, received_at: datetime) -> int:
        """로그인 표를 통째로 바꾸고 적재 기록을 더한다(한 트랜잭션). 넣은 행 수."""
        ...

    # 읽기
    def last_load(self) -> MasterLoad | None:
        """가장 최근(built_at 가장 늦은) 적재 기록."""
        ...

    def links(self, theme_id: str | None = None) -> list[ThemeLinkRow]: ...

    def excluded(self) -> list[NaverExcludedRow]: ...

    def naver_themes(self, nos: Collection[int] | None = None) -> list[NaverThemeRow]: ...

    def members(self, nos: Collection[int] | None = None) -> list[MemberRow]: ...

    def stocks(self, codes: Collection[str] | None = None) -> list[StockRow]: ...

    def groups(self) -> list[IndustryGroupRow]: ...


def _check(loaded_by: str, received_at: datetime) -> None:
    require_loaded_by(loaded_by)
    require_aware("received_at", received_at)


class PgThemeRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:  # 접속 정보를 보이지 않는다
        return "PgThemeRepo()"

    def replace_master(self, master: ThemeMaster, *, loaded_by: str, received_at: datetime) -> int:
        _check(loaded_by, received_at)
        extra: dict[str, object] = {"received_at": received_at, "loaded_by": loaded_by}
        n = 0
        with self._connect() as conn, conn.transaction():
            for t in _REPLACED:
                delete(conn, t, "TRUE", ())
            for t, rows in _parts(master):
                n += upsert(conn, t, rows, extra)
            upsert(conn, T_LOAD, [master.load], extra)
        return n

    def _read[R](
        self, t: Table[R], where: str = "TRUE", params: Sequence[object] = (), order: str = ""
    ) -> list[R]:
        with self._connect() as conn:
            return select(conn, t, where, params, order)

    def last_load(self) -> MasterLoad | None:
        with self._connect() as conn:
            got = select(conn, T_LOAD, order="built_at DESC", limit=1)
        return got[0] if got else None

    def links(self, theme_id: str | None = None) -> list[ThemeLinkRow]:
        if theme_id is None:
            return self._read(T_LINK, order="theme_id, ord")
        return self._read(T_LINK, "theme_id = %s", (theme_id,), "ord")

    def excluded(self) -> list[NaverExcludedRow]:
        return self._read(T_EXCLUDED, order="naver_no")

    def naver_themes(self, nos: Collection[int] | None = None) -> list[NaverThemeRow]:
        if nos is None:
            return self._read(T_NAVER, order="no")
        return self._read(T_NAVER, "no = ANY(%s)", (sorted(set(nos)),), "no")

    def members(self, nos: Collection[int] | None = None) -> list[MemberRow]:
        if nos is None:
            return self._read(T_MEMBER, order="naver_no, code")
        return self._read(T_MEMBER, "naver_no = ANY(%s)", (sorted(set(nos)),), "naver_no, code")

    def stocks(self, codes: Collection[str] | None = None) -> list[StockRow]:
        if codes is None:
            return self._read(T_STOCK, order="code")
        return self._read(T_STOCK, "code = ANY(%s)", (sorted(set(codes)),), "code")

    def groups(self) -> list[IndustryGroupRow]:
        return self._read(T_GROUP, order="id")


class MemoryThemeRepo:
    """메모리판 — Pg 와 같은 키·통째 바꾸기(시험이 표를 들여다볼 수 있게 `tables`·`loads`).
    `fail_after` 를 주면 그 수만큼 표를 쓴 뒤 오류를 낸다(원자적 교체 시험 — 아무것도 바뀌지
    않는다)."""

    def __init__(self) -> None:
        self.tables: dict[str, MemoryTable[Any]] = {t.name: MemoryTable(t) for t in _REPLACED}
        self.loads: MemoryTable[MasterLoad] = MemoryTable(T_LOAD)
        self.fail_after: int | None = None

    def replace_master(self, master: ThemeMaster, *, loaded_by: str, received_at: datetime) -> int:
        _check(loaded_by, received_at)
        extra: dict[str, object] = {"received_at": received_at, "loaded_by": loaded_by}
        staged: dict[str, MemoryTable[Any]] = {t.name: MemoryTable[Any](t) for t in _REPLACED}
        n = 0
        for i, (t, rows) in enumerate(_parts(master)):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("시험 — 쓰기 도중 실패")
            n += staged[t.name].upsert(rows, extra)
            if len(staged[t.name].rows) != len(rows):
                raise ValueError(f"{t.name}: 기본 키가 겹친다")
        self.tables = staged  # 한 번에 바꾼다(트랜잭션과 같은 뜻)
        self.loads.upsert([master.load], extra)
        return n

    def _all[R](self, t: Table[R]) -> list[R]:
        return self.tables[t.name].all()

    def last_load(self) -> MasterLoad | None:
        rows = self.loads.all()
        return max(rows, key=lambda r: r.built_at) if rows else None

    def links(self, theme_id: str | None = None) -> list[ThemeLinkRow]:
        rows = [r for r in self._all(T_LINK) if theme_id is None or r.theme_id == theme_id]
        return sorted(rows, key=lambda r: (r.theme_id, r.ord))

    def excluded(self) -> list[NaverExcludedRow]:
        return self._all(T_EXCLUDED)

    def naver_themes(self, nos: Collection[int] | None = None) -> list[NaverThemeRow]:
        want = None if nos is None else set(nos)
        return [r for r in self._all(T_NAVER) if want is None or r.no in want]

    def members(self, nos: Collection[int] | None = None) -> list[MemberRow]:
        want = None if nos is None else set(nos)
        return [r for r in self._all(T_MEMBER) if want is None or r.naver_no in want]

    def stocks(self, codes: Collection[str] | None = None) -> list[StockRow]:
        want = None if codes is None else set(codes)
        return [r for r in self._all(T_STOCK) if want is None or r.code in want]

    def groups(self) -> list[IndustryGroupRow]:
        return self._all(T_GROUP)
