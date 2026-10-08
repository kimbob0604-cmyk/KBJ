"""공개 산출물의 봉투·목록(manifest)·내용 검사 — 공개 사이트로 나가는 JSON 모양은 여기 한 곳.

- 파일 하나 = 봉투 하나(docs/p3_design.md §5.2 와 같은 모양): `source`·`as_of`(시간대
  포함 ISO)·`quality`(ok·stale·estimated·invalid)·`notes`·`generated_at`·`data`. 절대 규칙 1.
- `manifest.json` = `{schema_version, generated_at, files: [{name, source, as_of, quality,
  sha256}]}`. 프런트(`web/src/data/static.ts:parseManifest`)·합치기 스크립트
  (`web/scripts/merge-public-data.mjs`)와 같은 모양이다 — 목록에 적힌 파일만 Pages 로
  간다(이름 허용 목록).
- 내용 검사(§7.4): 어느 깊이의 `source` 값에도 로그인 등급 출처 이름(KIS·KRX·
  ETF_ISSUERS·YAHOO … — DATA_TIERS §1)이 나오면 실패. 글자 전체에 로그인 API 흔적
  (§6.6 번들 검사와 같은 문자열 — 합친 데이터도 번들 검사를 다시 받는다)이 있어도 실패.
  규칙은 JS 쪽과 같게 둔다(`\\b` 는 ASCII 기준).
- 문제 목록에는 값이 아니라 파일 이름·JSON 경로·규칙만 담는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from kbj.core.time import KST

__all__ = [
    "LOGIN_SOURCES",
    "MANIFEST_NAME",
    "NAME_RE",
    "PUBLIC_FORBIDDEN",
    "QUALITIES",
    "SCHEMA_VERSION",
    "FileEntry",
    "Manifest",
    "PublicFile",
    "Quality",
    "check_payload",
    "check_tree",
    "forbidden_hits",
    "iso_kst",
    "login_source_paths",
    "read_manifest",
    "sha256_text",
]

SCHEMA_VERSION: Final = 1
MANIFEST_NAME: Final = "manifest.json"
NAME_RE: Final = re.compile(r"[a-z0-9_]+\.json")
Quality = Literal["ok", "stale", "estimated", "invalid"]
QUALITIES: Final[tuple[Quality, ...]] = ("ok", "stale", "estimated", "invalid")
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
_AWARE_ISO: Final = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})")
_ENVELOPE_KEYS: Final = frozenset({"source", "as_of", "quality", "notes", "generated_at", "data"})


def iso_kst(ts: datetime) -> str:
    """aware 시각 → KST ISO(초 단위, `+09:00`). naive 는 받지 않는다."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 은 받지 않는다")
    return ts.astimezone(KST).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PublicFile:
    """공개 JSON 파일 하나(봉투). `data` 는 JSON 으로 바꿀 수 있는 값만."""

    name: str
    source: str
    as_of: datetime
    quality: Quality
    data: Mapping[str, Any]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not NAME_RE.fullmatch(self.name) or self.name == MANIFEST_NAME:
            raise ValueError(f"공개 파일 이름 형식: {self.name!r}")
        if not self.source.strip():
            raise ValueError(f"{self.name}: source 가 비었다")
        if self.quality not in QUALITIES:
            raise ValueError(f"{self.name}: 모르는 quality")
        iso_kst(self.as_of)  # naive 거부

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
        """파일 글자(UTF-8, 키 정렬 없음 — 사람이 읽는 순서 그대로, 끝 줄바꿈)."""
        return json.dumps(self.envelope(generated_at), ensure_ascii=False, indent=1) + "\n"


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
class Manifest:
    generated_at: datetime
    files: tuple[FileEntry, ...] = ()
    schema_version: int = SCHEMA_VERSION
    names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", tuple(f.name for f in self.files))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": iso_kst(self.generated_at),
            "files": [f.to_json() for f in self.files],
        }

    def render(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, indent=1) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _entries(manifest: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest 에 files 목록이 없다")
    out: list[Mapping[str, Any]] = []
    for e in files:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(e, dict):
            raise ValueError("manifest files 항목이 객체가 아님")
        out.append(e)  # pyright: ignore[reportUnknownArgumentType]
    return out


def check_tree(directory: Path) -> list[str]:
    """내보낸 디렉터리 전체 검사 — manifest 와 각 파일(존재·sha256·메타 일치·내용). 문제 목록."""
    try:
        manifest_text = (directory / MANIFEST_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{MANIFEST_NAME}: 없음"]
    problems = check_payload(MANIFEST_NAME, manifest_text)
    try:
        manifest = read_manifest(directory)
        entries = _entries(manifest)
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
    return problems
