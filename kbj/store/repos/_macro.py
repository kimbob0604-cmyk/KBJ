"""매크로 저장소 공용 — 공개(`pub_macro`)·로그인(`prv_macro`) 두 스키마가 같은 모양이다(0014).

근거: docs/p5_design.md §1.1·§3.3, D-P5-6·8, metrics §16.

공개 작업과 로그인 작업이 서로를 모르게(계약 ⑭ — WRAP) 저장소 **모듈**을 나눈다:
`kbj.store.repos.macro`(공개 — `MacroPublicRepo`)·`kbj.store.repos.macro_prv`(로그인 —
`MacroPrivateRepo`). 이 모듈은 스키마 이름만 받는 공용 구현이라 어느 등급도 모른다.

규칙
- 시리즈 값 덮어쓰기(개정): 같은 (series_id, period) 에 다른 값이 오면 덮고 **덮기 전 값을
  prev_value 에** 남긴다(metrics §16 [확인 필요]). 값이 None 인 새 행은 이미 있는 값을 지우지
  않는다(0 으로도
  None 으로도 덮지 않는다 — 절대 규칙 2).
- 기간 문자열(YYYY·YYYY-MM·YYYY-Qn·YYYY-MM-DD)은 같은 주기끼리 사전순 = 시간순이다.
- 이 모듈은 `kbj.core`·psycopg 만 쓴다(계약 ⑦).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Protocol

from psycopg import sql

from kbj.core.p5_rows import CalendarEvent, MacroSeriesMeta, MacroSeriesRow
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, select, upsert

__all__ = ["MacroRepo", "MemoryMacroBase", "PgMacroBase", "macro_tables"]


class MacroRepo(Protocol):
    """공개·로그인 매크로 저장소의 같은 모양(각 모듈의 Protocol 이 이 이름을 다시 쓴다)."""

    def upsert_meta(self, rows: Sequence[MacroSeriesMeta], *, loaded_by: str, now: datetime) -> int:
        """시리즈 메타를 넣거나 덮는다. 다른 등급의 시리즈가 섞이면 ValueError."""
        ...

    def meta(self, tab: str | None = None) -> list[MacroSeriesMeta]:
        """(tab, series_id) 순."""
        ...

    def upsert_series(
        self, rows: Sequence[MacroSeriesRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """관측값을 넣는다(개정이면 prev_value 에 덮기 전 값). 쓴(덮은) 행 수 — None 이라 건너뛴 행
        제외."""
        ...

    def series(
        self, series_ids: Collection[str], start: str | None = None, end: str | None = None
    ) -> dict[str, list[MacroSeriesRow]]:
        """{series_id: 기간 오름차순}. start·end 는 기간 문자열(포함)."""
        ...

    def latest(self, series_ids: Collection[str]) -> dict[str, MacroSeriesRow]:
        """시리즈마다 값이 있는 가장 최근 기간 한 줄(값 없는 행은 건너뛴다)."""
        ...

    def released_between(self, start: datetime, end: datetime) -> list[MacroSeriesRow]:
        """공표 시각(released_at)이 [start, end) 인 행(브리핑 ③ — 전일 이후 새 공표)."""
        ...

    def upsert_calendar(
        self, rows: Sequence[CalendarEvent], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def calendar(self, start: date, end: date) -> list[CalendarEvent]:
        """[start, end] 날짜(포함)의 일정 — (날짜, 시각, event_id) 순."""
        ...


def macro_tables(
    schema: str,
) -> tuple[Table[MacroSeriesMeta], Table[MacroSeriesRow], Table[CalendarEvent]]:
    meta = Table(
        f"{schema}.series_meta", MacroSeriesMeta, key=("series_id",),
        extra=("updated_at", "loaded_by"),
    )  # fmt: skip
    series = Table(
        f"{schema}.series", MacroSeriesRow, key=("series_id", "period"),
        extra=("received_at", "loaded_by"),
    )  # fmt: skip
    cal = Table(
        f"{schema}.calendar", CalendarEvent, key=("event_id",),
        extra=("received_at", "loaded_by"), rename={"date": "event_date"},
        skip=frozenset() if schema.startswith("prv_") else frozenset({"forecast"}),
    )  # fmt: skip
    return meta, series, cal


def _check_series(rows: Sequence[MacroSeriesRow], loaded_by: str, received_at: datetime) -> None:
    require_loaded_by(loaded_by)
    require_aware("received_at", received_at)
    keys = [(r.series_id, r.period) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("같은 (series_id, period) 가 두 번 있다")


def _sorted_series(rows: list[MacroSeriesRow]) -> dict[str, list[MacroSeriesRow]]:
    out: dict[str, list[MacroSeriesRow]] = {}
    for r in sorted(rows, key=lambda r: (r.series_id, r.period)):
        out.setdefault(r.series_id, []).append(r)
    return out


def _cal_key(e: CalendarEvent) -> tuple[date, int, str]:
    t = e.time_kst
    return (e.date, -1 if t is None else t.hour * 3600 + t.minute * 60 + t.second, e.event_id)


class PgMacroBase:
    """`MacroRepo` 의 Postgres 구현(스키마 이름만 다르다). 등급 검사는 하위 클래스의 `_check_*`."""

    schema: str = ""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory
        self._meta_t, self._series_t, self._cal_t = macro_tables(self.schema)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    # 하위 클래스가 등급 규칙을 더한다
    def _check_meta(self, rows: Sequence[MacroSeriesMeta]) -> None: ...

    def _check_calendar(self, rows: Sequence[CalendarEvent]) -> None: ...

    def upsert_meta(self, rows: Sequence[MacroSeriesMeta], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        self._check_meta(rows)
        with self._connect() as conn:
            return upsert(conn, self._meta_t, rows, {"updated_at": now, "loaded_by": loaded_by})

    def meta(self, tab: str | None = None) -> list[MacroSeriesMeta]:
        with self._connect() as conn:
            return select(
                conn, self._meta_t, "(%s::text IS NULL OR tab = %s)", (tab, tab), "tab, series_id"
            )

    def upsert_series(
        self, rows: Sequence[MacroSeriesRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check_series(rows, loaded_by, received_at)
        if not rows:
            return 0
        t = self._series_t
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in t.columns)
        marks = sql.SQL(", ").join(sql.Placeholder() for _ in t.columns)
        q = sql.SQL(
            "INSERT INTO {} AS cur ({}) VALUES ({}) ON CONFLICT (series_id, period) DO UPDATE SET "
            "prev_value = CASE WHEN cur.value IS DISTINCT FROM (EXCLUDED.value) THEN cur.value "
            "ELSE cur.prev_value END, value = EXCLUDED.value, unit = EXCLUDED.unit, "
            "source = EXCLUDED.source, as_of = EXCLUDED.as_of, "
            "released_at = COALESCE(EXCLUDED.released_at, cur.released_at), "
            "quality = EXCLUDED.quality, received_at = EXCLUDED.received_at, "
            "loaded_by = EXCLUDED.loaded_by WHERE EXCLUDED.value IS NOT NULL OR cur.value IS NULL"
        ).format(t.ident(), cols, marks)
        extra = {"received_at": received_at, "loaded_by": loaded_by}
        n = 0
        with self._connect() as conn, conn.cursor() as cur:
            for r in rows:
                cur.execute(q, t.values(r, extra))
                n += cur.rowcount
        return n

    def series(
        self, series_ids: Collection[str], start: str | None = None, end: str | None = None
    ) -> dict[str, list[MacroSeriesRow]]:
        ids = sorted(set(series_ids))
        if not ids:
            return {}
        with self._connect() as conn:
            rows = select(
                conn, self._series_t,
                "series_id = ANY(%s::text[]) AND (%s::text IS NULL OR period >= %s) "
                "AND (%s::text IS NULL OR period <= %s)",
                (ids, start, start, end, end), "series_id, period",
            )  # fmt: skip
        return _sorted_series(rows)

    def latest(self, series_ids: Collection[str]) -> dict[str, MacroSeriesRow]:
        ids = sorted(set(series_ids))
        if not ids:
            return {}
        t = self._series_t
        q = sql.SQL(
            "SELECT DISTINCT ON (series_id) {} FROM {} WHERE series_id = ANY(%s::text[]) "
            "AND value IS NOT NULL ORDER BY series_id, period DESC"
        ).format(t.select_sql(), t.ident())
        with self._connect() as conn:
            got = [t.from_db(r) for r in conn.execute(q, (ids,)).fetchall()]
        return {r.series_id: r for r in got}

    def released_between(self, start: datetime, end: datetime) -> list[MacroSeriesRow]:
        require_aware("start", start)
        require_aware("end", end)
        with self._connect() as conn:
            return select(
                conn, self._series_t, "released_at >= %s AND released_at < %s", (start, end),
                "released_at, series_id, period",
            )  # fmt: skip

    def upsert_calendar(
        self, rows: Sequence[CalendarEvent], *, loaded_by: str, received_at: datetime
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        self._check_calendar(rows)
        with self._connect() as conn:
            return upsert(
                conn, self._cal_t, rows, {"received_at": received_at, "loaded_by": loaded_by}
            )

    def calendar(self, start: date, end: date) -> list[CalendarEvent]:
        with self._connect() as conn:
            got = select(conn, self._cal_t, "event_date BETWEEN %s AND %s", (start, end))
        return sorted(got, key=_cal_key)


class MemoryMacroBase:
    """메모리판 — Pg 와 같은 키·개정·None 규칙."""

    schema: str = ""

    def __init__(self) -> None:
        meta_t, series_t, cal_t = macro_tables(self.schema)
        self.meta_t = MemoryTable(meta_t)
        self.series_t = MemoryTable(series_t)
        self.cal_t = MemoryTable(cal_t)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def _check_meta(self, rows: Sequence[MacroSeriesMeta]) -> None: ...

    def _check_calendar(self, rows: Sequence[CalendarEvent]) -> None: ...

    def upsert_meta(self, rows: Sequence[MacroSeriesMeta], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        self._check_meta(rows)
        return self.meta_t.upsert(rows, {"updated_at": now, "loaded_by": loaded_by})

    def meta(self, tab: str | None = None) -> list[MacroSeriesMeta]:
        got = self.meta_t.where(lambda m: tab is None or m.tab == tab)
        return sorted(got, key=lambda m: (m.tab, m.series_id))

    def upsert_series(
        self, rows: Sequence[MacroSeriesRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        _check_series(rows, loaded_by, received_at)
        n = 0
        extra = {"received_at": received_at, "loaded_by": loaded_by}
        for r in rows:
            old = self.series_t.get((r.series_id, r.period))
            if old is not None:
                if r.value is None and old.value is not None:
                    continue  # 있는 값을 None 으로 지우지 않는다
                prev = old.value if old.value != r.value else old.prev_value
                r = replace(r, prev_value=prev, released_at=r.released_at or old.released_at)
            self.series_t.upsert([r], extra)
            n += 1
        return n

    def series(
        self, series_ids: Collection[str], start: str | None = None, end: str | None = None
    ) -> dict[str, list[MacroSeriesRow]]:
        ids = set(series_ids)
        got = self.series_t.where(
            lambda r: (
                r.series_id in ids
                and (start is None or r.period >= start)
                and (end is None or r.period <= end)
            )
        )
        return _sorted_series(got)

    def latest(self, series_ids: Collection[str]) -> dict[str, MacroSeriesRow]:
        out: dict[str, MacroSeriesRow] = {}
        for sid, rows in self.series(series_ids).items():
            vals = [r for r in rows if r.value is not None]
            if vals:
                out[sid] = vals[-1]
        return out

    def released_between(self, start: datetime, end: datetime) -> list[MacroSeriesRow]:
        require_aware("start", start)
        require_aware("end", end)
        got = self.series_t.where(
            lambda r: r.released_at is not None and start <= r.released_at < end
        )
        return sorted(got, key=lambda r: (r.released_at, r.series_id, r.period))

    def upsert_calendar(
        self, rows: Sequence[CalendarEvent], *, loaded_by: str, received_at: datetime
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        self._check_calendar(rows)
        return self.cal_t.upsert(rows, {"received_at": received_at, "loaded_by": loaded_by})

    def calendar(self, start: date, end: date) -> list[CalendarEvent]:
        return sorted(self.cal_t.where(lambda e: start <= e.date <= end), key=_cal_key)
