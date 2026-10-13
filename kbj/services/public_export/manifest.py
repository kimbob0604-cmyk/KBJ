"""공개 산출물의 봉투·목록(manifest)·내용 검사 — 공개 사이트로 나가는 JSON 모양은 여기 한 곳.

- 파일 하나 = 봉투 하나(docs/p3_design.md §5.2 와 같은 모양): `source`·`as_of`(시간대 포함
  ISO)·`quality`(ok·stale·estimated·invalid)·`notes`·`generated_at`·`data`. 절대 규칙 1.
- `manifest.json` = `{schema_version, generated_at, files: [{name, source, as_of, quality, sha256}],
  dirs: [{name, count, bytes, digest, source, as_of, quality}]}`. 프런트
  (`web/src/data/static.ts:parseManifest`)·합치기 스크립트(`web/scripts/merge-public-data.mjs`)와
  같은 모양이다 — 목록에 적힌 것만 Pages 로 간다(이름 허용 목록).
- **하위 폴더(P4 — docs/p4_design.md §1.9·§7)**: `fin/<종목코드>.json`(상장사 DART 재무)·
  `reports/<종목코드>.json`(+ `.xlsx` — dart-report 공개 데모)은 파일이 수천 개라 manifest 에
  파일마다 적지 않는다. 대신 **파일 자체가 봉투**(source `DART`·as_of·quality — W 의 `static.ts` 와
  약속)이고, manifest `dirs` 에 폴더마다 개수·합계 크기·요약 해시(`dir_digest` — 정렬한
  `경로<탭>sha256` 줄들의 sha256) 한 항목을 둔다. 종목코드 `^[0-9A-Z]{6}$`, 엑셀은 같은 이름 JSON 의
  `data.xlsx_sha256` 과 맞아야 한다.
- 내용 검사(§7.4): 어느 깊이의 `source` 값에도 로그인 등급 출처 이름(KIS·KRX·ETF_ISSUERS·YAHOO … —
  DATA_TIERS §1)이 나오면 실패. 글자 전체에 로그인 API 흔적(§6.6 번들 검사와 같은 문자열 — 합친
  데이터도 번들 검사를 다시 받는다)이 있어도 실패. 규칙은 JS 쪽과 같게 둔다(`\\b` 는 ASCII 기준).
- **DART 전용 파일**(P4 공시·재무 — `DART_ONLY_NAMES`·하위 폴더 전부): 모든 깊이의 `source` 가
  정확히 `DART` 여야 하고(p4_design §7 '내용 검사'), 관심종목 흔적(이름에 `watch` 가 든 키·로그인
  스키마 이름 `prv_`)이 0 이어야 한다(관심종목은 개인 데이터 — 공개 파일에 표식·목록을 넣지 않는다).
- 문제 목록에는 값이 아니라 파일 이름·JSON 경로·규칙만 담는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from kbj.core.fin_rows import XLSX_MAX_BYTES
from kbj.core.time import KST

__all__ = [
    "DART_ONLY_NAMES",
    "LOGIN_SOURCES",
    "MANIFEST_NAME",
    "NAME_RE",
    "PUBLIC_FORBIDDEN",
    "QUALITIES",
    "SCHEMA_VERSION",
    "SUBDIRS",
    "SUBDIR_JSON_RE",
    "SUBDIR_XLSX_RE",
    "BlobFile",
    "DirEntry",
    "FileEntry",
    "Manifest",
    "PublicFile",
    "Quality",
    "check_payload",
    "check_tree",
    "dart_only",
    "dir_digest",
    "forbidden_hits",
    "iso_kst",
    "listed_paths",
    "login_source_paths",
    "non_dart_source_paths",
    "read_manifest",
    "sha256_bytes",
    "sha256_text",
    "watch_trace_paths",
    "worst_quality",
]

SCHEMA_VERSION: Final = 1
MANIFEST_NAME: Final = "manifest.json"
NAME_RE: Final = re.compile(r"[a-z0-9_]+\.json")
# P4 하위 폴더 — web/src/data/static.ts PUBLIC_SUBDIR·PUBLIC_DOWNLOAD, merge-public-data.mjs 와 같은
# 규칙
SUBDIRS: Final[tuple[str, ...]] = ("fin", "reports")
SUBDIR_JSON_RE: Final = re.compile(r"(fin|reports)/[0-9A-Z]{6}\.json")
SUBDIR_XLSX_RE: Final = re.compile(r"reports/[0-9A-Z]{6}\.xlsx")
# P4 공시·재무 목록 파일 — 원천이 DART 하나(§7). merge-public-data.mjs DART_ONLY 와 같은 목록
DART_ONLY_NAMES: Final = frozenset({
    "filings_feed.json",
    "earnings_prelim.json",
    "overhang_recent.json",
    "insider_recent.json",
    "earnings_schedule.json",
    "corp_index.json",
})  # fmt: skip
Quality = Literal["ok", "stale", "estimated", "invalid"]
QUALITIES: Final[tuple[Quality, ...]] = ("ok", "stale", "estimated", "invalid")
_Q_RANK: Final[dict[str, int]] = {"ok": 0, "stale": 1, "estimated": 2, "invalid": 3}
# web/scripts/merge-public-data.mjs LOGIN_SOURCES 와 같은 이름들. re.ASCII — JS 의 \b 와 같은 경계
# (한글 뒤에 붙은 'KRX가' 도 잡는다). 한글 이름은 이 쪽에서 더 본다.
LOGIN_SOURCES: Final = re.compile(
    r"\b(KIS|KRX|ETF_ISSUERS|YAHOO|FSC_STOCK|FSC_INDEX|FNGUIDE|CONSENSUS|NAVER)\b"
    r"|한국투자|한국거래소|네이버|야후|에프앤가이드",
    re.IGNORECASE | re.ASCII,
)
# web/scripts/check-bundle.mjs PUBLIC_FORBIDDEN 과 같은 문자열 — (needle, 대소문자 무시)
PUBLIC_FORBIDDEN: Final[tuple[tuple[str, bool], ...]] = (
    ("/api", False),
    ("/telegram", False),
    ("auth/login", False),
    ("x-kbj-csrf", True),
    ("KBJ_", False),
    ("kbj_session", True),
)
_DART: Final = "DART"
_WATCH_KEY: Final = re.compile(r"watch", re.IGNORECASE)
_LOGIN_SCHEMA_TEXT: Final = "prv_"
_AWARE_ISO: Final = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})")
_ENVELOPE_KEYS: Final = frozenset({"source", "as_of", "quality", "notes", "generated_at", "data"})


def iso_kst(ts: datetime) -> str:
    """aware 시각 → KST ISO(초 단위, `+09:00`). naive 는 받지 않는다."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 은 받지 않는다")
    return ts.astimezone(KST).isoformat(timespec="seconds")


