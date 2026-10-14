"""개인 기록 쓰기 저장소 — 0021
`prv_alerts.rule`·`rule_version`·`prv_journal.analysis`·`trade`·`pick`.

근거: docs/p8_design.md §3.3·D-P8-15·16·19. 로그인 등급.

표 하나에 쓰는 주체 하나(`kbj.core.rules_rows.P8_TABLE_WRITERS`) — 함수 이름에 주체를 적는다.
- `put_rule_personal`·`set_rule_state_personal`: 쓰기 모듈 `kbj.services.personal.rules`(API
  라우트·CLI 가 같이
  부른다). 수정은 **새 판**(rule_version 은 바꾸지 않는다 — 같은 판을 다시 넣으면 ValueError),
삭제는 보관. -
  `add_analysis_personal`·`update_analysis_personal`·`delete_analysis_personal`·`add_trade_personal`·
  `delete_trade_personal`: 쓰기 모듈 `kbj.services.personal.journal`.
- `put_picks_legacy_import`: `kbj.store.legacy_import`(SD 추천 이력 이관 — 한 번).
- 읽기는 `kbj.store.repos.rules`(규칙)·`kbj.store.repos.journal`(일지·매매·추천).

일지 금액(eps·tp 의 bear/base/bull)은 jsonb 에 **문자열**로 둔다(Decimal 그대로 — float 로 바꾸지
않는다).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Final, Protocol

from psycopg import sql

from kbj.core.rules_rows import AnalysisRow, PickRow, RuleRow, RuleVersion, TradeRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, upsert
from kbj.store.repos.journal import ANALYSIS_COLS, PICK, TRADE_COLS, analysis_to_db
from kbj.store.repos.rules import RULE, RULE_VERSION

__all__ = ["MemoryPersonalRepo", "PersonalRepo", "PgPersonalRepo"]


def _insert_sql(table: str, cols: Sequence[str]) -> sql.Composed:
    schema, name = table.split(".", 1)
    return sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(schema, name),
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        sql.SQL(", ").join(sql.Placeholder() for _ in cols),
    )


_A_INSERT: Final = _insert_sql("prv_journal.analysis", ANALYSIS_COLS)
_A_UPDATE: Final = sql.SQL("UPDATE prv_journal.analysis SET {} WHERE id = %s").format(
    sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in ANALYSIS_COLS)
)
_T_INSERT: Final = _insert_sql("prv_journal.trade", TRADE_COLS)


class PersonalRepo(Protocol):
    def put_rule_personal(self, head: RuleRow, version: RuleVersion | None) -> None:
        """규칙 머리를 쓰고(있으면 덮음) 새 판을 넣는다(한 트랜잭션). 판은 head.current_version 과
        같아야 하고 이미 있으면 ValueError(판은 바꾸지 않는다). version=None 이면 머리만."""
        ...

    def set_rule_state_personal(
        self,
        rule_id: str,
        *,
        updated_at: datetime,
        enabled: bool | None = None,
        archived_at: datetime | None = None,
    ) -> bool:
        """켜짐·보관 상태만 바꾼다. 규칙이 없으면 False."""
        ...

    def add_analysis_personal(self, row: AnalysisRow) -> int:
        """새 id 를 돌려준다(row.id 는 None)."""
        ...

    def update_analysis_personal(self, analysis_id: int, row: AnalysisRow) -> bool: ...

    def delete_analysis_personal(self, analysis_id: int) -> bool: ...

    def add_trade_personal(self, row: TradeRow) -> int: ...

    def delete_trade_personal(self, trade_id: int) -> bool: ...

    def put_picks_legacy_import(self, rows: Sequence[PickRow], *, loaded_by: str) -> int: ...


def _check_rule(head: RuleRow, version: RuleVersion | None) -> None:
    if version is None:
        return
    if version.rule_id != head.rule_id or version.version != head.current_version:
        raise ValueError("새 판은 머리의 current_version 과 같은 규칙·번호여야 한다")


class PgPersonalRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgPersonalRepo()"

    def put_rule_personal(self, head: RuleRow, version: RuleVersion | None) -> None:
        _check_rule(head, version)
        with self._connect() as conn, conn.transaction():
            upsert(conn, RULE, [head], {})
            if version is not None:
                cur = conn.execute(
                    "SELECT 1 FROM prv_alerts.rule_version WHERE rule_id = %s AND version = %s",
                    (version.rule_id, version.version),
                )
                if cur.fetchone() is not None:
                    raise ValueError("이미 있는 판 — 판은 바꾸지 않는다(새 판으로)")
                upsert(conn, RULE_VERSION, [version], {})

    def set_rule_state_personal(
        self,
        rule_id: str,
        *,
        updated_at: datetime,
        enabled: bool | None = None,
        archived_at: datetime | None = None,
    ) -> bool:
        require_aware("updated_at", updated_at)
        require_aware("archived_at", archived_at)
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(
                "UPDATE prv_alerts.rule SET enabled = COALESCE(%s, enabled), "
                "archived_at = COALESCE(%s, archived_at), updated_at = %s WHERE rule_id = %s",
                (enabled, archived_at, updated_at, rule_id),
            )
            return cur.rowcount > 0

    def add_analysis_personal(self, row: AnalysisRow) -> int:
        if row.id is not None:
            raise ValueError("새 일지는 id 가 없어야 한다")
        with self._connect() as conn, conn.transaction():
            got = conn.execute(_A_INSERT, analysis_to_db(row)).fetchone()
        if got is None:  # pragma: no cover
            raise RuntimeError("id 를 받지 못했다")
        return int(got[0])

    def update_analysis_personal(self, analysis_id: int, row: AnalysisRow) -> bool:
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(_A_UPDATE, (*analysis_to_db(row), analysis_id))
            return cur.rowcount > 0

    def delete_analysis_personal(self, analysis_id: int) -> bool:
        with self._connect() as conn, conn.transaction():
            cur = conn.execute("DELETE FROM prv_journal.analysis WHERE id = %s", (analysis_id,))
            return cur.rowcount > 0

    def add_trade_personal(self, row: TradeRow) -> int:
        if row.id is not None:
            raise ValueError("새 매매는 id 가 없어야 한다")
        vals = tuple(getattr(row, c) for c in TRADE_COLS)
        with self._connect() as conn, conn.transaction():
            got = conn.execute(_T_INSERT, vals).fetchone()
        if got is None:  # pragma: no cover
            raise RuntimeError("id 를 받지 못했다")
        return int(got[0])

    def delete_trade_personal(self, trade_id: int) -> bool:
        with self._connect() as conn, conn.transaction():
            cur = conn.execute("DELETE FROM prv_journal.trade WHERE id = %s", (trade_id,))
            return cur.rowcount > 0

    def put_picks_legacy_import(self, rows: Sequence[PickRow], *, loaded_by: str) -> int:
        require_loaded_by(loaded_by)
        with self._connect() as conn, conn.transaction():
            return upsert(conn, PICK, rows, {"loaded_by": loaded_by})


class MemoryPersonalRepo:
    """메모리판 — 규칙·판 표는 `MemoryRulesRepo` 와, 일지·매매·추천 표는 `MemoryJournalRepo` 와 나눠
    쓴다(`memory_repos` 가 묶는다)."""

    def __init__(
        self,
        rule_t: MemoryTable[RuleRow],
        version_t: MemoryTable[RuleVersion],
        analyses: dict[int, AnalysisRow],
        trades: dict[int, TradeRow],
        pick_t: MemoryTable[PickRow],
    ) -> None:
        self.rule_t = rule_t
        self.version_t = version_t
        self.analyses = analyses
        self.trades = trades
        self.pick_t = pick_t

    def __repr__(self) -> str:
        return "MemoryPersonalRepo()"

    def put_rule_personal(self, head: RuleRow, version: RuleVersion | None) -> None:
        _check_rule(head, version)
        if version is not None and self.version_t.get((version.rule_id, version.version)):
            raise ValueError("이미 있는 판 — 판은 바꾸지 않는다(새 판으로)")
        self.rule_t.upsert([head], {})
        if version is not None:
            self.version_t.upsert([version], {})

    def set_rule_state_personal(
        self,
        rule_id: str,
        *,
        updated_at: datetime,
        enabled: bool | None = None,
        archived_at: datetime | None = None,
    ) -> bool:
        require_aware("updated_at", updated_at)
        require_aware("archived_at", archived_at)
        old = self.rule_t.get((rule_id,))
        if old is None:
            return False
        new = replace(
            old,
            enabled=old.enabled if enabled is None else enabled,
            archived_at=old.archived_at if archived_at is None else archived_at,
            updated_at=updated_at,
        )
        self.rule_t.upsert([new], {})
        return True

    def add_analysis_personal(self, row: AnalysisRow) -> int:
        if row.id is not None:
            raise ValueError("새 일지는 id 가 없어야 한다")
        new_id = max(self.analyses, default=0) + 1
        self.analyses[new_id] = replace(row, id=new_id)
        return new_id

    def update_analysis_personal(self, analysis_id: int, row: AnalysisRow) -> bool:
        if analysis_id not in self.analyses:
            return False
        self.analyses[analysis_id] = replace(row, id=analysis_id)
        return True

    def delete_analysis_personal(self, analysis_id: int) -> bool:
        return self.analyses.pop(analysis_id, None) is not None

    def add_trade_personal(self, row: TradeRow) -> int:
        if row.id is not None:
            raise ValueError("새 매매는 id 가 없어야 한다")
        new_id = max(self.trades, default=0) + 1
        self.trades[new_id] = replace(row, id=new_id)
        return new_id

    def delete_trade_personal(self, trade_id: int) -> bool:
        return self.trades.pop(trade_id, None) is not None

    def put_picks_legacy_import(self, rows: Sequence[PickRow], *, loaded_by: str) -> int:
        require_loaded_by(loaded_by)
        return self.pick_t.upsert(rows, {"loaded_by": loaded_by})
