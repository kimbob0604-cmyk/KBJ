"""지수 정기변경 예측 저장소(로그인) — 0018 `prv_index.*`(P5 2차 묶음 IDX, metrics §24).

- 쓰기 주인: `index.calendar`(review_calendar),
  `index.predict`(prediction_run·predictions·passive_aum),
  수동 CLI `python -m kbj.services.engine.index_review load-constituents|set-cutoff`(constituents·
  review_calendar.user_cutoff_won).
- 한 실행(지수·리뷰·기준일·컷오프 종류)의 판정 행은 통째로 바꾼다(그 키의 옛 행을 지우고 넣는다 —
  멱등).
- 공개 작업·공개 내보내기는 이 모듈을 import 하지 않는다(로그인 등급 — 구성종목·컷오프 추정).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date, datetime
from typing import Final, Protocol

from kbj.core.index_rows import (
    ConstituentRow,
    PassiveAumRow,
    PredictionRow,
    PredictionRun,
    ReviewEvent,
)
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = ["IndexReviewRepo", "MemoryIndexReviewRepo", "PgIndexReviewRepo"]

CONSTITUENTS: Final = Table(
    "prv_index.constituents", ConstituentRow,
    key=("index_id", "code", "valid_from"), extra=("received_at", "loaded_by"),
)  # fmt: skip
CALENDAR: Final = Table(
    "prv_index.review_calendar", ReviewEvent,
    key=("index_id", "review_id"), extra=("updated_at", "loaded_by"),
    keep=frozenset({"user_cutoff_won"}),  # 일정 다시 쓰기가 사용자 컷오프를 지우지 않는다
)  # fmt: skip
RUNS: Final = Table(
    "prv_index.prediction_run", PredictionRun,
    key=("index_id", "review_id", "base_date", "cutoff_kind"), extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"notes"}),
)  # fmt: skip
PREDICTIONS: Final = Table(
    "prv_index.predictions", PredictionRow,
    key=("index_id", "review_id", "base_date", "cutoff_kind", "code"),
    extra=("computed_at", "loaded_by"), json_cols=frozenset({"evidence"}),
)  # fmt: skip
PASSIVE: Final = Table(
    "prv_index.passive_aum", PassiveAumRow,
    key=("index_id", "as_of", "kind"), extra=("computed_at", "loaded_by"),
    json_cols=frozenset({"detail"}),
)  # fmt: skip


class IndexReviewRepo(Protocol):
    # ── 구성종목 ──
    def put_constituents(
        self, rows: Sequence[ConstituentRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        """(지수, 종목, valid_from) 덮어쓰기."""
        ...

    def constituents(self, index_id: str, on: date) -> list[ConstituentRow]:
        """그날 유효한 구성종목(code 순)."""
        ...

    def constituent_history(self, index_id: str) -> list[ConstituentRow]:
        """모든 기간(code·valid_from 순) — 백테스트·목록 갱신."""
        ...

    # ── 일정 ──
    def put_calendar(self, rows: Sequence[ReviewEvent], *, loaded_by: str, now: datetime) -> int:
        """user_cutoff_won 은 덮지 않는다(사용자 값 유지)."""
        ...

    def calendar(self, index_id: str | None = None) -> list[ReviewEvent]:
        """(display_order, effective_date) 순."""
        ...

    def set_user_cutoff(
        self, index_id: str, review_id: str, won: int | None, *, loaded_by: str, now: datetime
    ) -> bool:
        """사용자 컷오프(없애려면 None). 일정 행이 없으면 False."""
        ...

    # ── 예측 ──
    def put_run(
        self,
        run: PredictionRun,
        rows: Sequence[PredictionRow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        """실행 요약 + 판정 행(그 키의 옛 판정은 지운다). 쓴 판정 행 수."""
        ...

    def latest_runs(self, index_id: str, review_id: str) -> list[PredictionRun]:
        """가장 늦은 base_date 의 실행(컷오프 종류별 — model 먼저)."""
        ...

    def predictions(
        self, index_id: str, review_id: str, base_date: date, cutoff_kind: str
    ) -> list[PredictionRow]:
        """code 순."""
        ...

    def predictions_for_code(self, code: str) -> list[PredictionRow]:
        """종목의 (지수, 리뷰, 컷오프 종류) 별 가장 늦은 base_date 판정(지수·리뷰 순)."""
        ...

    # ── 패시브 자금 ──
    def put_passive_aum(
        self, rows: Sequence[PassiveAumRow], *, loaded_by: str, now: datetime
    ) -> int: ...

    def passive_aum(self, index_id: str, upto: date) -> list[PassiveAumRow]:
        """종류별로 upto 이하 가장 늦은 행."""
        ...


def _extra(loaded_by: str, now: datetime, col: str) -> dict[str, object]:
    require_loaded_by(loaded_by)
    require_aware(col, now)
    return {col: now, "loaded_by": loaded_by}


def _check_run(run: PredictionRun, rows: Sequence[PredictionRow]) -> None:
    key = (run.index_id, run.review_id, run.base_date, run.cutoff_kind)
    bad = [r.code for r in rows if (r.index_id, r.review_id, r.base_date, r.cutoff_kind) != key]
    if bad:
        raise ValueError(f"실행 키와 다른 판정 행: {bad[:3]}")
    if run.status != "ok" and rows:
        raise ValueError("ok 가 아닌 실행은 판정 행을 쓰지 않는다(지어내지 않음)")


def _latest_per_kind(rows: Sequence[PassiveAumRow]) -> list[PassiveAumRow]:
    best: dict[str, PassiveAumRow] = {}
    for r in rows:
        cur = best.get(r.kind)
        if cur is None or r.as_of > cur.as_of:
            best[r.kind] = r
    return [best[k] for k in sorted(best)]


class PgIndexReviewRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgIndexReviewRepo()"

    def put_constituents(
        self, rows: Sequence[ConstituentRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        extra = _extra(loaded_by, received_at, "received_at")
        with self._connect() as conn:
            return upsert(conn, CONSTITUENTS, rows, extra)

    def constituents(self, index_id: str, on: date) -> list[ConstituentRow]:
        with self._connect() as conn:
            return select(
                conn, CONSTITUENTS,
                "index_id = %s AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)",
                (index_id, on, on), "code",
            )  # fmt: skip

    def constituent_history(self, index_id: str) -> list[ConstituentRow]:
        with self._connect() as conn:
            return select(conn, CONSTITUENTS, "index_id = %s", (index_id,), "code, valid_from")

    def put_calendar(self, rows: Sequence[ReviewEvent], *, loaded_by: str, now: datetime) -> int:
        extra = _extra(loaded_by, now, "updated_at")
        with self._connect() as conn:
            return upsert(conn, CALENDAR, rows, extra)

    def calendar(self, index_id: str | None = None) -> list[ReviewEvent]:
        with self._connect() as conn:
            if index_id is None:
                return select(conn, CALENDAR, "TRUE", (), "display_order, effective_date")
            return select(conn, CALENDAR, "index_id = %s", (index_id,), "effective_date")

    def set_user_cutoff(
        self, index_id: str, review_id: str, won: int | None, *, loaded_by: str, now: datetime
    ) -> bool:
        _extra(loaded_by, now, "updated_at")
        if won is not None and won <= 0:
            raise ValueError("컷오프는 0 보다 크다")
        with self._connect() as conn:
            cur = conn.execute(
                b"UPDATE prv_index.review_calendar SET user_cutoff_won = %s, updated_at = %s, "
                b"loaded_by = %s WHERE index_id = %s AND review_id = %s",
                (won, now, loaded_by, index_id, review_id),
            )
            return cur.rowcount > 0

    def put_run(
        self,
        run: PredictionRun,
        rows: Sequence[PredictionRow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        _check_run(run, rows)
        extra = _extra(loaded_by, now, "computed_at")
        with self._connect() as conn, conn.transaction():
            upsert(conn, RUNS, [run], extra)
            delete(
                conn, PREDICTIONS,
                "index_id = %s AND review_id = %s AND base_date = %s AND cutoff_kind = %s",
                (run.index_id, run.review_id, run.base_date, run.cutoff_kind),
            )  # fmt: skip
            return upsert(conn, PREDICTIONS, rows, extra)

    def latest_runs(self, index_id: str, review_id: str) -> list[PredictionRun]:
        with self._connect() as conn:
            return select(
                conn, RUNS,
                "index_id = %s AND review_id = %s AND base_date = (SELECT max(base_date) FROM "
                "prv_index.prediction_run WHERE index_id = %s AND review_id = %s)",
                (index_id, review_id, index_id, review_id), "cutoff_kind",
            )  # fmt: skip

    def predictions(
        self, index_id: str, review_id: str, base_date: date, cutoff_kind: str
    ) -> list[PredictionRow]:
        with self._connect() as conn:
            return select(
                conn, PREDICTIONS,
                "index_id = %s AND review_id = %s AND base_date = %s AND cutoff_kind = %s",
                (index_id, review_id, base_date, cutoff_kind), "code",
            )  # fmt: skip

    def predictions_for_code(self, code: str) -> list[PredictionRow]:
        with self._connect() as conn:
            return select(
                conn, PREDICTIONS,
                "code = %s AND (index_id, review_id, cutoff_kind, base_date) IN ("
                "SELECT index_id, review_id, cutoff_kind, max(base_date) FROM "
                "prv_index.predictions WHERE code = %s GROUP BY index_id, review_id, cutoff_kind)",
                (code, code), "index_id, review_id, cutoff_kind",
            )  # fmt: skip

    def put_passive_aum(
        self, rows: Sequence[PassiveAumRow], *, loaded_by: str, now: datetime
    ) -> int:
        extra = _extra(loaded_by, now, "computed_at")
        with self._connect() as conn:
            return upsert(conn, PASSIVE, rows, extra)

    def passive_aum(self, index_id: str, upto: date) -> list[PassiveAumRow]:
        with self._connect() as conn:
            rows = select(
                conn, PASSIVE, "index_id = %s AND as_of <= %s", (index_id, upto), "kind, as_of"
            )
        return _latest_per_kind(rows)


class MemoryIndexReviewRepo:
    """메모리판 — Pg 와 같은 키·덮어쓰기 규칙(시험·시뮬레이션)."""

    def __init__(self) -> None:
        self.constituent_rows = MemoryTable(CONSTITUENTS)
        self.calendar_rows = MemoryTable(CALENDAR)
        self.run_rows = MemoryTable(RUNS)
        self.prediction_rows = MemoryTable(PREDICTIONS)
        self.passive_rows = MemoryTable(PASSIVE)

    def put_constituents(
        self, rows: Sequence[ConstituentRow], *, loaded_by: str, received_at: datetime
    ) -> int:
        return self.constituent_rows.upsert(rows, _extra(loaded_by, received_at, "received_at"))

    def constituents(self, index_id: str, on: date) -> list[ConstituentRow]:
        rows = self.constituent_rows.where(lambda r: r.index_id == index_id and r.active_on(on))
        return sorted(rows, key=lambda r: r.code)

    def constituent_history(self, index_id: str) -> list[ConstituentRow]:
        rows = self.constituent_rows.where(lambda r: r.index_id == index_id)
        return sorted(rows, key=lambda r: (r.code, r.valid_from))

    def put_calendar(self, rows: Sequence[ReviewEvent], *, loaded_by: str, now: datetime) -> int:
        return self.calendar_rows.upsert(rows, _extra(loaded_by, now, "updated_at"))

    def calendar(self, index_id: str | None = None) -> list[ReviewEvent]:
        rows = self.calendar_rows.where(lambda r: index_id is None or r.index_id == index_id)
        return sorted(rows, key=lambda r: (r.display_order, r.effective_date))

    def set_user_cutoff(
        self, index_id: str, review_id: str, won: int | None, *, loaded_by: str, now: datetime
    ) -> bool:
        extra = _extra(loaded_by, now, "updated_at")
        if won is not None and won <= 0:
            raise ValueError("컷오프는 0 보다 크다")
        key = (index_id, review_id)
        got = self.calendar_rows.rows.get(key)
        if got is None:
            return False
        got.row = dataclasses.replace(got.row, user_cutoff_won=won)
        got.extra.update(extra)
        return True

    def put_run(
        self,
        run: PredictionRun,
        rows: Sequence[PredictionRow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        _check_run(run, rows)
        extra = _extra(loaded_by, now, "computed_at")
        self.run_rows.upsert([run], extra)
        key = (run.index_id, run.review_id, run.base_date, run.cutoff_kind)
        self.prediction_rows.remove(
            lambda r: (r.index_id, r.review_id, r.base_date, r.cutoff_kind) == key
        )
        return self.prediction_rows.upsert(rows, extra)

    def latest_runs(self, index_id: str, review_id: str) -> list[PredictionRun]:
        rows = self.run_rows.where(lambda r: r.index_id == index_id and r.review_id == review_id)
        if not rows:
            return []
        last = max(r.base_date for r in rows)
        return sorted((r for r in rows if r.base_date == last), key=lambda r: r.cutoff_kind)

    def predictions(
        self, index_id: str, review_id: str, base_date: date, cutoff_kind: str
    ) -> list[PredictionRow]:
        key = (index_id, review_id, base_date, cutoff_kind)
        rows = self.prediction_rows.where(
            lambda r: (r.index_id, r.review_id, r.base_date, r.cutoff_kind) == key
        )
        return sorted(rows, key=lambda r: r.code)

    def predictions_for_code(self, code: str) -> list[PredictionRow]:
        rows = self.prediction_rows.where(lambda r: r.code == code)
        last: dict[tuple[str, str, str], date] = {}
        for r in rows:
            k = (r.index_id, r.review_id, r.cutoff_kind)
            last[k] = max(last.get(k, r.base_date), r.base_date)
        out = [r for r in rows if last[(r.index_id, r.review_id, r.cutoff_kind)] == r.base_date]
        return sorted(out, key=lambda r: (r.index_id, r.review_id, r.cutoff_kind))

    def put_passive_aum(
        self, rows: Sequence[PassiveAumRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self.passive_rows.upsert(rows, _extra(loaded_by, now, "computed_at"))

    def passive_aum(self, index_id: str, upto: date) -> list[PassiveAumRow]:
        rows = self.passive_rows.where(lambda r: r.index_id == index_id and r.as_of <= upto)
        return _latest_per_kind(rows)