def _public_name(name: str) -> bool:
    return name != MANIFEST_NAME and bool(NAME_RE.fullmatch(name) or SUBDIR_JSON_RE.fullmatch(name))


def dart_only(name: str) -> bool:
    """원천이 DART 하나여야 하는 파일인가(P4 목록 파일·하위 폴더 전부)."""
    return name in DART_ONLY_NAMES or "/" in name


@dataclass(frozen=True)
class PublicFile:
    """공개 JSON 파일 하나(봉투). `data` 는 JSON 으로 바꿀 수 있는 값만.

    `name` 은 평평한 이름(`calendar.json`) 또는 하위 폴더 경로(`fin/005930.json`). 하위 폴더 파일과
    `compact=True` 는 공백 없이(크기 상한 — §7) 쓴다.
    """

    name: str
    source: str
    as_of: datetime
    quality: Quality
    data: Mapping[str, Any]
    notes: tuple[str, ...] = ()
    compact: bool = False

    def __post_init__(self) -> None:
        if not _public_name(self.name):
            raise ValueError(f"공개 파일 이름 형식: {self.name!r}")
        if not self.source.strip():
            raise ValueError(f"{self.name}: source 가 비었다")
        if self.quality not in QUALITIES:
            raise ValueError(f"{self.name}: 모르는 quality")
        iso_kst(self.as_of)  # naive 거부

    @property
    def subdir(self) -> str | None:
        return self.name.split("/", 1)[0] if "/" in self.name else None

    def envelope(self, generated_at: datetime) -> dict[str, Any]:
        return {
            "source": self.source,
            "as_of": iso_kst(self.as_of),
            "quality": self.quality,
            "notes": list(self.notes),
            "generated_at": iso_kst(generated_at),
            "data": dict(self.data),
        }

    def render(self, generated_at: datetime) -> str:
        """파일 글자(UTF-8, 키 정렬 없음 — 사람이 읽는 순서 그대로, 끝 줄바꿈). NaN·inf 는 오류."""
        env = self.envelope(generated_at)
        if self.compact or self.subdir is not None:
            return (
                json.dumps(env, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
            )
        return json.dumps(env, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


@dataclass(frozen=True)
class BlobFile:
    """하위 폴더의 바이너리 파일 하나 — 지금은 dart-report 엑셀(`reports/<코드>.xlsx`)뿐."""

    name: str
    body: bytes

    def __post_init__(self) -> None:
        if not SUBDIR_XLSX_RE.fullmatch(self.name):
            raise ValueError(f"공개 바이너리 파일 이름 형식: {self.name!r}")
        if len(self.body) > XLSX_MAX_BYTES:
            raise ValueError(f"{self.name}: {XLSX_MAX_BYTES}B 초과")

    @property
    def subdir(self) -> str:
        return self.name.split("/", 1)[0]


@dataclass(frozen=True)
class FileEntry:
    name: str
    source: str
    as_of: str
    quality: Quality
    sha256: str

    def to_json(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source": self.source,
            "as_of": self.as_of,
            "quality": self.quality,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DirEntry:
    """하위 폴더 한 항목 — 파일마다 적지 않고 개수·합계 크기·요약 해시만(§1.9)."""

    name: str
    count: int
    bytes: int
    digest: str
    source: str
    as_of: str
    quality: Quality

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "bytes": self.bytes,
            "digest": self.digest,
            "source": self.source,
            "as_of": self.as_of,
            "quality": self.quality,
        }


@dataclass(frozen=True)
class Manifest:
    generated_at: datetime
    files: tuple[FileEntry, ...] = ()
    dirs: tuple[DirEntry, ...] = ()
    schema_version: int = SCHEMA_VERSION
    # 만들지 않은 파일과 그 이유(로그·처리기 detail 용 — manifest.json 에는 싣지 않는다)
    skipped: tuple[str, ...] = ()
    names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", tuple(f.name for f in self.files))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": iso_kst(self.generated_at),
            "files": [f.to_json() for f in self.files],
            "dirs": [d.to_json() for d in self.dirs],
        }

    def render(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, indent=1) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def dir_digest(items: Iterable[tuple[str, str]]) -> str:
    """하위 폴더 요약 해시 — (경로, 파일 sha256) 들을 경로 순으로 `경로\\t해시\\n` 줄로 이어 sha256.

    merge-public-data.mjs `dirDigest` 와 같은 정의(경로는 `fin/005930.json` 처럼 데이터 루트 기준).
    """
    lines = "".join(f"{p}\t{h}\n" for p, h in sorted(items))
    return sha256_text(lines)


def worst_quality(qs: Iterable[str]) -> Quality:
    """품질 문자열 중 가장 나쁜 것(ok < stale < estimated < invalid). 비면 ok."""
    out: Quality = "ok"
    for q in qs:
        if q in _Q_RANK and _Q_RANK[q] > _Q_RANK[out]:
            out = q  # pyright: ignore[reportAssignmentType]
    return out


def login_source_paths(v: object, path: str = "$") -> list[str]:
    """JSON 안의 모든 `source` 값(깊이 무관)에서 로그인 등급 출처 이름을 찾는다. 경로만 돌려준다."""
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += login_source_paths(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if k == "source" and isinstance(x, str) and LOGIN_SOURCES.search(x):
                out.append(p)
            else:
                out += login_source_paths(x, p)
    return out


def non_dart_source_paths(v: object, path: str = "$") -> list[str]:
    """모든 `source` 값(깊이 무관)이 정확히 `DART` 인지 — 아닌 곳의 경로만(값 없음)."""
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += non_dart_source_paths(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if k == "source":
                if x != _DART:
                    out.append(p)
            else:
                out += non_dart_source_paths(x, p)
    return out


def watch_trace_paths(v: object, path: str = "$") -> list[str]:
    """관심종목 흔적 — 이름에 `watch` 가 든 키의 경로(값 없음)."""
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += watch_trace_paths(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if _WATCH_KEY.search(str(k)):  # pyright: ignore[reportUnknownArgumentType]
                out.append(p)
            out += watch_trace_paths(x, p)
    return out


def forbidden_hits(text: str) -> list[str]:
    """번들 검사(§6.6)와 같은 금지 문자열 — 찾은 규칙 이름만(값 없음)."""
    low = text.lower()
    return [n for n, ci in PUBLIC_FORBIDDEN if (n.lower() in low if ci else n in text)]


def check_payload(name: str, text: str) -> list[str]:
    """공개 JSON 한 파일의 글자를 검사한다. 문제 목록(빈 목록 = 통과)."""
    problems: list[str] = []
    for hit in forbidden_hits(text):
        problems.append(f"{name}: 로그인 API 흔적 문자열({hit!r} 규칙)")
    try:
        raw: object = json.loads(text)
    except ValueError:
        return [*problems, f"{name}: JSON 이 아님"]
    problems += [f"{name}: source 에 로그인 등급 출처 이름({p})" for p in login_source_paths(raw)]
    if name == MANIFEST_NAME:
        return problems
    if dart_only(name):
        problems += [f"{name}: source 가 DART 가 아님({p})" for p in non_dart_source_paths(raw)]
        problems += [f"{name}: 관심종목 흔적 키({p})" for p in watch_trace_paths(raw)]
        if _LOGIN_SCHEMA_TEXT in text:
            problems.append(f"{name}: 로그인 등급 스키마 이름('{_LOGIN_SCHEMA_TEXT}' 규칙)")
    if not isinstance(raw, dict):
        return [*problems, f"{name}: 봉투가 아님(최상위가 객체가 아님)"]
    env: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    missing = sorted(_ENVELOPE_KEYS - set(env))
    if missing:
        problems.append(f"{name}: 봉투 필드 없음 {missing}")
    if not isinstance(env.get("source"), str) or not env.get("source"):
        problems.append(f"{name}: source 없음")
    for key in ("as_of", "generated_at"):
        val = env.get(key)
        if not isinstance(val, str) or not _AWARE_ISO.fullmatch(val):
            problems.append(f"{name}: {key} 없음·시간대 없음")
    if env.get("quality") not in QUALITIES:
        problems.append(f"{name}: quality 없음·모르는 값")
    return problems


def read_manifest(directory: Path) -> dict[str, Any]:
    path = directory / MANIFEST_NAME
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest.json 최상위가 객체가 아님")
    return raw  # pyright: ignore[reportUnknownVariableType]


def _objects(manifest: Mapping[str, Any], key: str, *, required: bool) -> list[Mapping[str, Any]]:
    items = manifest.get(key)
    if items is None and not required:
        return []  # P3 manifest(하위 폴더 없음)
    if not isinstance(items, list):
        raise ValueError(f"manifest 에 {key} 목록이 없다")
    out: list[Mapping[str, Any]] = []
    for e in items:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(e, dict):
            raise ValueError(f"manifest {key} 항목이 객체가 아님")
        out.append(e)  # pyright: ignore[reportUnknownArgumentType]
    return out


def _entries(manifest: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    return _objects(manifest, "files", required=True)


def _dir_files(directory: Path, sub: str) -> tuple[list[str], list[str]]:
    """하위 폴더의 (허용 이름 경로들, 허용하지 않는 이름 경로들) — 데이터 루트 기준 경로, 정렬."""
    ok: list[str] = []
    bad: list[str] = []
    root = directory / sub
    if not root.is_dir():
        return ok, bad
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(directory).as_posix()
        if SUBDIR_JSON_RE.fullmatch(rel) or SUBDIR_XLSX_RE.fullmatch(rel):
            ok.append(rel)
        else:
            bad.append(rel)
    return ok, bad


def _xlsx_claim(text: str) -> str | None:
    """reports JSON 의 `data.xlsx_sha256`(엑셀이 없으면 None)."""
    try:
        env: object = json.loads(text)
    except ValueError:
        return None
    data: object = env.get("data") if isinstance(env, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    claim: object = data.get("xlsx_sha256") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return claim if isinstance(claim, str) else None


def _check_dir(directory: Path, entry: Mapping[str, Any]) -> list[str]:
    sub = str(entry.get("name", ""))
    paths, bad = _dir_files(directory, sub)
    problems = [f"{p}: 허용하지 않는 이름(하위 폴더)" for p in bad]
    hashes: list[tuple[str, str]] = []
    total = 0
    sha_of: dict[str, str] = {}
    claims: dict[str, str | None] = {}  # 엑셀 경로 → 짝 JSON 이 적은 sha256
    for rel in paths:
        body = (directory / rel).read_bytes()
        total += len(body)
        sha_of[rel] = sha256_bytes(body)
        hashes.append((rel, sha_of[rel]))
        if rel.endswith(".xlsx"):
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{rel}: UTF-8 이 아님")
            continue
        problems += check_payload(rel, text)
        if rel.startswith("reports/"):
            claims[rel.removesuffix(".json") + ".xlsx"] = _xlsx_claim(text)
    for rel in paths:
        if rel.endswith(".xlsx"):
            claim = claims.get(rel)
            if claim is None:
                problems.append(f"{rel}: 짝 JSON 의 xlsx_sha256 이 없음")
            elif claim != sha_of[rel]:
                problems.append(f"{rel}: 짝 JSON 의 xlsx_sha256 과 다름")
    for xl, claim in claims.items():
        if claim is not None and xl not in sha_of:
            problems.append(f"{xl}: 짝 JSON 은 엑셀이 있다는데 파일이 없음")
    if entry.get("count") != len(paths):
        problems.append(f"{sub}/: manifest 개수와 다름")
    if entry.get("bytes") != total:
        problems.append(f"{sub}/: manifest 합계 크기와 다름")
    if entry.get("digest") != dir_digest(hashes):
        problems.append(f"{sub}/: manifest 요약 해시와 다름")
    if entry.get("source") != _DART:
        problems.append(f"{sub}/: source 가 DART 가 아님")
    as_of = entry.get("as_of")
    if not isinstance(as_of, str) or not _AWARE_ISO.fullmatch(as_of):
        problems.append(f"{sub}/: as_of 없음·시간대 없음")
    if entry.get("quality") not in QUALITIES:
        problems.append(f"{sub}/: quality 없음·모르는 값")
    return problems


def check_tree(directory: Path) -> list[str]:
    """내보낸 디렉터리 전체 검사 — manifest 와 각 파일(존재·sha256·메타 일치·내용), 하위 폴더
    (개수·크기·요약 해시·각 파일 봉투·DART 전용·엑셀 짝). 문제 목록."""
    try:
        manifest_text = (directory / MANIFEST_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{MANIFEST_NAME}: 없음"]
    problems = check_payload(MANIFEST_NAME, manifest_text)
    try:
        manifest = read_manifest(directory)
        entries = _entries(manifest)
        dirs = _objects(manifest, "dirs", required=False)
    except ValueError as e:
        return [*problems, f"{MANIFEST_NAME}: {e}"]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{MANIFEST_NAME}: schema_version 이 {SCHEMA_VERSION} 이 아님")
    gen = manifest.get("generated_at")
    if not isinstance(gen, str) or not _AWARE_ISO.fullmatch(gen):
        problems.append(f"{MANIFEST_NAME}: generated_at 없음·시간대 없음")
    listed: set[str] = set()
    for e in entries:
        name = str(e.get("name", ""))
        if not NAME_RE.fullmatch(name) or name == MANIFEST_NAME:
            problems.append(f"{name or '(이름 없음)'}: 허용하지 않는 이름")
            continue
        listed.add(name)
        try:
            text = (directory / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            problems.append(f"{name}: manifest 에 있으나 파일이 없음")
            continue
        if e.get("sha256") != sha256_text(text):
            problems.append(f"{name}: sha256 불일치")
        problems += check_payload(name, text)
        try:
            env: object = json.loads(text)
        except ValueError:
            continue
        if isinstance(env, dict):
            for key in ("source", "as_of", "quality"):
                if env.get(key) != e.get(key):  # pyright: ignore[reportUnknownMemberType]
                    problems.append(f"{name}: manifest 의 {key} 와 파일 봉투가 다름")
    extra = sorted(
        p.name for p in directory.glob("*.json") if p.name not in listed | {MANIFEST_NAME}
    )
    problems += [f"{n}: manifest 에 없는 파일(공개로 나가지 않는다 — 지울 것)" for n in extra]
    seen: set[str] = set()
    for d in dirs:
        sub = str(d.get("name", ""))
        if sub not in SUBDIRS or sub in seen:
            problems.append(f"{sub or '(이름 없음)'}/: 허용하지 않는(또는 겹친) 하위 폴더")
            continue
        seen.add(sub)
        problems += _check_dir(directory, d)
    for sub in SUBDIRS:
        if sub not in seen and any(_dir_files(directory, sub)):
            problems.append(f"{sub}/: manifest 에 없는 하위 폴더(공개로 나가지 않는다 — 지울 것)")
    return problems


def listed_paths(directory: Path) -> list[str]:
    """manifest 가 덮는 파일 경로 전부(평평한 파일 + 적힌 하위 폴더의 허용 이름 파일). manifest
    제외."""
    manifest = read_manifest(directory)
    out = [str(e["name"]) for e in _entries(manifest)]
    for d in _objects(manifest, "dirs", required=False):
        sub = str(d.get("name", ""))
        if sub in SUBDIRS:
            out += _dir_files(directory, sub)[0]
    return out
