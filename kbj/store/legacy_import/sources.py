"""이관 원본 열기 — 옛 SQLite 는 읽기 전용, JSON 은 읽기만(docs/p2_design.md §8.6).

- SQLite 는 `file:<경로>?mode=ro&immutable=1` URI 로 연다: 쓰기·잠금·WAL 파일 생성 없이 읽는다
  (원본은 맥 로컬 사본 — ADR 0001 Q12. 원본을 건드리지 않는다). 없는 파일은 만들지 않고 오류.
- 원본 파일의 sha256 을 `ops.legacy_import.source_sha256` 에 남긴다. 폴더 원본(ET `state/*/`)은
  안의 대상 파일 (상대 경로, sha256) 목록의 sha256.
- 행은 dict(열 이름 → 값)로 준다. 값·키를 로그에 찍지 않는다(호출자 몫 — 이 모듈은 출력하지 않는다).
- 토큰 캐시(`kis_token.json`·`.kis_token.json`·`kis.token.json`)는 열지 않는다 — 이름으로 거부한다
  (§8.4 "절대 이관·열람 안 함").
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

SQLITE_SOURCES = ("sd", "board", "us", "backtest", "us_backtest", "etf")
JSON_SOURCES = ("inbox", "stockflows", "krflows")
_TOKEN_FILE = re.compile(r"(?i)(^|[._-])(kis[._-]?token|token)([._-]|$)|\.token\.json$")
_CHUNK = 1 << 20


class SourceError(RuntimeError):
    """원본을 열 수 없다(없음·형식·토큰 캐시). 문구에 값은 없다."""


def refuse_token_cache(path: Path) -> None:
    if _TOKEN_FILE.search(path.name):
        raise SourceError(f"토큰 캐시로 보이는 파일은 열지 않는다: {path.name}")


def file_sha256(path: Path) -> str:
    """파일 전체의 sha256(16진수). 폴더면 그 안 대상 파일들의 (상대 경로, sha256) 목록의 sha256."""
    if path.is_dir():
        h = hashlib.sha256()
        for p in sorted(stockflow_files(path)):
            rel = p.relative_to(path).as_posix()
            h.update(f"{rel}\0{file_sha256(p)}\n".encode())
        return h.hexdigest()
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def open_sqlite_ro(path: Path) -> sqlite3.Connection:
    """읽기 전용·불변으로 연다. 쓰기는 `sqlite3.OperationalError`(attempt to write a readonly …)."""
    refuse_token_cache(path)
    p = Path(path).resolve()
    if not p.is_file():
        raise SourceError(f"원본 SQLite 파일이 없다: {path.name}")
    conn = sqlite3.connect(f"file:{quote(str(p))}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(r[0]) for r in rows}


def iter_sqlite(conn: sqlite3.Connection, select_sql: str) -> Iterator[dict[str, object]]:
    for row in conn.execute(select_sql):
        yield {k: row[k] for k in row.keys()}


def read_json(path: Path) -> object:
    refuse_token_cache(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SourceError(f"JSON 을 읽지 못했다: {path.name} ({type(e).__name__})") from None


def _dict(v: object) -> dict[str, object]:
    return {str(k): x for k, x in v.items()} if isinstance(v, dict) else {}


# ── JSON 원본 → 행 ───────────────────────────────────────────────────────────────────────


def iter_inbox(path: Path) -> Iterator[dict[str, object]]:
    """ET `state/inbox.json` 의 items(board/ingest/tg_inbox.py 계약)."""
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise SourceError("inbox.json 형식이 아니다(객체가 아님)")
    items = _dict(doc).get("items")
    if not isinstance(items, list):
        raise SourceError("inbox.json 에 items 목록이 없다")
    for it in items:
        yield _dict(it) if isinstance(it, dict) else {"_bad": True}


def stockflow_files(state_dir: Path) -> list[Path]:
    """ET `state/<YYYYMMDD>/stockflows.json` — 최근 날짜 폴더부터(같은 종목·날짜는 최근 파일
    우선)."""
    files = [
        p for p in state_dir.glob("*/stockflows.json") if re.fullmatch(r"\d{8}", p.parent.name)
    ]
    return sorted(files, key=lambda p: p.parent.name, reverse=True)


def iter_stockflows(state_dir: Path) -> Iterator[dict[str, object]]:
    """ET 종목별 수급(board/ingest/stockflows.py `collect` 의 by_code 항목) — 항목마다 한 행."""
    if not state_dir.is_dir():
        raise SourceError(f"stockflows 원본은 ET state 폴더여야 한다: {state_dir.name}")
    for p in stockflow_files(state_dir):
        doc = _dict(read_json(p))
        for code, entry in _dict(doc.get("by_code")).items():
            row = _dict(entry)
            row.setdefault("code", code)
            yield row


def iter_krflows(path: Path) -> Iterator[dict[str, object]]:
    """ET monitor/kr `cache/flows.json` — {by_code: {code: {YYYYMMDD: {f, o, p}}}} 를
    (종목, 날짜) 행으로."""
    doc = _dict(read_json(path))
    unit = doc.get("unit")
    source = doc.get("source")
    for code, by_date in _dict(doc.get("by_code")).items():
        for day, vals in _dict(by_date).items():
            yield {"code": code, "date": day, "unit": unit, "source": source, **_dict(vals)}
