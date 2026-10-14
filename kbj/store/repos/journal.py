"""관심종목·저널 읽기 저장소 — 0013 `prv_journal.watchlist` + 0021 `analysis`·`trade`·`pick` 읽기
(개인 데이터 — 로그인).

근거: docs/p4_design.md §1.1·§3.4·§3.8, D-P4-13, docs/p8_design.md §3.3(P8 — 일지·매매·추천 읽기).
일지·매매·추천 **쓰기**는 `kbj.store.repos.personal`(쓰기 모듈 하나 — D-P8-15).

- 읽기: API(`/api/watchlist`·관심종목 칩)·알림 작업(관심종목 잠정실적·리비전)·상세 수집
  대상(D-P4-5).
- 쓰기: 가져오기 CLI(`python -m kbj.services.watchlist import`)와 legacy 이관만. 쓰기 API 는 P8.
  `replace_watchlist` 는 그 목록을 **통째로 바꾼다**(한 트랜잭션 — 사용자가 올린 파일이 정본).
- 공개 작업·공개 내보내기는 이 모듈을 import 하지 않는다(계약 ⑬·⑧ — 공개판은 관심종목을 모른다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Protocol

from psycopg.types.json import Jsonb

from kbj.core.fin_rows import WatchItem
from kbj.core.rules_rows import AnalysisRow, PickRow, TradeRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = [
    "ANALYSIS_COLS",
    "PICK",
    "TRADE_COLS",
    "JournalRepo",
    "MemoryJournalRepo",
    "PgJournalRepo",
    "analysis_from_db",
    "analysis_to_db",
    "trade_from_db",
]

PICK: Final = Table(
    "prv_journal.pick", PickRow, key=("date", "source", "code"), extra=("loaded_by",),
    json_cols=frozenset({"tags"}),
)  # fmt: skip

ANALYSIS_COLS: Final = (
    "code", "analyzed_on", "chain_id_1", "chain_id_2", "chain_id_3", "price", "ret_52w",
    "fwd_per", "band_pos", "ev_ebitda", "mktcap", "opm", "eps", "per", "tp", "tp_source",
    "conviction", "memo", "tags", "conclusion", "value_sources", "source", "updated_at",
)  # fmt: skip
TRADE_COLS: Final = (
    "code", "name_at_trade", "side", "trade_date", "price", "qty", "fee", "tax", "strategy",
    "memo", "signal_id", "source", "created_at",
)  # fmt: skip


def _dec_map(m: Mapping[str, Decimal | None]) -> dict[str, str | None]:
    return {k: None if v is None else str(v) for k, v in m.items()}


def _undec_map(m: object) -> dict[str, Decimal | None]:
    if not isinstance(m, Mapping):
        return {}
    out: dict[str, Decimal | None] = {}
    for k, v in m.items():  # pyright: ignore[reportUnknownVariableType]
        out[str(k)] = None if v is None else Decimal(str(v))  # pyright: ignore[reportUnknownArgumentType]
    return out


def analysis_to_db(row: AnalysisRow) -> tuple[object, ...]:
    """ANALYSIS_COLS 순서의 값."""
    c1, c2, c3 = row.chain_ids
    return (
        row.code, row.analyzed_on, c1, c2, c3, row.price, row.ret_52w, row.fwd_per, row.band_pos,
        row.ev_ebitda, row.mktcap, row.opm, Jsonb(_dec_map(row.eps)), Jsonb(dict(row.per)),
        Jsonb(_dec_map(row.tp)), row.tp_source, row.conviction, row.memo, list(row.tags),
        row.conclusion, Jsonb(dict(row.value_sources)), row.source, row.updated_at,
    )  # fmt: skip


def analysis_from_db(rec: Sequence[Any]) -> AnalysisRow:
    """(id, *ANALYSIS_COLS) 한 줄 → 행."""
    (i, code, on, c1, c2, c3, price, r52, fper, band, eveb, mcap, opm, eps, per, tp, tps, conv,
     memo, tags, concl, vs, src, upd) = rec  # fmt: skip

    def f(x: object) -> float | None:
        return None if x is None else float(x)  # pyright: ignore[reportArgumentType]

    return AnalysisRow(
        id=int(i), code=code, analyzed_on=on, chain_ids=(c1, c2, c3),
        price=None if price is None else Decimal(price), ret_52w=f(r52), fwd_per=f(fper),
        band_pos=f(band), ev_ebitda=f(eveb), mktcap=None if mcap is None else Decimal(mcap),
        opm=f(opm), eps=_undec_map(eps), per={k: f(v) for k, v in dict(per or {}).items()},
        tp=_undec_map(tp), tp_source=tps, conviction=conv, memo=memo, tags=tuple(tags or ()),
        conclusion=concl, value_sources=dict(vs or {}), source=src, updated_at=upd,
    )  # fmt: skip


def trade_from_db(rec: Sequence[Any]) -> TradeRow:
    """(id, *TRADE_COLS) 한 줄 → 행."""
    i, code, name, side, d, price, qty, fee, tax, strat, memo, sid, src, created = rec
    return TradeRow(
        id=int(i), code=code, name_at_trade=name, side=side, trade_date=d, price=Decimal(price),
        qty=int(qty), fee=Decimal(fee), tax=Decimal(tax), strategy=strat, memo=memo,
        signal_id=sid, source=src, created_at=created,
    )  # fmt: skip


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

    # ── P8 저널 읽기(0021) ──
    def analyses(self, *, code: str | None = None, limit: int = 200) -> list[AnalysisRow]:
        """분석일 최신 먼저(같은 날은 id 큰 것 먼저)."""
        ...

    def analysis(self, analysis_id: int) -> AnalysisRow | None: ...

    def trades(self, *, code: str | None = None) -> list[TradeRow]:
        """(매매일, id) 오름차순 — 평균 단가 계산 순서."""
        ...

    def picks(self, start: date | None = None, end: date | None = None) -> list[PickRow]:
        """(날짜, 출처, 순위, 종목) 순."""
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

    def analyses(self, *, code: str | None = None, limit: int = 200) -> list[AnalysisRow]:
        where, params = ("code = %s", (code,)) if code else ("TRUE", ())
        with self._connect() as conn:
            recs = conn.execute(
                f"SELECT {_A_COLS} FROM prv_journal.analysis WHERE {where} "  # noqa: S608 — 상수
                "ORDER BY analyzed_on DESC, id DESC LIMIT %s",
                (*params, int(limit)),
            ).fetchall()
        return [analysis_from_db(r) for r in recs]

    def analysis(self, analysis_id: int) -> AnalysisRow | None:
        with self._connect() as conn:
            rec = conn.execute(
                f"SELECT {_A_COLS} FROM prv_journal.analysis WHERE id = %s",  # noqa: S608 — 상수
                (analysis_id,),
            ).fetchone()
        return None if rec is None else analysis_from_db(rec)

    def trades(self, *, code: str | None = None) -> list[TradeRow]:
        where, params = ("code = %s", (code,)) if code else ("TRUE", ())
        with self._connect() as conn:
            recs = conn.execute(
                f"SELECT {_T_COLS} FROM prv_journal.trade WHERE {where} "  # noqa: S608 — 상수
                "ORDER BY trade_date, id",
                params,
            ).fetchall()
        return [trade_from_db(r) for r in recs]

    def picks(self, start: date | None = None, end: date | None = None) -> list[PickRow]:
        where = "date >= %s AND date <= %s"
        params = (start or date.min, end or date.max)
        with self._connect() as conn:
            return select(conn, PICK, where, params, "date, source, rank, code")


_A_COLS: Final = ", ".join(("id", *ANALYSIS_COLS))
_T_COLS: Final = ", ".join(("id", *TRADE_COLS))


class MemoryJournalRepo:
    def __init__(self) -> None:
        self.watch_t = MemoryTable(WATCHLIST)
        # P8 — MemoryPersonalRepo 가 같은 객체에 쓴다(memory_repos)
        self.analyses_d: dict[int, AnalysisRow] = {}
        self.trades_d: dict[int, TradeRow] = {}
        self.pick_t = MemoryTable(PICK)

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

    def analyses(self, *, code: str | None = None, limit: int = 200) -> list[AnalysisRow]:
        rows = [a for a in self.analyses_d.values() if code is None or a.code == code]
        rows.sort(key=lambda a: (a.analyzed_on, a.id or 0), reverse=True)
        return rows[:limit]

    def analysis(self, analysis_id: int) -> AnalysisRow | None:
        return self.analyses_d.get(analysis_id)

    def trades(self, *, code: str | None = None) -> list[TradeRow]:
        rows = [t for t in self.trades_d.values() if code is None or t.code == code]
        return sorted(rows, key=lambda t: (t.trade_date, t.id or 0))

    def picks(self, start: date | None = None, end: date | None = None) -> list[PickRow]:
        lo, hi = start or date.min, end or date.max
        rows = self.pick_t.where(lambda p: lo <= p.date <= hi)
        return sorted(rows, key=lambda p: (p.date, p.source, p.rank or 0, p.code))
