"""규칙 알림 저장소 — 0021 `prv_alerts.*`(장중 슬롯 시세·신호·성과·백테스트·일치 감시 + 규칙 읽기).

근거: docs/p8_design.md §3.3·D-P8-14·15, metrics §30. 로그인 등급 — 공개 작업·공개 내보내기는 이
모듈을 import 하지 않는다.

표 하나에 쓰는 주체 하나(`kbj.core.rules_rows.P8_TABLE_WRITERS`) — 쓰기 함수 이름에 주체를 적는다.
- `put_quote_slots_intraday`·`put_signals_intraday`: `rules.intraday`
- `put_signals_close`: `rules.close`
- `put_outcomes_rules_outcome`: `rules.outcome`
- `put_backtest_nightly`: `rules.backtest_nightly`(실행·신호·일치 감시를 한 트랜잭션)
- 규칙·판 쓰기는 `kbj.store.repos.personal`(쓰기 모듈 `kbj.services.personal.rules`).

신호 멱등: 같은 (규칙, 판, 종목, 평가 시각)은 한 행 — 다시 쓰면 상태·발송 표·근거만 바뀌고 signal_id
는 처음 값을 지킨다(재실행·재기동 중복 0 — 완료 기준 sim ②).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import date, datetime
from typing import Final, Protocol

from kbj.core.rules_rows import (
    BacktestRun,
    BacktestSignal,
    MatchRow,
    OutcomeRow,
    QuoteSlot,
    RuleRow,
    RuleVersion,
    SignalMode,
    SignalRow,
)
from kbj.store.repos._common import ConnFactory, require_aware
from kbj.store.repos._table import MemoryTable, Table, select, upsert

__all__ = [
    "BACKTEST_RUN",
    "BACKTEST_SIGNAL",
    "MATCH",
    "OUTCOME",
    "QUOTE_SLOT",
    "RULE",
    "RULE_VERSION",
    "MemoryRulesRepo",
    "PgRulesRepo",
    "RulesRepo",
]

RULE: Final = Table("prv_alerts.rule", RuleRow, key=("rule_id",), extra=())
RULE_VERSION: Final = Table(
    "prv_alerts.rule_version", RuleVersion, key=("rule_id", "version"), extra=(),
    json_cols=frozenset({"dsl"}),
)  # fmt: skip
QUOTE_SLOT: Final = Table(
    "prv_alerts.watch_quote_slot", QuoteSlot, key=("slot_ts", "code"), extra=()
)
_SIG_KEY: Final = ("rule_id", "version", "code", "eval_ts")
SIGNAL_INTRADAY: Final = Table(
    "prv_alerts.signal_intraday", SignalRow, key=_SIG_KEY, extra=(),
    json_cols=frozenset({"evidence"}), keep=frozenset({"signal_id"}), skip=frozenset({"mode"}),
)  # fmt: skip
SIGNAL_CLOSE: Final = Table(
    "prv_alerts.signal_close", SignalRow, key=_SIG_KEY, extra=(),
    json_cols=frozenset({"evidence"}), keep=frozenset({"signal_id"}), skip=frozenset({"mode"}),
)  # fmt: skip
SIGNAL_VIEW: Final = Table(
    "prv_alerts.signal", SignalRow, key=_SIG_KEY, extra=(), json_cols=frozenset({"evidence"})
)
OUTCOME: Final = Table(
    "prv_alerts.signal_outcome", OutcomeRow, key=("signal_id", "horizon"), extra=()
)
BACKTEST_RUN: Final = Table(
    "prv_alerts.backtest_run", BacktestRun, key=("run_id",), extra=(),
    rename={"start": "start_date", "end": "end_date"}, json_cols=frozenset({"params", "summary"}),
)  # fmt: skip
BACKTEST_SIGNAL: Final = Table(
    "prv_alerts.backtest_signal", BacktestSignal, key=("run_id", "code", "eval_ts"), extra=(),
    json_cols=frozenset({"rets"}),
)  # fmt: skip
MATCH: Final = Table(
    "prv_alerts.match_check", MatchRow, key=("trade_date", "rule_id"), extra=("logic_n",),
    json_cols=frozenset({"diffs"}),
)  # fmt: skip


class RulesRepo(Protocol):
    # ── 규칙 읽기(쓰기는 personal) ──
    def rules(self, *, include_archived: bool = False) -> list[RuleRow]:
        """이름·id 순."""
        ...

    def rule(self, rule_id: str) -> RuleRow | None: ...

    def versions(self, rule_id: str) -> list[RuleVersion]:
        """판 오름차순."""
        ...

    def enabled_rules(self) -> list[tuple[RuleRow, RuleVersion]]:
        """켜져 있고 보관되지 않은 규칙과 그 현재 판(평가 작업의 입력)."""
        ...

    # ── 장중 슬롯 시세(rules.intraday) ──
    def put_quote_slots_intraday(self, rows: Sequence[QuoteSlot]) -> int: ...

    def quote_slots(
        self, codes: Collection[str] | None, start: datetime, end: datetime
    ) -> list[QuoteSlot]:
        """[start, end) 의 슬롯(시각·종목 순) — 장중 재생 백테스트 입력."""
        ...

    # ── 신호 ──
    def put_signals_intraday(self, rows: Sequence[SignalRow]) -> int: ...

    def put_signals_close(self, rows: Sequence[SignalRow]) -> int: ...

    def signals(
        self,
        start: date,
        end: date,
        *,
        rule_id: str | None = None,
        code: str | None = None,
        mode: SignalMode | None = None,
    ) -> list[SignalRow]:
        """trade_date ∈ [start, end] 의 신호(뷰 — 평가 시각·종목 순)."""
        ...

    def signals_since(self, rule_id: str, code: str, since: datetime) -> list[SignalRow]:
        """쿨다운 판정 입력 — 같은 (규칙, 종목)의 since 이후 신호(장중·마감 모두)."""
        ...

    # ── 성과(rules.outcome) ──
    def put_outcomes_rules_outcome(self, rows: Sequence[OutcomeRow]) -> int: ...

    def outcomes(self, signal_ids: Collection[str]) -> list[OutcomeRow]: ...

    # ── 백테스트·일치 감시(rules.backtest_nightly) ──
    def put_backtest_nightly(
        self, run: BacktestRun, signals: Sequence[BacktestSignal], matches: Sequence[MatchRow]
    ) -> int: ...

    def backtest_runs(self, *, rule_id: str | None = None, limit: int = 20) -> list[BacktestRun]:
        """최신 먼저."""
        ...

    def backtest_signals(self, run_id: str) -> list[BacktestSignal]: ...

    def match_checks(
        self, start: date, end: date, *, rule_id: str | None = None
    ) -> list[MatchRow]: ...


def _check_mode(rows: Sequence[SignalRow], mode: SignalMode) -> None:
    bad = [r.signal_id for r in rows if r.mode != mode]
    if bad:
        raise ValueError(f"{mode} 표에 다른 모드 신호: {bad[:3]}")


def _check_run(
    run: BacktestRun, signals: Sequence[BacktestSignal], matches: Sequence[MatchRow]
) -> None:
    bad = [s.code for s in signals if s.run_id != run.run_id]
    if bad:
        raise ValueError(f"다른 실행의 백테스트 신호: {bad[:3]}")
    if any(m.rule_id != run.rule_id for m in matches):
        raise ValueError("일치 감시 행의 규칙이 실행과 다르다")


class PgRulesRepo:
    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgRulesRepo()"

    def rules(self, *, include_archived: bool = False) -> list[RuleRow]:
        where = "TRUE" if include_archived else "archived_at IS NULL"
        with self._connect() as conn:
            return select(conn, RULE, where, (), "name, rule_id")

    def rule(self, rule_id: str) -> RuleRow | None:
        with self._connect() as conn:
            got = select(conn, RULE, "rule_id = %s", (rule_id,))
        return got[0] if got else None

    def versions(self, rule_id: str) -> list[RuleVersion]:
        with self._connect() as conn:
            return select(conn, RULE_VERSION, "rule_id = %s", (rule_id,), "version")

    def enabled_rules(self) -> list[tuple[RuleRow, RuleVersion]]:
        with self._connect() as conn:
            heads = select(conn, RULE, "enabled AND archived_at IS NULL", (), "rule_id")
            out: list[tuple[RuleRow, RuleVersion]] = []
            for h in heads:
                v = select(
                    conn, RULE_VERSION, "rule_id = %s AND version = %s",
                    (h.rule_id, h.current_version),
                )  # fmt: skip
                if v:
                    out.append((h, v[0]))
            return out

    def put_quote_slots_intraday(self, rows: Sequence[QuoteSlot]) -> int:
        with self._connect() as conn, conn.transaction():
            return upsert(conn, QUOTE_SLOT, rows, {})

    def quote_slots(
        self, codes: Collection[str] | None, start: datetime, end: datetime
    ) -> list[QuoteSlot]:
        require_aware("start", start)
        require_aware("end", end)
        where = "slot_ts >= %s AND slot_ts < %s"
        params: list[object] = [start, end]
        if codes is not None:
            where += " AND code = ANY(%s)"
            params.append(sorted(codes))
        with self._connect() as conn:
            return select(conn, QUOTE_SLOT, where, params, "slot_ts, code")

    def put_signals_intraday(self, rows: Sequence[SignalRow]) -> int:
        _check_mode(rows, "intraday")
        with self._connect() as conn, conn.transaction():
            return upsert(conn, SIGNAL_INTRADAY, rows, {})

    def put_signals_close(self, rows: Sequence[SignalRow]) -> int:
        _check_mode(rows, "close")
        with self._connect() as conn, conn.transaction():
            return upsert(conn, SIGNAL_CLOSE, rows, {})

    def signals(
        self,
        start: date,
        end: date,
        *,
        rule_id: str | None = None,
        code: str | None = None,
        mode: SignalMode | None = None,
    ) -> list[SignalRow]:
        where = "trade_date >= %s AND trade_date <= %s"
        params: list[object] = [start, end]
        for col, val in (("rule_id", rule_id), ("code", code), ("mode", mode)):
            if val is not None:
                where += f" AND {col} = %s"
                params.append(val)
        with self._connect() as conn:
            return select(conn, SIGNAL_VIEW, where, params, "eval_ts, code, rule_id")

    def signals_since(self, rule_id: str, code: str, since: datetime) -> list[SignalRow]:
        require_aware("since", since)
        with self._connect() as conn:
            return select(
                conn, SIGNAL_VIEW, "rule_id = %s AND code = %s AND eval_ts >= %s",
                (rule_id, code, since), "eval_ts",
            )  # fmt: skip

    def put_outcomes_rules_outcome(self, rows: Sequence[OutcomeRow]) -> int:
        with self._connect() as conn, conn.transaction():
            return upsert(conn, OUTCOME, rows, {})

    def outcomes(self, signal_ids: Collection[str]) -> list[OutcomeRow]:
        if not signal_ids:
            return []
        with self._connect() as conn:
            return select(
                conn, OUTCOME, "signal_id = ANY(%s)", (sorted(signal_ids),), "signal_id, horizon"
            )

    def put_backtest_nightly(
        self, run: BacktestRun, signals: Sequence[BacktestSignal], matches: Sequence[MatchRow]
    ) -> int:
        _check_run(run, signals, matches)
        with self._connect() as conn, conn.transaction():
            n = upsert(conn, BACKTEST_RUN, [run], {})
            n += upsert(conn, BACKTEST_SIGNAL, signals, {})
            for m in matches:
                n += upsert(conn, MATCH, [m], {"logic_n": m.logic_n})
            return n

    def backtest_runs(self, *, rule_id: str | None = None, limit: int = 20) -> list[BacktestRun]:
        where, params = ("rule_id = %s", (rule_id,)) if rule_id else ("TRUE", ())
        with self._connect() as conn:
            return select(conn, BACKTEST_RUN, where, params, "created_at DESC, run_id", limit)

    def backtest_signals(self, run_id: str) -> list[BacktestSignal]:
        with self._connect() as conn:
            return select(conn, BACKTEST_SIGNAL, "run_id = %s", (run_id,), "eval_ts, code")

    def match_checks(self, start: date, end: date, *, rule_id: str | None = None) -> list[MatchRow]:
        where = "trade_date >= %s AND trade_date <= %s"
        params: list[object] = [start, end]
        if rule_id is not None:
            where += " AND rule_id = %s"
            params.append(rule_id)
        with self._connect() as conn:
            return select(conn, MATCH, where, params, "trade_date, rule_id")


class MemoryRulesRepo:
    """메모리판 — 규칙·판 표는 `MemoryPersonalRepo` 와 같은 표 객체를 나눠 쓴다(같은 DB 처럼)."""

    def __init__(
        self,
        rule_t: MemoryTable[RuleRow] | None = None,
        version_t: MemoryTable[RuleVersion] | None = None,
    ) -> None:
        self.rule_t = rule_t or MemoryTable(RULE)
        self.version_t = version_t or MemoryTable(RULE_VERSION)
        self.slot_t = MemoryTable(QUOTE_SLOT)
        self.sig_i = MemoryTable(SIGNAL_INTRADAY)
        self.sig_c = MemoryTable(SIGNAL_CLOSE)
        self.outcome_t = MemoryTable(OUTCOME)
        self.run_t = MemoryTable(BACKTEST_RUN)
        self.bts_t = MemoryTable(BACKTEST_SIGNAL)
        self.match_t = MemoryTable(MATCH)

    def __repr__(self) -> str:
        return "MemoryRulesRepo()"

    def rules(self, *, include_archived: bool = False) -> list[RuleRow]:
        rows = self.rule_t.where(lambda r: include_archived or r.archived_at is None)
        return sorted(rows, key=lambda r: (r.name, r.rule_id))

    def rule(self, rule_id: str) -> RuleRow | None:
        return self.rule_t.get((rule_id,))

    def versions(self, rule_id: str) -> list[RuleVersion]:
        return sorted(self.version_t.where(lambda v: v.rule_id == rule_id), key=lambda v: v.version)

    def enabled_rules(self) -> list[tuple[RuleRow, RuleVersion]]:
        out: list[tuple[RuleRow, RuleVersion]] = []
        for h in sorted(self.rule_t.where(lambda r: r.enabled and r.archived_at is None),
                        key=lambda r: r.rule_id):  # fmt: skip
            v = self.version_t.get((h.rule_id, h.current_version))
            if v is not None:
                out.append((h, v))
        return out

    def put_quote_slots_intraday(self, rows: Sequence[QuoteSlot]) -> int:
        return self.slot_t.upsert(rows, {})

    def quote_slots(
        self, codes: Collection[str] | None, start: datetime, end: datetime
    ) -> list[QuoteSlot]:
        require_aware("start", start)
        require_aware("end", end)
        rows = self.slot_t.where(
            lambda s: start <= s.slot_ts < end and (codes is None or s.code in codes)
        )
        return sorted(rows, key=lambda s: (s.slot_ts, s.code))

    def _put_signals(self, t: MemoryTable[SignalRow], rows: Sequence[SignalRow]) -> int:
        return t.upsert(rows, {})

    def put_signals_intraday(self, rows: Sequence[SignalRow]) -> int:
        _check_mode(rows, "intraday")
        return self._put_signals(self.sig_i, rows)

    def put_signals_close(self, rows: Sequence[SignalRow]) -> int:
        _check_mode(rows, "close")
        return self._put_signals(self.sig_c, rows)

    def _all_signals(self, pred: Callable[[SignalRow], bool]) -> list[SignalRow]:
        rows = [*self.sig_i.where(pred), *self.sig_c.where(pred)]
        return sorted(rows, key=lambda s: (s.eval_ts, s.code, s.rule_id))

    def signals(
        self,
        start: date,
        end: date,
        *,
        rule_id: str | None = None,
        code: str | None = None,
        mode: SignalMode | None = None,
    ) -> list[SignalRow]:
        return self._all_signals(
            lambda s: (
                start <= s.trade_date <= end
                and (rule_id is None or s.rule_id == rule_id)
                and (code is None or s.code == code)
                and (mode is None or s.mode == mode)
            )
        )

    def signals_since(self, rule_id: str, code: str, since: datetime) -> list[SignalRow]:
        require_aware("since", since)
        return self._all_signals(
            lambda s: s.rule_id == rule_id and s.code == code and s.eval_ts >= since
        )

    def put_outcomes_rules_outcome(self, rows: Sequence[OutcomeRow]) -> int:
        return self.outcome_t.upsert(rows, {})

    def outcomes(self, signal_ids: Collection[str]) -> list[OutcomeRow]:
        ids = set(signal_ids)
        return self.outcome_t.where(lambda o: o.signal_id in ids)

    def put_backtest_nightly(
        self, run: BacktestRun, signals: Sequence[BacktestSignal], matches: Sequence[MatchRow]
    ) -> int:
        _check_run(run, signals, matches)
        n = self.run_t.upsert([run], {})
        n += self.bts_t.upsert(signals, {})
        for m in matches:
            n += self.match_t.upsert([m], {"logic_n": m.logic_n})
        return n

    def backtest_runs(self, *, rule_id: str | None = None, limit: int = 20) -> list[BacktestRun]:
        rows = self.run_t.where(lambda r: rule_id is None or r.rule_id == rule_id)
        rows.sort(key=lambda r: r.run_id)
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]

    def backtest_signals(self, run_id: str) -> list[BacktestSignal]:
        rows = self.bts_t.where(lambda s: s.run_id == run_id)
        return sorted(rows, key=lambda s: (s.eval_ts, s.code))

    def match_checks(self, start: date, end: date, *, rule_id: str | None = None) -> list[MatchRow]:
        return self.match_t.where(
            lambda m: start <= m.trade_date <= end and (rule_id is None or m.rule_id == rule_id)
        )
