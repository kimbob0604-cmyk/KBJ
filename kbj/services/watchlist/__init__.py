"""관심종목 가져오기·보기 — `python -m kbj.services.watchlist import|show` (docs/p4_design.md
§1.8·§3.8, D-P4-13).

관심종목은 개인 데이터다(`prv_journal.watchlist` — 로그인). 사용자가 VM 에 올린 파일(SD
`cache/server_watchlist.json` — 브라우저 localStorage 를 쓴 `[{code, name, market, addedAt}]` — 또는
CSV)을 읽어 그 목록을 **통째로 바꾼다**(`JournalRepo.replace_watchlist` — 한 트랜잭션, 파일이 정본).
그 파일은 레포에 넣지 않는다(U-P4-5).

P8(묶음 JNL — docs/p8_design.md §5, ADR 0033): 쓰기 API(`PUT·DELETE /api/watchlist/{list}/{code}`·
`POST /api/watchlist/{list}/order`)도 이 모듈 하나를 부른다(`add`·`remove`·`reorder` — 출처 `api`).
저장은 가져오기와 같은 `replace_watchlist`(그 목록을 읽어 고친 뒤 통째로 바꾼다 — 한 트랜잭션).
`add` 는 알려진 코드만(유니버스·DART 상장사), 이미 있으면 그대로(멱등), 끝에 붙는다.

- SD JSON 변환은 legacy 이관 매핑과 같은 함수(`kbj.store.legacy_import.transforms.sd_watchlist` — KR
  만, 앞자리 0 채움, 오프셋 있는 addedAt 만, 파일 순서 = 정렬 순서)를 쓴다(두 벌 금지).
- 코드 검증: `^[0-9A-Z]{6}$`, 그리고 유니버스(KRX 기본정보) 또는 DART 상장사 고유번호 표에 있어야
  한다. 모르는 코드·형식 오류·중복은 **거절 목록**(코드 값만)으로 출력하고, 거절이 있으면 쓰지
  않는다(`--skip-rejects` 로 나머지만 쓸 수 있다). 미국 티커(SD market=us)는 이 표 대상이 아니라
  따로 센다.
- 출력에는 종목코드·수만 — 비밀이 없다. 오류는 삼키지 않는다(종료 코드).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, Protocol

from kbj.core.fin_rows import WatchItem, is_stock_code
from kbj.core.time import KST
from kbj.store.legacy_import.sources import SourceError, iter_sd_watchlist, refuse_token_cache
from kbj.store.legacy_import.transforms import Drop, kr_code, parse_aware, sd_watchlist
from kbj.store.repos.filings import FilingsRepo
from kbj.store.repos.journal import JournalRepo
from kbj.store.repos.market import MarketRepo

__all__ = [
    "API_LOADED_BY",
    "FORMATS",
    "LOADED_BY",
    "Format",
    "ImportPlan",
    "Reject",
    "WatchRepos",
    "WatchRow",
    "WatchWriteError",
    "add",
    "apply_plan",
    "describe",
    "known_codes",
    "plan_import",
    "read_csv",
    "read_file",
    "read_sd_json",
    "remove",
    "reorder",
    "source_of",
]

LOADED_BY: Final = "cli.watchlist"
API_LOADED_BY: Final = "api.watchlist"
FORMATS: Final = ("sd-json", "csv")
MAX_ITEMS: Final = 32767  # sort smallint 상한(0013)
Format = Literal["sd-json", "csv"]


class WatchRepos(Protocol):
    """CLI 가 쓰는 저장소 — `kbj.store.repos.Repos`(Pg)·`MemoryRepos`(시험) 둘 다 맞는다."""

    @property
    def journal(self) -> JournalRepo: ...

    @property
    def market(self) -> MarketRepo: ...

    @property
    def filings(self) -> FilingsRepo: ...


@dataclass(frozen=True)
class Reject:
    """거절 한 줄 — 위치(파일 안 몇 번째 — 1부터)·값(코드 또는 원문 앞부분)·사유."""

    line: int
    value: str
    reason: Literal["bad_code", "unknown_code", "duplicate", "bad_row"]


@dataclass(frozen=True)
class WatchRow:
    line: int
    code: str
    name: str | None
    added_at: datetime | None
    note: str | None


@dataclass
class ImportPlan:
    list_name: str
    source: str
    items: list[WatchItem] = field(default_factory=list[WatchItem])
    rejects: list[Reject] = field(default_factory=list[Reject])
    skipped_foreign: int = 0  # SD market≠kr(미국 티커) — 이 표 대상이 아니다

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.items]


# ── 파일 읽기 ─────────────────────────────────────────────────────────────────────────


def read_sd_json(path: Path) -> tuple[list[WatchRow], list[Reject], int]:
    """SD `server_watchlist.json` → 행·거절·미국 티커 수(legacy 이관과 같은 변환)."""
    rows: list[WatchRow] = []
    rejects: list[Reject] = []
    foreign = 0
    for raw in iter_sd_watchlist(path):
        idx = raw.get("_index")
        line = idx + 1 if isinstance(idx, int) else 0
        for cand in sd_watchlist(raw):
            if isinstance(cand, Drop):
                if cand.reason == "not_kr":
                    foreign += 1
                else:
                    value = str(raw.get("code") or "")[:16]
                    reason = "bad_code" if cand.reason == "bad_code" else "bad_row"
                    rejects.append(Reject(line, value, reason))
                continue
            name = cand.get("name_at_add")
            added = cand.get("added_at")
            rows.append(
                WatchRow(
                    line=line,
                    code=str(cand["code"]),
                    name=name if isinstance(name, str) else None,
                    added_at=added if isinstance(added, datetime) else None,
                    note=None,
                )
            )
    return rows, rejects, foreign


def _csv_lines(text: str) -> Iterator[tuple[int, list[str]]]:
    for n, rec in enumerate(csv.reader(io.StringIO(text)), start=1):
        cells = [c.strip() for c in rec]
        if not any(cells) or cells[0].startswith("#"):
            continue
        yield n, cells


def read_csv(path: Path) -> tuple[list[WatchRow], list[Reject], int]:
    """CSV `code[,name[,note[,added_at]]]` — 머리 줄(첫 칸이 'code'·'종목코드')은 건너뛴다."""
    refuse_token_cache(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise SourceError(f"CSV 를 읽지 못했다: {path.name} ({type(e).__name__})") from None
    rows: list[WatchRow] = []
    rejects: list[Reject] = []
    for n, cells in _csv_lines(text):
        head = cells[0].lower()
        if n == 1 and head in ("code", "종목코드", "stock_code"):
            continue
        code = kr_code(cells[0])
        if code is None:
            rejects.append(Reject(n, cells[0][:16], "bad_code"))
            continue
        name = cells[1] if len(cells) > 1 and cells[1] else None
        note = cells[2] if len(cells) > 2 and cells[2] else None
        added = parse_aware(cells[3]) if len(cells) > 3 else None
        rows.append(WatchRow(n, code, name, added, note))
    return rows, rejects, 0


# ── 계획·적용 ─────────────────────────────────────────────────────────────────────────


def known_codes(repos: WatchRepos, now: datetime) -> set[str]:
    """알려진 종목코드 = 유니버스(오늘(KST) 이하 최신) ∪ DART 상장사 고유번호 표."""
    day = now.astimezone(KST).date()  # 운영 시계는 UTC — 날짜는 KST 로(자정~09:00 KST 어긋남)
    codes = {u.code for u in repos.market.universe(day)}
    codes |= set(repos.filings.corp_index())
    return codes


def plan_import(
    rows: Iterable[WatchRow],
    rejects: Iterable[Reject],
    *,
    list_name: str,
    source: str,
    known: set[str] | None,
    foreign: int = 0,
) -> ImportPlan:
    """행 → 넣을 항목(파일 순서 = 정렬)·거절. `known` 이 None 이면 존재 검사를 하지 않는다."""
    plan = ImportPlan(list_name=list_name, source=source, skipped_foreign=foreign)
    plan.rejects.extend(rejects)
    seen: set[str] = set()
    for r in rows:
        if not is_stock_code(r.code):
            plan.rejects.append(Reject(r.line, r.code, "bad_code"))
            continue
        if r.code in seen:
            plan.rejects.append(Reject(r.line, r.code, "duplicate"))
            continue
        if known is not None and r.code not in known:
            plan.rejects.append(Reject(r.line, r.code, "unknown_code"))
            continue
        if len(plan.items) >= MAX_ITEMS:
            plan.rejects.append(Reject(r.line, r.code, "bad_row"))
            continue
        seen.add(r.code)
        plan.items.append(
            WatchItem(
                list_name=list_name,
                code=r.code,
                name_at_add=r.name,
                added_at=r.added_at,
                sort=len(plan.items),
                note=r.note,
                source=source,
            )
        )
    plan.rejects.sort(key=lambda x: (x.line, x.value))
    return plan


def apply_plan(repos: WatchRepos, plan: ImportPlan, *, now: datetime) -> int:
    """목록을 통째로 바꾼다(한 트랜잭션). 쓴 행 수."""
    return repos.journal.replace_watchlist(plan.list_name, plan.items, loaded_by=LOADED_BY, now=now)


def read_file(fmt: Format, path: Path) -> tuple[list[WatchRow], list[Reject], int]:
    if fmt == "sd-json":
        return read_sd_json(path)
    return read_csv(path)


def source_of(fmt: Format) -> str:
    return "import:sd" if fmt == "sd-json" else "import:csv"


def describe(plan: ImportPlan) -> Sequence[str]:
    """사람이 읽는 요약 줄(코드 값·수만)."""
    lines = [
        f"목록 {plan.list_name}: 넣을 종목 {len(plan.items)}개, 거절 {len(plan.rejects)}개"
        + (f", 미국 티커 {plan.skipped_foreign}개 건너뜀" if plan.skipped_foreign else ""),
    ]
    for rj in plan.rejects:
        lines.append(f"  거절 {rj.line}행 {rj.value or '(빈 값)'} — {rj.reason}")
    return lines


# ── 쓰기 API(P8) ──────────────────────────────────────────────────────────────────────


class WatchWriteError(ValueError):
    """쓰기 거절 — 사유 코드(`bad_code`·`unknown_code`·`not_in_list`·`bad_order`·`full`)만."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _resorted(items: Sequence[WatchItem]) -> list[WatchItem]:
    return [replace(it, sort=i) for i, it in enumerate(items)]


