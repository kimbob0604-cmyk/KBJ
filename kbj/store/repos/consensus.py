"""컨센서스·밸류에이션 저장소 — 0012 `prv_fin.*`·`prv_market.share_class`(로그인).

근거: docs/p4_design.md §1.1·§3.4·§4.2·§4.7~§4.9.

로그인 작업(`consensus.snapshot` — P4 동안 꺼짐, `fin.valuation_band`,
`earnings.alerts`·`earnings.backfill`)과
API 가 쓴다. **공개 작업은 이 모듈을 import 하지 않는다**(계약 ⑬).

- 파생 표(목표가·리비전·밴드·시나리오)는 (종목, as_of) 단위로 통째로 바꾼다(같은 as_of 재실행 멱등).
  `target(code)`·`bands(code)`·`scenarios(code)`·`revisions(code)` 는 as_of 를 안 주면 가장 최근 것.
- 밸류 일별은 (종목, 거래일) 키 — 같은 날 다시 쓰면 덮는다.
- 잠정실적 분류(`earnings_surprise`)에 행이 없으면 '미처리'다(earnings.alerts 가 5분마다 본다 —
  D-P4-4).
  `perf_pending()` = 알림을 넣었고 성과 기록을 마치지 않은 행.
- 열 이름이 행 필드와 다른 곳(SQL 예약어·함수 이름 회피): RevisionRow.current → current_value,
  BandRow.min·max·current → min_value·max_value·current_value, ScenarioRow.case → scenario_case.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Final, Protocol

from kbj.core.fin_rows import (
    BandRow,
    ConsensusRow,
    OpinionRow,
    RevisionRow,
    ScenarioRow,
    ShareClass,
    SurpriseRow,
    TargetConsensus,
    ValuationDay,
)
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = ["ConsensusRepo", "MemoryConsensusRepo", "PgConsensusRepo"]

_RECEIVED: Final = ("received_at", "loaded_by")
_COMPUTED: Final = ("computed_at", "loaded_by")

SNAPSHOT: Final = Table(
    "prv_fin.consensus_snapshot",
    ConsensusRow,
    key=("code", "snap_date", "fiscal_ym", "source"),
    extra=_RECEIVED,
    json_cols=frozenset({"extra"}),
)
OPINION: Final = Table(
    "prv_fin.invest_opinion",
    OpinionRow,
    key=("code", "broker", "opinion_date", "source"),
    extra=_RECEIVED,
)
TARGET: Final = Table(
    "prv_fin.target_consensus", TargetConsensus, key=("code", "as_of"), extra=_COMPUTED
)
REVISION: Final = Table(
    "prv_fin.revision",
    RevisionRow,
    key=("code", "as_of", "metric", "fiscal_ym", "window_days"),
    extra=_COMPUTED,
    rename={"current": "current_value"},
)
VALUATION: Final = Table(
    "prv_fin.valuation_daily",
    ValuationDay,
    key=("code", "trade_date"),
    extra=_COMPUTED,
    json_cols=frozenset({"flags"}),
)
BAND: Final = Table(
    "prv_fin.valuation_band",
    BandRow,
    key=("code", "as_of", "metric"),
    extra=_COMPUTED,
    rename={"min": "min_value", "max": "max_value", "current": "current_value"},
)
SCENARIO: Final = Table(
    "prv_fin.scenario",
    ScenarioRow,
    key=("code", "as_of", "scenario_case"),
    extra=_COMPUTED,
    rename={"case": "scenario_case"},
)
SURPRISE: Final = Table(
    "prv_fin.earnings_surprise",
    SurpriseRow,
    key=("rcept_no",),
    extra=_COMPUTED,
    json_cols=frozenset({"notes"}),
)
SHARE_CLASS: Final = Table(
    "prv_market.share_class", ShareClass, key=("code",), extra=("updated_at", "loaded_by")
)


class ConsensusRepo(Protocol):
    # 추정실적·투자의견
    def put_snapshot(
        self, rows: Sequence[ConsensusRow], *, loaded_by: str, now: datetime
    ) -> int: ...

    def snapshots(self, code: str, start: date, end: date) -> list[ConsensusRow]:
        """snap_date ∈ [start, end] — (snap_date, fiscal_ym) 오름차순."""
        ...

    def put_opinions(self, rows: Sequence[OpinionRow], *, loaded_by: str, now: datetime) -> int: ...

    def opinions(self, code: str, start: date, end: date) -> list[OpinionRow]:
        """opinion_date ∈ [start, end] — (날짜, 증권사) 오름차순."""
        ...

    # 파생(목표가·리비전)
    def put_targets(
        self, rows: Sequence[TargetConsensus], *, loaded_by: str, now: datetime
    ) -> int: ...

    def target(self, code: str, as_of: date | None = None) -> TargetConsensus | None: ...

    def put_revisions(
        self, code: str, as_of: date, rows: Sequence[RevisionRow], *, loaded_by: str, now: datetime
    ) -> int:
        """(종목, as_of) 의 리비전을 통째로 바꾼다."""
        ...

    def revisions(self, code: str, as_of: date | None = None) -> list[RevisionRow]: ...

    def revisions_on(self, as_of: date) -> list[RevisionRow]:
        """그날 모든 종목(관심종목 리비전 알림 묶음)."""
        ...

    # 밸류에이션
    def put_valuation_days(
        self, rows: Sequence[ValuationDay], *, loaded_by: str, now: datetime
    ) -> int: ...

    def valuation_days(self, code: str, start: date, end: date) -> list[ValuationDay]:
        """trade_date ∈ [start, end] 오름차순."""
        ...

    def last_valuation_day(self, code: str) -> date | None: ...

    def put_bands(
        self, code: str, as_of: date, rows: Sequence[BandRow], *, loaded_by: str, now: datetime
    ) -> int:
        """(종목, as_of) 의 밴드를 통째로 바꾼다."""
        ...

    def bands(self, code: str, as_of: date | None = None) -> list[BandRow]:
        """as_of 를 안 주면 가장 최근 — metric 순."""
        ...

    def put_scenarios(
        self, code: str, as_of: date, rows: Sequence[ScenarioRow], *, loaded_by: str, now: datetime
    ) -> int: ...

    def scenarios(self, code: str, as_of: date | None = None) -> list[ScenarioRow]:
        """as_of 를 안 주면 가장 최근 — bear·base·bull 순."""
        ...

    # 잠정실적 분류·알림
    def put_surprises(
        self, rows: Sequence[SurpriseRow], *, loaded_by: str, now: datetime
    ) -> int: ...

    def surprise(self, rcept_no: str) -> SurpriseRow | None: ...

    def surprises_known(self, rcept_nos: Iterable[str]) -> set[str]:
        """이미 분류한 접수번호(나머지가 '미처리')."""
        ...

    def surprises(
        self, *, code: str | None = None, corp_code: str | None = None
    ) -> list[SurpriseRow]: ...

    def perf_pending(self) -> list[SurpriseRow]:
        """알림을 넣었고(alerted_at) 성과 기록을 마치지 않은 행 — 알림 시각 순."""
        ...

    # 종류주식
    def put_share_classes(
        self, rows: Sequence[ShareClass], *, loaded_by: str, now: datetime
    ) -> int: ...

    def share_classes(self, codes: Collection[str] | None = None) -> dict[str, ShareClass]: ...


def _base(loaded_by: str, now: datetime) -> dict[str, object]:
    require_loaded_by(loaded_by)
    require_aware("now", now)
    return {"received_at": now, "computed_at": now, "updated_at": now, "loaded_by": loaded_by}


def _ext(table: Table[Any], base: dict[str, object]) -> dict[str, object]:
    return {c: base[c] for c in table.extra}


def _same(code: str, as_of: date, rows: Iterable[Any]) -> None:
    for r in rows:
        if r.code != code or r.as_of != as_of:
            raise ValueError(f"다른 종목·날짜의 행이 섞였다: {r.code} {r.as_of}")


_CASE_ORDER: Final = {"bear": 0, "base": 1, "bull": 2}


class PgConsensusRepo:
    """`ConsensusRepo` 의 Postgres 구현."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgConsensusRepo()"

    def _put(self, table: Table[Any], rows: Sequence[Any], loaded_by: str, now: datetime) -> int:
        base = _base(loaded_by, now)
        if not rows:
            return 0
        with self._connect() as conn:
            return upsert(conn, table, rows, _ext(table, base))

    def _replace(
        self,
        table: Table[Any],
        code: str,
        as_of: date,
        rows: Sequence[Any],
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _base(loaded_by, now)
        _same(code, as_of, rows)
        with self._connect() as conn, conn.transaction():
            delete(conn, table, "code = %s AND as_of = %s", (code, as_of))
            return upsert(conn, table, rows, _ext(table, base))

    def _get(
        self,
        table: Table[Any],
        where: str,
        params: Sequence[object],
        order: str = "",
        limit: int | None = None,
    ) -> list[Any]:
        with self._connect() as conn:
            return select(conn, table, where, params, order, limit)

    def _latest(self, table: Table[Any], code: str) -> date | None:
        with self._connect() as conn:
            r = conn.execute(
                f"SELECT max(as_of) FROM {table.name} WHERE code = %s".encode(),  # noqa: S608
                (code,),
            ).fetchone()
        return None if r is None else r[0]

    def put_snapshot(self, rows: Sequence[ConsensusRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(SNAPSHOT, rows, loaded_by, now)

    def snapshots(self, code: str, start: date, end: date) -> list[ConsensusRow]:
        return self._get(
            SNAPSHOT,
            "code = %s AND snap_date BETWEEN %s AND %s",
            (code, start, end),
            "snap_date, fiscal_ym, source",
        )

    def put_opinions(self, rows: Sequence[OpinionRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(OPINION, rows, loaded_by, now)

    def opinions(self, code: str, start: date, end: date) -> list[OpinionRow]:
        return self._get(
            OPINION,
            "code = %s AND opinion_date BETWEEN %s AND %s",
            (code, start, end),
            "opinion_date, broker, source",
        )

    def put_targets(self, rows: Sequence[TargetConsensus], *, loaded_by: str, now: datetime) -> int:
        return self._put(TARGET, rows, loaded_by, now)

    def target(self, code: str, as_of: date | None = None) -> TargetConsensus | None:
        day = as_of or self._latest(TARGET, code)
        if day is None:
            return None
        got = self._get(TARGET, "code = %s AND as_of = %s", (code, day))
        return got[0] if got else None

    def put_revisions(
        self, code: str, as_of: date, rows: Sequence[RevisionRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._replace(REVISION, code, as_of, rows, loaded_by, now)

    def revisions(self, code: str, as_of: date | None = None) -> list[RevisionRow]:
        day = as_of or self._latest(REVISION, code)
        if day is None:
            return []
        return self._get(
            REVISION,
            "code = %s AND as_of = %s",
            (code, day),
            "metric, fiscal_ym, window_days",
        )

    def revisions_on(self, as_of: date) -> list[RevisionRow]:
        return self._get(REVISION, "as_of = %s", (as_of,), "code, metric, fiscal_ym, window_days")

    def put_valuation_days(
        self, rows: Sequence[ValuationDay], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(VALUATION, rows, loaded_by, now)

    def valuation_days(self, code: str, start: date, end: date) -> list[ValuationDay]:
        return self._get(
            VALUATION,
            "code = %s AND trade_date BETWEEN %s AND %s",
            (code, start, end),
            "trade_date",
        )

    def last_valuation_day(self, code: str) -> date | None:
        with self._connect() as conn:
            r = conn.execute(
                b"SELECT max(trade_date) FROM prv_fin.valuation_daily WHERE code = %s", (code,)
            ).fetchone()
        return None if r is None else r[0]

    def put_bands(
        self, code: str, as_of: date, rows: Sequence[BandRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._replace(BAND, code, as_of, rows, loaded_by, now)

    def bands(self, code: str, as_of: date | None = None) -> list[BandRow]:
        day = as_of or self._latest(BAND, code)
        if day is None:
            return []
        return self._get(BAND, "code = %s AND as_of = %s", (code, day), "metric")

    def put_scenarios(
        self, code: str, as_of: date, rows: Sequence[ScenarioRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._replace(SCENARIO, code, as_of, rows, loaded_by, now)

    def scenarios(self, code: str, as_of: date | None = None) -> list[ScenarioRow]:
        day = as_of or self._latest(SCENARIO, code)
        if day is None:
            return []
        rows: list[ScenarioRow] = self._get(SCENARIO, "code = %s AND as_of = %s", (code, day))
        return sorted(rows, key=lambda r: _CASE_ORDER[r.case])

    def put_surprises(self, rows: Sequence[SurpriseRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(SURPRISE, rows, loaded_by, now)

    def surprise(self, rcept_no: str) -> SurpriseRow | None:
        got = self._get(SURPRISE, "rcept_no = %s", (rcept_no,))
        return got[0] if got else None

    def surprises_known(self, rcept_nos: Iterable[str]) -> set[str]:
        wanted = sorted(set(rcept_nos))
        if not wanted:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT rcept_no FROM prv_fin.earnings_surprise WHERE rcept_no = ANY(%s::text[])",
                (wanted,),
            ).fetchall()
        return {r[0] for r in rows}

    def surprises(
        self, *, code: str | None = None, corp_code: str | None = None
    ) -> list[SurpriseRow]:
        return self._get(
            SURPRISE,
            "(%s::text IS NULL OR code = %s) AND (%s::text IS NULL OR corp_code = %s)",
            (code, code, corp_code, corp_code),
            "rcept_no",
        )

    def perf_pending(self) -> list[SurpriseRow]:
        return self._get(
            SURPRISE, "alerted_at IS NOT NULL AND NOT perf_done", (), "alerted_at, rcept_no"
        )

    def put_share_classes(
        self, rows: Sequence[ShareClass], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(SHARE_CLASS, rows, loaded_by, now)

    def share_classes(self, codes: Collection[str] | None = None) -> dict[str, ShareClass]:
        wanted = None if codes is None else sorted(set(codes))
        if wanted == []:
            return {}
        rows: list[ShareClass] = self._get(
            SHARE_CLASS,
            "(%s::text[] IS NULL OR code = ANY(%s::text[]))",
            (wanted, wanted),
            "code",
        )
        return {r.code: r for r in rows}


class MemoryConsensusRepo:
    """`ConsensusRepo` 의 메모리 구현 — Pg 와 같은 규칙."""

    def __init__(self) -> None:
        self.snapshot_t = MemoryTable(SNAPSHOT)
        self.opinion_t = MemoryTable(OPINION)
        self.target_t = MemoryTable(TARGET)
        self.revision_t = MemoryTable(REVISION)
        self.valuation_t = MemoryTable(VALUATION)
        self.band_t = MemoryTable(BAND)
        self.scenario_t = MemoryTable(SCENARIO)
        self.surprise_t = MemoryTable(SURPRISE)
        self.share_class_t = MemoryTable(SHARE_CLASS)

    def __repr__(self) -> str:
        return "MemoryConsensusRepo()"

    @staticmethod
    def _put(t: MemoryTable[Any], rows: Sequence[Any], loaded_by: str, now: datetime) -> int:
        return t.upsert(rows, _ext(t.table, _base(loaded_by, now)))

    @staticmethod
    def _replace(
        t: MemoryTable[Any],
        code: str,
        as_of: date,
        rows: Sequence[Any],
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _base(loaded_by, now)
        _same(code, as_of, rows)
        t.remove(lambda r: r.code == code and r.as_of == as_of)
        return t.upsert(rows, _ext(t.table, base))

    @staticmethod
    def _latest(t: MemoryTable[Any], code: str) -> date | None:
        days = [r.as_of for r in t.all() if r.code == code]
        return max(days) if days else None

    def put_snapshot(self, rows: Sequence[ConsensusRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.snapshot_t, rows, loaded_by, now)

    def snapshots(self, code: str, start: date, end: date) -> list[ConsensusRow]:
        rows = self.snapshot_t.where(lambda r: r.code == code and start <= r.snap_date <= end)
        return sorted(rows, key=lambda r: (r.snap_date, r.fiscal_ym, r.source))

    def put_opinions(self, rows: Sequence[OpinionRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.opinion_t, rows, loaded_by, now)

    def opinions(self, code: str, start: date, end: date) -> list[OpinionRow]:
        rows = self.opinion_t.where(lambda r: r.code == code and start <= r.opinion_date <= end)
        return sorted(rows, key=lambda r: (r.opinion_date, r.broker, r.source))

    def put_targets(self, rows: Sequence[TargetConsensus], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.target_t, rows, loaded_by, now)

    def target(self, code: str, as_of: date | None = None) -> TargetConsensus | None:
        day = as_of or self._latest(self.target_t, code)
        return None if day is None else self.target_t.get((code, day))

    def put_revisions(
        self, code: str, as_of: date, rows: Sequence[RevisionRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._replace(self.revision_t, code, as_of, rows, loaded_by, now)

    def revisions(self, code: str, as_of: date | None = None) -> list[RevisionRow]:
        day = as_of or self._latest(self.revision_t, code)
        return self.revision_t.where(lambda r: r.code == code and r.as_of == day)

    def revisions_on(self, as_of: date) -> list[RevisionRow]:
        return self.revision_t.where(lambda r: r.as_of == as_of)

    def put_valuation_days(
        self, rows: Sequence[ValuationDay], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.valuation_t, rows, loaded_by, now)

    def valuation_days(self, code: str, start: date, end: date) -> list[ValuationDay]:
        return self.valuation_t.where(lambda r: r.code == code and start <= r.trade_date <= end)

    def last_valuation_day(self, code: str) -> date | None:
        days = [r.trade_date for r in self.valuation_t.all() if r.code == code]
        return max(days) if days else None

    def put_bands(
        self, code: str, as_of: date, rows: Sequence[BandRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._replace(self.band_t, code, as_of, rows, loaded_by, now)

    def bands(self, code: str, as_of: date | None = None) -> list[BandRow]:
        day = as_of or self._latest(self.band_t, code)
        return self.band_t.where(lambda r: r.code == code and r.as_of == day)

    def put_scenarios(
        self, code: str, as_of: date, rows: Sequence[ScenarioRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._replace(self.scenario_t, code, as_of, rows, loaded_by, now)

    def scenarios(self, code: str, as_of: date | None = None) -> list[ScenarioRow]:
        day = as_of or self._latest(self.scenario_t, code)
        rows = self.scenario_t.where(lambda r: r.code == code and r.as_of == day)
        return sorted(rows, key=lambda r: _CASE_ORDER[r.case])

    def put_surprises(self, rows: Sequence[SurpriseRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.surprise_t, rows, loaded_by, now)

    def surprise(self, rcept_no: str) -> SurpriseRow | None:
        return self.surprise_t.get((rcept_no,))

    def surprises_known(self, rcept_nos: Iterable[str]) -> set[str]:
        return {rc for rc in rcept_nos if (rc,) in self.surprise_t.rows}

    def surprises(
        self, *, code: str | None = None, corp_code: str | None = None
    ) -> list[SurpriseRow]:
        return self.surprise_t.where(
            lambda r: (
                (code is None or r.code == code) and (corp_code is None or r.corp_code == corp_code)
            )
        )

    def perf_pending(self) -> list[SurpriseRow]:
        rows = self.surprise_t.where(lambda r: r.alerted_at is not None and not r.perf_done)
        return sorted(rows, key=lambda r: (r.alerted_at, r.rcept_no))

    def put_share_classes(
        self, rows: Sequence[ShareClass], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.share_class_t, rows, loaded_by, now)

    def share_classes(self, codes: Collection[str] | None = None) -> dict[str, ShareClass]:
        return {r.code: r for r in self.share_class_t.all() if codes is None or r.code in codes}


# 성과 갱신은 행을 통째로 다시 쓴다(put_surprises) — 이 함수는 처리기가 쓰는 편의 도구
def with_perf(
    row: SurpriseRow,
    *,
    price_at_alert: float | None,
    perf: tuple[float | None, float | None, float | None],
    done: bool,
) -> SurpriseRow:
    """3·5·7거래일 성과를 채운 새 행(정지로 종가가 없으면 그 칸 None — 지어내지 않는다)."""
    p3, p5, p7 = perf
    return replace(
        row, price_at_alert=price_at_alert, perf_3d=p3, perf_5d=p5, perf_7d=p7, perf_done=done
    )
