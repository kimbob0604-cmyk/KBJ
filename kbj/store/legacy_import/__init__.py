"""옛 SQLite·JSON → Postgres 이관(docs/p2_design.md §8.4~§8.7, conflict_map §1.5).

    python -m kbj.store.legacy_import --phase P2
        --source sd=<dashboard.db> --source board=<board.db> [--source us=<us_board.db>]
        [--source backtest=<backtest.db> --source us_backtest=<…>]
        [--json inbox=<state/inbox.json> --json stockflows=<ET state 폴더>]
        [--json krflows=<flows.json>] [--dry-run | --verify-only]

- 원본은 읽기 전용으로만 연다(`sources.open_sqlite_ro` — `mode=ro&immutable=1`). 원본 사본 선택은
  ADR 0001 Q12(맥 로컬 사본 — 사용자 제공). 토큰 캐시는 열지 않는다.
- 매핑마다(`mappings.MAPPINGS` 순서 = 우선순위): 원본 행 → 순수 변환(`transforms`) → 버림
  사유별 집계 → 같은 키 중복은 앞의 것(`dup_key`) → 대상에 한 트랜잭션으로 넣고(`targets`) →
  행 수·키 다이제스트·값 합 대조(`verify`) → `ops.legacy_import` 한 줄. 어긋나면 그 매핑만
  되돌리고 종료 코드 1(다른 매핑은 계속 — 절대 규칙 4). 같은 원본으로 두 번 돌리면 두 번째는
  `씀 0`(멱등).
- `--dry-run` 은 DB 없이 읽고 변환·집계만 한다. `--verify-only` 는 쓰지 않고 대조만 한다.
- 출력은 `원본.표 → 스키마.표: 읽음 N · 버림 M(사유별) · 씀 K · 키 다이제스트 일치/불일치` —
  값·키는 찍지 않는다(절대 규칙 5).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from kbj.store.legacy_import import sources as src
from kbj.store.legacy_import.mappings import MAPPINGS, TableMapping, for_phase
from kbj.store.legacy_import.targets import ImportRecord, MemoryTarget, PgTarget, TargetStore
from kbj.store.legacy_import.transforms import Drop, TargetRow
from kbj.store.legacy_import.verify import VerifyReport, key_text, verify

__all__ = [
    "MAPPINGS",
    "ImportReport",
    "MappingResult",
    "MemoryTarget",
    "PgTarget",
    "TableMapping",
    "run",
]

Status = Literal["ok", "mismatch", "failed", "skipped", "dry_run", "verified"]
Mode = Literal["write", "dry_run", "verify_only"]

_JSON_READERS: dict[str, Callable[[Path], Iterator[dict[str, object]]]] = {
    "inbox": src.iter_inbox,
    "stockflows": src.iter_stockflows,
    "krflows": src.iter_krflows,
}


@dataclass
class MappingResult:
    mapping: str
    source_name: str
    source_table: str
    target_table: str
    status: Status
    source_rows: int = 0
    candidates: int = 0
    drops: Counter[str] = field(default_factory=Counter[str])
    rows_written: int | None = None
    planned: int = 0  # 넣으려 한 행(후보 − 버림)
    report: VerifyReport | None = None
    reason: str = ""

    @property
    def rows_dropped(self) -> int:
        return sum(self.drops.values())

    def line(self) -> str:
        head = f"{self.source_name}.{self.source_table} → {self.target_table}"
        if self.status == "skipped":
            return f"{head}: 건너뜀 — {self.reason}"
        if self.status == "failed":
            return f"{head}: 실패 — {self.reason}"
        read = f"읽음 {self.source_rows}"
        if self.candidates != self.source_rows:
            read += f"(후보 {self.candidates})"
        dropped = f"버림 {self.rows_dropped}"
        if self.drops:
            dropped += "(" + ", ".join(f"{k} {v}" for k, v in sorted(self.drops.items())) + ")"
        if self.status == "dry_run":
            return f"{head}: {read} · {dropped} · 씀 0(dry-run — {self.planned} 예정)"
        written = "씀 0(verify-only)" if self.status == "verified" else f"씀 {self.rows_written}"
        rep = self.report
        if self.status == "mismatch" and rep is not None and rep.digest_ok:
            digest = "키 다이제스트 일치(그 밖 불일치: " + "; ".join(rep.problems()) + ")"
        elif rep is not None and rep.ok:
            digest = "키 다이제스트 일치"
        else:
            probs = "; ".join(rep.problems()) if rep is not None else self.reason
            digest = f"키 다이제스트 불일치({probs})"
        return f"{head}: {read} · {dropped} · {written} · {digest}"


@dataclass
class ImportReport:
    batch_id: str
    phase: str
    mode: Mode
    results: list[MappingResult] = field(default_factory=list[MappingResult])
    notes: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return all(r.status in ("ok", "skipped", "dry_run", "verified") for r in self.results)

    def lines(self) -> list[str]:
        head = f"legacy_import {self.phase} · {self.mode} · 배치 {self.batch_id}"
        return [head, *(r.line() for r in self.results), *self.notes]


class _Abort(Exception):
    """트랜잭션을 되돌리게 하는 내부 신호(대조 불일치·verify-only)."""

    def __init__(self, report: VerifyReport) -> None:
        super().__init__("abort")
        self.report = report


def _batch_id(now: datetime, shas: dict[str, str]) -> str:
    h = hashlib.sha256("|".join(f"{k}={v}" for k, v in sorted(shas.items())).encode())
    return f"{now:%Y%m%dT%H%M%SZ}-{h.hexdigest()[:8]}"


def _read_rows(
    m: TableMapping, path: Path, conns: dict[str, sqlite3.Connection]
) -> tuple[Iterator[dict[str, object]] | None, str]:
    """원본 행 반복자, 또는 (None, 건너뛰는 사유)."""
    if m.is_json:
        return _JSON_READERS[m.source](path), ""
    conn = conns.get(m.source)
    if conn is None:
        conn = conns[m.source] = src.open_sqlite_ro(path)
    if m.source_table not in src.sqlite_tables(conn):
        return None, "원본에 표가 없다"
    if m.select_sql is None:  # SQLite 매핑은 늘 SELECT 가 있다(mappings.py)
        raise ValueError(f"{m.name}: select_sql 이 없다")
    return src.iter_sqlite(conn, m.select_sql), ""


def _prepare(
    m: TableMapping, rows: Iterator[dict[str, object]], res: MappingResult
) -> list[TargetRow]:
    """변환·버림 집계·원본 표시·같은 키 중복 제거(앞의 것을 둔다)."""
    seen: set[str] = set()
    out: list[TargetRow] = []
    for row in rows:
        res.source_rows += 1
        for cand in m.transform(row):
            res.candidates += 1
            if isinstance(cand, Drop):
                res.drops[cand.reason] += 1
                continue
            if m.target.origin is not None:
                cand[m.target.origin] = m.origin
            k = key_text(cand, m.key_cols)
            if k in seen:
                res.drops["dup_key"] += 1
                continue
            seen.add(k)
            out.append(cand)
    return out


def _record(
    res: MappingResult,
    m: TableMapping,
    *,
    batch_id: str,
    sha: str,
    phase: str,
    started: datetime,
    finished: datetime,
) -> ImportRecord:
    rep = res.report
    return ImportRecord(
        batch_id=batch_id,
        mapping=m.name,
        source_name=m.source,
        source_sha256=sha,
        source_table=m.source_table,
        target_table=m.target.table,
        phase=phase,
        rows_read=res.candidates,
        rows_dropped=res.rows_dropped,
        drops=dict(sorted(res.drops.items())),
        rows_written=res.rows_written or 0,
        key_digest_src=rep.digest_src if rep else None,
        key_digest_dst=rep.digest_dst if rep else None,
        sums={c: {"src": str(a), "dst": str(b)} for c, (a, b) in (rep.sums if rep else {}).items()},
        status="ok"
        if res.status == "ok"
        else ("mismatch" if res.status == "mismatch" else "failed"),
        started_at=started,
        finished_at=finished,
    )


def run(
    sources: dict[str, Path],
    target: TargetStore | None,
    *,
    phase: str,
    now: Callable[[], datetime],
    dry_run: bool = False,
    verify_only: bool = False,
    batch_id: str | None = None,
) -> ImportReport:
    """주어진 원본의 `phase` 매핑을 차례로 옮긴다. 잘못된 인자(모르는 원본·매핑 없는 단계)는
    ValueError — 원본을 열기 전에 멈춘다."""
    known = set(src.SQLITE_SOURCES) | set(src.JSON_SOURCES)
    unknown = sorted(set(sources) - known)
    if unknown:
        raise ValueError(
            f"모르는 원본 이름: {', '.join(unknown)} (가능: {', '.join(sorted(known))})"
        )
    if dry_run and verify_only:
        raise ValueError("--dry-run 과 --verify-only 는 함께 쓰지 않는다")
    mappings = for_phase(phase)
    if not mappings:
        raise ValueError(f"{phase} 에 옮길 매핑이 없다(P2 만 있다 — 나머지는 mappings.LATER)")
    if not dry_run and target is None:
        raise ValueError("대상(DB)이 없다 — --dry-run 이 아니면 대상이 필요하다")
    for name, path in sources.items():
        try:
            src.refuse_token_cache(path)
        except src.SourceError as e:
            raise ValueError(str(e)) from None
        if not path.exists():
            raise ValueError(f"원본이 없다: {name}={path.name}")
    shas = {name: src.file_sha256(path) for name, path in sorted(sources.items())}
    mode: Mode = "dry_run" if dry_run else ("verify_only" if verify_only else "write")
    report = ImportReport(batch_id or _batch_id(now(), shas), phase, mode)
    used = {m.source for m in mappings}
    report.notes += [f"{name}: {phase} 에 옮길 표 없음" for name in sorted(set(sources) - used)]
    conns: dict[str, sqlite3.Connection] = {}
    try:
        for m in mappings:
            if m.source not in sources:
                continue
            report.results.append(
                _one(m, sources[m.source], conns, target, report, shas[m.source], now)
            )
    finally:
        for c in conns.values():
            c.close()
    return report


def _one(
    m: TableMapping,
    path: Path,
    conns: dict[str, sqlite3.Connection],
    target: TargetStore | None,
    report: ImportReport,
    sha: str,
    now: Callable[[], datetime],
) -> MappingResult:
    res = MappingResult(m.name, m.source, m.source_table, m.target.table, "failed")
    started = now()
    rows: list[TargetRow] = []
    rows_ok = False
    try:
        rows_iter, why = _read_rows(m, path, conns)
        if rows_iter is None:
            res.status, res.reason = "skipped", why
            return res
        rows = _prepare(m, rows_iter, res)
    except (src.SourceError, sqlite3.Error) as e:
        res.reason = f"원본을 읽지 못했다({type(e).__name__})"
    except Exception as e:  # 변환 오류도 그 매핑만 실패(절대 규칙 4) — 종류만 남긴다
        res.reason = f"변환 실패({type(e).__name__})"
    else:
        rows_ok = True
    if not rows_ok:
        if report.mode == "write" and target is not None:
            _try_record(target, res, m, report, sha, started, now)
        return res
    res.planned = len(rows)
    if report.mode == "dry_run" or target is None:
        res.status = "dry_run"
        return res
    try:
        with target.transaction():
            other = target.stage(m, rows)
            if other:
                res.drops["other_origin"] += len(other)
                rows = [r for r in rows if key_text(r, m.key_cols) not in other]
            res.rows_written = 0 if report.mode == "verify_only" else target.merge(m)
            rep = verify(m.target, rows, target.observe(m))
            res.report = rep
            if report.mode == "verify_only" or not rep.ok:
                raise _Abort(rep)  # 되돌린다(verify-only 는 늘, 불일치면 쓴 것까지)
            res.status = "ok"
            target.record(
                _record(
                    res, m, batch_id=report.batch_id, sha=sha, phase=report.phase,
                    started=started, finished=now(),
                )
            )  # fmt: skip
    except _Abort as a:
        res.report = a.report
        res.status = "verified" if report.mode == "verify_only" and a.report.ok else "mismatch"
        if report.mode == "write":
            res.rows_written = 0  # 되돌렸다
            _try_record(target, res, m, report, sha, started, now)
    except Exception as e:  # 한 매핑의 실패가 나머지를 멈추지 않는다(절대 규칙 4) — 종류만 남긴다
        res.status = "failed"
        res.rows_written = 0
        res.reason = type(e).__name__
        if report.mode == "write":
            _try_record(target, res, m, report, sha, started, now)
    return res


def _try_record(
    target: TargetStore,
    res: MappingResult,
    m: TableMapping,
    report: ImportReport,
    sha: str,
    started: datetime,
    now: Callable[[], datetime],
) -> None:
    """실패·불일치 기록은 따로 남긴다. 그것마저 안 되면 보고서 메모에만(삼키지 않는다)."""
    try:
        with target.transaction():
            target.record(
                _record(
                    res, m, batch_id=report.batch_id, sha=sha, phase=report.phase,
                    started=started, finished=now(),
                )
            )  # fmt: skip
    except Exception as e:
        report.notes.append(f"{m.name}: 실패 기록도 남기지 못했다({type(e).__name__})")
