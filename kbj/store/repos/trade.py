"""수출입·매핑 저장소(공개) — 0019 `pub_trade.*`(P6 묶음 FND — docs/p6_design.md §1.1·§3.3).

- **쓰기 함수는 표마다 하나**이고 그 표의 쓰기 주인 작업만 부른다(config/jobs.yaml — 등록부 시험):
  `trade.customs_tenday` → `upsert_ten_day`·`upsert_ten_day_columns`, `trade.customs_monthly` →
  `upsert_item_monthly`·`upsert_country_monthly`·`upsert_item_country_monthly`·
  `upsert_sigungu_monthly`·`upsert_hs_dim`, `trade.derive` → `upsert_surge`·
  `upsert_item_group_monthly`, `trade.map_load` → `replace_stock_map`, `trade.stock_index` →
  `upsert_stock_export`.
- 읽기: 기간(`ym_from`·`ym_to` 포함)·HS 접두·국가·시도·지역·종목. 결과는 기본 키 오름차순.
- 10일 잠정치는 `first_usd`·`first_seen_at` 을 덮지 않는다(발표 당시 값 — metrics §25). 값이 None 인
  새 행은 있는 값을 지우지 않는다(월간·10일 — 절대 규칙 2).
- 매핑표는 판(map_version) 단위로 **통째로 바꾼다**(`replace_stock_map` — 한 트랜잭션: 지우고 넣기.
  검증은 부르는 쪽(MAP)이 먼저 — 실패하면 부르지 않아 옛 판이 남는다).
- 공개 내보내기는 이 저장소가 아니라 pub_ SQL 로 읽는다(계약 ⑧). 이 모듈은 공개 표만 다룬다(계약 ⑭ —
  공개 작업이 import 한다). `kbj.core`·psycopg 만 쓴다(계약 ⑦).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final, Literal, Protocol

from psycopg import sql

from kbj.core.trade_rows import (
    MAP_ORIGINS,
    CountryMonthRow,
    HsDim,
    ItemCountryMonthRow,
    ItemGroupMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    StockExportRow,
    StockMapRow,
    SurgeRow,
    TenDayColumn,
    TenDayRow,
)
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, key_of, upsert

__all__ = ["MemoryTradeRepo", "PgTradeRepo", "TradeRepo"]

_RECV: Final = ("received_at", "loaded_by")
_COMP: Final = ("computed_at", "loaded_by")

T_TEN_DAY: Final = Table(
    "pub_trade.ten_day", TenDayRow, key=("kind", "ym", "seg", "col_no"), extra=_RECV,
    keep=frozenset({"first_usd", "first_seen_at"}),
)  # fmt: skip
T_TEN_DAY_COLUMNS: Final = Table(
    "pub_trade.ten_day_columns", TenDayColumn, key=("kind", "col_no", "valid_from"),
    extra=("loaded_by",), keep=frozenset({"seen_at"}),
)  # fmt: skip
T_ITEM: Final = Table("pub_trade.item_monthly", ItemMonthRow, key=("ym", "hs"), extra=_RECV)
T_COUNTRY: Final = Table(
    "pub_trade.country_monthly", CountryMonthRow, key=("ym", "country"), extra=_RECV
)
T_ITEM_COUNTRY: Final = Table(
    "pub_trade.item_country_monthly", ItemCountryMonthRow, key=("ym", "country", "hs"),
    extra=_RECV,
)  # fmt: skip
T_SIGUNGU: Final = Table(
    "pub_trade.sigungu_monthly", SigunguMonthRow, key=("ym", "sido_cd", "sgg_nm", "hs6"),
    extra=_RECV,
)  # fmt: skip
# first_ym·last_ym 은 `upsert_hs_dim` 이 기존 값과 LEAST·GREATEST 로 합친 뒤 쓴다(백필이 정기 수집
# 뒤에 더 옛날 달을 넣어도 처음 본 달이 맞게 — COL 요청). 그래서 keep 은 두지 않는다
T_HS_DIM: Final = Table("pub_trade.hs_dim", HsDim, key=("hs",), extra=_RECV)
T_SURGE: Final = Table("pub_trade.surge", SurgeRow, key=("ym", "level", "key", "flow"), extra=_COMP)
T_ITEM_GROUP: Final = Table(
    "pub_trade.item_group_monthly", ItemGroupMonthRow, key=("ym", "group_id"), extra=_COMP
)
T_STOCK_MAP: Final = Table(
    "pub_trade.stock_map", StockMapRow, key=("code", "hs", "flow", "region_id"), extra=_RECV,
    json_cols=frozenset({"evidence"}),
)  # fmt: skip
T_STOCK_EXPORT: Final = Table(
    "pub_trade.stock_export", StockExportRow, key=("code", "period"), extra=_COMP,
    json_cols=frozenset({"lag_corr"}),
)  # fmt: skip

# 값이 None 인 새 행이 있는 값을 지우지 않게 — 표마다 값 칸(Pg update_where·메모리 병합이 같은 규칙)
_VALUE_COLS: Final[dict[str, tuple[str, ...]]] = {
    T_TEN_DAY.name: ("usd",),
    T_ITEM.name: ("exp_usd", "imp_usd"),
    T_COUNTRY.name: ("exp_usd", "imp_usd"),
    T_ITEM_COUNTRY.name: ("exp_usd", "imp_usd"),
    T_SIGUNGU.name: ("exp_usd", "imp_usd"),
}


def _update_where(table: Table[Any]) -> str | None:
    cols = _VALUE_COLS.get(table.name)
    if not cols:
        return None
    new_has = " OR ".join(f"EXCLUDED.{c} IS NOT NULL" for c in cols)
    old_empty = " AND ".join(f"cur.{c} IS NULL" for c in cols)
    return f"({new_has}) OR ({old_empty})"


# ── 거르기(Pg·메모리가 같은 명세를 쓴다) ──────────────────────────────────────────────────────

Op = Literal["eq", "ge", "le", "prefix", "in"]


@dataclass(frozen=True)
class _Cond:
    col: str
    op: Op
    value: object


def _conds(**kw: tuple[str, Op, object] | None) -> list[_Cond]:
    return [_Cond(*v) for v in kw.values() if v is not None and v[2] is not None]


def _pg_where(table: Table[Any], conds: Sequence[_Cond]) -> tuple[sql.Composable, list[object]]:
    parts: list[sql.Composable] = [sql.SQL("TRUE")]
    params: list[object] = []
    for c in conds:
        col = sql.Identifier(c.col)
        if c.op == "eq":
            parts.append(sql.SQL("{} = %s").format(col))
            params.append(c.value)
        elif c.op == "ge":
            parts.append(sql.SQL("{} >= %s").format(col))
            params.append(c.value)
        elif c.op == "le":
            parts.append(sql.SQL("{} <= %s").format(col))
            params.append(c.value)
        elif c.op == "prefix":
            parts.append(sql.SQL("left({}, %s) = %s").format(col))
            params.extend([len(str(c.value)), c.value])
        else:
            parts.append(sql.SQL("{} = ANY(%s)").format(col))
            params.append(sorted(set(c.value)))  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]
    del table
    return sql.SQL(" AND ").join(parts), params


def _mem_pred[R](table: Table[R], conds: Sequence[_Cond]) -> Callable[[R], bool]:
    back = {table.col(f): f for f in table.fields}

    def ok(row: R) -> bool:
        for c in conds:
            v: Any = getattr(row, back[c.col])
            v = getattr(v, "value", v)  # 열거(Quality 등)는 값으로
            if c.op == "eq" and v != c.value:
                return False
            if c.op == "ge" and not v >= c.value:
                return False
            if c.op == "le" and not v <= c.value:
                return False
            if c.op == "prefix" and not str(v).startswith(str(c.value)):
                return False
            if c.op == "in" and v not in set(c.value):  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]
                return False
        return True

    return ok


def _ym_range(ym_from: str | None, ym_to: str | None, col: str = "ym") -> dict[str, Any]:
    return {"ym_from": (col, "ge", ym_from), "ym_to": (col, "le", ym_to)}


# ── 저장소 모양 ───────────────────────────────────────────────────────────────────────────────


class TradeRepo(Protocol):
    """`pub_trade.*` 저장소. 쓰기 함수 = 표 하나(주인 작업만), 읽기 = 기본 키 오름차순."""

    # 쓰기 — trade.customs_tenday
    def upsert_ten_day(
        self, rows: Sequence[TenDayRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """10일 잠정치(first_usd·first_seen_at 은 처음 값 유지, None 값은 있는 값을 지우지
        않는다)."""
        ...

    def upsert_ten_day_columns(self, rows: Sequence[TenDayColumn], *, loaded_by: str) -> int: ...

    # 쓰기 — trade.customs_monthly
    def upsert_item_monthly(
        self, rows: Sequence[ItemMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def upsert_country_monthly(
        self, rows: Sequence[CountryMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def upsert_item_country_monthly(
        self, rows: Sequence[ItemCountryMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def upsert_sigungu_monthly(
        self, rows: Sequence[SigunguMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def upsert_hs_dim(self, rows: Sequence[HsDim], *, loaded_by: str, received_at: datetime) -> int:
        """HS 차원 — first_ym 은 기존 값과 더 이른 쪽, last_ym 은 더 늦은 쪽을 남긴다."""
        ...

    # 쓰기 — trade.derive
    def upsert_surge(
        self, rows: Sequence[SurgeRow], *, loaded_by: str, computed_at: datetime
    ) -> int: ...

    def replace_surge(
        self, rows: Sequence[SurgeRow], *, ym: str, loaded_by: str, computed_at: datetime
    ) -> int:
        """그 달의 급등·급락을 통째로 바꾼다(한 트랜잭션) — 다시 돌려 더 이상 급등이 아닌 옛 행이
        남지 않게(EXP 요청). 행의 ym 이 다르면 ValueError."""
        ...

    def upsert_item_group_monthly(
        self, rows: Sequence[ItemGroupMonthRow], *, loaded_by: str, computed_at: datetime
    ) -> int: ...

    # 쓰기 — trade.map_load
    def replace_stock_map(
        self, rows: Sequence[StockMapRow], *, origin: str, loaded_by: str, received_at: datetime
    ) -> int:
        """그 origin 의 매핑표를 통째로 바꾼다(한 트랜잭션). 행의 origin 이 다르면 ValueError."""
        ...

    # 쓰기 — trade.stock_index
    def upsert_stock_export(
        self, rows: Sequence[StockExportRow], *, loaded_by: str, computed_at: datetime
    ) -> int: ...

    def replace_stock_export(
        self, rows: Sequence[StockExportRow], *, loaded_by: str, computed_at: datetime
    ) -> int:
        """종목 수출 지수 표를 통째로 바꾼다(한 트랜잭션) — 매핑표에서 빠진 종목의 옛 행이 남지
        않게(MAP 요청)."""
        ...

    # 읽기
    def ten_day(
        self, kind: str | None = None, ym_from: str | None = None, ym_to: str | None = None
    ) -> list[TenDayRow]: ...

    def ten_day_columns(self, kind: str | None = None) -> list[TenDayColumn]: ...

    def item_monthly(
        self,
        prefix: str | None = None,
        hs_len: int | None = None,
        ym_from: str | None = None,
        ym_to: str | None = None,
    ) -> list[ItemMonthRow]:
        """prefix = HS 접두(그 아래 모든 자릿수), hs_len = 그 자릿수 행만."""
        ...

    def country_monthly(
        self, country: str | None = None, ym_from: str | None = None, ym_to: str | None = None
    ) -> list[CountryMonthRow]: ...

    def item_country_monthly(
        self,
        prefix: str | None = None,
        country: str | None = None,
        ym_from: str | None = None,
        ym_to: str | None = None,
    ) -> list[ItemCountryMonthRow]: ...

    def sigungu_monthly(
        self,
        hs6: str | None = None,
        sido_cd: str | None = None,
        region_id: str | None = None,
        ym_from: str | None = None,
        ym_to: str | None = None,
    ) -> list[SigunguMonthRow]: ...

    def hs_dim(self, prefix: str | None = None, hs_len: int | None = None) -> list[HsDim]: ...

    def surge(
        self, ym: str | None = None, level: str | None = None, flow: str | None = None
    ) -> list[SurgeRow]: ...

    def item_group_monthly(
        self, group_id: str | None = None, ym_from: str | None = None, ym_to: str | None = None
    ) -> list[ItemGroupMonthRow]: ...

    def stock_map(self, code: str | None = None, origin: str | None = None) -> list[StockMapRow]:
        """origin 을 주지 않으면 public·local 모두(공개 내보내기는 이 저장소를 쓰지 않는다)."""
        ...

    def stock_export(
        self, code: str | None = None, period_from: str | None = None, period_to: str | None = None
    ) -> list[StockExportRow]:
        """period 는 'YYYY-MM'·'YYYYQn' 이 섞여 있다 — 범위는 문자열 비교(같은 꼴끼리 시간순)."""
        ...

    def last_ym(self, table: str) -> str | None:
        """그 표(`pub_trade.<표>` — ym 칸이 있는 표)의 가장 최근 기준 월."""
        ...


def _check(loaded_by: str, at: datetime, what: str) -> None:
    require_loaded_by(loaded_by)
    require_aware(what, at)


def _unique[R](table: Table[R], rows: Sequence[R]) -> None:
    keys = [key_of(table, r) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{table.name}: 같은 키가 두 번 있다")


_YM_TABLES: Final[dict[str, Table[Any]]] = {
    t.name: t
    for t in (T_TEN_DAY, T_ITEM, T_COUNTRY, T_ITEM_COUNTRY, T_SIGUNGU, T_SURGE, T_ITEM_GROUP)
}


def _ym_table(table: str) -> Table[Any]:
    t = _YM_TABLES.get(table)
    if t is None:
        raise ValueError(f"ym 칸이 있는 pub_trade 표가 아니다: {table!r} — {sorted(_YM_TABLES)}")
    return t


def _check_origin(rows: Sequence[StockMapRow], origin: str) -> None:
    if origin not in MAP_ORIGINS:
        raise ValueError(f"origin 은 {MAP_ORIGINS} 중 하나: {origin!r}")
    bad = [r.code for r in rows if r.origin != origin]
    if bad:
        raise ValueError(f"replace_stock_map(origin={origin}) 에 다른 origin 행: {bad[:3]}")


class _Base:
    """쓰기·읽기 공통 진입 — 하위가 `_write`·`_read`·`_replace_map`·`_max_ym` 을 채운다."""

    def _write(self, table: Table[Any], rows: Sequence[Any], extra: dict[str, object]) -> int:
        raise NotImplementedError

    def _read(self, table: Table[Any], conds: list[_Cond]) -> list[Any]:
        raise NotImplementedError

    def upsert_ten_day(
        self, rows: Sequence[TenDayRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        return self._write(T_TEN_DAY, rows, {"received_at": received_at, "loaded_by": loaded_by})

    def upsert_ten_day_columns(self, rows: Sequence[TenDayColumn], *, loaded_by: str) -> int:
        require_loaded_by(loaded_by)
        return self._write(T_TEN_DAY_COLUMNS, rows, {"loaded_by": loaded_by})

    def upsert_item_monthly(
        self, rows: Sequence[ItemMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        return self._write(T_ITEM, rows, {"received_at": received_at, "loaded_by": loaded_by})

    def upsert_country_monthly(
        self, rows: Sequence[CountryMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        return self._write(T_COUNTRY, rows, {"received_at": received_at, "loaded_by": loaded_by})

    def upsert_item_country_monthly(
        self, rows: Sequence[ItemCountryMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        extra: dict[str, object] = {"received_at": received_at, "loaded_by": loaded_by}
        return self._write(T_ITEM_COUNTRY, rows, extra)

    def upsert_sigungu_monthly(
        self, rows: Sequence[SigunguMonthRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        return self._write(T_SIGUNGU, rows, {"received_at": received_at, "loaded_by": loaded_by})

    def upsert_hs_dim(self, rows: Sequence[HsDim], *, loaded_by: str, received_at: datetime) -> int:
        _check(loaded_by, received_at, "received_at")
        _unique(T_HS_DIM, rows)
        old = {
            r.hs: r for r in self._read(T_HS_DIM, _conds(hs=("hs", "in", [r.hs for r in rows])))
        } if rows else {}  # fmt: skip
        merged = [
            replace(r, first_ym=min(r.first_ym, o.first_ym), last_ym=max(r.last_ym, o.last_ym))
            if (o := old.get(r.hs)) is not None
            else r
            for r in rows
        ]
        return self._write(T_HS_DIM, merged, {"received_at": received_at, "loaded_by": loaded_by})

    def upsert_surge(
        self, rows: Sequence[SurgeRow], *, loaded_by: str, computed_at: datetime
    ) -> int:
        _check(loaded_by, computed_at, "computed_at")
        return self._write(T_SURGE, rows, {"computed_at": computed_at, "loaded_by": loaded_by})

    def replace_surge(
        self, rows: Sequence[SurgeRow], *, ym: str, loaded_by: str, computed_at: datetime
    ) -> int:
        _check(loaded_by, computed_at, "computed_at")
        bad = [r.ym for r in rows if r.ym != ym]
        if bad:
            raise ValueError(f"replace_surge(ym={ym}) 에 다른 달 행: {bad[:3]}")
        _unique(T_SURGE, rows)
        extra: dict[str, object] = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._replace(T_SURGE, rows, extra, ("ym", ym))

    def replace_stock_export(
        self, rows: Sequence[StockExportRow], *, loaded_by: str, computed_at: datetime
    ) -> int:
        _check(loaded_by, computed_at, "computed_at")
        _unique(T_STOCK_EXPORT, rows)
        extra: dict[str, object] = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._replace(T_STOCK_EXPORT, rows, extra, None)

    def _replace(
        self,
        table: Table[Any],
        rows: Sequence[Any],
        extra: dict[str, object],
        where: tuple[str, object] | None,
    ) -> int:
        """`where`(열, 값) 에 맞는 행(None 이면 전부)을 지우고 넣는다 — 한 트랜잭션."""
        raise NotImplementedError

    def upsert_item_group_monthly(
        self, rows: Sequence[ItemGroupMonthRow], *, loaded_by: str, computed_at: datetime
    ) -> int:
        _check(loaded_by, computed_at, "computed_at")
        extra: dict[str, object] = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._write(T_ITEM_GROUP, rows, extra)

    def upsert_stock_export(
        self, rows: Sequence[StockExportRow], *, loaded_by: str, computed_at: datetime
    ) -> int:
        _check(loaded_by, computed_at, "computed_at")
        extra: dict[str, object] = {"computed_at": computed_at, "loaded_by": loaded_by}
        return self._write(T_STOCK_EXPORT, rows, extra)

    def ten_day(
        self, kind: str | None = None, ym_from: str | None = None, ym_to: str | None = None
    ) -> list[TenDayRow]:
        return self._read(T_TEN_DAY, _conds(kind=("kind", "eq", kind), **_ym_range(ym_from, ym_to)))

    def ten_day_columns(self, kind: str | None = None) -> list[TenDayColumn]:
        return self._read(T_TEN_DAY_COLUMNS, _conds(kind=("kind", "eq", kind)))

    def item_monthly(
        self,
        prefix: str | None = None,
        hs_len: int | None = None,
        ym_from: str | None = None,
        ym_to: str | None = None,
    ) -> list[ItemMonthRow]:
        conds = _conds(
            prefix=("hs", "prefix", prefix), hs_len=("hs_len", "eq", hs_len),
            **_ym_range(ym_from, ym_to),
        )  # fmt: skip
        return self._read(T_ITEM, conds)

    def country_monthly(
        self, country: str | None = None, ym_from: str | None = None, ym_to: str | None = None
    ) -> list[CountryMonthRow]:
        conds = _conds(country=("country", "eq", country), **_ym_range(ym_from, ym_to))
        return self._read(T_COUNTRY, conds)

    def item_country_monthly(
        self,
        prefix: str | None = None,
        country: str | None = None,
        ym_from: str | None = None,
        ym_to: str | None = None,
    ) -> list[ItemCountryMonthRow]:
        conds = _conds(
            prefix=("hs", "prefix", prefix), country=("country", "eq", country),
            **_ym_range(ym_from, ym_to),
        )  # fmt: skip
        return self._read(T_ITEM_COUNTRY, conds)

    def sigungu_monthly(
        self,
        hs6: str | None = None,
        sido_cd: str | None = None,
        region_id: str | None = None,
        ym_from: str | None = None,
        ym_to: str | None = None,
    ) -> list[SigunguMonthRow]:
        conds = _conds(
            hs6=("hs6", "eq", hs6), sido=("sido_cd", "eq", sido_cd),
            region=("region_id", "eq", region_id), **_ym_range(ym_from, ym_to),
        )  # fmt: skip
        return self._read(T_SIGUNGU, conds)

    def hs_dim(self, prefix: str | None = None, hs_len: int | None = None) -> list[HsDim]:
        conds = _conds(prefix=("hs", "prefix", prefix), hs_len=("hs_len", "eq", hs_len))
        return self._read(T_HS_DIM, conds)

    def surge(
        self, ym: str | None = None, level: str | None = None, flow: str | None = None
    ) -> list[SurgeRow]:
        conds = _conds(ym=("ym", "eq", ym), level=("level", "eq", level), flow=("flow", "eq", flow))
        return self._read(T_SURGE, conds)

    def item_group_monthly(
        self, group_id: str | None = None, ym_from: str | None = None, ym_to: str | None = None
    ) -> list[ItemGroupMonthRow]:
        conds = _conds(group=("group_id", "eq", group_id), **_ym_range(ym_from, ym_to))
        return self._read(T_ITEM_GROUP, conds)

    def stock_map(self, code: str | None = None, origin: str | None = None) -> list[StockMapRow]:
        if origin is not None and origin not in MAP_ORIGINS:
            raise ValueError(f"origin 은 {MAP_ORIGINS} 중 하나: {origin!r}")
        return self._read(
            T_STOCK_MAP, _conds(code=("code", "eq", code), o=("origin", "eq", origin))
        )

    def stock_export(
        self, code: str | None = None, period_from: str | None = None, period_to: str | None = None
    ) -> list[StockExportRow]:
        conds = _conds(
            code=("code", "eq", code), pf=("period", "ge", period_from),
            pt=("period", "le", period_to),
        )  # fmt: skip
        return self._read(T_STOCK_EXPORT, conds)


def _order(table: Table[Any]) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(c) for c in table.key)


class PgTradeRepo(_Base):
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgTradeRepo()"

    def _write(self, table: Table[Any], rows: Sequence[Any], extra: dict[str, object]) -> int:
        _unique(table, rows)
        if not rows:
            return 0
        with self._connect() as conn, conn.transaction():
            return upsert(conn, table, rows, extra, update_where=_update_where(table))

    def _read(self, table: Table[Any], conds: list[_Cond]) -> list[Any]:
        where, params = _pg_where(table, conds)
        q = sql.SQL("SELECT {} FROM {} WHERE {} ORDER BY {}").format(
            table.select_sql(), table.ident(), where, _order(table)
        )
        with self._connect() as conn:
            recs = conn.execute(q, tuple(params)).fetchall()
        return [table.from_db(r) for r in recs]

    def _replace(
        self,
        table: Table[Any],
        rows: Sequence[Any],
        extra: dict[str, object],
        where: tuple[str, object] | None,
    ) -> int:
        with self._connect() as conn, conn.transaction():
            if where is None:
                conn.execute(sql.SQL("DELETE FROM {}").format(table.ident()))
            else:
                q = sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                    table.ident(), sql.Identifier(where[0])
                )
                conn.execute(q, (where[1],))
            return upsert(conn, table, rows, extra) if rows else 0

    def replace_stock_map(
        self, rows: Sequence[StockMapRow], *, origin: str, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        _check_origin(rows, origin)
        _unique(T_STOCK_MAP, rows)
        extra: dict[str, object] = {"received_at": received_at, "loaded_by": loaded_by}
        with self._connect() as conn, conn.transaction():
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE origin = %s").format(T_STOCK_MAP.ident()), (origin,)
            )
            return upsert(conn, T_STOCK_MAP, rows, extra)

    def last_ym(self, table: str) -> str | None:
        t = _ym_table(table)
        with self._connect() as conn:
            r = conn.execute(sql.SQL("SELECT max(ym) FROM {}").format(t.ident())).fetchone()
        return None if r is None else r[0]


class MemoryTradeRepo(_Base):
    """메모리판 — Pg 와 같은 키·덮어쓰기·처음 값 유지 규칙(시험이 표를 들여다볼 수 있게
    `tables`)."""

    def __init__(self) -> None:
        self.tables: dict[str, MemoryTable[Any]] = {
            t.name: MemoryTable(t)
            for t in (
                T_TEN_DAY,
                T_TEN_DAY_COLUMNS,
                T_ITEM,
                T_COUNTRY,
                T_ITEM_COUNTRY,
                T_SIGUNGU,
                T_HS_DIM,
                T_SURGE,
                T_ITEM_GROUP,
                T_STOCK_MAP,
                T_STOCK_EXPORT,
            )
        }

    def __repr__(self) -> str:
        return "MemoryTradeRepo()"

    def _write(self, table: Table[Any], rows: Sequence[Any], extra: dict[str, object]) -> int:
        _unique(table, rows)
        mem = self.tables[table.name]
        cols = _VALUE_COLS.get(table.name, ())
        back = {table.col(f): f for f in table.fields}
        n = 0
        for r in rows:
            old = mem.get(key_of(table, r))
            if (
                old is not None
                and cols
                and all(getattr(r, back[c]) is None for c in cols)
                and any(getattr(old, back[c]) is not None for c in cols)
            ):
                n += 1  # Pg ON CONFLICT … WHERE 거짓도 센다(upsert 의 행 수 규칙)
                continue
            n += mem.upsert([r], extra)
        return n

    def _read(self, table: Table[Any], conds: list[_Cond]) -> list[Any]:
        return self.tables[table.name].where(_mem_pred(table, conds))

    def _replace(
        self,
        table: Table[Any],
        rows: Sequence[Any],
        extra: dict[str, object],
        where: tuple[str, object] | None,
    ) -> int:
        mem = self.tables[table.name]
        if where is None:
            mem.remove(lambda r: True)
        else:
            attr = {table.col(f): f for f in table.fields}[where[0]]
            mem.remove(lambda r: getattr(r, attr) == where[1])
        return mem.upsert(rows, extra)

    def replace_stock_map(
        self, rows: Sequence[StockMapRow], *, origin: str, loaded_by: str, received_at: datetime
    ) -> int:
        _check(loaded_by, received_at, "received_at")
        _check_origin(rows, origin)
        _unique(T_STOCK_MAP, rows)
        mem = self.tables[T_STOCK_MAP.name]
        mem.remove(lambda r: r.origin == origin)
        return mem.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})

    def last_ym(self, table: str) -> str | None:
        t = _ym_table(table)
        yms = [r.ym for r in self.tables[t.name].all()]
        return max(yms) if yms else None
