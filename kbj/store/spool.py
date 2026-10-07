"""DB 쓰기 실패 묶음의 로컬 디스크 큐 — GEXLAB `data/spool.py` 승격(docs/p2_design.md §1.8).

코드는 GX 그대로다(전환 기간 legacy GX 의 `PostgresSink` 와 같은 줄 형식 — 한 디렉터리를 둘이 함께
쓰지는 않는다). GX 설계(phase1_design §2 `db` 장애 시): 서비스별 로컬 큐(디스크)에 쌓고 복구 뒤
재적재. 디렉터리는 `Settings.spool_dir/<서비스>`(KBJ_SPOOL_DIR — 기본 state/spool, gitignore).

- 서비스마다 디렉터리 하나(`state/spool/<서비스>/`). 표마다 하위 디렉터리, 그 안에 세그먼트 파일
  `<시작 마이크로초 17자리>.jsonl`. 한 줄 = 묶음 하나(JSON: 표·열·거래일·세션·행 수·행). 줄마다
  flush + fsync, 새 파일은 디렉터리도 fsync — 돌아온 묶음은 전원이 나가도 남는다
- 값은 타입을 달아 적는다(Decimal·aware datetime·date·bytes) — 재적재가 처음과 같은 파라미터를
  보낸다. 접속 정보는 이 모듈에 오지 않는다(행 값만 받는다). 파일은 0600, 디렉터리 0700
- 재적재는 오래된 세그먼트부터(모든 표의 파일 이름 순), 파일 안은 줄 순서대로. 표마다 순서가
  지켜진다 — 스풀에 남은 것이 있으면 새 묶음도 스풀 뒤에 붙이는 것은 호출자(`PostgresSink`) 몫.
  재적재는 멱등(유니크 키, 001_init.sql)이라 중간에 죽어 다시 보내도 중복이 생기지 않는다
- 크기 상한(`max_bytes`)을 넘으면 가장 오래된 세그먼트부터 버리고 `DropNotice` 를 돌려준다 —
  호출자가 health 로 남긴다(조용히 잃지 않는다). 한 묶음이 상한보다 크면 그 묶음을 버린다
- 재적재 중 데이터 오류(제약 위반 등)로 들어가지 않는 묶음은 `_dead/<표>.jsonl` 로 옮기고(상한
  따로) 다음 묶음으로 간다 — 한 건이 큐 전체를 막지 않게. 쓰기 함수가 묶음 중 일부 행만 거부됐다고
  알리면(`RejectedRows`) 그 행들만 같은 형식의 줄로 옮긴다(나머지는 들어갔다). 깨진 줄(쓰는 중 죽은
  마지막 줄 등)은 `_dead/_corrupt.jsonl` 로
- 한 디렉터리는 한 프로세스만 쓴다(`.lock` flock). 다시 기동하면 기존 세그먼트는 읽기만 하고
  새 세그먼트를 연다(쓰다 끊긴 줄 뒤에 붙이지 않는다)
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

VERSION = 1
DEFAULT_MAX_BYTES = 1 << 30  # 1 GiB — 서비스 하나 기준 (확인 필요: 녹화량 실측 뒤 조정)
DEFAULT_SEGMENT_BYTES = 8 << 20
DEAD = "_dead"
CORRUPT = "_corrupt"
_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEGMENT = re.compile(r"^(\d{17})\.jsonl$")

Row = tuple[Any, ...]
Tag = tuple[date | None, str | None]


class SpoolError(RuntimeError):
    """스풀에 쓰지 못했다(디스크·권한·잠금) 또는 값을 적을 수 없다."""


class TransientWrite(Exception):
    """재적재 쓰기 함수가 던지는 '지금은 못 쓴다(연결 오류) — 멈추고 나중에 다시' 신호."""


class RejectedRows(Exception):
    """재적재 쓰기 함수가 던지는 '이 행들만 들어가지 않았다(데이터 오류), 나머지는 들어갔다' 신호.

    스풀은 이 행들만 데드레터로 옮긴다 — 한 행 때문에 묶음의 나머지를 함께 버리지 않게.
    reason 은 health 에 싣는 짧은 사유(오류 종류·SQLSTATE — 행 값은 싣지 않는다).
    """

    def __init__(self, rows: Sequence[Row], reason: str) -> None:
        super().__init__(reason)
        self.rows: list[Row] = list(rows)
        self.reason = reason


# ── 값 ↔ JSON ────────────────────────────────────────────────────────────────


def encode_value(v: object) -> object:
    """행 값 하나 → JSON 값. 문자열·정수·불·None 은 그대로, 나머지는 타입 표시 객체."""
    if isinstance(v, Enum):
        v = v.value  # StrEnum·IntEnum 은 값으로
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else {"$f": repr(v)}
    if isinstance(v, Decimal):
        return {"$n": str(v)}
    if isinstance(v, datetime):
        if v.tzinfo is None or v.utcoffset() is None:
            raise SpoolError("naive datetime 금지")
        return {"$t": v.astimezone(UTC).isoformat()}
    if isinstance(v, date):
        return {"$d": v.isoformat()}
    if isinstance(v, bytes | bytearray | memoryview):
        return {"$b": base64.b64encode(bytes(v)).decode("ascii")}
    raise SpoolError(f"스풀에 적을 수 없는 값 타입: {type(v).__name__}")


def decode_value(j: object) -> object:
    if isinstance(j, dict):
        if len(j) != 1:
            raise ValueError("타입 표시 객체가 아니다")
        ((tag, s),) = j.items()
        if not isinstance(s, str):
            raise ValueError("타입 표시 값은 문자열")
        if tag == "$n":
            return Decimal(s)
        if tag == "$t":
            t = datetime.fromisoformat(s)
            if t.tzinfo is None:
                raise ValueError("naive datetime")
            return t
        if tag == "$d":
            return date.fromisoformat(s)
        if tag == "$b":
            return base64.b64decode(s, validate=True)
        if tag == "$f":
            return float(s)
        raise ValueError(f"모르는 타입 표시: {tag}")
    if isinstance(j, list):
        raise ValueError("행 값에 목록은 없다")
    return j


@dataclass(frozen=True)
class SpooledBatch:
    table: str
    columns: tuple[str, ...]
    rows: list[Row]
    trade_date: date | None
    session: str | None
    at: datetime  # 스풀에 넣은 시각 (UTC)

    @property
    def tag(self) -> Tag:
        return self.trade_date, self.session


def encode_batch(
    table: str, columns: Sequence[str], rows: Sequence[Row], tag: Tag, at: datetime
) -> bytes:
    """묶음 한 줄(끝에 개행). 값을 적을 수 없으면 SpoolError."""
    body = {
        "v": VERSION,
        "table": table,
        "at": at.astimezone(UTC).isoformat(),
        "trade_date": tag[0].isoformat() if tag[0] is not None else None,
        "session": tag[1],
        "n": len(rows),
        "columns": list(columns),
        "rows": [[encode_value(v) for v in r] for r in rows],
    }
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8") + b"\n"


def decode_batch(line: bytes) -> SpooledBatch:
    """한 줄 → 묶음. 형식이 틀리면 ValueError (깨진 줄)."""
    if not line.endswith(b"\n"):
        raise ValueError("줄이 끝나지 않았다 (쓰는 중 끊김)")
    obj = json.loads(line)
    if not isinstance(obj, dict) or obj.get("v") != VERSION:
        raise ValueError("스풀 줄 형식·버전이 다르다")
    rows_raw = obj["rows"]
    columns = obj["columns"]
    if not isinstance(rows_raw, list) or not isinstance(columns, list):
        raise ValueError("rows·columns 는 목록")
    rows: list[Row] = []
    for r in rows_raw:
        if not isinstance(r, list) or len(r) != len(columns):
            raise ValueError("행 길이가 열 수와 다르다")
        rows.append(tuple(decode_value(v) for v in r))
    if obj["n"] != len(rows):
        raise ValueError("행 수가 맞지 않는다")
    td = obj["trade_date"]
    at = datetime.fromisoformat(obj["at"])
    if at.tzinfo is None:
        raise ValueError("naive datetime")
    return SpooledBatch(
        table=str(obj["table"]),
        columns=tuple(str(c) for c in columns),
        rows=rows,
        trade_date=date.fromisoformat(td) if td is not None else None,
        session=obj["session"],
        at=at,
    )


# ── 스풀 ─────────────────────────────────────────────────────────────────────


@dataclass
class _Segment:
    path: Path
    table: str
    start_us: int
    size: int
    offset: int = 0  # 재적재를 끝낸 바이트 (이 프로세스)


@dataclass(frozen=True)
class DropNotice:
    """상한 때문에 버린 것 (health 로 남긴다). reason: 오래된 세그먼트 · 상한보다 큰 새 묶음 ·
    데드레터 상한."""

    reason: Literal["oldest", "too_large", "dead_full"]
    tables: tuple[str, ...]
    batches: int
    rows: int
    bytes: int
    oldest_at: datetime | None = None
    newest_at: datetime | None = None


@dataclass
class ReplayResult:
    batches: int = 0
    rows: int = 0
    dead_batches: int = 0
    dead_rows: int = 0
    corrupt_lines: int = 0
    stopped: Literal["transient", "deadline"] | None = None
    drops: list[DropNotice] = field(default_factory=list[DropNotice])
    errors: list[str] = field(default_factory=list[str])  # 데드레터 사유(첫 몇 건)

    @property
    def complete(self) -> bool:
        return self.stopped is None


@dataclass(frozen=True)
class SpoolStatus:
    segments: int
    bytes: int  # 재적재를 기다리는 바이트
    tables: tuple[str, ...]
    dead_bytes: int


class DiskSpool:
    """서비스 하나의 디스크 큐. 스레드 안전하지 않다 — 호출자가 잠근다(`PostgresSink` 는 잠금
    안에서 부른다)."""

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        segment_bytes: int = DEFAULT_SEGMENT_BYTES,
        dead_max_bytes: int | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_bytes <= 0 or segment_bytes <= 0:
            raise ValueError("max_bytes·segment_bytes 는 0보다 커야 한다")
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.segment_bytes = segment_bytes
        self.dead_max_bytes = dead_max_bytes if dead_max_bytes is not None else max_bytes // 8
        self._now = now if now is not None else (lambda: datetime.now(tz=UTC))
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._lock_fd = os.open(self.directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            raise SpoolError(f"스풀 디렉터리를 열지 못했다: {type(e).__name__}") from None
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_fd)
            raise SpoolError("다른 프로세스가 이 스풀 디렉터리를 쓰고 있다") from None
        self._segments: list[_Segment] = []
        self._active: dict[str, _Segment] = {}  # 이 프로세스가 연 세그먼트 (표마다)
        self._last_us = 0
        self._scan()

    # ── 상태 ──

    @property
    def pending(self) -> bool:
        return any(s.offset < s.size for s in self._segments)

    def status(self) -> SpoolStatus:
        return SpoolStatus(
            segments=len(self._segments),
            bytes=self._pending_bytes(),
            tables=tuple(sorted({s.table for s in self._segments})),
            dead_bytes=self._dead_bytes(),
        )

    def _pending_bytes(self) -> int:
        return sum(s.size - s.offset for s in self._segments)

    def _disk_bytes(self) -> int:
        return sum(s.size for s in self._segments)

    def _dead_bytes(self) -> int:
        d = self.directory / DEAD
        if not d.is_dir():
            return 0
        return sum(p.stat().st_size for p in d.glob("*.jsonl"))

    def close(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self._lock_fd)

    # ── 쓰기 ──

    def append(
        self, table: str, columns: Sequence[str], rows: Sequence[Row], tag: Tag
    ) -> list[DropNotice]:
        """묶음 하나를 붙인다(fsync 까지). 자리를 만들려고 버린 것을 돌려준다.

        상한보다 큰 묶음은 붙이지 않고 `too_large` 로 돌려준다. 디스크 오류는 SpoolError.
        """
        if not _TABLE.fullmatch(table):
            raise SpoolError(f"표 이름이 아니다: {table!r}")
        at = self._now()
        line = encode_batch(table, columns, rows, tag, at)
        if len(line) > self.max_bytes:
            return [DropNotice("too_large", (table,), 1, len(rows), len(line), at, at)]
        drops = self._make_room(len(line))
        seg = self._active.get(table)
        if seg is None or seg.size >= self.segment_bytes or seg not in self._segments:
            seg = self._new_segment(table)
        try:
            fd = os.open(seg.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError as e:
            raise SpoolError(f"스풀 쓰기 실패: {type(e).__name__}") from None
        try:
            _write_all(fd, line)
            os.fsync(fd)
        except OSError as e:
            # 반쯤 쓴 줄을 걷어 내고(가능하면) 이 세그먼트엔 더 붙이지 않는다 — 다음 묶음이 끊긴 줄
            # 뒤에 붙어 함께 깨지지 않게
            with contextlib.suppress(OSError):
                os.ftruncate(fd, seg.size)
            self._active.pop(table, None)
            raise SpoolError(f"스풀 쓰기 실패: {type(e).__name__}") from None
        finally:
            os.close(fd)
        seg.size += len(line)
        return drops

    def _new_segment(self, table: str) -> _Segment:
        start = max(_now_us(), self._last_us + 1)
        self._last_us = start
        d = self.directory / table
        try:
            d.mkdir(exist_ok=True, mode=0o700)
            path = d / f"{start:017d}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except OSError as e:
            raise SpoolError(f"스풀 세그먼트를 만들지 못했다: {type(e).__name__}") from None
        _fsync_dir(d)
        seg = _Segment(path, table, start, 0)
        self._segments.append(seg)
        self._active[table] = seg
        return seg

    def _make_room(self, need: int) -> list[DropNotice]:
        drops: list[DropNotice] = []
        while self._segments and self._disk_bytes() + need > self.max_bytes:
            seg = self._segments[0]
            batches, rows, first, last = _count(seg)
            self._remove(seg)
            left = seg.size - seg.offset
            drops.append(DropNotice("oldest", (seg.table,), batches, rows, left, first, last))
        return drops

    def _remove(self, seg: _Segment) -> None:
        with contextlib.suppress(FileNotFoundError):
            seg.path.unlink()
        self._segments.remove(seg)
        if self._active.get(seg.table) is seg:
            del self._active[seg.table]

    # ── 재적재 ──

    def replay(
        self,
        write: Callable[[SpooledBatch], None],
        *,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> ReplayResult:
        """오래된 것부터 `write` 로 보낸다. write 가 `TransientWrite` 를 던지면 그 묶음에서 멈춘다
        (다음에 거기서부터). `RejectedRows` 면 그 행들만, 다른 예외면 묶음을 데드레터로 옮기고
        계속한다. deadline(clock 기준)이 지나면 묶음 사이에서 멈춘다. 다 보낸 세그먼트는 지운다."""
        res = ReplayResult()
        for seg in list(self._segments):
            if seg not in self._segments:
                continue
            for line, end in _lines(seg):
                if deadline is not None and clock() >= deadline:
                    res.stopped = "deadline"
                    return res
                try:
                    batch = decode_batch(line)
                except (ValueError, KeyError, TypeError) as e:
                    res.corrupt_lines += 1
                    res.errors.append(f"{seg.table}: 깨진 줄 ({type(e).__name__})")
                    res.drops += self._dead_raw(line)
                    seg.offset = end
                    continue
                try:
                    write(batch)
                except TransientWrite:
                    res.stopped = "transient"
                    return res
                except RejectedRows as e:
                    bad, n = len(e.rows), len(batch.rows)
                    if bad < n:
                        res.batches += 1
                        res.rows += n - bad
                    if bad:
                        res.dead_batches += 1
                        res.dead_rows += bad
                        if len(res.errors) < 5:
                            res.errors.append(f"{batch.table}: {bad}/{n}행 {e.reason}")
                        res.drops += self._dead_rows(batch, e.rows, line)
                except Exception as e:
                    res.dead_batches += 1
                    res.dead_rows += len(batch.rows)
                    if len(res.errors) < 5:
                        res.errors.append(f"{batch.table}: {type(e).__name__}")
                    res.drops += self._dead_line(batch.table, line, len(batch.rows))
                else:
                    res.batches += 1
                    res.rows += len(batch.rows)
                seg.offset = end
            self._remove(seg)
        return res

    def _dead_line(self, table: str, line: bytes, rows: int) -> list[DropNotice]:
        return self._dead_append(f"{table}.jsonl", line, table, rows)

    def _dead_rows(self, batch: SpooledBatch, rows: Sequence[Row], line: bytes) -> list[DropNotice]:
        """거부된 행만 원래 묶음과 같은 형식(표·열·태그·스풀 시각)의 줄로."""
        try:
            text = encode_batch(batch.table, batch.columns, rows, batch.tag, batch.at)
        except SpoolError:  # 읽어 낸 값이라 다시 적을 수 있다 — 그래도 못 하면 원래 줄째
            text = line
        return self._dead_line(batch.table, text, len(rows))

    def _dead_raw(self, line: bytes) -> list[DropNotice]:
        text = line if line.endswith(b"\n") else line + b"\n"
        return self._dead_append(f"{CORRUPT}.jsonl", text, CORRUPT, 0)

    def _dead_append(self, name: str, line: bytes, table: str, rows: int) -> list[DropNotice]:
        at = self._now()
        if self._dead_bytes() + len(line) > self.dead_max_bytes:
            return [DropNotice("dead_full", (table,), 1, rows, len(line), at, at)]
        d = self.directory / DEAD
        try:
            d.mkdir(exist_ok=True, mode=0o700)
            fd = os.open(d / name, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                _write_all(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            return [DropNotice("dead_full", (table,), 1, rows, len(line), at, at)]
        return []

    # ── 기동 ──

    def _scan(self) -> None:
        found: list[_Segment] = []
        for d in sorted(self.directory.iterdir()):
            if not d.is_dir() or not _TABLE.fullmatch(d.name):
                continue
            for p in d.iterdir():
                m = _SEGMENT.fullmatch(p.name)
                if m is None:
                    continue
                size = p.stat().st_size
                if size == 0:
                    with contextlib.suppress(FileNotFoundError):
                        p.unlink()
                    continue
                found.append(_Segment(p, d.name, int(m.group(1)), size))
        found.sort(key=lambda s: (s.start_us, s.table))
        self._segments = found
        self._last_us = max((s.start_us for s in found), default=0)


def _now_us() -> int:
    return time.time_ns() // 1000


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _fsync_dir(d: Path) -> None:
    """새 파일 이름을 디스크에 (가능한 곳에서만 — 실패해도 파일 fsync 는 했다)."""
    with contextlib.suppress(OSError):
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _lines(seg: _Segment) -> Iterator[tuple[bytes, int]]:
    """세그먼트의 재적재 안 한 줄들과 각 줄 끝 오프셋. 마지막 줄이 끊겼으면 그대로 준다."""
    try:
        f = seg.path.open("rb")
    except FileNotFoundError:
        return
    with f:
        f.seek(seg.offset)
        pos = seg.offset
        while pos < seg.size:
            line = f.readline(seg.size - pos)
            if not line:
                break
            pos += len(line)
            yield line, pos


def _count(seg: _Segment) -> tuple[int, int, datetime | None, datetime | None]:
    """버리기 전에 센다: (묶음, 행, 가장 이른·늦은 스풀 시각). 깨진 줄은 묶음으로만 센다."""
    batches = rows = 0
    first: datetime | None = None
    last: datetime | None = None
    for line, _ in _lines(seg):
        batches += 1
        try:
            obj = json.loads(line)
            rows += int(obj["n"])
            at = datetime.fromisoformat(obj["at"])
        except (ValueError, KeyError, TypeError):
            continue
        first = at if first is None else min(first, at)
        last = at if last is None else max(last, at)
    return batches, rows, first, last