def _write(repos: WatchRepos, list_name: str, items: Sequence[WatchItem], now: datetime) -> None:
    repos.journal.replace_watchlist(list_name, _resorted(items), loaded_by=API_LOADED_BY, now=now)


def add(
    repos: WatchRepos,
    list_name: str,
    code: str,
    *,
    now: datetime,
    note: str | None = None,
    known: set[str] | None = None,
) -> tuple[list[WatchItem], bool]:
    """목록 끝에 더한다. 이미 있으면 바꾸지 않는다. (목록, 새로 더했나)."""
    if not is_stock_code(code):
        raise WatchWriteError("bad_code")
    items = repos.journal.watchlist(list_name)
    if any(it.code == code for it in items):
        return items, False
    k = known if known is not None else known_codes(repos, now)
    if code not in k:
        raise WatchWriteError("unknown_code")
    if len(items) >= MAX_ITEMS:
        raise WatchWriteError("full")
    name = next((u.name for u in repos.market.universe(now.astimezone(KST).date())
                 if u.code == code), None)  # fmt: skip
    new = WatchItem(
        list_name=list_name,
        code=code,
        name_at_add=name,
        added_at=now,
        sort=len(items),
        note=note,
        source="api",
    )
    _write(repos, list_name, [*items, new], now)
    return repos.journal.watchlist(list_name), True


def remove(repos: WatchRepos, list_name: str, code: str, *, now: datetime) -> list[WatchItem]:
    items = repos.journal.watchlist(list_name)
    kept = [it for it in items if it.code != code]
    if len(kept) == len(items):
        raise WatchWriteError("not_in_list")
    _write(repos, list_name, kept, now)
    return repos.journal.watchlist(list_name)


def reorder(
    repos: WatchRepos, list_name: str, codes: Sequence[str], *, now: datetime
) -> list[WatchItem]:
    """codes 가 목록의 종목을 정확히 한 번씩 담아야 한다(빠짐·더함·중복은 `bad_order`)."""
    items = repos.journal.watchlist(list_name)
    by_code = {it.code: it for it in items}
    if len(codes) != len(set(codes)) or set(codes) != set(by_code):
        raise WatchWriteError("bad_order")
    _write(repos, list_name, [by_code[c] for c in codes], now)
    return repos.journal.watchlist(list_name)
